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
        allow_title_search=True,
        allow_openalex_preprint_crosswalk=True,
        allow_huggingface_fallback=True,
    )

    assert result.resolved_url is None
    assert result.canonical_arxiv_url is None
    openalex_client.find_exact_arxiv_match_by_identifier.assert_not_awaited()
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
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(
            side_effect=lambda identifier, title=None: (
                ("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title")
                if identifier == "https://openalex.org/W123"
                else (None, None)
            )
        )
    )

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        openalex_client=openalex_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=True,
        allow_openalex_preprint_crosswalk=True,
        allow_huggingface_fallback=True,
        extra_identifiers=["https://openalex.org/W123"],
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert openalex_client.find_exact_arxiv_match_by_identifier.await_args_list == [
        (( "https://openalex.org/W123",), {"title": "Published Paper"}),
    ]


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
async def test_resolve_arxiv_url_strips_html_markup_before_openalex_preprint_lookup():
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(return_value=(None, None)),
        find_preprint_match_by_identifier=AsyncMock(
            side_effect=lambda identifier, title=None: (
                ("https://arxiv.org/abs/2507.01125", "VISTA: Open-Vocabulary, Task-Relevant Robot Exploration with Online Semantic Gaussian Splatting")
                if identifier == "https://doi.org/10.1109/lra.2026.3653276"
                and title == "VISTA : Open-Vocabulary, Task-Relevant Robot Exploration With Online Semantic Gaussian Splatting"
                else (None, None)
            )
        ),
    )
    arxiv_client = SimpleNamespace(get_arxiv_id_by_title=AsyncMock())

    result = await resolve_arxiv_url(
        title="<b>VISTA</b> : Open-Vocabulary, Task-Relevant Robot Exploration With Online Semantic Gaussian Splatting",
        raw_url="https://doi.org/10.1109/lra.2026.3653276",
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        allow_title_search=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2507.01125"
    assert result.source == "openalex_preprint_doi"
    openalex_client.find_exact_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1109/lra.2026.3653276",
        title="VISTA : Open-Vocabulary, Task-Relevant Robot Exploration With Online Semantic Gaussian Splatting",
    )
    openalex_client.find_preprint_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1109/lra.2026.3653276",
        title="VISTA : Open-Vocabulary, Task-Relevant Robot Exploration With Online Semantic Gaussian Splatting",
    )
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_runs_title_search_before_openalex_preprint():
    call_order: list[str] = []

    async def openalex_exact(identifier: str, title=None):
        call_order.append("openalex_exact")
        return None, "Published Paper"

    async def html_title_search(title: str):
        call_order.append("arxiv_html")
        return None, None, "No arXiv ID found from title search"

    async def api_title_search(title: str):
        call_order.append("arxiv_api")
        return None, None, None, "No arXiv ID found from title search"

    async def openalex_preprint(identifier: str, title=None):
        call_order.append("openalex_preprint")
        return "https://arxiv.org/abs/2507.01125", "Mapped By OpenAlex Preprint"

    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(side_effect=openalex_exact),
        find_preprint_match_by_identifier=AsyncMock(side_effect=openalex_preprint),
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(side_effect=html_title_search),
        get_arxiv_match_by_title_from_api=AsyncMock(side_effect=api_title_search),
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
        allow_huggingface_fallback=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2507.01125"
    assert result.source == "openalex_preprint_doi"
    assert call_order == ["openalex_exact", "arxiv_html", "arxiv_api", "openalex_preprint"]
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
async def test_resolve_arxiv_url_falls_back_to_api_title_search_after_html_miss():
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(return_value=(None, "Published Paper"))
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=(None, None, "No arXiv ID found from title search")),
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
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        allow_title_search=True,
        allow_huggingface_fallback=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2508.18242"
    assert result.resolved_title == "GSVisLoc: Generalizable Visual Localization for Gaussian Splatting Scene Representations"
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
async def test_resolve_arxiv_url_runs_crossref_then_datacite_after_html_title_search_fails_without_hf():
    call_order: list[str] = []

    async def openalex_exact(identifier: str, title=None):
        call_order.append("openalex_exact")
        return None, "Published Paper"

    async def openalex_preprint(identifier: str, title=None):
        call_order.append("openalex_preprint")
        return None, "Published Paper"

    async def html_title_search(title: str):
        call_order.append("arxiv_html")
        return None, None, "No arXiv ID found from title search"

    async def api_title_search(title: str):
        call_order.append("arxiv_api")
        return None, None, None, "No arXiv ID found from title search"

    async def crossref_lookup(doi_url: str):
        call_order.append("crossref")
        return None, "Published Paper"

    async def datacite_lookup(doi_url: str):
        call_order.append("datacite")
        return "https://arxiv.org/abs/2501.12345", "Published Paper"

    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(side_effect=openalex_exact),
        find_preprint_match_by_identifier=AsyncMock(side_effect=openalex_preprint),
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(side_effect=html_title_search),
        get_arxiv_match_by_title_from_api=AsyncMock(side_effect=api_title_search),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(side_effect=crossref_lookup))
    datacite_client = SimpleNamespace(
        find_arxiv_match_by_doi=AsyncMock(side_effect=datacite_lookup)
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
    assert call_order == [
        "openalex_exact",
        "arxiv_html",
        "arxiv_api",
        "openalex_preprint",
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

    async def openalex_exact(identifier: str, title=None):
        call_order.append("openalex_exact")
        return None, "Published Paper"

    async def openalex_preprint(identifier: str, title=None):
        call_order.append("openalex_preprint")
        return None, "Published Paper"

    async def html_title_search(title: str):
        call_order.append("arxiv_html")
        return None, None, "No arXiv ID found from title search"

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

    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(side_effect=openalex_exact),
        find_preprint_match_by_identifier=AsyncMock(side_effect=openalex_preprint),
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(side_effect=html_title_search),
        get_arxiv_match_by_title_from_api=AsyncMock(
            return_value=(None, None, None, "No arXiv ID found from title search")
        ),
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
        openalex_client=openalex_client,
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
        "openalex_exact",
        "arxiv_html",
        "openalex_preprint",
        "crossref",
        "datacite",
        "huggingface",
    ]
    crossref_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    datacite_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    discovery_client.get_huggingface_paper_search_results.assert_awaited_once_with("Published Paper", limit=1)
