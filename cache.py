import argparse
import sys
from pathlib import Path

from src.shared.relation_resolution_cache import RelationResolutionCacheStore
from src.shared.repo_cache import RepoCacheStore
from src.shared.settings import REPO_CACHE_DB_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or clear negative GitHub repo discovery cache entries.",
    )
    parser.add_argument(
        "--db",
        "--db-path",
        dest="db",
        default=REPO_CACHE_DB_PATH,
        help="Path to the repo cache SQLite database. Defaults to ./cache.db.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview how many negative cache entries would be deleted. This is the default mode.",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Delete negative cache entries from the repo cache database.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db).expanduser()

    if not db_path.exists() or not db_path.is_file():
        print(f"Repo cache DB not found: {db_path}", file=sys.stderr)
        return 1

    repo_store = RepoCacheStore(db_path)
    relation_store = RelationResolutionCacheStore(db_path)
    try:
        stats = repo_store.get_stats()
        negative_entry_count = repo_store.count_negative_repo_discovery_entries()
        relation_negative_entry_count = relation_store.count_negative_entries()
        print(f"Repo cache DB: {db_path}")
        print(f"Total entries: {stats.total_entries}")
        print(f"Positive entries: {stats.positive_entries}")
        print(f"Negative entries: {stats.negative_entries}")
        print(f"Relation negative entries: {relation_negative_entry_count}")

        if args.apply:
            deleted = repo_store.delete_negative_repo_discovery_entries()
            deleted_relation = relation_store.delete_negative_entries()
            after = repo_store.get_stats()
            print(f"Deleted {deleted} negative repo discovery cache entries from {db_path}.")
            print(f"Deleted {deleted_relation} negative relation resolution cache entries from {db_path}.")
            print(f"Deleted negative entries: {deleted + deleted_relation}")
            print(f"Remaining entries: {after.total_entries}")
            print(f"Remaining positive entries: {after.positive_entries}")
            print(f"Remaining negative entries: {after.negative_entries}")
        else:
            print(
                "Dry run: found "
                f"{negative_entry_count} negative repo discovery cache entries in {db_path}. "
                "Re-run with --apply to delete them."
            )
            print(
                "Dry run: found "
                f"{relation_negative_entry_count} negative relation resolution cache entries in {db_path}. "
                "Re-run with --apply to delete them."
            )
            print(f"Dry run: would delete {stats.negative_entries + relation_negative_entry_count} negative entries")
            print("Re-run with --apply to delete them")

        return 0
    finally:
        relation_store.close()
        repo_store.close()


if __name__ == "__main__":
    raise SystemExit(main())
