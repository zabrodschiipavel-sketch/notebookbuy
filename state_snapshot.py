"""Pack and restore the pipeline's state: the database plus the lookup caches.

The daily workflow kept all of it only in the GitHub Actions cache, which is
evicted after 7 days without access — one missed week and the price history
and digest history were gone without an error anywhere. The workflow now also
publishes a snapshot to the `pipeline-data` branch and restores from it when
the cache misses.

    python state_snapshot.py pack OUT_DIR [--previous META_JSON]
    python state_snapshot.py unpack SNAPSHOT_TAR_GZ
"""
import argparse
import datetime
import json
import logging
import os
import sqlite3
import sys
import tarfile
import tempfile

from app_config import DB_NAME


log = logging.getLogger(__name__)

SNAPSHOT_NAME = "state.tar.gz"
META_NAME = "state_meta.json"
CACHE_FILES = (
    "pricehistory_cache.json",
    "notebookcheck_cache.json",
    "digest_history.json",
)
# A snapshot with fewer than this share of the previous one's ads is a broken
# restore (an empty database), not a quiet day — publishing it would overwrite
# the only good copy.
MIN_SHARE_OF_PREVIOUS = 0.5


def _counts(db_path: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("ads", "price_history", "analysis_cache")
        }


def _previous_ads(meta_path: str | None) -> int:
    """Ad count of the last published snapshot; 0 when there is none.

    The workflow redirects `git show` into the file, so a missing snapshot
    leaves an empty file rather than no file.
    """
    if not meta_path or not os.path.exists(meta_path):
        return 0
    try:
        with open(meta_path, encoding="utf-8") as f:
            return int(json.load(f).get("ads", 0))
    except (ValueError, AttributeError, OSError):
        return 0


def pack(out_dir: str, previous_meta: str | None = None, db_path: str = DB_NAME) -> dict:
    """Write OUT_DIR/state.tar.gz and OUT_DIR/state_meta.json; return the meta.

    Raises ValueError when the database is missing, empty, or has shrunk to
    less than half of the previous snapshot.
    """
    if not os.path.exists(db_path):
        raise ValueError(f"{db_path} does not exist")
    counts = _counts(db_path)
    if not counts["ads"]:
        raise ValueError("the database has no ads")

    prev_ads = _previous_ads(previous_meta)
    if prev_ads and counts["ads"] < prev_ads * MIN_SHARE_OF_PREVIOUS:
        raise ValueError(
            f"{counts['ads']} ads against {prev_ads} in the previous snapshot — "
            "refusing to overwrite it with what looks like a failed restore"
        )

    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        # A consistent copy even if something still holds the file open.
        backup = os.path.join(tmp, os.path.basename(db_path))
        with sqlite3.connect(db_path) as src, sqlite3.connect(backup) as dst:
            src.backup(dst)
        with tarfile.open(os.path.join(out_dir, SNAPSHOT_NAME), "w:gz") as tar:
            tar.add(backup, arcname=os.path.basename(db_path))
            for name in CACHE_FILES:
                if os.path.exists(name):
                    tar.add(name, arcname=name)

    meta = {**counts, "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    with open(os.path.join(out_dir, META_NAME), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    return meta


def unpack(snapshot: str, dest: str = ".", db_name: str = DB_NAME) -> list[str]:
    """Extract only the known state files into *dest*; returns their names."""
    allowed = {os.path.basename(db_name), *CACHE_FILES}
    with tarfile.open(snapshot, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile() and m.name in allowed]
        # Names are checked against a fixed list, so nothing escapes *dest*;
        # the "data" filter (where this Python has it) also strips links and
        # odd permissions.
        safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        for member in members:
            tar.extract(member, path=dest, **safe)
    return [m.name for m in members]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_pack = sub.add_parser("pack")
    p_pack.add_argument("out_dir")
    p_pack.add_argument("--previous", help="state_meta.json of the last published snapshot")
    p_unpack = sub.add_parser("unpack")
    p_unpack.add_argument("snapshot")
    args = parser.parse_args(argv)

    if args.command == "pack":
        try:
            meta = pack(args.out_dir, args.previous)
        except ValueError as e:
            log.error("Not publishing a snapshot: %s", e)
            return 1
        log.info("Snapshot: %s", meta)
        return 0

    restored = unpack(args.snapshot)
    log.info("Restored from snapshot: %s", ", ".join(restored) or "nothing")
    return 0 if restored else 1


if __name__ == "__main__":
    sys.exit(main())
