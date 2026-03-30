import sqlite3
from datetime import datetime, timedelta, timezone

from src.shared.relation_resolution_cache import RelationResolutionCacheStore


def test_relation_resolution_cache_store_initializes_expected_schema(tmp_path):
    store = RelationResolutionCacheStore(tmp_path / "cache.db")
    columns = {
        row["name"]: row["pk"]
        for row in store.connection.execute(
            "PRAGMA table_info(relation_resolution_cache)"
        ).fetchall()
    }

    assert columns == {
        "key_type": 1,
        "key_value": 2,
        "arxiv_url": 0,
        "resolved_title": 0,
        "checked_at": 0,
    }


def test_relation_resolution_cache_store_records_and_reads_positive_mapping(tmp_path):
    store = RelationResolutionCacheStore(tmp_path / "cache.db")

    store.record_resolution(
        key_type="openalex_work",
        key_value="https://openalex.org/W123",
        arxiv_url="https://arxiv.org/abs/2501.12345",
        resolved_title="Mapped Arxiv Title",
    )

    entry = store.get("openalex_work", "https://openalex.org/W123")

    assert entry is not None
    assert entry.arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert entry.resolved_title == "Mapped Arxiv Title"
    assert entry.checked_at is not None


def test_relation_resolution_cache_store_records_negative_mapping(tmp_path):
    store = RelationResolutionCacheStore(tmp_path / "cache.db")

    store.record_resolution(
        key_type="doi",
        key_value="https://doi.org/10.1000/example",
        arxiv_url=None,
    )

    entry = store.get("doi", "https://doi.org/10.1000/example")

    assert entry is not None
    assert entry.arxiv_url is None
    assert entry.resolved_title is None
    assert entry.checked_at is not None


def test_relation_resolution_cache_store_migrates_existing_db_without_resolved_title_column(tmp_path):
    db_path = tmp_path / "cache.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE relation_resolution_cache (
            key_type TEXT NOT NULL,
            key_value TEXT NOT NULL,
            arxiv_url TEXT,
            checked_at TEXT NOT NULL,
            PRIMARY KEY (key_type, key_value)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO relation_resolution_cache (key_type, key_value, arxiv_url, checked_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "openalex_work",
            "https://openalex.org/W123",
            "https://arxiv.org/abs/2501.12345",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    store = RelationResolutionCacheStore(db_path)

    columns = {
        row["name"]
        for row in store.connection.execute(
            "PRAGMA table_info(relation_resolution_cache)"
        ).fetchall()
    }
    entry = store.get("openalex_work", "https://openalex.org/W123")

    assert "resolved_title" in columns
    assert entry is not None
    assert entry.arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert entry.resolved_title is None

    store.record_resolution(
        key_type="openalex_work",
        key_value="https://openalex.org/W123",
        arxiv_url="https://arxiv.org/abs/2501.12345",
        resolved_title="Migrated Cached Title",
    )

    updated = store.get("openalex_work", "https://openalex.org/W123")

    assert updated is not None
    assert updated.resolved_title == "Migrated Cached Title"


def test_relation_resolution_cache_negative_freshness_uses_days_threshold():
    recent = datetime.now(timezone.utc).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()

    assert RelationResolutionCacheStore.is_negative_cache_fresh(recent, 30) is True
    assert RelationResolutionCacheStore.is_negative_cache_fresh(stale, 30) is False


def test_relation_resolution_cache_can_count_and_delete_negative_entries(tmp_path):
    store = RelationResolutionCacheStore(tmp_path / "cache.db")
    store.record_resolution(
        key_type="doi",
        key_value="https://doi.org/10.1000/negative",
        arxiv_url=None,
    )
    store.record_resolution(
        key_type="openalex_work",
        key_value="https://openalex.org/W1",
        arxiv_url="https://arxiv.org/abs/2501.12345",
        resolved_title="Mapped Arxiv Title",
    )

    assert store.count_negative_entries() == 1
    assert store.delete_negative_entries() == 1
    assert store.count_negative_entries() == 0
    assert store.get("openalex_work", "https://openalex.org/W1") is not None
