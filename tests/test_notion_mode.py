import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from src.notion_sync.config import load_config_from_env
from src.notion_sync.notion_client import NotionClient
from src.notion_sync.pipeline import (
    classify_github_value,
    get_current_stars_from_page,
    get_github_url_from_page,
    get_page_title,
    process_page,
    resolve_repo_for_page,
)


def test_load_config_requires_notion_token_and_database_id():
    with pytest.raises(ValueError):
        load_config_from_env({})


def test_load_config_accepts_env_values():
    cfg = load_config_from_env(
        {
            "NOTION_TOKEN": "notion_xxx",
            "GITHUB_TOKEN": "ghp_xxx",
            "DATABASE_ID": "db_123",
            "ALPHAXIV_TOKEN": "axv1_test",
            "HUGGINGFACE_TOKEN": "hf_test",
            "HF_EXACT_NO_REPO_RECHECK_DAYS": "7",
        }
    )

    assert cfg == {
        "notion_token": "notion_xxx",
        "github_token": "ghp_xxx",
        "database_id": "db_123",
        "alphaxiv_token": "axv1_test",
        "huggingface_token": "hf_test",
        "hf_exact_no_repo_recheck_days": 7,
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


def test_classify_github_value_covers_expected_states():
    assert classify_github_value(None) == "empty"
    assert classify_github_value("   ") == "empty"
    assert classify_github_value("WIP") == "other"
    assert classify_github_value("https://github.com/foo/bar") == "valid_github"
    assert classify_github_value("https://example.com/project") == "other"


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
async def test_update_page_properties_retries_after_error():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        pages=types.SimpleNamespace(update=AsyncMock(side_effect=[Exception("boom"), {"ok": True}]))
    )

    await client.update_page_properties("page-1", stars_count=42)

    assert client.client.pages.update.await_count == 2


@pytest.mark.anyio
async def test_resolve_repo_for_page_prefers_existing_valid_github():
    page = {
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
        }
    }
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())

    resolution = await resolve_repo_for_page(page, discovery_client)

    assert resolution == {
        "github_url": "https://github.com/foo/bar",
        "source": "existing",
        "needs_github_update": False,
        "reason": None,
    }
    discovery_client.resolve_github_url.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_repo_for_page_uses_discovery_for_empty_github():
    page = {
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test Paper"}]},
            "Github": {"type": "rich_text", "rich_text": []},
            "URL": {"type": "url", "url": "https://arxiv.org/abs/2603.05078"},
        }
    }
    discovery_client = types.SimpleNamespace(
        resolve_github_url=AsyncMock(return_value="https://github.com/hf/repo")
    )

    resolution = await resolve_repo_for_page(page, discovery_client)

    assert resolution == {
        "github_url": "https://github.com/hf/repo",
        "source": "discovered",
        "arxiv_source": "url_field",
        "needs_github_update": True,
        "reason": None,
    }


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
    )

    captured = capsys.readouterr()
    assert "[1/1] Test Paper" in captured.out
    assert "foo/bar" in captured.out
    assert "Updated: 10 → 12" in captured.out
