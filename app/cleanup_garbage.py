"""
Server-side cleanup for garbage patterns the v1.5+ client now blocks at the resolver.

Each category mirrors a resolver guard in lib/features/sync/services/sync_path_resolver.dart.
This script removes rows uploaded by pre-fix client versions.

Usage (inside the server container or with venv active):

    python -m app.cleanup_garbage                  # dry-run, all categories
    python -m app.cleanup_garbage --apply          # commit
    python -m app.cleanup_garbage --apply --only psp_stray,wii_nand
    python -m app.cleanup_garbage --user-id 1     # restrict to one user
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from typing import Callable, Iterable

import psycopg2

from app.config import DB_HOST, DB_NAME, DB_USER, DB_PASS, STORAGE_DIR


# ----- Pattern matchers (one per category) -----------------------------------
# Each returns True if the row is garbage under that category.

_PROFILE_ID_RE = re.compile(r'^[0-9A-Fa-f]{32}$')
_OS_DUP_RE = re.compile(r'(.*?)([ _-]?(?:\(\d+\)|copy|Copy))(\.[^.]+)?$')


def _is_psp_stray(path: str) -> bool:
    # psp/<file> or ppsspp/<file> with no SAVEDATA/ or PPSSPP_STATE/ anchor.
    parts = path.split('/')
    if len(parts) < 2:
        return False
    if parts[0].lower() not in ('psp', 'ppsspp'):
        return False
    upper = path.upper()
    return 'SAVEDATA/' not in upper and 'PPSSPP_STATE/' not in upper


def _is_wii_nand_blob(path: str) -> bool:
    parts = path.split('/')
    if not parts:
        return False
    top = parts[0].lower()
    if top not in ('wii', 'dolphin', 'gc'):
        return False
    lower = path.lower()
    return lower.endswith('.app') or lower.endswith('.tmd') or lower.endswith('.wad')


def _is_self_nested(path: str) -> tuple[bool, str]:
    # gc/GC/..., wii/Wii/..., psp/SAVEDATA/psp/..., dolphin/Dolphin/...
    parts = path.split('/')
    if len(parts) < 2:
        return False, ''
    top = parts[0].lower()
    # Direct self-nest: top/Top/...
    if len(parts) >= 2 and parts[1].lower() == top:
        relocated = '/'.join([parts[0]] + parts[2:])
        return True, relocated
    # PSP self-nest one level deeper: psp/SAVEDATA/psp/...
    if top in ('psp', 'ppsspp') and len(parts) >= 3:
        if parts[1].upper() in ('SAVEDATA', 'PPSSPP_STATE') and parts[2].lower() == top:
            relocated = '/'.join([parts[0], parts[1]] + parts[3:])
            return True, relocated
    return False, ''


def _is_retroarch_singular(path: str) -> tuple[bool, str]:
    # RetroArch/file/<core>/<save>  →  RetroArch/files/<core>/<save>
    if path.lower().startswith('retroarch/file/'):
        return True, 'RetroArch/files/' + path[len('RetroArch/file/'):]
    return False, ''


def _is_switch_kitchen_sink(path: str) -> bool:
    # Real switch path: switch/nand/user/save/0000000000000000/<32-hex>/...
    # Garbage: switch/<not-32-hex>/... where the second segment is e.g. a
    # game name, ROM id, or random folder dump.
    parts = path.split('/')
    if len(parts) < 2:
        return False
    if parts[0].lower() != 'switch':
        return False
    # Allow well-formed paths through.
    if parts[1].lower() == 'nand':
        return False
    # Anything else under switch/ that doesn't start with a 32-hex profile
    # at depth 1 is the kitchen-sink form.
    return not _PROFILE_ID_RE.match(parts[1])


def _is_syncthing_conflict(path: str) -> bool:
    return '.sync-conflict-' in path.lower()


def _is_os_dup(path: str) -> bool:
    # Filename ends with (1), (2), " copy", "- Copy", etc.
    name = path.rsplit('/', 1)[-1]
    base, _, ext = name.rpartition('.')
    stem = base if base else name
    return bool(re.search(r'(\(\d+\)|[ _-]copy|[ _-]Copy)$', stem))


# ----- Category definitions --------------------------------------------------

class Category:
    def __init__(
        self,
        key: str,
        description: str,
        matcher: Callable[[str], bool],
        relocator: Callable[[str], str] | None = None,
    ):
        self.key = key
        self.description = description
        self.matcher = matcher
        self.relocator = relocator  # if set, "fix" instead of "delete"


def _self_nest_relocator(path: str) -> str:
    ok, new = _is_self_nested(path)
    return new if ok else path


def _ra_singular_relocator(path: str) -> str:
    ok, new = _is_retroarch_singular(path)
    return new if ok else path


CATEGORIES: list[Category] = [
    Category('psp_stray', 'PSP/PPSSPP flat files at namespace root (no SAVEDATA anchor)',
             _is_psp_stray),
    Category('wii_nand', 'Wii NAND content blobs (.app/.tmd/.wad)',
             _is_wii_nand_blob),
    Category('self_nested', 'Self-nested namespaces (gc/GC/, psp/SAVEDATA/psp/, ...)',
             lambda p: _is_self_nested(p)[0],
             _self_nest_relocator),
    Category('retroarch_singular', 'RetroArch/file/ singular (relocate to RetroArch/files/)',
             lambda p: _is_retroarch_singular(p)[0],
             _ra_singular_relocator),
    Category('switch_kitchen_sink', 'Switch entries not under nand/user/save/<16hex>/<32hex>/',
             _is_switch_kitchen_sink),
    Category('syncthing_conflict', 'Syncthing .sync-conflict-* artifacts',
             _is_syncthing_conflict),
    Category('os_dup', 'OS duplicates: " (1)", " copy", "- Copy" suffix',
             _is_os_dup),
]


# ----- Cross-namespace duplicate detection (separate pass) -------------------

def _find_cross_namespace_dups(cursor, user_id: int | None):
    """
    Group rows by (user_id, hash, size) where the same content lives under
    multiple top-level system folders. Returns [(keeper_id, [dup_id, ...])].
    Keeper heuristic: prefer the row whose top-level dir matches a "real"
    system (gc/ps1/nds/wii/...) and whose extension matches it; fall back
    to the lowest id (oldest record).
    """
    where = "WHERE hash IS NOT NULL AND hash != ''"
    params: list = []
    if user_id is not None:
        where += " AND user_id = %s"
        params.append(user_id)

    cursor.execute(
        f"""
        SELECT user_id, hash, size, array_agg(id ORDER BY id), array_agg(path ORDER BY id)
        FROM files
        {where}
        GROUP BY user_id, hash, size
        HAVING COUNT(*) > 1
        """,
        params,
    )
    groups = []
    for uid, h, size, ids, paths in cursor.fetchall():
        tops = {p.split('/', 1)[0].lower() for p in paths if '/' in p}
        if len(tops) < 2:
            continue  # same namespace, not a cross-namespace dup
        keeper_idx = _pick_keeper(paths)
        keeper_id = ids[keeper_idx]
        dup_ids = [i for i in ids if i != keeper_id]
        dup_paths = [p for p, i in zip(paths, ids) if i != keeper_id]
        groups.append((uid, keeper_id, paths[keeper_idx], dup_ids, dup_paths, h, size))
    return groups


_EXT_TO_SYSTEM = {
    '.srm': {'retroarch'},
    '.state': {'retroarch'},
    '.ps2': {'ps2'},
    '.mcr': {'ps1'},
    '.mcd': {'ps1'},
    '.gci': {'gc'},
    '.raw': {'gc'},
    '.dsv': {'nds'},
    '.sav': set(),  # ambiguous
}


def _pick_keeper(paths: list[str]) -> int:
    # Prefer the path whose top-level dir matches its file extension.
    best = 0
    best_score = -1
    for i, p in enumerate(paths):
        top = p.split('/', 1)[0].lower()
        ext = os.path.splitext(p)[1].lower()
        score = 0
        if ext in _EXT_TO_SYSTEM and top in _EXT_TO_SYSTEM[ext]:
            score += 10
        if top != 'dc':  # dc/ was the contamination dumping ground
            score += 1
        if score > best_score:
            best_score = score
            best = i
    return best


# ----- Action helpers --------------------------------------------------------

def _physical_path(user_id: int, db_path: str) -> str:
    return os.path.join(STORAGE_DIR, str(user_id), db_path.lstrip('/\\'))


def _delete_row(cursor, user_id: int, file_id: int, db_path: str, apply: bool) -> None:
    phys = _physical_path(user_id, db_path)
    if apply:
        try:
            if os.path.exists(phys):
                os.remove(phys)
        except OSError as e:
            print(f"  ! could not unlink {phys}: {e}", file=sys.stderr)
        cursor.execute("DELETE FROM files WHERE id = %s", (file_id,))


def _relocate_row(
    cursor, user_id: int, file_id: int, old_path: str, new_path: str, apply: bool
) -> str:
    """Returns one of: 'moved', 'merged', 'skipped'."""
    if old_path == new_path:
        return 'skipped'
    if not apply:
        return 'moved'

    old_phys = _physical_path(user_id, old_path)
    new_phys = _physical_path(user_id, new_path)

    cursor.execute(
        "SELECT id FROM files WHERE user_id = %s AND path = %s",
        (user_id, new_path),
    )
    collision = cursor.fetchone()

    if collision:
        # Destination already exists in DB — drop the duplicate source.
        try:
            if os.path.exists(old_phys):
                os.remove(old_phys)
        except OSError as e:
            print(f"  ! could not unlink {old_phys}: {e}", file=sys.stderr)
        cursor.execute("DELETE FROM files WHERE id = %s", (file_id,))
        return 'merged'

    try:
        os.makedirs(os.path.dirname(new_phys), exist_ok=True)
        if os.path.exists(old_phys) and not os.path.exists(new_phys):
            os.rename(old_phys, new_phys)
    except OSError as e:
        print(f"  ! could not move {old_phys} → {new_phys}: {e}", file=sys.stderr)
        return 'skipped'

    cursor.execute(
        "UPDATE files SET path = %s WHERE id = %s",
        (new_path, file_id),
    )
    return 'moved'


# ----- Main pass -------------------------------------------------------------

def run(apply: bool, only: set[str] | None, user_id: int | None) -> None:
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    conn.autocommit = apply  # in dry-run we don't write anyway
    cursor = conn.cursor()

    where = ""
    params: list = []
    if user_id is not None:
        where = "WHERE user_id = %s"
        params.append(user_id)
    cursor.execute(f"SELECT id, user_id, path FROM files {where}", params)
    rows = cursor.fetchall()

    print(f"Scanning {len(rows):,} rows ({'APPLY' if apply else 'DRY-RUN'})\n")

    per_cat_counts: dict[str, int] = defaultdict(int)
    per_cat_examples: dict[str, list[str]] = defaultdict(list)
    relocate_outcomes: dict[str, int] = defaultdict(int)

    for file_id, uid, path in rows:
        for cat in CATEGORIES:
            if only and cat.key not in only:
                continue
            if not cat.matcher(path):
                continue
            per_cat_counts[cat.key] += 1
            if len(per_cat_examples[cat.key]) < 3:
                per_cat_examples[cat.key].append(path)
            if cat.relocator:
                new_path = cat.relocator(path)
                outcome = _relocate_row(cursor, uid, file_id, path, new_path, apply)
                relocate_outcomes[f"{cat.key}:{outcome}"] += 1
            else:
                _delete_row(cursor, uid, file_id, path, apply)
            break  # one category per row is enough

    if not only or 'xnamespace' in only:
        print("\n--- Cross-namespace duplicate scan ---")
        dup_groups = _find_cross_namespace_dups(cursor, user_id)
        print(f"Found {len(dup_groups)} content-hash groups spanning multiple systems.")
        for uid, keeper_id, keeper_path, dup_ids, dup_paths, h, _size in dup_groups[:20]:
            print(f"  keep [{keeper_id}] {keeper_path}")
            for did, dp in zip(dup_ids, dup_paths):
                print(f"  drop [{did}] {dp}")
        if len(dup_groups) > 20:
            print(f"  ... and {len(dup_groups) - 20} more groups")
        if apply:
            for uid, _keeper_id, _kp, dup_ids, dup_paths, _h, _s in dup_groups:
                for did, dp in zip(dup_ids, dup_paths):
                    _delete_row(cursor, uid, did, dp, apply=True)

    print("\n--- Per-category summary ---")
    for cat in CATEGORIES:
        n = per_cat_counts.get(cat.key, 0)
        if n == 0:
            continue
        verb = 'relocate' if cat.relocator else 'delete'
        print(f"  {cat.key:22s} {n:>6} rows to {verb}  ({cat.description})")
        for ex in per_cat_examples[cat.key]:
            print(f"      e.g. {ex}")

    if relocate_outcomes:
        print("\n--- Relocate outcomes ---")
        for k, v in sorted(relocate_outcomes.items()):
            print(f"  {k:30s} {v}")

    if not apply:
        print("\nDry-run only. Re-run with --apply to commit.")

    cursor.close()
    conn.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true', help='Commit changes; default is dry-run.')
    p.add_argument('--only', help='Comma-separated category keys (see code) plus optional "xnamespace".')
    p.add_argument('--user-id', type=int, help='Restrict cleanup to a single user.')
    args = p.parse_args()

    only = set(args.only.split(',')) if args.only else None
    run(apply=args.apply, only=only, user_id=args.user_id)


if __name__ == '__main__':
    main()
