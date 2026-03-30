from pathlib import Path

import cache
from src.shared.repo_cache import RepoCacheStore


def _seed_repo_cache(db_path: Path) -> tuple[str, str]:
    store = RepoCacheStore(db_path)
    negative_url = "https://arxiv.org/abs/2603.18493"
    positive_url = "https://arxiv.org/abs/2603.18494"
    store.record_discovery_no_repo(negative_url)
    store.record_found_repo(positive_url, "https://github.com/foo/bar")
    store.close()
    return negative_url, positive_url


def test_cache_main_dry_run_reports_negative_cache_count_without_deleting(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    negative_url, positive_url = _seed_repo_cache(db_path)

    exit_code = cache.main(["--db-path", str(db_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Dry run" in captured.out
    assert "1 negative repo discovery cache entries" in captured.out
    assert "--apply" in captured.out

    store = RepoCacheStore(db_path)
    assert store.get(negative_url) is not None
    assert store.get(positive_url) is not None
    store.close()


def test_cache_main_apply_deletes_only_negative_repo_discovery_cache_entries(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    negative_url, positive_url = _seed_repo_cache(db_path)

    exit_code = cache.main(["--db-path", str(db_path), "--apply"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Deleted 1 negative repo discovery cache entries" in captured.out

    store = RepoCacheStore(db_path)
    assert store.get(negative_url) is None
    positive_entry = store.get(positive_url)
    assert positive_entry is not None
    assert positive_entry.github_url == "https://github.com/foo/bar"
    store.close()
