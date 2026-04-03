import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import cache
from src.shared.relation_resolution_cache import RelationResolutionCacheStore
from src.shared.repo_cache import RepoCacheStore


def _seed_repo_cache(db_path: Path) -> tuple[str, str]:
    store = RepoCacheStore(db_path)
    negative_url = "https://arxiv.org/abs/2603.18493"
    positive_url = "https://arxiv.org/abs/2603.18494"
    store.record_discovery_no_repo(negative_url)
    store.record_found_repo(positive_url, "https://github.com/foo/bar")
    store.close()
    return negative_url, positive_url


def _seed_relation_resolution_cache(db_path: Path) -> tuple[tuple[str, str], tuple[str, str]]:
    store = RelationResolutionCacheStore(db_path)
    negative_key = ("doi", "https://doi.org/10.1000/negative")
    positive_key = ("source_url", "https://www.semanticscholar.org/paper/Foo/abc123")
    store.record_resolution(key_type=negative_key[0], key_value=negative_key[1], arxiv_url=None)
    store.record_resolution(
        key_type=positive_key[0],
        key_value=positive_key[1],
        arxiv_url="https://arxiv.org/abs/2501.12345",
        resolved_title="Mapped Arxiv Title",
    )
    store.close()
    return negative_key, positive_key


def test_cache_main_dry_run_reports_negative_cache_count_without_deleting(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    negative_url, positive_url = _seed_repo_cache(db_path)
    negative_relation_key, positive_relation_key = _seed_relation_resolution_cache(db_path)

    exit_code = cache.main(["--db-path", str(db_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Dry run" in captured.out
    assert "1 negative repo discovery cache entries" in captured.out
    assert "1 negative relation resolution cache entries" in captured.out
    assert "--apply" in captured.out

    store = RepoCacheStore(db_path)
    assert store.get(negative_url) is not None
    assert store.get(positive_url) is not None
    store.close()
    relation_store = RelationResolutionCacheStore(db_path)
    assert relation_store.get(*negative_relation_key) is not None
    assert relation_store.get(*positive_relation_key) is not None
    relation_store.close()


def test_cache_main_apply_deletes_only_negative_repo_discovery_cache_entries(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    negative_url, positive_url = _seed_repo_cache(db_path)
    negative_relation_key, positive_relation_key = _seed_relation_resolution_cache(db_path)

    exit_code = cache.main(["--db-path", str(db_path), "--apply"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Deleted 1 negative repo discovery cache entries" in captured.out
    assert "Deleted 1 negative relation resolution cache entries" in captured.out

    store = RepoCacheStore(db_path)
    assert store.get(negative_url) is None
    positive_entry = store.get(positive_url)
    assert positive_entry is not None
    assert positive_entry.github_url == "https://github.com/foo/bar"
    store.close()
    relation_store = RelationResolutionCacheStore(db_path)
    assert relation_store.get(*negative_relation_key) is None
    assert relation_store.get(*positive_relation_key) is not None
    relation_store.close()


def test_cache_main_dry_run_auto_migrates_legacy_cache_schema_and_reports_changes(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE repo_cache (
            arxiv_url TEXT PRIMARY KEY,
            github_url TEXT,
            hf_exact_no_repo_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_hf_exact_checked_at TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO repo_cache (
            arxiv_url, github_url, hf_exact_no_repo_count, created_at, updated_at, last_hf_exact_checked_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "https://arxiv.org/abs/2603.18493",
            None,
            10,
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
        ),
    )
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
            "source_url",
            "https://www.semanticscholar.org/paper/Foo/abc123",
            "https://arxiv.org/abs/2501.12345",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.execute(
        """
        INSERT INTO relation_resolution_cache (key_type, key_value, arxiv_url, checked_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "legacy_source",
            "https://legacy.example/paper/123",
            "https://arxiv.org/abs/2509.12345",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    exit_code = cache.main(["--db-path", str(db_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Cache migration summary:" in captured.out
    assert "repo_cache: migrated legacy schema to last_repo_discovery_checked_at" in captured.out
    assert "repo_metadata_cache: created missing table" in captured.out
    assert "relation_resolution_cache: added resolved_title column" in captured.out
    assert "relation_resolution_cache: deleted 1 unsupported legacy rows" in captured.out

    connection = sqlite3.connect(db_path)
    repo_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(repo_cache)").fetchall()
    }
    relation_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(relation_resolution_cache)").fetchall()
    }
    repo_metadata_table = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'repo_metadata_cache'
        """
    ).fetchone()
    supported_row = connection.execute(
        """
        SELECT arxiv_url
        FROM relation_resolution_cache
        WHERE key_type = ? AND key_value = ?
        """,
        (
            "source_url",
            "https://www.semanticscholar.org/paper/Foo/abc123",
        ),
    ).fetchone()
    legacy_row = connection.execute(
        """
        SELECT arxiv_url
        FROM relation_resolution_cache
        WHERE key_type = ? AND key_value = ?
        """,
        ("legacy_source", "https://legacy.example/paper/123"),
    ).fetchone()
    connection.close()

    assert "last_repo_discovery_checked_at" in repo_columns
    assert "last_hf_exact_checked_at" not in repo_columns
    assert "hf_exact_no_repo_count" not in repo_columns
    assert repo_metadata_table is not None
    assert "resolved_title" in relation_columns
    assert supported_row is not None
    assert supported_row[0] == "https://arxiv.org/abs/2501.12345"
    assert legacy_row is None
