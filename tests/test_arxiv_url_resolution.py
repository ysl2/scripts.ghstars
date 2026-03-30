from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.shared.arxiv_url_resolution import resolve_arxiv_url


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
async def test_resolve_arxiv_url_resolves_doi_via_openalex_and_records_positive_cache():
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title")
        )
    )
    cache = RecordingRelationResolutionCache()

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1007/978-3-031-72933-1_9",
        openalex_client=openalex_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=False,
    )

    assert result.resolved_url == "https://arxiv.org/abs/2501.12345"
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.resolved_title == "Mapped Arxiv Title"
    assert result.script_derived is True
    openalex_client.find_exact_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1007/978-3-031-72933-1_9",
        title="Published Paper",
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
async def test_resolve_arxiv_url_skips_openalex_when_fresh_negative_cache_exists():
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
    openalex_client = SimpleNamespace(find_exact_arxiv_match_by_identifier=AsyncMock())
    arxiv_client = SimpleNamespace(get_arxiv_id_by_title=AsyncMock())

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1007/978-3-031-72933-1_9",
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=False,
    )

    assert result.resolved_url is None
    assert result.canonical_arxiv_url is None
    openalex_client.find_exact_arxiv_match_by_identifier.assert_not_awaited()
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_uses_openalex_exact_before_all_fallbacks():
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped")
        )
    )
    arxiv_client = SimpleNamespace(get_arxiv_id_by_title=AsyncMock())
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        openalex_client=openalex_client,
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
async def test_resolve_arxiv_url_prefers_html_title_search_over_api_title_search():
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(return_value=(None, "Published Paper"))
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
        openalex_client=openalex_client,
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
async def test_resolve_arxiv_url_runs_crossref_then_datacite_after_html_title_search_fails_without_hf():
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(return_value=(None, "Published Paper"))
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=(None, None, "No arXiv ID found from title search"))
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(return_value=(None, "Published Paper")))
    datacite_client = SimpleNamespace(
        find_arxiv_match_by_doi=AsyncMock(return_value=("https://arxiv.org/abs/2501.12345", "Published Paper"))
    )
    discovery_client = SimpleNamespace(
        huggingface_token="hf-token",
        get_huggingface_paper_search_results=AsyncMock(),
        get_huggingface_search_html=AsyncMock(),
    )

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        allow_title_search=True,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    crossref_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    datacite_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    discovery_client.get_huggingface_paper_search_results.assert_not_awaited()
    discovery_client.get_huggingface_search_html.assert_not_awaited()
