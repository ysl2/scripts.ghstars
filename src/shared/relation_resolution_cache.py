import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class RelationResolutionCacheEntry:
    key_type: str
    key_value: str
    arxiv_url: str | None
    resolved_title: str | None
    checked_at: str


class RelationResolutionCacheStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def close(self) -> None:
        self.connection.close()

    def get(self, key_type: str, key_value: str) -> RelationResolutionCacheEntry | None:
        row = self.connection.execute(
            """
            SELECT key_type, key_value, arxiv_url, resolved_title, checked_at
            FROM relation_resolution_cache
            WHERE key_type = ? AND key_value = ?
            """,
            (key_type, key_value),
        ).fetchone()
        if row is None:
            return None

        return RelationResolutionCacheEntry(
            key_type=row["key_type"],
            key_value=row["key_value"],
            arxiv_url=row["arxiv_url"],
            resolved_title=row["resolved_title"],
            checked_at=row["checked_at"],
        )

    def record_resolution(
        self,
        *,
        key_type: str,
        key_value: str,
        arxiv_url: str | None,
        resolved_title: str | None = None,
    ) -> None:
        checked_at = _utc_now()
        self.connection.execute(
            """
            INSERT INTO relation_resolution_cache (
                key_type,
                key_value,
                arxiv_url,
                resolved_title,
                checked_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key_type, key_value) DO UPDATE SET
                arxiv_url = excluded.arxiv_url,
                resolved_title = excluded.resolved_title,
                checked_at = excluded.checked_at
            """,
            (key_type, key_value, arxiv_url, resolved_title, checked_at),
        )
        self.connection.commit()

    @staticmethod
    def is_negative_cache_fresh(checked_at: str | None, recheck_days: int) -> bool:
        if not checked_at:
            return False

        try:
            parsed = datetime.fromisoformat(checked_at)
        except ValueError:
            return False

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < parsed + timedelta(days=recheck_days)

    def _initialize_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS relation_resolution_cache (
                key_type TEXT NOT NULL,
                key_value TEXT NOT NULL,
                arxiv_url TEXT,
                resolved_title TEXT,
                checked_at TEXT NOT NULL,
                PRIMARY KEY (key_type, key_value)
            )
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(relation_resolution_cache)"
            ).fetchall()
        }
        if "resolved_title" not in columns:
            self.connection.execute(
                "ALTER TABLE relation_resolution_cache ADD COLUMN resolved_title TEXT"
            )
        self.connection.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
