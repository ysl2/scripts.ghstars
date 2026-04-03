import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from src.shared.relation_resolution_cache import RelationResolutionCacheStore
from src.shared.repo_cache import RepoCacheStats, RepoCacheStore
from src.shared.repo_metadata_cache import RepoMetadataCacheStore
from src.shared.settings import REPO_CACHE_DB_PATH


@dataclass(frozen=True)
class CacheMigrationSummary:
    repo_cache_created: bool = False
    repo_cache_migrated: bool = False
    repo_metadata_cache_created: bool = False
    relation_resolution_cache_created: bool = False
    relation_resolution_added_resolved_title: bool = False
    relation_resolution_deleted_unsupported_rows: int = 0

    def lines(self) -> list[str]:
        lines: list[str] = []
        if self.repo_cache_created:
            lines.append("repo_cache: created missing table")
        if self.repo_cache_migrated:
            lines.append("repo_cache: migrated legacy schema to last_repo_discovery_checked_at")
        if self.repo_metadata_cache_created:
            lines.append("repo_metadata_cache: created missing table")
        if self.relation_resolution_cache_created:
            lines.append("relation_resolution_cache: created missing table")
        if self.relation_resolution_added_resolved_title:
            lines.append("relation_resolution_cache: added resolved_title column")
        if self.relation_resolution_deleted_unsupported_rows:
            lines.append(
                "relation_resolution_cache: "
                f"deleted {self.relation_resolution_deleted_unsupported_rows} unsupported legacy rows"
            )
        if not lines:
            lines.append("no changes needed")
        return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or clear negative repo-discovery and relation-resolution cache entries.",
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

    migration = _migrate_cache_db(db_path)
    _print_migration_summary(migration)

    if args.apply:
        repo_store = RepoCacheStore(db_path)
        relation_store = RelationResolutionCacheStore(db_path)
        try:
            stats = repo_store.get_stats()
            relation_negative_entry_count = relation_store.count_negative_entries()
            print(f"Repo cache DB: {db_path}")
            print(f"Total entries: {stats.total_entries}")
            print(f"Positive entries: {stats.positive_entries}")
            print(f"Negative entries: {stats.negative_entries}")
            print(f"Relation negative entries: {relation_negative_entry_count}")

            deleted = repo_store.delete_negative_repo_discovery_entries()
            deleted_relation = relation_store.delete_negative_entries()
            after = repo_store.get_stats()
            print(f"Deleted {deleted} negative repo discovery cache entries from {db_path}.")
            print(f"Deleted {deleted_relation} negative relation resolution cache entries from {db_path}.")
            print(f"Deleted negative entries: {deleted + deleted_relation}")
            print(f"Remaining entries: {after.total_entries}")
            print(f"Remaining positive entries: {after.positive_entries}")
            print(f"Remaining negative entries: {after.negative_entries}")
            return 0
        finally:
            relation_store.close()
            repo_store.close()

    stats = _read_repo_cache_stats(db_path)
    negative_entry_count = stats.negative_entries
    relation_negative_entry_count = _count_relation_negative_entries(db_path)
    print(f"Repo cache DB: {db_path}")
    print(f"Total entries: {stats.total_entries}")
    print(f"Positive entries: {stats.positive_entries}")
    print(f"Negative entries: {stats.negative_entries}")
    print(f"Relation negative entries: {relation_negative_entry_count}")
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


def _migrate_cache_db(db_path: Path) -> CacheMigrationSummary:
    with sqlite3.connect(db_path) as connection:
        repo_cache_exists = _table_exists(connection, "repo_cache")
        repo_metadata_cache_exists = _table_exists(connection, "repo_metadata_cache")
        relation_resolution_cache_exists = _table_exists(connection, "relation_resolution_cache")
        repo_cache_columns = _table_columns(connection, "repo_cache")
        relation_resolution_columns = _table_columns(connection, "relation_resolution_cache")
        unsupported_relation_rows = _count_unsupported_relation_rows(connection)

    repo_store = RepoCacheStore(db_path)
    repo_store.close()

    repo_metadata_store = RepoMetadataCacheStore(db_path)
    repo_metadata_store.close()

    relation_store = RelationResolutionCacheStore(db_path)
    relation_store.close()

    return CacheMigrationSummary(
        repo_cache_created=not repo_cache_exists,
        repo_cache_migrated=_repo_cache_requires_migration(repo_cache_columns),
        repo_metadata_cache_created=not repo_metadata_cache_exists,
        relation_resolution_cache_created=not relation_resolution_cache_exists,
        relation_resolution_added_resolved_title=(
            relation_resolution_cache_exists and "resolved_title" not in relation_resolution_columns
        ),
        relation_resolution_deleted_unsupported_rows=unsupported_relation_rows,
    )


def _print_migration_summary(summary: CacheMigrationSummary) -> None:
    print("Cache migration summary:")
    for line in summary.lines():
        print(f"- {line}")


def _read_repo_cache_stats(db_path: Path):
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "repo_cache"):
            return RepoCacheStats(total_entries=0, positive_entries=0, negative_entries=0)

        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_entries,
                SUM(CASE WHEN github_url IS NOT NULL AND TRIM(github_url) <> '' THEN 1 ELSE 0 END) AS positive_entries,
                SUM(
                    CASE
                        WHEN (github_url IS NULL OR TRIM(github_url) = '')
                         AND last_repo_discovery_checked_at IS NOT NULL THEN 1
                        ELSE 0
                    END
                ) AS negative_entries
            FROM repo_cache
            """
        ).fetchone()
    return RepoCacheStats(
        total_entries=int((row["total_entries"] if row is not None else 0) or 0),
        positive_entries=int((row["positive_entries"] if row is not None else 0) or 0),
        negative_entries=int((row["negative_entries"] if row is not None else 0) or 0),
    )


def _count_relation_negative_entries(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "relation_resolution_cache"):
            return 0

        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM relation_resolution_cache
            WHERE arxiv_url IS NULL OR TRIM(arxiv_url) = ''
            """
        ).fetchone()
    return int((row["count"] if row is not None else 0) or 0)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _repo_cache_requires_migration(existing_columns: set[str]) -> bool:
    return bool(existing_columns) and (
        "hf_exact_no_repo_count" in existing_columns
        or "last_hf_exact_checked_at" in existing_columns
        or "last_repo_discovery_checked_at" not in existing_columns
    )


def _count_unsupported_relation_rows(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "relation_resolution_cache"):
        return 0

    columns = _table_columns(connection, "relation_resolution_cache")
    if "key_type" not in columns:
        return 0

    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM relation_resolution_cache
        WHERE key_type NOT IN ('doi', 'source_url')
        """
    ).fetchone()
    return int((row[0] if row is not None else 0) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
