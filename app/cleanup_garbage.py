"""
Server-side cleanup for garbage patterns the v1.5+ client now blocks at the resolver.

Each category mirrors a resolver guard in lib/features/sync/services/sync_path_resolver.dart.
This script quarantines rows uploaded by pre-fix client versions. Physical
files are moved under ``storage/<user>/.cleanup_trash`` before their database
rows are removed, so an incorrect classifier can be recovered.

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
import time
from collections import defaultdict
from typing import Callable, Iterable

import psycopg2

from app.config import DB_HOST, DB_NAME, DB_USER, DB_PASS, STORAGE_DIR


# ----- Pattern matchers (one per category) -----------------------------------
# Each returns True if the row is garbage under that category.

_PROFILE_ID_RE = re.compile(r'^[0-9A-Fa-f]{32}$')
# Switch title ID: 16 hex chars starting with "01". Mirrors the app's
# SwitchProfileResolver.isValidTitleId and the RomM SwitchHandler.
_TITLE_ID_RE = re.compile(r'^01[0-9A-Fa-f]{14}$')
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
    # Legitimate switch cloud forms (must NOT be deleted):
    #   1. switch/nand/...                      — legacy deep-nand layout
    #   2. switch/<32-hex profile>/...          — profile-first layout
    #   3. switch/<16-hex titleId>/...          — the CANONICAL title-ID-first
    #      form the client actually emits. SyncPathResolver.getCloudRelPath
    #      flattens every switch save to "<titleId>/<rest>" (see
    #      sync_path_resolver.dart), stripping the nand/user/save/<profile>
    #      prefix. Treating this as garbage deleted real saves, leaving the
    #      directory skeleton behind ("N directories, 0 files").
    # Garbage: switch/<game name | rom id | random dump>/...
    parts = path.split('/')
    if len(parts) < 2:
        return False
    if parts[0].lower() != 'switch':
        return False
    if parts[1].lower() == 'nand':
        return False
    if _PROFILE_ID_RE.match(parts[1]):
        return False
    if _TITLE_ID_RE.match(parts[1]):
        return False
    # Anything else under switch/ at depth 1 is the kitchen-sink form.
    return True


def _is_syncthing_conflict(path: str) -> bool:
    return '.sync-conflict-' in path.lower()


def _is_os_dup(path: str) -> bool:
    # Filename ends with (1), (2), " copy", "- Copy", etc.
    name = path.rsplit('/', 1)[-1]
    base, _, ext = name.rpartition('.')
    stem = base if base else name
    return bool(re.search(r'(\(\d+\)|[ _-]copy|[ _-]Copy)$', stem))


def _is_bak(path: str) -> bool:
    # RetroArch rotates the previous save/state to .bak on every write. These
    # are local-only backups and never belong in cloud storage. Apply across
    # all namespaces (.bak in psp/, gc/, etc. is just as wrong).
    return path.lower().endswith('.bak')


_RA_RESERVED_SECOND = {'saves', 'states', 'files'}
_RA_SAVE_EXTS = ('.srm', '.sav', '.save', '.dsv', '.eep', '.fla', '.mcr', '.fds')


def _is_ra_core_misnested(path: str) -> bool:
    """RetroArch/<core>/<file...> where <core> is not saves/states/files and the
    leaf file has a known save or state extension. Pre-v1.5 dumped these into
    per-core dirs directly under RetroArch/ instead of saves/<core>/ or
    states/<core>/."""
    parts = path.split('/')
    if len(parts) < 3:
        return False
    if parts[0].lower() != 'retroarch':
        return False
    if parts[1].lower() in _RA_RESERVED_SECOND:
        return False
    fname = parts[-1].lower()
    if fname.endswith('.bak'):
        return False  # bak_files owns these
    is_state = '.state' in fname or fname.endswith('.s00') or bool(re.search(r'\.s\d+$', fname))
    is_save = fname.endswith(_RA_SAVE_EXTS)
    return is_state or is_save


def _ra_core_misnested_relocator(path: str) -> str:
    if not _is_ra_core_misnested(path):
        return path
    parts = path.split('/')
    fname = parts[-1].lower()
    is_state = '.state' in fname or fname.endswith('.s00') or bool(re.search(r'\.s\d+$', fname))
    bucket = 'states' if is_state else 'saves'
    # RetroArch / <bucket> / <core> / <...remaining...>
    return '/'.join([parts[0], bucket] + parts[1:])


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
    # Order matters — first match wins per row. Put delete-everywhere rules
    # (bak_files, syncthing_conflict) BEFORE more-specific relocators so we
    # never relocate a backup file.
    Category('bak_files', 'RetroArch .bak rotation files (anywhere)',
             _is_bak),
    Category('syncthing_conflict', 'Syncthing .sync-conflict-* artifacts',
             _is_syncthing_conflict),
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
    Category('ra_core_misnested',
             'RetroArch/<core>/<save> at wrong nesting (relocate under saves/<core>/ or states/<core>/)',
             _is_ra_core_misnested,
             _ra_core_misnested_relocator),
    # Never register switch_kitchen_sink as a destructive category. A previous
    # version misclassified the canonical switch/<titleId>/... cloud layout and
    # permanently removed real saves. Keep the matcher for diagnostics/tests
    # only; Switch cleanup requires a purpose-built, non-destructive migration.
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


def _find_ra_root_flat(cursor, user_id: int | None):
    """
    Find rows like `RetroArch/<filename>` (exactly 2 segments) for which a
    canonical copy already exists under `RetroArch/saves/...` or
    `RetroArch/states/...` with the same filename — and, when both rows have
    hashes, the same hash. Those root copies are safe to delete because the
    canonical copy supersedes them.
    Returns: [(file_id, user_id, root_path, canonical_path, hash_matched), ...]
    """
    where = "WHERE path ILIKE 'RetroArch/%%'"
    params: list = []
    if user_id is not None:
        where += " AND user_id = %s"
        params.append(user_id)
    cursor.execute(f"SELECT id, user_id, path, hash FROM files {where}", params)
    rows = cursor.fetchall()

    # Index canonical (saves/ or states/ subtree) entries by (user_id, basename).
    canonical: dict[tuple[int, str], list[tuple[int, str, str]]] = {}
    for fid, uid, p, h in rows:
        parts = p.split('/')
        if len(parts) < 3 or parts[0].lower() != 'retroarch':
            continue
        if parts[1].lower() not in ('saves', 'states'):
            continue
        basename = parts[-1].lower()
        canonical.setdefault((uid, basename), []).append((fid, p, h))

    candidates = []
    for fid, uid, p, h in rows:
        parts = p.split('/')
        if len(parts) != 2 or parts[0].lower() != 'retroarch':
            continue
        basename = parts[-1].lower()
        siblings = canonical.get((uid, basename), [])
        if not siblings:
            continue  # no canonical copy — leave for manual review
        # Require hash agreement when we have it on both sides; otherwise
        # accept basename match (best we can do for rows missing hashes).
        if h:
            sib_hashes = {sh for _, _, sh in siblings if sh}
            if sib_hashes and h not in sib_hashes:
                continue
        canonical_path = siblings[0][1]
        hash_matched = bool(h and any(sh == h for _, _, sh in siblings))
        candidates.append((fid, uid, p, canonical_path, hash_matched))
    return candidates


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


def _quarantine_physical(user_id: int, file_id: int, db_path: str) -> str | None:
    """Move a physical blob into recoverable per-user cleanup quarantine.

    Returns the quarantine path, ``None`` when the source does not exist, and
    raises on unsafe paths or move failures. The caller must keep the DB row if
    this raises.
    """
    user_root = os.path.realpath(os.path.join(STORAGE_DIR, str(user_id)))
    source = os.path.realpath(_physical_path(user_id, db_path))
    if os.path.commonpath([user_root, source]) != user_root:
        raise ValueError(f"unsafe cleanup path outside user root: {db_path}")
    if not os.path.exists(source):
        return None
    if not os.path.isfile(source):
        raise OSError(f"cleanup source is not a regular file: {db_path}")

    batch = f"{int(time.time() * 1000)}-{file_id}"
    trash_root = os.path.realpath(os.path.join(user_root, '.cleanup_trash', batch))
    destination = os.path.realpath(
        os.path.join(trash_root, db_path.lstrip('/\\'))
    )
    if os.path.commonpath([trash_root, destination]) != trash_root:
        raise ValueError(f"unsafe cleanup quarantine path: {db_path}")

    if os.path.exists(destination):
        raise FileExistsError(f"cleanup quarantine destination exists: {destination}")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    os.replace(source, destination)
    return destination


def _prune_empty_dirs(user_id: int, db_path: str) -> None:
    """Remove parent directories left empty after unlinking a file, walking up
    until a non-empty directory or the user's storage root. Without this,
    deleting blobs leaves behind a directory skeleton ("N directories, 0
    files") — the exact artifact this script's switch cleanup produced before."""
    user_root = os.path.abspath(os.path.join(STORAGE_DIR, str(user_id)))
    d = os.path.dirname(_physical_path(user_id, db_path))
    while True:
        d_abs = os.path.abspath(d)
        # Never touch the user storage root itself or anything outside it.
        if d_abs == user_root or not d_abs.startswith(user_root + os.sep):
            break
        try:
            os.rmdir(d)  # raises if the directory is not empty
        except OSError:
            break
        d = os.path.dirname(d)


def _delete_row(cursor, user_id: int, file_id: int, db_path: str, apply: bool) -> None:
    if apply:
        try:
            quarantined = _quarantine_physical(user_id, file_id, db_path)
            if not quarantined:
                print(f"  ! source blob missing for {db_path}; keeping DB row", file=sys.stderr)
                return
            print(f"  quarantined {db_path} -> {quarantined}")
        except (OSError, ValueError) as e:
            print(f"  ! could not quarantine {db_path}; keeping DB row: {e}", file=sys.stderr)
            return
        _prune_empty_dirs(user_id, db_path)
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
        # Destination already exists in DB — quarantine the duplicate source.
        try:
            quarantined = _quarantine_physical(user_id, file_id, old_path)
            if not quarantined:
                print(f"  ! duplicate source missing for {old_path}; keeping DB row", file=sys.stderr)
                return 'skipped'
            print(f"  quarantined duplicate {old_path} -> {quarantined}")
        except (OSError, ValueError) as e:
            print(f"  ! could not quarantine duplicate {old_path}; keeping DB row: {e}", file=sys.stderr)
            return 'skipped'
        _prune_empty_dirs(user_id, old_path)
        cursor.execute("DELETE FROM files WHERE id = %s", (file_id,))
        return 'merged'

    try:
        os.makedirs(os.path.dirname(new_phys), exist_ok=True)
        if os.path.exists(old_phys) and not os.path.exists(new_phys):
            os.rename(old_phys, new_phys)
    except OSError as e:
        print(f"  ! could not move {old_phys} → {new_phys}: {e}", file=sys.stderr)
        return 'skipped'

    _prune_empty_dirs(user_id, old_path)
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

    if not only or 'ra_root_flat' in only:
        print("\n--- RetroArch root-flat scan (delete when canonical copy exists) ---")
        flat = _find_ra_root_flat(cursor, user_id)
        print(f"Found {len(flat)} root-flat rows with a canonical copy under saves/ or states/.")
        for fid, _uid, p, canon, hash_match in flat[:20]:
            tag = '(hash match)' if hash_match else '(basename only — no hash)'
            print(f"  drop [{fid}] {p}  ←  keep {canon}  {tag}")
        if len(flat) > 20:
            print(f"  ... and {len(flat) - 20} more rows")
        if apply:
            for fid, uid, p, _canon, _hm in flat:
                _delete_row(cursor, uid, fid, p, apply=True)

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
        verb = 'relocate' if cat.relocator else 'quarantine'
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
