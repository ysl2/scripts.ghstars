from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.record_model import PropertyStatus, Record, RecordFacts
from src.core.record_sync import RecordSyncService


@pytest.mark.anyio
async def test_record_sync_discovers_github_and_repo_metadata_for_paper_like_record():
    service = RecordSyncService(
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock(return_value="https://github.com/foo/bar")),
        github_client=SimpleNamespace(
            get_repo_metadata=AsyncMock(
                return_value=(
                    SimpleNamespace(
                        stars=12,
                        created="2020-01-01T00:00:00Z",
                        about="repo",
                    ),
                    None,
                )
            )
        ),
    )
    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345",
        source="url_to_csv",
    )

    updated = await service.sync(record, allow_title_search=True, allow_github_discovery=True)

    assert updated.github.value == "https://github.com/foo/bar"
    assert updated.stars.value == 12
    assert updated.created.value == "2020-01-01T00:00:00Z"
    assert updated.about.value == "repo"


@pytest.mark.anyio
async def test_record_sync_skips_repo_metadata_fetch_for_trusted_github_search_record():
    github_client = SimpleNamespace(get_repo_metadata=AsyncMock())
    service = RecordSyncService(
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=github_client,
    )
    record = Record.from_source(
        github="https://github.com/foo/bar",
        stars=99,
        created="2020-01-01T00:00:00Z",
        about="repo",
        source="github_search",
        trusted_fields={"github", "stars", "created", "about"},
    )

    updated = await service.sync(record, allow_title_search=False, allow_github_discovery=False)

    assert updated.github.trusted is True
    assert updated.stars.status is PropertyStatus.PRESENT
    github_client.get_repo_metadata.assert_not_awaited()


@pytest.mark.anyio
async def test_record_sync_preserves_existing_facts_when_trusted_existing_github_skips_url_resolution():
    service = RecordSyncService(
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=SimpleNamespace(get_star_count=AsyncMock(return_value=(42, None))),
    )
    record = Record.from_source(
        name="Paper A",
        url="https://doi.org/10.1000/example",
        github="https://github.com/foo/bar",
        source="csv",
    ).with_supporting_state(
        facts=RecordFacts(
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            normalized_url="https://arxiv.org/abs/2501.12345v2",
        )
    )

    updated = await service.sync(
        record,
        allow_title_search=True,
        allow_github_discovery=True,
        trust_existing_github=True,
    )

    assert updated.facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert updated.facts.normalized_url == "https://arxiv.org/abs/2501.12345v2"
    assert updated.facts.github_source == "existing"


@pytest.mark.anyio
async def test_record_sync_honors_trusted_existing_github_without_explicit_flag(monkeypatch):
    async def fail_resolve_arxiv_url(*args, **kwargs):
        raise AssertionError(
            "URL resolution should not run for a trusted existing Github value"
        )

    monkeypatch.setattr(
        "src.core.record_sync.resolve_arxiv_url",
        fail_resolve_arxiv_url,
    )

    discovery_client = SimpleNamespace(resolve_github_url=AsyncMock())
    service = RecordSyncService(
        discovery_client=discovery_client,
        github_client=SimpleNamespace(get_star_count=AsyncMock(return_value=(42, None))),
    )
    record = Record.from_source(
        name="Paper A",
        url="https://doi.org/10.1000/example",
        github="https://github.com/foo/bar",
        source="csv",
        trusted_fields={"github"},
    ).with_supporting_state(
        facts=RecordFacts(
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            normalized_url="https://arxiv.org/abs/2501.12345v2",
        )
    )

    updated = await service.sync(
        record,
        allow_title_search=True,
        allow_github_discovery=True,
    )

    assert updated.facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert updated.facts.normalized_url == "https://arxiv.org/abs/2501.12345v2"
    assert updated.facts.github_source == "existing"
    discovery_client.resolve_github_url.assert_not_awaited()


@pytest.mark.anyio
async def test_record_sync_surfaces_repo_metadata_failure_for_fully_populated_record():
    service = RecordSyncService(
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=SimpleNamespace(get_repo_metadata=AsyncMock(return_value=(None, "boom"))),
    )
    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345",
        github="https://github.com/foo/bar",
        stars=99,
        created="2020-01-01T00:00:00Z",
        about="repo",
        source="csv",
    )

    updated = await service.sync(
        record,
        allow_title_search=False,
        allow_github_discovery=False,
    )

    assert updated.github.value == "https://github.com/foo/bar"
    assert updated.stars.value == 99
    assert updated.created.value == "2020-01-01T00:00:00Z"
    assert updated.about.value == "repo"
    assert getattr(updated.facts, "repo_metadata_error", None) == "boom"
