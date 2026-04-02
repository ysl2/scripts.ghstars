from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.shared.property_resolvers import acquire_github_property, resolve_repo_metadata_properties


@pytest.mark.anyio
async def test_acquire_github_property_uses_existing_then_url_then_name():
    result = await acquire_github_property(
        existing_github_url="https://github.com/foo/bar",
        raw_url="https://doi.org/10.1145/example",
        name="Paper A",
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        arxiv_client=None,
        semanticscholar_graph_client=None,
        crossref_client=None,
        datacite_client=None,
        relation_resolution_cache=None,
        allow_title_search=True,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.github_source == "existing"


@pytest.mark.anyio
async def test_resolve_repo_metadata_properties_returns_partial_failure_without_erasing_github():
    github_client = SimpleNamespace(get_repo_metadata=AsyncMock(return_value=(None, "GitHub API error (500)")))

    result = await resolve_repo_metadata_properties(
        github_url="https://github.com/foo/bar",
        github_client=github_client,
        repo_metadata_cache=None,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.reason == "GitHub API error (500)"
    assert result.stars is None
    assert result.created is None
    assert result.about is None
