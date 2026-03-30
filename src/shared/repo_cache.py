import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RepoCacheEntry:
    arxiv_url: str
    github_url: str | None
    created_at: str
    updated_at: str
    last_repo_discovery_checked_at: str | None

    @property
    def last_hf_exact_checked_at(self) -> str | None:
        return self.last_repo_discovery_checked_at


@dataclass(frozen=True)
class RepoCacheStats:
    total_entries: int
    positive_entries: int
    negative_entries: int


class RepoCacheStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def close(self) -> None:
        self.connection.close()

    def get(self, arxiv_url: str) -> RepoCacheEntry | None:
        row = self.connection.execute(
            """
            SELECT arxiv_url, github_url, created_at, updated_at, last_repo_discovery_checked_at
            FROM repo_cache
            WHERE arxiv_url = ?
            """,
            (arxiv_url,),
        ).fetchone()
        if row is None:
            return None

        return RepoCacheEntry(
            arxiv_url=row["arxiv_url"],
            github_url=row["github_url"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_repo_discovery_checked_at=row["last_repo_discovery_checked_at"],
        )

    def record_found_repo(self, arxiv_url: str, github_url: str) -> None:
        now = _utc_now()
        self.connection.execute(
            """
            INSERT INTO repo_cache (
                arxiv_url,
                github_url,
                created_at,
                updated_at,
                last_repo_discovery_checked_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(arxiv_url) DO UPDATE SET
                github_url = excluded.github_url,
                updated_at = excluded.updated_at,
                last_repo_discovery_checked_at = excluded.last_repo_discovery_checked_at
            """,
            (arxiv_url, github_url, now, now, now),
        )
        self.connection.commit()

    def record_discovery_no_repo(self, arxiv_url: str) -> None:
        now = _utc_now()
        self.connection.execute(
            """
            INSERT INTO repo_cache (
                arxiv_url,
                github_url,
                created_at,
                updated_at,
                last_repo_discovery_checked_at
            )
            VALUES (?, NULL, ?, ?, ?)
            ON CONFLICT(arxiv_url) DO UPDATE SET
                github_url = CASE
                    WHEN repo_cache.github_url IS NULL THEN NULL
                    ELSE repo_cache.github_url
                END,
                updated_at = excluded.updated_at,
                last_repo_discovery_checked_at = excluded.last_repo_discovery_checked_at
            """,
            (arxiv_url, now, now, now),
        )
        self.connection.commit()

    def record_exact_no_repo(self, arxiv_url: str) -> None:
        self.record_discovery_no_repo(arxiv_url)

    def get_stats(self) -> RepoCacheStats:
        row = self.connection.execute(
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
            total_entries=int(row["total_entries"] or 0),
            positive_entries=int(row["positive_entries"] or 0),
            negative_entries=int(row["negative_entries"] or 0),
        )

    def delete_negative_entries(self) -> int:
        cursor = self.connection.execute(
            """
            DELETE FROM repo_cache
            WHERE (github_url IS NULL OR TRIM(github_url) = '')
              AND last_repo_discovery_checked_at IS NOT NULL
            """
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def count_negative_repo_discovery_entries(self) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM repo_cache
            WHERE (github_url IS NULL OR TRIM(github_url) = '')
              AND last_repo_discovery_checked_at IS NOT NULL
            """
        ).fetchone()
        return int(row["count"]) if row is not None else 0

    def delete_negative_repo_discovery_entries(self) -> int:
        cursor = self.connection.execute(
            """
            DELETE FROM repo_cache
            WHERE (github_url IS NULL OR TRIM(github_url) = '')
              AND last_repo_discovery_checked_at IS NOT NULL
            """
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def _initialize_schema(self) -> None:
        existing_columns = self._existing_columns()
        if not existing_columns:
            self._create_schema()
            return

        if (
            "hf_exact_no_repo_count" in existing_columns
            or "last_hf_exact_checked_at" in existing_columns
            or "last_repo_discovery_checked_at" not in existing_columns
        ):
            self._migrate_from_threshold_schema()

    def _existing_columns(self) -> set[str]:
        rows = self.connection.execute("PRAGMA table_info(repo_cache)").fetchall()
        return {row["name"] for row in rows}

    def _create_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_cache (
                arxiv_url TEXT PRIMARY KEY,
                github_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_repo_discovery_checked_at TEXT
            )
            """
        )
        self.connection.commit()

    def _migrate_from_threshold_schema(self) -> None:
        existing_columns = self._existing_columns()
        timestamp_source = "last_repo_discovery_checked_at"
        if "last_repo_discovery_checked_at" not in existing_columns:
            if "last_hf_exact_checked_at" in existing_columns:
                timestamp_source = "last_hf_exact_checked_at"
            else:
                timestamp_source = "NULL"

        self.connection.execute(
            """
            CREATE TABLE repo_cache_new (
                arxiv_url TEXT PRIMARY KEY,
                github_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_repo_discovery_checked_at TEXT
            )
            """
        )
        self.connection.execute(
            f"""
            INSERT INTO repo_cache_new (
                arxiv_url,
                github_url,
                created_at,
                updated_at,
                last_repo_discovery_checked_at
            )
            SELECT
                arxiv_url,
                github_url,
                created_at,
                updated_at,
                {timestamp_source}
            FROM repo_cache
            """
        )
        self.connection.execute("DROP TABLE repo_cache")
        self.connection.execute("ALTER TABLE repo_cache_new RENAME TO repo_cache")
        self.connection.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
