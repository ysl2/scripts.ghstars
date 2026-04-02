from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.record_model import PropertyStatus, Record
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
