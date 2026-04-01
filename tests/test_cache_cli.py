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


def test_cache_main_dry_run_does_not_mutate_unsupported_legacy_relation_rows(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    _seed_repo_cache(db_path)
    _seed_relation_resolution_cache(db_path)

    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO relation_resolution_cache (key_type, key_value, arxiv_url, resolved_title, checked_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "legacy_source",
            "https://legacy.example/paper/123",
            "https://arxiv.org/abs/2509.12345",
            "Legacy Cached Title",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    exit_code = cache.main(["--db-path", str(db_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Dry run" in captured.out

    connection = sqlite3.connect(db_path)
    row = connection.execute(
        """
        SELECT arxiv_url
        FROM relation_resolution_cache
        WHERE key_type = ? AND key_value = ?
        """,
        ("legacy_source", "https://legacy.example/paper/123"),
    ).fetchone()
    connection.close()

    assert row is not None
    assert row[0] == "https://arxiv.org/abs/2509.12345"
