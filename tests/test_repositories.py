from src.core.repositories import RepoMetadataRepository
from src.shared.repo_metadata_cache import RepoMetadataCacheStore


def test_repo_metadata_repository_reads_and_writes_durable_created_values(tmp_path):
    store = RepoMetadataCacheStore(tmp_path / "cache.db")
    repository = RepoMetadataRepository(store=store)

    repository.record_created("https://github.com/foo/bar", "2020-01-01T00:00:00Z")
    entry = repository.get("https://github.com/foo/bar")

    assert entry is not None
    assert entry.created == "2020-01-01T00:00:00Z"

    store.close()
