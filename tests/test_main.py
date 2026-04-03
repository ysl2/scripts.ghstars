import sqlite3
from pathlib import Path

import pytest

import src.app as app_module
from src.shared import runtime as runtime_module
from src.shared.relation_resolution_cache import RelationResolutionCacheStore
from src.shared.repo_metadata_cache import RepoMetadataCacheStore
from src.shared.runtime import load_runtime_config, open_runtime_clients


def test_load_runtime_config_reads_only_optional_tokens():
    config = load_runtime_config(
        {
            "GITHUB_TOKEN": "gh_token",
            "HUGGINGFACE_TOKEN": "hf_token",
            "ALPHAXIV_TOKEN": "ax_token",
            "AIFORSCHOLAR_TOKEN": "relay_token",
            "REPO_DISCOVERY_NO_REPO_RECHECK_DAYS": "7",
        }
    )

    assert config == {
        "github_token": "gh_token",
        "huggingface_token": "hf_token",
        "alphaxiv_token": "ax_token",
        "aiforscholar_token": "relay_token",
        "semantic_scholar_api_key": "",
        "arxiv_relation_no_arxiv_recheck_days": 30,
        "repo_discovery_no_repo_recheck_days": 7,
    }


def test_load_runtime_config_defaults_missing_values_to_empty_strings():
    assert load_runtime_config({}) == {
        "github_token": "",
        "huggingface_token": "",
        "alphaxiv_token": "",
        "aiforscholar_token": "",
        "semantic_scholar_api_key": "",
        "arxiv_relation_no_arxiv_recheck_days": 30,
        "repo_discovery_no_repo_recheck_days": 7,
    }


def test_load_runtime_config_prefers_generic_repo_discovery_recheck_days():
    config = load_runtime_config(
        {
            "REPO_DISCOVERY_NO_REPO_RECHECK_DAYS": "5",
            "HF_EXACT_NO_REPO_RECHECK_DAYS": "9",
        }
    )

    assert config["repo_discovery_no_repo_recheck_days"] == 5


def test_load_runtime_config_accepts_legacy_hf_exact_recheck_days_alias():
    assert load_runtime_config({"HF_EXACT_NO_REPO_RECHECK_DAYS": "8"})["repo_discovery_no_repo_recheck_days"] == 8


def test_load_runtime_config_falls_back_to_default_recheck_days_for_invalid_value():
    assert load_runtime_config({"REPO_DISCOVERY_NO_REPO_RECHECK_DAYS": "abc"})["repo_discovery_no_repo_recheck_days"] == 7


def test_load_runtime_config_ignores_unknown_legacy_tokens():
    config = load_runtime_config(
        {
            "LEGACY_METADATA_TOKEN": "legacy_key",
        }
    )

    assert "LEGACY_METADATA_TOKEN" not in config


def test_load_runtime_config_reads_optional_semantic_scholar_api_key():
    config = load_runtime_config(
        {
            "SEMANTIC_SCHOLAR_API_KEY": "ss_key",
        }
    )

    assert config["semantic_scholar_api_key"] == "ss_key"


def test_load_runtime_config_reads_optional_aiforscholar_token():
    config = load_runtime_config(
        {
            "AIFORSCHOLAR_TOKEN": "relay_token",
        }
    )

    assert config["aiforscholar_token"] == "relay_token"


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


def test_env_example_includes_aiforscholar_token():
    assert "AIFORSCHOLAR_TOKEN=" in Path(".env.example").read_text()


def test_env_example_lists_only_semantic_scholar_relation_tokens():
    text = Path(".env.example").read_text()

    assert "SEMANTIC_SCHOLAR_API_KEY=" in text
    assert "AIFORSCHOLAR_TOKEN=" in text
    assert "ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS=30" in text


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
        alphaxiv_token="",
        repo_cache=None,
        repo_discovery_no_repo_recheck_days=0,
        max_concurrent=0,
        min_interval=0,
    ):
        self.session = session
        self.huggingface_token = huggingface_token
        self.alphaxiv_token = alphaxiv_token
        self.repo_cache = repo_cache
        self.repo_discovery_no_repo_recheck_days = repo_discovery_no_repo_recheck_days
        self.max_concurrent = max_concurrent
        self.min_interval = min_interval


class FakeGitHubClient:
    def __init__(
        self,
        session,
        *,
        github_token="",
        repo_metadata_cache=None,
        max_concurrent=0,
        min_interval=0,
    ):
        self.session = session
        self.github_token = github_token
        self.repo_metadata_cache = repo_metadata_cache
        self.max_concurrent = max_concurrent
        self.min_interval = min_interval


@pytest.mark.anyio
async def test_open_runtime_clients_builds_shared_clients_with_optional_alphaxiv_token(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runtime_module, "REPO_CACHE_DB_PATH", tmp_path / "cache.db")

    config = load_runtime_config(
        {
            "GITHUB_TOKEN": "gh_token",
            "HUGGINGFACE_TOKEN": "hf_token",
            "ALPHAXIV_TOKEN": "ax_token",
            "REPO_DISCOVERY_NO_REPO_RECHECK_DAYS": "9",
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
        assert isinstance(runtime.repo_metadata_cache, RepoMetadataCacheStore)
        assert isinstance(runtime.relation_resolution_cache, RelationResolutionCacheStore)
        assert runtime.discovery_client.huggingface_token == "hf_token"
        assert runtime.discovery_client.alphaxiv_token == "ax_token"
        assert runtime.discovery_client.repo_cache is runtime.repo_cache
        assert runtime.discovery_client.repo_discovery_no_repo_recheck_days == 9
        assert runtime.discovery_client.max_concurrent == 7
        assert runtime.discovery_client.min_interval == 0.3
        assert runtime.github_client.github_token == "gh_token"
        assert runtime.github_client.repo_metadata_cache is runtime.repo_metadata_cache
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
            last_repo_discovery_checked_at TEXT
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

    class FakeRepoMetadataCacheStore:
        def __init__(self, db_path):
            self.db_path = db_path

        def close(self):
            close_calls.append("repo_metadata_cache")

    monkeypatch.setattr(runtime_module, "RepoCacheStore", FakeRepoCacheStore)
    monkeypatch.setattr(runtime_module, "RepoMetadataCacheStore", FakeRepoMetadataCacheStore)
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
        assert runtime.repo_metadata_cache is not None

    assert close_calls == ["repo_metadata_cache", "repo_cache"]


@pytest.mark.anyio
async def test_async_main_routes_supported_github_search_url_to_github_search_runner(
    monkeypatch,
):
    called = []

    async def fake_run_github_search_mode(raw_url: str):
        called.append(raw_url)
        return 0

    async def fail_run_url_mode(raw_url: str):
        raise AssertionError(f"unexpected url-mode dispatch for {raw_url}")

    monkeypatch.setattr(
        app_module,
        "run_github_search_mode",
        fake_run_github_search_mode,
        raising=False,
    )
    monkeypatch.setattr(app_module, "run_url_mode", fail_run_url_mode, raising=False)

    exit_code = await app_module.async_main(
        ["https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc"]
    )

    assert exit_code == 0
    assert called == [
        "https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc"
    ]


def test_app_detects_input_shapes_without_exposing_mode_as_top_level_concept():
    assert app_module.detect_input_shape([]) == app_module.InputShape.NOTION
    assert app_module.detect_input_shape(["/tmp/input.csv"]) == app_module.InputShape.CSV_FILE
    assert (
        app_module.detect_input_shape(
            ["https://github.com/search?q=cvpr%202026&type=repositories"]
        )
        == app_module.InputShape.GITHUB_SEARCH_URL
    )
