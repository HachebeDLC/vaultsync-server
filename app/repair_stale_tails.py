"""Find (and optionally trim) stale bytes left at the end of encrypted blobs.

Until get_encrypted_file_size() existed, finalize_upload truncated a blob to
`size + blocks * 39`, assuming PKCS7 always adds 16 bytes. It adds 1-16, so
when a file shrank and its length was not a multiple of 16, up to 15 bytes of
the previous version stayed at the end. Such a blob fails to decrypt
(WRONG_FINAL_BLOCK_LENGTH on download, "not a multiple of the block length" in
RomM reassembly), and every version snapshotted from it inherits the tail.

The trailing bytes lie past the padded ciphertext, so trimming them only
removes garbage. A blob is only touched when its structure checks out:
NEOSYNC magic at offset 0 and at the start of the last block.

Dry run by default; run from the repo root:
    python -m app.repair_stale_tails            # report only
    python -m app.repair_stale_tails --apply    # trim
"""
import argparse
import os
from typing import Iterator, List, Optional, Tuple

from .config import (
    BLOCK_THRESHOLD, LARGE_BLOCK_SIZE, MAGIC_IV, OVERHEAD, SMALL_BLOCK_SIZE,
    STORAGE_DIR, get_encrypted_file_size,
)

MAGIC = b"NEOSYNC"


def _read_at(path: str, offset: int, n: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(n)


def expected_size_from_structure(blob_size: int) -> Optional[int]:
    """Exact size implied by the block layout alone (no metadata needed).

    Returns None when the last block is too short to be a valid block.
    """
    threshold_blob = get_encrypted_file_size(BLOCK_THRESHOLD)
    bs = LARGE_BLOCK_SIZE if blob_size >= threshold_blob else SMALL_BLOCK_SIZE
    full_block = bs + OVERHEAD
    rem = blob_size % full_block
    if rem == 0:
        return blob_size
    if rem < MAGIC_IV + 16:
        return None
    return blob_size - (rem - MAGIC_IV) % 16


def last_block_offset(blob_size: int, expected: int) -> int:
    threshold_blob = get_encrypted_file_size(BLOCK_THRESHOLD)
    bs = LARGE_BLOCK_SIZE if expected >= threshold_blob else SMALL_BLOCK_SIZE
    full_block = bs + OVERHEAD
    rem = expected % full_block
    return expected - (rem if rem else full_block)


def check_blob(path: str, expected: Optional[int]) -> Tuple[str, int, int]:
    """Classify one blob: ('ok'|'tail'|'short'|'plaintext'|'malformed', size, expected)."""
    size = os.path.getsize(path)
    if size < len(MAGIC) or _read_at(path, 0, len(MAGIC)) != MAGIC:
        return "plaintext", size, size
    if expected is None:
        expected = expected_size_from_structure(size)
        if expected is None:
            return "malformed", size, size
    if size == expected:
        return "ok", size, expected
    if size < expected:
        return "short", size, expected
    if _read_at(path, last_block_offset(size, expected), len(MAGIC)) != MAGIC:
        return "malformed", size, expected
    return "tail", size, expected


def iter_current_blobs(rows: List[Tuple[int, str, int]]) -> Iterator[Tuple[str, Optional[int]]]:
    for user_id, path, plain_size in rows:
        blob = os.path.join(STORAGE_DIR, str(user_id), path)
        if os.path.isfile(blob):
            yield blob, get_encrypted_file_size(plain_size)


def iter_version_blobs() -> Iterator[Tuple[str, Optional[int]]]:
    if not os.path.isdir(STORAGE_DIR):
        return
    for user_dir in os.listdir(STORAGE_DIR):
        vdir = os.path.join(STORAGE_DIR, user_dir, ".versions")
        if os.path.isdir(vdir):
            for name in sorted(os.listdir(vdir)):
                p = os.path.join(vdir, name)
                if os.path.isfile(p):
                    yield p, None


def run(apply: bool, rows: List[Tuple[int, str, int]]) -> dict:
    counts: dict = {}
    fixed = []
    for label, source in (("current", iter_current_blobs(rows)), ("version", iter_version_blobs())):
        for path, expected in source:
            status, size, exp = check_blob(path, expected)
            counts[(label, status)] = counts.get((label, status), 0) + 1
            if status == "tail":
                print(f"  {'TRIM' if apply else 'would trim'} {size - exp:>2} B  {label:7}  {os.path.relpath(path, STORAGE_DIR)}")
                if apply:
                    with open(path, "r+b") as f:
                        f.truncate(exp)
                    fixed.append(path)
            elif status in ("short", "malformed"):
                print(f"  LEFT AS IS ({status}, {size} B, expected {exp})  {label:7}  {os.path.relpath(path, STORAGE_DIR)}")
    for (label, status), n in sorted(counts.items()):
        print(f"{label:7} {status:9} {n}")
    return {"counts": counts, "fixed": fixed}


def _load_rows() -> List[Tuple[int, str, int]]:
    from .database import get_db
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, path, size FROM files")
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="trim stale tails (default: report only)")
    args = parser.parse_args()
    run(args.apply, _load_rows())


if __name__ == "__main__":
    main()
