import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.shared.github import normalize_github_url


@dataclass(frozen=True)
class RepoMetadataCacheEntry:
    github_url: str
    created: str | None
    updated_at: str


class RepoMetadataCacheStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def close(self) -> None:
        self.connection.close()

    def get(self, github_url: str) -> RepoMetadataCacheEntry | None:
        normalized = self._normalize_key(github_url)
        if normalized is None:
            return None

        row = self.connection.execute(
            """
            SELECT github_url, created, updated_at
            FROM repo_metadata_cache
            WHERE github_url = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            return None

        return RepoMetadataCacheEntry(
            github_url=row["github_url"],
            created=row["created"],
            updated_at=row["updated_at"],
        )

    def record_created(self, github_url: str, created: str) -> None:
        normalized = self._normalize_key(github_url)
        if normalized is None or not created:
            return

        now = _utc_now()
        self.connection.execute(
            """
            INSERT INTO repo_metadata_cache (
                github_url,
                created,
                updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(github_url) DO UPDATE SET
                created = excluded.created,
                updated_at = excluded.updated_at
            """,
            (normalized, created, now),
        )
        self.connection.commit()

    def _initialize_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_metadata_cache (
                github_url TEXT PRIMARY KEY,
                created TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def _normalize_key(self, github_url: str) -> str | None:
        return normalize_github_url(github_url)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
