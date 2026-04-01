import types
from unittest.mock import AsyncMock

import pytest

from src.shared.paper_enrichment import PaperEnrichmentRequest, process_single_paper


def test_paper_enrichment_module_no_longer_exposes_compatibility_shim():
    import src.shared.paper_enrichment as paper_enrichment

    assert "EnrichedPaper" not in vars(paper_enrichment)
    assert "enrich_paper" not in vars(paper_enrichment)


class RecordingContentCache:
    def __init__(self):
        self.calls: list[str] = []

    async def ensure_local_content_cache(self, canonical_arxiv_url: str) -> None:
        self.calls.append(canonical_arxiv_url)


@pytest.mark.anyio
async def test_process_single_paper_prefers_existing_valid_github_and_warms_content():
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(17, None)))
    content_cache = RecordingContentCache()

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper A",
            raw_url="https://arxiv.org/pdf/2603.20000v2.pdf",
            existing_github_url="https://github.com/foo/bar",
            allow_title_search=False,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=content_cache,
    )

    assert result.title == "Paper A"
    assert result.raw_url == "https://arxiv.org/pdf/2603.20000v2.pdf"
    assert result.normalized_url == "https://arxiv.org/pdf/2603.20000v2.pdf"
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2603.20000"
    assert result.github_url == "https://github.com/foo/bar"
    assert result.github_source == "existing"
    assert result.stars == 17
    assert result.reason is None
    assert content_cache.calls == ["https://arxiv.org/abs/2603.20000"]
    discovery_client.resolve_github_url.assert_not_awaited()
    github_client.get_star_count.assert_awaited_once_with("foo", "bar")


@pytest.mark.anyio
async def test_process_single_paper_discovers_github_when_allowed():
    discovery_client = types.SimpleNamespace(
        resolve_github_url=AsyncMock(return_value="https://github.com/foo/discovered")
    )
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(42, None)))

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper B",
            raw_url="https://arxiv.org/abs/2603.10000v3",
            existing_github_url="",
            allow_title_search=False,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
    )

    assert result.normalized_url == "https://arxiv.org/abs/2603.10000v3"
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2603.10000"
    assert result.github_url == "https://github.com/foo/discovered"
    assert result.github_source == "discovered"
    assert result.stars == 42
    assert result.reason is None
    discovery_client.resolve_github_url.assert_awaited_once()


@pytest.mark.anyio
async def test_process_single_paper_rejects_invalid_existing_github_without_discovery():
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(get_star_count=AsyncMock())

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper C",
            raw_url="https://arxiv.org/abs/2603.10001",
            existing_github_url="https://example.com/not-github",
            allow_title_search=False,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
    )

    assert result.github_source == "existing"
    assert result.reason == "Existing Github URL is not a valid GitHub repository"
    discovery_client.resolve_github_url.assert_not_awaited()
    github_client.get_star_count.assert_not_awaited()


@pytest.mark.anyio
async def test_process_single_paper_rejects_invalid_discovered_github():
    discovery_client = types.SimpleNamespace(
        resolve_github_url=AsyncMock(return_value="https://example.com/not-github")
    )
    github_client = types.SimpleNamespace(get_star_count=AsyncMock())

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper D",
            raw_url="https://arxiv.org/abs/2603.10002",
            existing_github_url="",
            allow_title_search=False,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
    )

    assert result.github_source == "discovered"
    assert result.github_url == "https://example.com/not-github"
    assert result.reason == "Discovered URL is not a valid GitHub repository"
    github_client.get_star_count.assert_not_awaited()


@pytest.mark.anyio
async def test_process_single_paper_reports_discovery_miss():
    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock(return_value=None))
    github_client = types.SimpleNamespace(get_star_count=AsyncMock())
    content_cache = RecordingContentCache()

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper E",
            raw_url="https://arxiv.org/abs/2603.10003v1",
            existing_github_url="",
            allow_title_search=False,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=content_cache,
    )

    assert result.normalized_url == "https://arxiv.org/abs/2603.10003v1"
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2603.10003"
    assert result.github_url is None
    assert result.reason == "No Github URL found from discovery"
    assert content_cache.calls == []
    github_client.get_star_count.assert_not_awaited()


@pytest.mark.anyio
async def test_process_single_paper_skips_title_search_when_request_flag_is_false():
    discovery_client = types.SimpleNamespace(
        huggingface_token="",
        resolve_github_url=AsyncMock(return_value="https://github.com/foo/from-title"),
    )
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(7, None)))
    arxiv_client = types.SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=("2603.10004", "title_search_arxiv", None))
    )
    semanticscholar_graph_client = types.SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(return_value=(None, None, None)),
        find_arxiv_match_by_title=AsyncMock(
            return_value=("https://arxiv.org/abs/2603.10004", "Mapped Title", "semantic_scholar_title_exact")
        ),
    )

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper F",
            raw_url="https://example.com/no-arxiv",
            existing_github_url="",
            allow_title_search=False,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
    )

    assert result.normalized_url is None
    assert result.canonical_arxiv_url is None
    assert result.github_url is None
    assert result.reason == "No valid arXiv URL found"
    discovery_client.resolve_github_url.assert_not_awaited()
    github_client.get_star_count.assert_not_awaited()
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://example.com/no-arxiv",
        title="Paper F",
        allow_title_fallback=False,
    )
    semanticscholar_graph_client.find_arxiv_match_by_title.assert_not_awaited()


@pytest.mark.anyio
async def test_process_single_paper_warms_content_before_github_stars_and_keeps_warming_on_star_failure():
    events: list[str] = []

    class OrderedContentCache:
        async def ensure_local_content_cache(self, canonical_arxiv_url: str) -> None:
            assert canonical_arxiv_url == "https://arxiv.org/abs/2603.10005"
            events.append("content")

    async def get_star_count(owner: str, repo: str):
        assert (owner, repo) == ("foo", "bar")
        events.append("stars")
        return None, "GitHub API error (503)"

    discovery_client = types.SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(side_effect=get_star_count))

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper G",
            raw_url="https://arxiv.org/abs/2603.10005",
            existing_github_url="https://github.com/foo/bar",
            allow_title_search=False,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=OrderedContentCache(),
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.stars is None
    assert result.reason == "GitHub API error (503)"
    assert events == ["content", "stars"]


@pytest.mark.anyio
async def test_process_single_paper_skips_content_warming_without_valid_repo():
    content_cache = RecordingContentCache()

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper H",
            raw_url="https://arxiv.org/abs/2603.10006",
            existing_github_url="https://example.com/not-github",
            allow_title_search=False,
            allow_github_discovery=False,
        ),
        discovery_client=types.SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=types.SimpleNamespace(get_star_count=AsyncMock()),
        content_cache=content_cache,
    )

    assert result.reason == "Existing Github URL is not a valid GitHub repository"
    assert content_cache.calls == []


@pytest.mark.anyio
async def test_process_single_paper_keeps_repo_and_stars_when_no_canonical_arxiv_identity_exists():
    content_cache = RecordingContentCache()
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(9, None)))

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Paper I",
            raw_url="https://example.com/paper",
            existing_github_url="https://github.com/foo/bar",
            allow_title_search=False,
            allow_github_discovery=False,
        ),
        discovery_client=types.SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=github_client,
        content_cache=content_cache,
    )

    assert result.raw_url == "https://example.com/paper"
    assert result.normalized_url is None
    assert result.github_url == "https://github.com/foo/bar"
    assert result.github_source == "existing"
    assert result.stars == 9
    assert result.reason is None
    assert content_cache.calls == []
    github_client.get_star_count.assert_awaited_once_with("foo", "bar")


@pytest.mark.anyio
async def test_process_single_paper_resolves_doi_via_semantic_scholar_before_github_discovery():
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(5, None)))
    content_cache = RecordingContentCache()

    class FakeDiscoveryClient:
        def __init__(self):
            self.seen_urls: list[str] = []

        async def resolve_github_url(self, seed):
            self.seen_urls.append(seed.url)
            return "https://github.com/foo/from-doi"

    discovery_client = FakeDiscoveryClient()
    semanticscholar_graph_client = types.SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title", "semantic_scholar_exact_doi")
        )
    )

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Published DOI Paper",
            raw_url="https://doi.org/10.1007/978-3-031-72933-1_9",
            existing_github_url="",
            allow_title_search=False,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        content_cache=content_cache,
    )

    assert result.normalized_url == "https://arxiv.org/abs/2501.12345"
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.github_url == "https://github.com/foo/from-doi"
    assert result.reason is None
    assert discovery_client.seen_urls == ["https://arxiv.org/abs/2501.12345"]
    assert content_cache.calls == ["https://arxiv.org/abs/2501.12345"]
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1007/978-3-031-72933-1_9",
        title="Published DOI Paper",
        allow_title_fallback=False,
    )


@pytest.mark.anyio
async def test_process_single_paper_threads_crossref_after_semantic_scholar_and_title_misses():
    github_client = types.SimpleNamespace(get_star_count=AsyncMock(return_value=(5, None)))
    content_cache = RecordingContentCache()

    class FakeDiscoveryClient:
        def __init__(self):
            self.seen_urls: list[str] = []

        async def resolve_github_url(self, seed):
            self.seen_urls.append(seed.url)
            return "https://github.com/foo/from-crossref"

    discovery_client = FakeDiscoveryClient()
    semanticscholar_graph_client = types.SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(return_value=(None, None, None)),
        find_arxiv_match_by_title=AsyncMock(return_value=(None, None, None)),
    )
    arxiv_client = types.SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=(None, None, "No arXiv ID found from title search"))
    )
    crossref_client = types.SimpleNamespace(
        find_arxiv_match_by_doi=AsyncMock(return_value=("https://arxiv.org/abs/2501.54321", "Published DOI Paper"))
    )
    datacite_client = types.SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    result = await process_single_paper(
        PaperEnrichmentRequest(
            title="Published DOI Paper",
            raw_url="https://doi.org/10.1145/example",
            existing_github_url="",
            allow_title_search=True,
            allow_github_discovery=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
    )

    assert result.normalized_url == "https://arxiv.org/abs/2501.54321"
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.54321"
    assert result.github_url == "https://github.com/foo/from-crossref"
    assert result.reason is None
    assert discovery_client.seen_urls == ["https://arxiv.org/abs/2501.54321"]
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1145/example",
        title="Published DOI Paper",
        allow_title_fallback=False,
    )
    semanticscholar_graph_client.find_arxiv_match_by_title.assert_awaited_once_with("Published DOI Paper")
    crossref_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()
