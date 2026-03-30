import cache as cache_script

from src.shared.repo_cache import RepoCacheStore


def _seed_repo_cache(db_path):
    store = RepoCacheStore(db_path)
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
    store.close()


def test_cache_script_dry_run_reports_negative_entries_without_deleting(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    _seed_repo_cache(db_path)

    exit_code = cache_script.main(["--db", str(db_path)])
    captured = capsys.readouterr()
    store = RepoCacheStore(db_path)
    stats = store.get_stats()

    assert exit_code == 0
    assert f"Repo cache DB: {db_path}" in captured.out
    assert "Dry run: would delete 2 negative entries" in captured.out
    assert stats.total_entries == 3
    assert stats.positive_entries == 1
    assert stats.negative_entries == 2


def test_cache_script_apply_deletes_only_negative_entries(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    _seed_repo_cache(db_path)

    exit_code = cache_script.main(["--db", str(db_path), "--apply"])
    captured = capsys.readouterr()
    store = RepoCacheStore(db_path)
    stats = store.get_stats()

    assert exit_code == 0
    assert "Deleted negative entries: 2" in captured.out
    assert stats.total_entries == 1
    assert stats.positive_entries == 1
    assert stats.negative_entries == 0
    assert store.get("https://arxiv.org/abs/2603.18493") is not None
    assert store.get("https://arxiv.org/abs/2603.18494") is None
    assert store.get("https://arxiv.org/abs/2603.18495") is None


def test_cache_script_errors_when_db_is_missing(tmp_path, capsys):
    db_path = tmp_path / "missing-cache.db"

    exit_code = cache_script.main(["--db", str(db_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert f"Repo cache DB not found: {db_path}" in captured.err
    assert not db_path.exists()
