import asyncio
import types
from unittest.mock import AsyncMock

import pytest

import src.notion_sync.runner as notion_runner
from src.notion_sync.config import load_config_from_env
from src.notion_sync.notion_client import NotionClient
import src.notion_sync.pipeline as notion_pipeline
from src.core.record_model import Record, RecordContext
from src.notion_sync.pipeline import (
    build_page_enrichment_request,
    classify_github_value,
    get_current_stars_from_page,
    get_github_url_from_page,
    get_github_property_type,
    get_page_title,
    process_page,
)
from src.notion_sync.runner import run_notion_mode


def test_load_config_requires_notion_token_and_database_id():
    with pytest.raises(ValueError):
        load_config_from_env({})


def test_load_config_accepts_env_values():
    cfg = load_config_from_env(
        {
            "NOTION_TOKEN": "notion_xxx",
            "GITHUB_TOKEN": "ghp_xxx",
            "DATABASE_ID": "db_123",
            "HUGGINGFACE_TOKEN": "hf_test",
            "ALPHAXIV_TOKEN": "ax_token",
            "AIFORSCHOLAR_TOKEN": "relay_token",
            "SEMANTIC_SCHOLAR_API_KEY": "ss_test",
            "REPO_DISCOVERY_NO_REPO_RECHECK_DAYS": "7",
        }
    )

    assert cfg == {
        "notion_token": "notion_xxx",
        "github_token": "ghp_xxx",
        "database_id": "db_123",
        "huggingface_token": "hf_test",
        "alphaxiv_token": "ax_token",
        "aiforscholar_token": "relay_token",
        "semantic_scholar_api_key": "ss_test",
        "arxiv_relation_no_arxiv_recheck_days": 30,
        "repo_discovery_no_repo_recheck_days": 7,
    }


def test_page_helpers_read_title_github_and_stars():
    page = {
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 17},
        }
    }

    assert get_page_title(page) == "Test Paper"
    assert get_github_url_from_page(page) == "https://github.com/foo/bar"
    assert get_current_stars_from_page(page) == 17
    assert get_github_property_type(page) == "url"


def test_page_helpers_do_not_read_legacy_rich_text_github_type():
    page = {
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {
                "type": "rich_text",
                "rich_text": [{"text": {"content": "https://github.com/foo/bar"}}],
            },
        }
    }

    assert get_github_url_from_page(page) is None
    assert get_github_property_type(page) is None


def test_classify_github_value_covers_expected_states():
    assert classify_github_value(None) == "empty"
    assert classify_github_value("   ") == "empty"
    assert classify_github_value("WIP") == "other"
    assert classify_github_value("https://github.com/foo/bar") == "valid_github"
    assert classify_github_value("https://example.com/project") == "other"


def test_build_page_enrichment_request_preserves_non_arxiv_raw_url_for_shared_normalization():
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "DOI Paper"}]},
            "Github": {"type": "url", "url": None},
            "URL": {"type": "url", "url": "https://doi.org/10.1007/978-3-031-72933-1_9"},
        },
    }

    request, needs_github_update, reason = build_page_enrichment_request(page)

    assert reason is None
    assert needs_github_update is True
    assert request is not None
    assert request.raw_url == "https://doi.org/10.1007/978-3-031-72933-1_9"
    assert request.allow_title_search is True


def test_build_page_enrichment_request_uses_notion_input_adapter(monkeypatch):
    adapter_calls = []

    class FakeAdapter:
        def to_record(self, page):
            adapter_calls.append(page["id"])
            return Record.from_source(
                name="Adapter Title",
                url="https://doi.org/10.1145/example",
                github="https://github.com/foo/bar",
                source="notion",
                trusted_fields={"github"},
            ).with_supporting_state(context=RecordContext(notion_page_id=page["id"]))

    monkeypatch.setattr(notion_pipeline, "NotionPageInputAdapter", FakeAdapter)

    request, needs_github_update, reason = build_page_enrichment_request(
        {
            "id": "page-1",
            "url": "https://notion.so/page-1",
            "properties": {},
        }
    )

    assert adapter_calls == ["page-1"]
    assert reason is None
    assert needs_github_update is False
    assert request is not None
    assert request.title == "Adapter Title"
    assert request.raw_url == "https://doi.org/10.1145/example"
    assert request.existing_github_url == "https://github.com/foo/bar"


@pytest.mark.anyio
async def test_update_page_properties_writes_stars_property():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        pages=types.SimpleNamespace(update=AsyncMock(return_value={"ok": True}))
    )

    await client.update_page_properties("page-1", stars_count=42)

    client.client.pages.update.assert_awaited_once_with(
        page_id="page-1",
        properties={"Stars": {"number": 42}},
    )


@pytest.mark.anyio
async def test_update_page_properties_writes_created_and_empty_about_properties():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        pages=types.SimpleNamespace(update=AsyncMock(return_value={"ok": True}))
    )

    await client.update_page_properties(
        "page-1",
        stars_count=42,
        created_value="2024-01-01T00:00:00Z",
        about_text="",
    )

    client.client.pages.update.assert_awaited_once_with(
        page_id="page-1",
        properties={
            "Stars": {"number": 42},
            "Created": {"date": {"start": "2024-01-01T00:00:00Z"}},
            "About": {"rich_text": []},
        },
    )


@pytest.mark.anyio
async def test_update_page_properties_writes_non_empty_about_property():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        pages=types.SimpleNamespace(update=AsyncMock(return_value={"ok": True}))
    )

    await client.update_page_properties(
        "page-1",
        about_text="repo summary",
    )

    client.client.pages.update.assert_awaited_once_with(
        page_id="page-1",
        properties={
            "About": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": "repo summary"},
                    }
                ]
            }
        },
    )


@pytest.mark.anyio
async def test_update_page_properties_retries_after_error():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        pages=types.SimpleNamespace(update=AsyncMock(side_effect=[Exception("boom"), {"ok": True}]))
    )

    await client.update_page_properties("page-1", stars_count=42)

    assert client.client.pages.update.await_count == 2


@pytest.mark.anyio
async def test_update_page_properties_writes_github_url_property():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        pages=types.SimpleNamespace(update=AsyncMock(return_value={"ok": True}))
    )

    await client.update_page_properties(
        "page-1",
        github_url="https://github.com/foo/bar",
    )

    client.client.pages.update.assert_awaited_once_with(
        page_id="page-1",
        properties={
            "Github": {"url": "https://github.com/foo/bar"}
        },
    )


@pytest.mark.anyio
async def test_update_page_properties_rejects_non_url_github_property_type():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        pages=types.SimpleNamespace(update=AsyncMock(return_value={"ok": True}))
    )

    with pytest.raises(ValueError, match="Notion property Github must have type url"):
        await client.update_page_properties(
            "page-1",
            github_url="https://github.com/foo/bar",
            github_property_type="rich_text",
        )

    client.client.pages.update.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_sync_properties_adds_created_and_about_with_expected_types():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        data_sources=types.SimpleNamespace(
            retrieve=AsyncMock(
                return_value={
                    "properties": {
                        "Name": {"type": "title", "title": {}},
                    }
                }
            ),
            update=AsyncMock(return_value={"ok": True}),
        )
    )

    await client.ensure_sync_properties("data-source-1")

    client.client.data_sources.update.assert_awaited_once_with(
        data_source_id="data-source-1",
        properties={
            "Github": {
                "type": "url",
                "url": {},
            },
            "Stars": {
                "type": "number",
                "number": {"format": "number"},
            },
            "Created": {
                "type": "date",
                "date": {},
            },
            "About": {
                "type": "rich_text",
                "rich_text": {},
            },
        },
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("property_name", "property_value", "expected_reason"),
    [
        ("Github", {"type": "rich_text", "rich_text": []}, "Notion property Github must have type url"),
        ("Stars", {"type": "rich_text", "rich_text": []}, "Notion property Stars must have type number"),
        ("Created", {"type": "rich_text", "rich_text": []}, "Notion property Created must have type date"),
        ("About", {"type": "url", "url": "https://example.com"}, "Notion property About must have type rich_text"),
    ],
)
async def test_ensure_sync_properties_hard_fails_for_wrong_existing_types(
    property_name,
    property_value,
    expected_reason,
):
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        data_sources=types.SimpleNamespace(
            retrieve=AsyncMock(
                return_value={
                    "properties": {
                        "Name": {"type": "title", "title": {}},
                        property_name: property_value,
                    }
                }
            ),
            update=AsyncMock(return_value={"ok": True}),
        )
    )

    with pytest.raises(ValueError, match=expected_reason):
        await client.ensure_sync_properties("data-source-1")

    client.client.data_sources.update.assert_not_awaited()


@pytest.mark.anyio
async def test_process_page_records_notion_update_failure_without_crashing_batch():
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 10},
        },
    }
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(12, None)))
    notion_client = types.SimpleNamespace(
        update_page_properties=AsyncMock(side_effect=Exception("network down"))
    )
    content_cache = types.SimpleNamespace(ensure_local_content_cache=AsyncMock())
    results = {"updated": 0, "skipped": []}
    lock = asyncio.Lock()

    await process_page(
        page,
        index=1,
        total=1,
        discovery_client=discovery_client,
        github_client=github_client,
        notion_client=notion_client,
        results=results,
        lock=lock,
        content_cache=content_cache,
    )

    assert results["updated"] == 0
    assert len(results["skipped"]) == 1
    assert results["skipped"][0]["reason"] == "Notion update failed: network down"


@pytest.mark.anyio
async def test_process_page_prints_progress_for_successful_update(capsys):
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 10},
        },
    }
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(12, None)))
    notion_client = types.SimpleNamespace(update_page_properties=AsyncMock(return_value=None))
    content_cache = types.SimpleNamespace(ensure_local_content_cache=AsyncMock())
    results = {"updated": 0, "skipped": []}
    lock = asyncio.Lock()

    await process_page(
        page,
        index=1,
        total=1,
        discovery_client=discovery_client,
        github_client=github_client,
        notion_client=notion_client,
        results=results,
        lock=lock,
        content_cache=content_cache,
    )

    captured = capsys.readouterr()
    assert "[1/1] Test Paper" in captured.out
    assert "foo/bar" in captured.out
    assert "Updated: 10 → 12" in captured.out
    content_cache.ensure_local_content_cache.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("property_name", "property_value", "expected_reason"),
    [
        ("Github", {"type": "rich_text", "rich_text": []}, "Notion property Github must have type url"),
        ("Stars", {"type": "rich_text", "rich_text": []}, "Notion property Stars must have type number"),
        ("Created", {"type": "rich_text", "rich_text": []}, "Notion property Created must have type date"),
        ("About", {"type": "url", "url": "https://example.com"}, "Notion property About must have type rich_text"),
    ],
)
async def test_process_page_hard_fails_for_wrong_managed_property_types(
    property_name,
    property_value,
    expected_reason,
):
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 10},
            "Created": {"type": "date", "date": {"start": None}},
            "About": {"type": "rich_text", "rich_text": []},
            "URL": {"type": "url", "url": "https://arxiv.org/abs/2603.05078"},
            property_name: property_value,
        },
    }
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(12, None)))
    notion_client = types.SimpleNamespace(update_page_properties=AsyncMock(return_value=None))
    content_cache = types.SimpleNamespace(ensure_local_content_cache=AsyncMock())
    results = {"updated": 0, "skipped": []}
    lock = asyncio.Lock()

    await process_page(
        page,
        index=1,
        total=1,
        discovery_client=discovery_client,
        github_client=github_client,
        notion_client=notion_client,
        results=results,
        lock=lock,
        content_cache=content_cache,
    )

    assert results["updated"] == 0
    assert results["skipped"] == [
        {
            "title": "Test Paper",
            "github_url": None,
            "detail_url": "https://notion.so/page-1",
            "reason": expected_reason,
        }
    ]
    discovery_client.resolve_github_url.assert_not_awaited()
    github_client.get_star_count.assert_not_awaited()
    notion_client.update_page_properties.assert_not_awaited()
    content_cache.ensure_local_content_cache.assert_not_awaited()


@pytest.mark.anyio
async def test_process_page_skips_unsupported_github_field_before_engine_runs():
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {"type": "url", "url": "WIP"},
            "Stars": {"type": "number", "number": 10},
        },
    }
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(get_star_count=AsyncMock())
    notion_client = types.SimpleNamespace(update_page_properties=AsyncMock(return_value=None))
    content_cache = types.SimpleNamespace(ensure_local_content_cache=AsyncMock())
    results = {"updated": 0, "skipped": []}
    lock = asyncio.Lock()

    await process_page(
        page,
        index=1,
        total=1,
        discovery_client=discovery_client,
        github_client=github_client,
        notion_client=notion_client,
        results=results,
        lock=lock,
        content_cache=content_cache,
    )

    assert results["updated"] == 0
    assert results["skipped"] == [
        {
            "title": "Test Paper",
            "github_url": "WIP",
            "detail_url": "https://notion.so/page-1",
            "reason": "Existing Github URL is not a valid GitHub repository",
        }
    ]
    discovery_client.resolve_github_url.assert_not_awaited()
    github_client.get_star_count.assert_not_awaited()
    notion_client.update_page_properties.assert_not_awaited()
    content_cache.ensure_local_content_cache.assert_not_awaited()


@pytest.mark.anyio
async def test_process_page_prefers_existing_github_without_discovery():
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 10},
            "URL": {"type": "url", "url": "https://arxiv.org/abs/2603.05078"},
        },
    }
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(12, None)))
    notion_client = types.SimpleNamespace(update_page_properties=AsyncMock(return_value=None))
    content_cache = types.SimpleNamespace(ensure_local_content_cache=AsyncMock())
    results = {"updated": 0, "skipped": []}
    lock = asyncio.Lock()

    await process_page(
        page,
        index=1,
        total=1,
        discovery_client=discovery_client,
        github_client=github_client,
        notion_client=notion_client,
        results=results,
        lock=lock,
        content_cache=content_cache,
    )

    discovery_client.resolve_github_url.assert_not_awaited()
    notion_client.update_page_properties.assert_awaited_once_with(
        "page-1",
        github_url=None,
        stars_count=12,
        created_value=None,
        about_text=None,
        github_property_type="url",
    )


@pytest.mark.anyio
async def test_process_page_writes_stars_about_and_created_backfill_when_created_is_empty():
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Paper A"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 10},
            "Created": {"type": "date", "date": None},
            "About": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "old about", "text": {"content": "old about"}}],
            },
        },
    }
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(
        get_repo_metadata=AsyncMock(
            return_value=(types.SimpleNamespace(stars=12, created="2024-02-02T00:00:00Z", about=""), None)
        )
    )
    notion_client = types.SimpleNamespace(update_page_properties=AsyncMock(return_value=None))
    content_cache = types.SimpleNamespace(ensure_local_content_cache=AsyncMock())
    results = {"updated": 0, "skipped": []}
    lock = asyncio.Lock()

    await process_page(
        page,
        index=1,
        total=1,
        discovery_client=discovery_client,
        github_client=github_client,
        notion_client=notion_client,
        results=results,
        lock=lock,
        content_cache=content_cache,
    )

    notion_client.update_page_properties.assert_awaited_once_with(
        "page-1",
        github_url=None,
        stars_count=12,
        created_value="2024-02-02T00:00:00Z",
        about_text="",
        github_property_type="url",
    )
    discovery_client.resolve_github_url.assert_not_awaited()


@pytest.mark.anyio
async def test_process_page_does_not_overwrite_existing_created_value():
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Paper A"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 10},
            "Created": {"type": "date", "date": {"start": "2020-01-01T00:00:00Z"}},
            "About": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "old about", "text": {"content": "old about"}}],
            },
        },
    }
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(
        get_repo_metadata=AsyncMock(
            return_value=(types.SimpleNamespace(stars=12, created="2024-02-02T00:00:00Z", about="remote about"), None)
        )
    )
    notion_client = types.SimpleNamespace(update_page_properties=AsyncMock(return_value=None))
    content_cache = types.SimpleNamespace(ensure_local_content_cache=AsyncMock())
    results = {"updated": 0, "skipped": []}
    lock = asyncio.Lock()

    await process_page(
        page,
        index=1,
        total=1,
        discovery_client=discovery_client,
        github_client=github_client,
        notion_client=notion_client,
        results=results,
        lock=lock,
        content_cache=content_cache,
    )

    notion_client.update_page_properties.assert_awaited_once_with(
        "page-1",
        github_url=None,
        stars_count=12,
        created_value=None,
        about_text="remote about",
        github_property_type="url",
    )
    discovery_client.resolve_github_url.assert_not_awaited()


@pytest.mark.anyio
async def test_process_page_uses_title_search_when_github_field_is_empty():
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Title Search Paper"}]},
            "Github": {"type": "url", "url": None},
            "Stars": {"type": "number", "number": 10},
        },
    }
    discovery_client = types.SimpleNamespace(
        huggingface_token="",
        resolve_github_url=AsyncMock(return_value="https://github.com/foo/bar"),
    )
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(12, None)))
    arxiv_client = types.SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=("2603.05078", "title_search_arxiv", None))
    )
    notion_client = types.SimpleNamespace(update_page_properties=AsyncMock(return_value=None))
    content_cache = types.SimpleNamespace(ensure_local_content_cache=AsyncMock())
    results = {"updated": 0, "skipped": []}
    lock = asyncio.Lock()

    await process_page(
        page,
        index=1,
        total=1,
        discovery_client=discovery_client,
        github_client=github_client,
        notion_client=notion_client,
        results=results,
        lock=lock,
        arxiv_client=arxiv_client,
        content_cache=content_cache,
    )

    arxiv_client.get_arxiv_id_by_title.assert_awaited_once_with("Title Search Paper")
    discovery_client.resolve_github_url.assert_awaited_once()
    notion_client.update_page_properties.assert_awaited_once_with(
        "page-1",
        github_url="https://github.com/foo/bar",
        stars_count=12,
        created_value=None,
        about_text=None,
        github_property_type="url",
    )
    content_cache.ensure_local_content_cache.assert_awaited_once_with("https://arxiv.org/abs/2603.05078")


@pytest.mark.anyio
async def test_run_notion_mode_ensures_sync_properties_before_querying_pages(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "notion_xxx")
    monkeypatch.setenv("DATABASE_ID", "db_123")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_xxx")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", repo_cache=None, hf_exact_no_repo_recheck_days=0, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeNotionClient:
        instances = []

        def __init__(self, token, max_concurrent):
            self.calls = []
            type(self).instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_data_source_id(self, database_id):
            self.calls.append(("get_data_source_id", database_id))
            return "data-source-1"

        async def ensure_sync_properties(self, data_source_id):
            self.calls.append(("ensure_sync_properties", data_source_id))

        async def query_pages(self, data_source_id):
            self.calls.append(("query_pages", data_source_id))
            return []

    exit_code = await run_notion_mode(
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        notion_client_cls=FakeNotionClient,
    )

    assert exit_code == 0
    assert FakeNotionClient.instances[0].calls == [
        ("get_data_source_id", "db_123"),
        ("ensure_sync_properties", "data-source-1"),
        ("query_pages", "data-source-1"),
    ]


@pytest.mark.anyio
async def test_run_notion_mode_returns_error_when_schema_validation_fails(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "notion_xxx")
    monkeypatch.setenv("DATABASE_ID", "db_123")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_xxx")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", repo_cache=None, hf_exact_no_repo_recheck_days=0, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeNotionClient:
        def __init__(self, token, max_concurrent):
            self.token = token

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_data_source_id(self, database_id):
            return "data-source-1"

        async def ensure_sync_properties(self, data_source_id):
            raise ValueError("Notion property Github must have type url")

        async def query_pages(self, data_source_id):
            raise AssertionError("query_pages should not run when schema validation fails")

    exit_code = await run_notion_mode(
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        notion_client_cls=FakeNotionClient,
    )

    assert exit_code == 1


@pytest.mark.anyio
async def test_run_notion_mode_builds_and_passes_content_cache(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "notion_xxx")
    monkeypatch.setenv("DATABASE_ID", "db_123")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_xxx")
    monkeypatch.setenv("ALPHAXIV_TOKEN", "ax_token")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "ss_key")
    monkeypatch.setenv("AIFORSCHOLAR_TOKEN", "relay_token")

    received = {}

    async def fake_process_page(page, index, total, **kwargs):
        received["content_cache"] = kwargs.get("content_cache")
        received["semanticscholar_graph_client"] = kwargs.get("semanticscholar_graph_client")
        received["crossref_client"] = kwargs.get("crossref_client")
        received["datacite_client"] = kwargs.get("datacite_client")
        received["page"] = page

    monkeypatch.setattr(notion_runner, "process_page", fake_process_page)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeCrossrefClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDataCiteClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", repo_cache=None, hf_exact_no_repo_recheck_days=0, max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeSemanticScholarGraphClient:
        def __init__(
            self,
            session,
            *,
            semantic_scholar_api_key="",
            aiforscholar_token="",
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session
            self.semantic_scholar_api_key = semantic_scholar_api_key
            self.aiforscholar_token = aiforscholar_token

    class FakeContentClient:
        def __init__(self, session, *, alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.alphaxiv_token = alphaxiv_token

    class FakeNotionClient:
        def __init__(self, token, max_concurrent):
            self.token = token

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_data_source_id(self, database_id):
            return "data-source-1"

        async def ensure_sync_properties(self, data_source_id):
            return None

        async def query_pages(self, data_source_id):
            return [{"id": "page-1", "url": "https://notion.so/page-1", "properties": {}}]

    exit_code = await run_notion_mode(
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        semanticscholar_graph_client_cls=FakeSemanticScholarGraphClient,
        crossref_client_cls=FakeCrossrefClient,
        datacite_client_cls=FakeDataCiteClient,
        notion_client_cls=FakeNotionClient,
        content_client_cls=FakeContentClient,
    )

    assert exit_code == 0
    assert received["page"]["id"] == "page-1"
    assert received["content_cache"] is not None
    assert received["content_cache"].content_client.alphaxiv_token == "ax_token"
    assert received["semanticscholar_graph_client"] is not None
    assert received["semanticscholar_graph_client"].semantic_scholar_api_key == "ss_key"
    assert received["semanticscholar_graph_client"].aiforscholar_token == "relay_token"
    assert isinstance(received["crossref_client"], FakeCrossrefClient)
    assert isinstance(received["datacite_client"], FakeDataCiteClient)


@pytest.mark.anyio
async def test_run_notion_mode_limits_started_pages_to_notion_concurrent_limit(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "notion_xxx")
    monkeypatch.setenv("DATABASE_ID", "db_123")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_xxx")
    monkeypatch.setattr(notion_runner, "NOTION_CONCURRENT_LIMIT", 2)

    release = asyncio.Event()
    started: list[int] = []

    async def fake_process_page(page, index, total, **kwargs):
        started.append(index)
        await release.wait()

    monkeypatch.setattr(notion_runner, "process_page", fake_process_page)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", repo_cache=None, hf_exact_no_repo_recheck_days=0, max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeContentClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeNotionClient:
        def __init__(self, token, max_concurrent):
            self.token = token

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_data_source_id(self, database_id):
            return "data-source-1"

        async def ensure_sync_properties(self, data_source_id):
            return None

        async def query_pages(self, data_source_id):
            return [{"id": f"page-{index}", "url": f"https://notion.so/page-{index}", "properties": {}} for index in range(1, 6)]

    notion_task = asyncio.create_task(
        run_notion_mode(
            session_factory=lambda **kwargs: FakeSession(),
            arxiv_client_cls=FakeArxivClient,
            discovery_client_cls=FakeDiscoveryClient,
            github_client_cls=FakeGitHubClient,
            notion_client_cls=FakeNotionClient,
            content_client_cls=FakeContentClient,
        )
    )

    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == [1, 2]
    finally:
        release.set()

    assert await notion_task == 0
