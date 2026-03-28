import sqlite3

import pytest

from src.shared import runtime as runtime_module
from src.shared.relation_resolution_cache import RelationResolutionCacheStore
from src.shared.runtime import load_runtime_config, open_runtime_clients


def test_load_runtime_config_reads_only_optional_tokens():
    config = load_runtime_config(
        {
            "GITHUB_TOKEN": "gh_token",
            "HUGGINGFACE_TOKEN": "hf_token",
            "HF_EXACT_NO_REPO_RECHECK_DAYS": "7",
        }
    )

    assert config == {
        "github_token": "gh_token",
        "huggingface_token": "hf_token",
        "openalex_api_key": "",
        "arxiv_relation_no_arxiv_recheck_days": 30,
        "hf_exact_no_repo_recheck_days": 7,
    }


def test_load_runtime_config_defaults_missing_values_to_empty_strings():
    assert load_runtime_config({}) == {
        "github_token": "",
        "huggingface_token": "",
        "openalex_api_key": "",
        "arxiv_relation_no_arxiv_recheck_days": 30,
        "hf_exact_no_repo_recheck_days": 7,
    }


def test_load_runtime_config_falls_back_to_default_recheck_days_for_invalid_value():
    assert load_runtime_config({"HF_EXACT_NO_REPO_RECHECK_DAYS": "abc"})["hf_exact_no_repo_recheck_days"] == 7


def test_load_runtime_config_reads_optional_openalex_token():
    config = load_runtime_config(
        {
            "OPENALEX_API_KEY": "oa_key",
        }
    )

    assert config["openalex_api_key"] == "oa_key"


def test_load_runtime_config_reads_relation_resolution_recheck_days():
    config = load_runtime_config(
        {
            "ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS": "30",
        }
    )

    assert config["arxiv_relation_no_arxiv_recheck_days"] == 30


def test_load_runtime_config_defaults_relation_resolution_recheck_days():
    config = load_runtime_config({})

    assert config["arxiv_relation_no_arxiv_recheck_days"] == 30


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDiscoveryClient:
    def __init__(
        self,
        session,
        *,
        huggingface_token="",
        repo_cache=None,
        hf_exact_no_repo_recheck_days=0,
        max_concurrent=0,
        min_interval=0,
    ):
        self.session = session
        self.huggingface_token = huggingface_token
        self.repo_cache = repo_cache
        self.hf_exact_no_repo_recheck_days = hf_exact_no_repo_recheck_days
        self.max_concurrent = max_concurrent
        self.min_interval = min_interval


class FakeGitHubClient:
    def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
        self.session = session
        self.github_token = github_token
        self.max_concurrent = max_concurrent
        self.min_interval = min_interval


@pytest.mark.anyio
async def test_open_runtime_clients_builds_shared_clients_without_alphaxiv_token(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runtime_module, "REPO_CACHE_DB_PATH", tmp_path / "cache.db")

    config = load_runtime_config(
        {
            "GITHUB_TOKEN": "gh_token",
            "HUGGINGFACE_TOKEN": "hf_token",
            "HF_EXACT_NO_REPO_RECHECK_DAYS": "9",
            "ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS": "31",
        }
    )

    async with open_runtime_clients(
        config,
        session_factory=lambda **kwargs: FakeSession(),
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        concurrent_limit=7,
        request_delay=0.3,
        github_min_interval=0.4,
        enable_relation_resolution_cache=True,
    ) as runtime:
        assert isinstance(runtime.relation_resolution_cache, RelationResolutionCacheStore)
        assert runtime.discovery_client.huggingface_token == "hf_token"
        assert runtime.discovery_client.repo_cache is runtime.repo_cache
        assert runtime.discovery_client.hf_exact_no_repo_recheck_days == 9
        assert runtime.discovery_client.max_concurrent == 7
        assert runtime.discovery_client.min_interval == 0.3
        assert runtime.github_client.github_token == "gh_token"
        assert runtime.github_client.max_concurrent == 7
        assert runtime.github_client.min_interval == 0.4

        runtime.relation_resolution_cache.record_resolution(
            key_type="doi",
            key_value="https://doi.org/10.1000/example",
            arxiv_url=None,
        )

        entry = runtime.relation_resolution_cache.get(
            "doi", "https://doi.org/10.1000/example"
        )

        assert entry is not None
        assert entry.arxiv_url is None


@pytest.mark.anyio
async def test_open_runtime_clients_leaves_relation_cache_disabled_by_default_for_read_only_db(
    tmp_path, monkeypatch
):
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
    connection.commit()
    connection.close()
    db_path.chmod(0o444)

    monkeypatch.setattr(runtime_module, "REPO_CACHE_DB_PATH", db_path)

    async with open_runtime_clients(
        load_runtime_config({}),
        session_factory=lambda **kwargs: FakeSession(),
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        concurrent_limit=2,
        request_delay=0.1,
    ) as runtime:
        assert runtime.relation_resolution_cache is None


@pytest.mark.anyio
async def test_open_runtime_clients_degrades_to_uncached_mode_if_relation_cache_setup_fails(
    monkeypatch,
):
    close_calls = []

    class FakeRepoCacheStore:
        def __init__(self, db_path):
            self.db_path = db_path

        def close(self):
            close_calls.append("repo_cache")

    class ExplodingRelationResolutionCacheStore:
        def __init__(self, db_path):
            raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(runtime_module, "RepoCacheStore", FakeRepoCacheStore)
    monkeypatch.setattr(
        runtime_module,
        "RelationResolutionCacheStore",
        ExplodingRelationResolutionCacheStore,
    )

    async with open_runtime_clients(
        load_runtime_config({}),
        session_factory=lambda **kwargs: FakeSession(),
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        concurrent_limit=2,
        request_delay=0.1,
        enable_relation_resolution_cache=True,
    ) as runtime:
        assert runtime.relation_resolution_cache is None
        assert runtime.repo_cache is not None

    assert close_calls == ["repo_cache"]

    assert close_calls == ["repo_cache"]
