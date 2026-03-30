import sqlite3

from src.shared.repo_cache import RepoCacheStore


def test_repo_cache_store_records_found_repo_and_keeps_timestamp_fields(tmp_path):
    store = RepoCacheStore(tmp_path / "cache.db")

    store.record_discovery_no_repo("https://arxiv.org/abs/2603.18493")
    store.record_found_repo("https://arxiv.org/abs/2603.18493", "https://github.com/foo/bar")

    entry = store.get("https://arxiv.org/abs/2603.18493")

    assert entry is not None
    assert entry.github_url == "https://github.com/foo/bar"
    assert entry.last_repo_discovery_checked_at is not None


def test_repo_cache_store_records_successful_discovery_no_repo_timestamp(tmp_path):
    store = RepoCacheStore(tmp_path / "cache.db")

    store.record_discovery_no_repo("https://arxiv.org/abs/2603.18493")

    entry = store.get("https://arxiv.org/abs/2603.18493")

    assert entry is not None
    assert entry.github_url is None
    assert entry.last_repo_discovery_checked_at is not None


def test_repo_cache_store_keeps_legacy_record_exact_no_repo_alias(tmp_path):
    store = RepoCacheStore(tmp_path / "cache.db")

    store.record_exact_no_repo("https://arxiv.org/abs/2603.18493")

    entry = store.get("https://arxiv.org/abs/2603.18493")

    assert entry is not None
    assert entry.github_url is None
    assert entry.last_repo_discovery_checked_at is not None


def test_repo_cache_store_migrates_old_threshold_schema(tmp_path):
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
    connection.commit()
    connection.close()

    store = RepoCacheStore(db_path)
    entry = store.get("https://arxiv.org/abs/2603.18493")
    columns = {
        row[1]
        for row in store.connection.execute("PRAGMA table_info(repo_cache)").fetchall()
    }

    assert entry is not None
    assert entry.github_url is None
    assert entry.last_repo_discovery_checked_at == "2026-03-20T00:00:00+00:00"
    assert "hf_exact_no_repo_count" not in columns


def test_repo_cache_store_migrates_legacy_timestamp_column_name(tmp_path):
    db_path = tmp_path / "cache.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE repo_cache (
            arxiv_url TEXT PRIMARY KEY,
            github_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_hf_exact_checked_at TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO repo_cache (
            arxiv_url, github_url, created_at, updated_at, last_hf_exact_checked_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "https://arxiv.org/abs/2603.18493",
            None,
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    store = RepoCacheStore(db_path)
    entry = store.get("https://arxiv.org/abs/2603.18493")
    columns = {
        row[1]
        for row in store.connection.execute("PRAGMA table_info(repo_cache)").fetchall()
    }

    assert entry is not None
    assert entry.last_repo_discovery_checked_at == "2026-03-20T00:00:00+00:00"
    assert "last_repo_discovery_checked_at" in columns
    assert "last_hf_exact_checked_at" not in columns


def test_repo_cache_store_reports_positive_and_negative_entry_counts(tmp_path):
    store = RepoCacheStore(tmp_path / "cache.db")
    store.record_found_repo("https://arxiv.org/abs/2603.18493", "https://github.com/foo/bar")
    store.record_discovery_no_repo("https://arxiv.org/abs/2603.18494")
    store.connection.execute(
        """
        INSERT INTO repo_cache (
            arxiv_url, github_url, created_at, updated_at, last_repo_discovery_checked_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "https://arxiv.org/abs/2603.18495",
            "",
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
        ),
    )
    store.connection.commit()

    stats = store.get_stats()

    assert stats.total_entries == 3
    assert stats.positive_entries == 1
    assert stats.negative_entries == 2


def test_repo_cache_store_delete_negative_entries_removes_only_negative_rows(tmp_path):
    store = RepoCacheStore(tmp_path / "cache.db")
    store.record_found_repo("https://arxiv.org/abs/2603.18493", "https://github.com/foo/bar")
    store.record_discovery_no_repo("https://arxiv.org/abs/2603.18494")
    store.connection.execute(
        """
        INSERT INTO repo_cache (
            arxiv_url, github_url, created_at, updated_at, last_repo_discovery_checked_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "https://arxiv.org/abs/2603.18495",
            "",
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
        ),
    )
    store.connection.commit()

    deleted = store.delete_negative_entries()
    stats = store.get_stats()

    assert deleted == 2
    assert store.get("https://arxiv.org/abs/2603.18493") is not None
    assert store.get("https://arxiv.org/abs/2603.18494") is None
    assert store.get("https://arxiv.org/abs/2603.18495") is None
    assert stats.total_entries == 1
    assert stats.positive_entries == 1
    assert stats.negative_entries == 0


def test_repo_cache_store_counts_and_deletes_only_negative_discovery_cache_entries(tmp_path):
    store = RepoCacheStore(tmp_path / "cache.db")
    negative_url = "https://arxiv.org/abs/2603.18493"
    positive_url = "https://arxiv.org/abs/2603.18494"
    unchecked_url = "https://arxiv.org/abs/2603.18495"

    store.record_discovery_no_repo(negative_url)
    store.record_found_repo(positive_url, "https://github.com/foo/bar")
    store.connection.execute(
        """
        INSERT INTO repo_cache (
            arxiv_url,
            github_url,
            created_at,
            updated_at,
            last_repo_discovery_checked_at
        )
        VALUES (?, NULL, ?, ?, NULL)
        """,
        (
            unchecked_url,
            "2026-03-20T00:00:00+00:00",
            "2026-03-20T00:00:00+00:00",
        ),
    )
    store.connection.commit()

    assert store.count_negative_repo_discovery_entries() == 1

    deleted = store.delete_negative_repo_discovery_entries()

    assert deleted == 1
    assert store.get(negative_url) is None
    assert store.get(positive_url) is not None
    assert store.get(positive_url).github_url == "https://github.com/foo/bar"
    assert store.get(unchecked_url) is not None
    assert store.count_negative_repo_discovery_entries() == 0
