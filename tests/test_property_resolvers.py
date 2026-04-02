from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


@pytest.mark.anyio
async def test_resolve_repo_metadata_properties_returns_metadata_from_get_repo_metadata():
    github_client = SimpleNamespace(
        get_repo_metadata=AsyncMock(
            return_value=(
                SimpleNamespace(stars=99, created="2021-02-03T00:00:00Z", about="a repo"),
                None,
            )
        )
    )

    result = await resolve_repo_metadata_properties(
        github_url="https://github.com/foo/bar",
        github_client=github_client,
        repo_metadata_cache=None,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.stars == 99
    assert result.created == "2021-02-03T00:00:00Z"
    assert result.about == "a repo"
    assert result.reason is None
    github_client.get_repo_metadata.assert_awaited_once_with("foo", "bar")


@pytest.mark.anyio
async def test_resolve_repo_metadata_properties_treats_missing_metadata_without_error_as_failure():
    github_client = SimpleNamespace(get_repo_metadata=AsyncMock(return_value=(None, None)))

    result = await resolve_repo_metadata_properties(
        github_url="https://github.com/foo/bar",
        github_client=github_client,
        repo_metadata_cache=None,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.stars is None
    assert result.created is None
    assert result.about is None
    assert result.reason == "GitHub client returned no repo metadata"


@pytest.mark.anyio
async def test_resolve_repo_metadata_properties_falls_back_to_cached_created_when_metadata_lacks_it():
    github_client = SimpleNamespace(
        get_repo_metadata=AsyncMock(
            return_value=(
                SimpleNamespace(stars=13, created=None, about="description"),
                None,
            )
        )
    )
    repo_metadata_cache = SimpleNamespace(
        get=Mock(return_value=SimpleNamespace(created="2019-01-01T00:00:00Z"))
    )

    result = await resolve_repo_metadata_properties(
        github_url="https://github.com/foo/bar",
        github_client=github_client,
        repo_metadata_cache=repo_metadata_cache,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.stars == 13
    assert result.created == "2019-01-01T00:00:00Z"
    assert result.about == "description"
    assert result.reason is None
    repo_metadata_cache.get.assert_called_once_with("https://github.com/foo/bar")


@pytest.mark.anyio
async def test_resolve_repo_metadata_properties_treats_empty_error_as_error_in_star_fallback():
    github_client = SimpleNamespace(get_star_count=AsyncMock(return_value=(42, "")))

    result = await resolve_repo_metadata_properties(
        github_url="https://github.com/foo/bar",
        github_client=github_client,
        repo_metadata_cache=None,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.stars is None
    assert result.created is None
    assert result.about is None
    assert result.reason == ""


@pytest.mark.anyio
async def test_acquire_github_property_trust_existing_skips_url_resolution_and_discovery():
    discovery_client = SimpleNamespace(resolve_github_url=AsyncMock())
    arxiv_client = SimpleNamespace(get_arxiv_id_by_title=AsyncMock())
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(),
        find_arxiv_match_by_title=AsyncMock(),
    )

    result = await acquire_github_property(
        existing_github_url="https://github.com/foo/bar",
        raw_url="https://doi.org/10.1145/example",
        name="Paper A",
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=None,
        datacite_client=None,
        relation_resolution_cache=None,
        allow_title_search=True,
        allow_github_discovery=True,
        trust_existing_github=True,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.github_source == "existing"
    assert result.normalized_url is None
    assert result.canonical_arxiv_url is None
    assert result.reason is None
    discovery_client.resolve_github_url.assert_not_awaited()
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_not_awaited()
    semanticscholar_graph_client.find_arxiv_match_by_title.assert_not_awaited()
