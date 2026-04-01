from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.shared.arxiv_url_resolution import NO_MATCH_TITLE_SEARCH_ERROR, resolve_arxiv_url


class RecordingRelationResolutionCache:
    def __init__(self, entries=None):
        self.entries = dict(entries or {})
        self.get_calls: list[tuple[str, str]] = []
        self.record_calls: list[dict] = []

    def get(self, key_type: str, key_value: str):
        self.get_calls.append((key_type, key_value))
        return self.entries.get((key_type, key_value))

    def record_resolution(self, **kwargs):
        self.record_calls.append(kwargs)

    @staticmethod
    def is_negative_cache_fresh(checked_at: str | None, recheck_days: int) -> bool:
        if not checked_at:
            return False
        checked = datetime.fromisoformat(checked_at)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - checked).days < recheck_days


@pytest.mark.anyio
async def test_resolve_arxiv_url_preserves_existing_arxiv_value_without_cache_writes():
    cache = RecordingRelationResolutionCache()

    result = await resolve_arxiv_url(
        title="Existing Arxiv Paper",
        raw_url="https://arxiv.org/pdf/2603.12345v2.pdf",
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert result.resolved_url == "https://arxiv.org/pdf/2603.12345v2.pdf"
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2603.12345"
    assert result.script_derived is False
    assert cache.get_calls == []
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_resolve_arxiv_url_resolves_doi_via_semantic_scholar_and_records_positive_cache():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            return_value=(
                "https://arxiv.org/abs/2501.12345",
                "Mapped Arxiv Title",
                "semantic_scholar_exact_doi",
            )
        )
    )
    cache = RecordingRelationResolutionCache()

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1007/978-3-031-72933-1_9",
        semanticscholar_graph_client=semanticscholar_graph_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=False,
    )

    assert result.resolved_url == "https://arxiv.org/abs/2501.12345"
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.resolved_title == "Mapped Arxiv Title"
    assert result.source == "semantic_scholar_exact_doi"
    assert result.script_derived is True
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1007/978-3-031-72933-1_9",
        title="Published Paper",
        allow_title_fallback=False,
    )
    assert cache.record_calls == [
        {
            "key_type": "doi",
            "key_value": "https://doi.org/10.1007/978-3-031-72933-1_9",
            "arxiv_url": "https://arxiv.org/abs/2501.12345",
            "resolved_title": "Mapped Arxiv Title",
        }
    ]


@pytest.mark.anyio
async def test_resolve_arxiv_url_uses_source_url_cache_key_for_semantic_scholar_inputs():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            side_effect=lambda identifier, title=None, allow_title_fallback=False: (
                (
                    "https://arxiv.org/abs/2507.01125",
                    "Mapped Title",
                    "semantic_scholar_exact_source_url",
                )
                if identifier == "https://www.semanticscholar.org/paper/Foo/abc123"
                else (None, None, None)
            )
        )
    )
    cache = RecordingRelationResolutionCache()

    result = await resolve_arxiv_url(
        title="Foo",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        relation_resolution_cache=cache,
        extra_identifiers=["https://www.semanticscholar.org/paper/Foo/abc123"],
        allow_title_search=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2507.01125"
    assert result.source == "semantic_scholar_exact_source_url"
    assert ("source_url", "https://www.semanticscholar.org/paper/Foo/abc123") in cache.get_calls


@pytest.mark.anyio
async def test_resolve_arxiv_url_skips_semantic_scholar_when_fresh_negative_cache_exists():
    recent = datetime.now(timezone.utc).isoformat()
    cache = RecordingRelationResolutionCache(
        {
            ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9"): SimpleNamespace(
                key_type="doi",
                key_value="https://doi.org/10.1007/978-3-031-72933-1_9",
                arxiv_url=None,
                resolved_title=None,
                checked_at=recent,
            )
        }
    )
    semanticscholar_graph_client = SimpleNamespace(find_arxiv_match_by_identifier=AsyncMock())
    arxiv_client = SimpleNamespace(get_arxiv_id_by_title=AsyncMock())

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1007/978-3-031-72933-1_9",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=True,
        allow_huggingface_fallback=True,
    )

    assert result.resolved_url is None
    assert result.canonical_arxiv_url is None
    assert result.source == "relation_resolution_cache_negative"
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_not_awaited()
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_does_not_short_circuit_when_only_subset_of_keys_have_fresh_negative_cache():
    recent = datetime.now(timezone.utc).isoformat()
    cache = RecordingRelationResolutionCache(
        {
            ("doi", "https://doi.org/10.1145/example"): SimpleNamespace(
                key_type="doi",
                key_value="https://doi.org/10.1145/example",
                arxiv_url=None,
                resolved_title=None,
                checked_at=recent,
            )
        }
    )
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            side_effect=lambda identifier, title=None, allow_title_fallback=False: (
                (
                    "https://arxiv.org/abs/2501.12345",
                    "Mapped Arxiv Title",
                    "semantic_scholar_exact_source_url",
                )
                if identifier == "https://www.semanticscholar.org/paper/Foo/abc123"
                else (None, None, None)
            )
        )
    )

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=True,
        allow_huggingface_fallback=True,
        extra_identifiers=["https://www.semanticscholar.org/paper/Foo/abc123"],
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.source == "semantic_scholar_exact_source_url"
    assert ("source_url", "https://www.semanticscholar.org/paper/Foo/abc123") in cache.get_calls


@pytest.mark.anyio
async def test_resolve_arxiv_url_records_negative_after_stable_miss_without_hf_stage():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(return_value=(None, None, None))
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=(None, None, NO_MATCH_TITLE_SEARCH_ERROR)),
        get_arxiv_match_by_title_from_api=AsyncMock(
            return_value=(None, None, None, NO_MATCH_TITLE_SEARCH_ERROR)
        ),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(return_value=(None, "Published Paper")))
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(return_value=(None, "Published Paper")))
    cache = RecordingRelationResolutionCache()

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=True,
        allow_huggingface_fallback=False,
    )

    assert result.canonical_arxiv_url is None
    assert cache.record_calls == [
        {
            "key_type": "doi",
            "key_value": "https://doi.org/10.1145/example",
            "arxiv_url": None,
        }
    ]


@pytest.mark.anyio
async def test_resolve_arxiv_url_does_not_record_negative_when_any_attempted_stage_has_transient_failure():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            side_effect=RuntimeError("Semantic Scholar Graph API error (429)")
        )
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=(None, None, NO_MATCH_TITLE_SEARCH_ERROR)),
        get_arxiv_match_by_title_from_api=AsyncMock(
            return_value=(None, None, None, NO_MATCH_TITLE_SEARCH_ERROR)
        ),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(return_value=(None, "Published Paper")))
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(return_value=(None, "Published Paper")))
    cache = RecordingRelationResolutionCache()

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=True,
        allow_huggingface_fallback=False,
    )

    assert result.canonical_arxiv_url is None
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_resolve_arxiv_url_uses_semantic_scholar_exact_before_all_fallbacks():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped", "semantic_scholar_exact_doi")
        )
    )
    arxiv_client = SimpleNamespace(get_arxiv_id_by_title=AsyncMock())
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        allow_title_search=True,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()
    crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_strips_html_markup_before_semantic_scholar_lookup():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            side_effect=lambda identifier, title=None, allow_title_fallback=False: (
                (
                    "https://arxiv.org/abs/2507.01125",
                    "VISTA: Open-Vocabulary, Task-Relevant Robot Exploration with Online Semantic Gaussian Splatting",
                    "semantic_scholar_exact_doi",
                )
                if identifier == "https://doi.org/10.1109/lra.2026.3653276"
                and title
                == "VISTA : Open-Vocabulary, Task-Relevant Robot Exploration With Online Semantic Gaussian Splatting"
                else (None, None, None)
            )
        )
    )
    arxiv_client = SimpleNamespace(get_arxiv_id_by_title=AsyncMock())

    result = await resolve_arxiv_url(
        title="<b>VISTA</b> : Open-Vocabulary, Task-Relevant Robot Exploration With Online Semantic Gaussian Splatting",
        raw_url="https://doi.org/10.1109/lra.2026.3653276",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        allow_title_search=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2507.01125"
    assert result.source == "semantic_scholar_exact_doi"
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1109/lra.2026.3653276",
        title="VISTA : Open-Vocabulary, Task-Relevant Robot Exploration With Online Semantic Gaussian Splatting",
        allow_title_fallback=False,
    )
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_accepts_semantic_scholar_title_exact_before_arxiv_title_search():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(return_value=(None, None, None)),
        find_arxiv_match_by_title=AsyncMock(
            return_value=(
                "https://arxiv.org/abs/2507.01125",
                "Mapped By Semantic Scholar Title",
                "semantic_scholar_title_exact",
            )
        )
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(),
        get_arxiv_match_by_title_from_api=AsyncMock(),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        allow_title_search=True,
        allow_huggingface_fallback=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2507.01125"
    assert result.source == "semantic_scholar_title_exact"
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1145/example",
        title="Published Paper",
        allow_title_fallback=False,
    )
    semanticscholar_graph_client.find_arxiv_match_by_title.assert_awaited_once_with("Published Paper")
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()
    arxiv_client.get_arxiv_match_by_title_from_api.assert_not_awaited()
    crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_checks_all_semantic_scholar_exact_identifiers_before_title_fallback():
    call_order: list[tuple[str, str, bool | None]] = []

    async def semantic_exact(identifier: str, title=None, allow_title_fallback=True):
        call_order.append(("exact", identifier, allow_title_fallback))
        if identifier == "https://www.semanticscholar.org/paper/Foo/abc123" and allow_title_fallback:
            return (
                "https://arxiv.org/abs/2999.99999",
                "Premature Title Fallback",
                "semantic_scholar_title_exact",
            )
        if identifier == "https://doi.org/10.1145/example":
            return (
                "https://arxiv.org/abs/2501.12345",
                "Exact DOI Match",
                "semantic_scholar_exact_doi",
            )
        return None, None, None

    async def semantic_title(title: str):
        call_order.append(("title", title, None))
        return None, None, None

    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(side_effect=semantic_exact),
        find_arxiv_match_by_title=AsyncMock(side_effect=semantic_title),
    )
    arxiv_client = SimpleNamespace(get_arxiv_id_by_title=AsyncMock())

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        extra_identifiers=["https://www.semanticscholar.org/paper/Foo/abc123"],
        allow_title_search=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.resolved_title == "Exact DOI Match"
    assert result.source == "semantic_scholar_exact_doi"
    assert call_order == [
        ("exact", "https://www.semanticscholar.org/paper/Foo/abc123", False),
        ("exact", "https://doi.org/10.1145/example", False),
    ]
    semanticscholar_graph_client.find_arxiv_match_by_title.assert_not_awaited()
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_prefers_html_title_search_over_api_title_search():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(return_value=(None, None, None))
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=("2501.54321", "title_search_exact", None)),
        get_arxiv_match_by_title_from_api=AsyncMock(
            return_value=("2999.99999", "Wrong API Match", "title_search_exact", None)
        ),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        allow_title_search=True,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.54321"
    arxiv_client.get_arxiv_id_by_title.assert_awaited_once_with("Published Paper")
    arxiv_client.get_arxiv_match_by_title_from_api.assert_not_awaited()
    crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_falls_back_to_api_title_search_after_html_miss():
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(return_value=(None, None, None))
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=(None, None, NO_MATCH_TITLE_SEARCH_ERROR)),
        get_arxiv_match_by_title_from_api=AsyncMock(
            return_value=(
                "2508.18242",
                "GSVisLoc: Generalizable Visual Localization for Gaussian Splatting Scene Representations",
                "title_search_contained",
                None,
            )
        ),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    result = await resolve_arxiv_url(
        title="Generalizable Visual Localization for Gaussian Splatting Scene Representations",
        raw_url="https://doi.org/10.1109/iccvw69036.2025.00025",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        allow_title_search=True,
        allow_huggingface_fallback=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2508.18242"
    assert (
        result.resolved_title
        == "GSVisLoc: Generalizable Visual Localization for Gaussian Splatting Scene Representations"
    )
    assert result.source == "title_search"
    arxiv_client.get_arxiv_id_by_title.assert_awaited_once_with(
        "Generalizable Visual Localization for Gaussian Splatting Scene Representations"
    )
    arxiv_client.get_arxiv_match_by_title_from_api.assert_awaited_once_with(
        "Generalizable Visual Localization for Gaussian Splatting Scene Representations"
    )
    crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_runs_crossref_then_datacite_after_semantic_scholar_and_title_search_fail():
    call_order: list[str] = []

    async def semantic_exact(identifier: str, title=None, allow_title_fallback=False):
        call_order.append("semantic_exact")
        return None, None, None

    async def html_title_search(title: str):
        call_order.append("arxiv_html")
        return None, None, NO_MATCH_TITLE_SEARCH_ERROR

    async def api_title_search(title: str):
        call_order.append("arxiv_api")
        return None, None, None, NO_MATCH_TITLE_SEARCH_ERROR

    async def crossref_lookup(doi_url: str):
        call_order.append("crossref")
        return None, "Published Paper"

    async def datacite_lookup(doi_url: str):
        call_order.append("datacite")
        return "https://arxiv.org/abs/2501.12345", "Published Paper"

    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(side_effect=semantic_exact)
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(side_effect=html_title_search),
        get_arxiv_match_by_title_from_api=AsyncMock(side_effect=api_title_search),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(side_effect=crossref_lookup))
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(side_effect=datacite_lookup))
    discovery_client = SimpleNamespace(
        huggingface_token="hf-token",
        get_huggingface_paper_search_results=AsyncMock(),
        get_huggingface_search_html=AsyncMock(),
    )

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        allow_title_search=True,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert call_order == [
        "semantic_exact",
        "arxiv_html",
        "arxiv_api",
        "crossref",
        "datacite",
    ]
    crossref_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    datacite_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    discovery_client.get_huggingface_paper_search_results.assert_not_awaited()
    discovery_client.get_huggingface_search_html.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_can_use_optional_huggingface_fallback_after_metadata_misses():
    call_order: list[str] = []

    async def semantic_exact(identifier: str, title=None, allow_title_fallback=False):
        call_order.append("semantic_exact")
        return None, None, None

    async def html_title_search(title: str):
        call_order.append("arxiv_html")
        return None, None, NO_MATCH_TITLE_SEARCH_ERROR

    async def api_title_search(title: str):
        call_order.append("arxiv_api")
        return None, None, None, NO_MATCH_TITLE_SEARCH_ERROR

    async def crossref_lookup(doi_url: str):
        call_order.append("crossref")
        return None, "Published Paper"

    async def datacite_lookup(doi_url: str):
        call_order.append("datacite")
        return None, "Published Paper"

    async def hf_lookup(title: str, *, limit: int = 1):
        call_order.append("huggingface")
        return (
            [
                {
                    "paper": {
                        "id": "2501.12345",
                        "title": "Published Paper",
                    }
                }
            ],
            None,
        )

    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(side_effect=semantic_exact)
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(side_effect=html_title_search),
        get_arxiv_match_by_title_from_api=AsyncMock(side_effect=api_title_search),
        get_title=AsyncMock(return_value=("Mapped Arxiv Title", None)),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(side_effect=crossref_lookup))
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(side_effect=datacite_lookup))
    discovery_client = SimpleNamespace(
        huggingface_token="hf-token",
        get_huggingface_paper_search_results=AsyncMock(side_effect=hf_lookup),
    )

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_graph_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        allow_title_search=True,
        allow_huggingface_fallback=True,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.resolved_title == "Mapped Arxiv Title"
    assert call_order == [
        "semantic_exact",
        "arxiv_html",
        "arxiv_api",
        "crossref",
        "datacite",
        "huggingface",
    ]
    crossref_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    datacite_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    discovery_client.get_huggingface_paper_search_results.assert_awaited_once_with(
        "Published Paper",
        limit=1,
    )
