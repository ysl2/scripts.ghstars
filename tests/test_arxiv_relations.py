import csv
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from src.arxiv_relations.pipeline import ArxivRelationsExportResult, export_arxiv_relations_to_csv
from src.shared.relation_candidates import RelatedWorkCandidate
from src.shared.papers import ConversionResult, PaperSeed
from src.shared.papers import PaperRecord
from src.arxiv_relations.runner import run_arxiv_relations_mode


class FakeRelationResolutionCache:
    def __init__(self, entries: dict[tuple[str, str], object] | None = None):
        self.entries = entries or {}
        self.get_calls: list[tuple[str, str]] = []
        self.record_calls: list[tuple[str, str, str | None]] = []
        self.record_detail_calls: list[tuple[str, str, str | None, str | None]] = []

    def get(self, key_type: str, key_value: str):
        self.get_calls.append((key_type, key_value))
        return self.entries.get((key_type, key_value))

    def record_resolution(
        self,
        *,
        key_type: str,
        key_value: str,
        arxiv_url: str | None,
        resolved_title: str | None = None,
    ) -> None:
        self.record_calls.append((key_type, key_value, arxiv_url))
        self.record_detail_calls.append((key_type, key_value, arxiv_url, resolved_title))
        self.entries[(key_type, key_value)] = SimpleNamespace(
            key_type=key_type,
            key_value=key_value,
            arxiv_url=arxiv_url,
            resolved_title=resolved_title,
            checked_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def is_negative_cache_fresh(checked_at: str | None, recheck_days: int) -> bool:
        from src.shared.relation_resolution_cache import RelationResolutionCacheStore

        return RelationResolutionCacheStore.is_negative_cache_fresh(checked_at, recheck_days)


def test_dedup_prefers_direct_arxiv_over_title_mapped_row():
    from src.arxiv_relations.pipeline import (
        NormalizationStrength,
        NormalizedRelatedRow,
        _dedupe_normalized_rows,
    )

    rows = [
        NormalizedRelatedRow(
            title="Mapped Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.TITLE_SEARCH,
        ),
        NormalizedRelatedRow(
            title="Direct Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.DIRECT_ARXIV,
        ),
    ]

    winner = _dedupe_normalized_rows(rows)

    assert winner == [
        NormalizedRelatedRow(
            title="Direct Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.DIRECT_ARXIV,
        )
    ]


def test_dedup_breaks_same_strength_ties_by_normalized_then_original_title():
    from src.arxiv_relations.pipeline import (
        NormalizationStrength,
        NormalizedRelatedRow,
        _dedupe_normalized_rows,
    )

    rows = [
        NormalizedRelatedRow(
            title="Zoo",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        ),
        NormalizedRelatedRow(
            title="alpha",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        ),
    ]

    winner = _dedupe_normalized_rows(rows)

    assert winner == [
        NormalizedRelatedRow(
            title="alpha",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        )
    ]


def test_dedup_breaks_equal_normalized_titles_by_original_title():
    from src.arxiv_relations.pipeline import (
        NormalizationStrength,
        NormalizedRelatedRow,
        _dedupe_normalized_rows,
    )

    rows = [
        NormalizedRelatedRow(
            title="A-study",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        ),
        NormalizedRelatedRow(
            title="A  Study",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        ),
    ]

    winner = _dedupe_normalized_rows(rows)

    assert winner == [
        NormalizedRelatedRow(
            title="A  Study",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        )
    ]


def test_dedup_breaks_same_strength_title_mapped_ties_by_original_openalex_title():
    from src.arxiv_relations.pipeline import (
        NormalizationStrength,
        NormalizedRelatedRow,
        _dedupe_normalized_rows,
    )

    rows = [
        NormalizedRelatedRow(
            title="Mapped Arxiv Title",
            original_title="Zoo",
            url="https://arxiv.org/abs/2501.12345",
            strength=NormalizationStrength.TITLE_SEARCH,
        ),
        NormalizedRelatedRow(
            title="Mapped Arxiv Title",
            original_title="alpha",
            url="https://arxiv.org/abs/2501.12345",
            strength=NormalizationStrength.TITLE_SEARCH,
        ),
    ]

    winner = _dedupe_normalized_rows(rows)

    assert winner == [
        NormalizedRelatedRow(
            title="Mapped Arxiv Title",
            original_title="alpha",
            url="https://arxiv.org/abs/2501.12345",
            strength=NormalizationStrength.TITLE_SEARCH,
        )
    ]


def test_dedup_breaks_title_mapped_ties_by_final_title_before_original_title():
    from src.arxiv_relations.pipeline import (
        NormalizationStrength,
        NormalizedRelatedRow,
        _dedupe_normalized_rows,
    )

    rows = [
        NormalizedRelatedRow(
            title="Z Matched",
            original_title="alpha",
            url="https://arxiv.org/abs/2501.12345",
            strength=NormalizationStrength.TITLE_SEARCH,
        ),
        NormalizedRelatedRow(
            title="A Matched",
            original_title="zulu",
            url="https://arxiv.org/abs/2501.12345",
            strength=NormalizationStrength.TITLE_SEARCH,
        ),
    ]

    winner = _dedupe_normalized_rows(rows)

    assert winner == [
        NormalizedRelatedRow(
            title="A Matched",
            original_title="zulu",
            url="https://arxiv.org/abs/2501.12345",
            strength=NormalizationStrength.TITLE_SEARCH,
        )
    ]


@pytest.mark.anyio
async def test_normalize_related_works_maps_non_arxiv_title_hits_to_canonical_arxiv():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R1": RelatedWorkCandidate(
                    title="Direct Paper",
                    direct_arxiv_url="https://arxiv.org/abs/2403.00001",
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/W1",
                ),
                "R2": RelatedWorkCandidate(
                    title="Original OpenAlex Title",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/mapped",
                    source_url="https://openalex.org/W2",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            if title == "Original OpenAlex Title":
                return "2501.12345", "title_search_exact", None
            raise AssertionError(f"Unexpected title search: {title}")

        async def get_arxiv_match_by_title_from_api(self, title: str):
            if title == "Publisher Reference":
                return None, None, None, "No arXiv ID found from title search"
            raise AssertionError(f"Unexpected API title search: {title}")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Optimized relation normalization should use the title-search helper result directly")

        async def get_title(self, arxiv_identifier: str):
            if arxiv_identifier in {"2501.12345", "https://arxiv.org/abs/2501.12345"}:
                return "Mapped Arxiv Title", None
            raise AssertionError(f"Unexpected arXiv title lookup: {arxiv_identifier}")

    related_works = [{"id": "R1"}, {"id": "R2"}]
    seeds = await normalize_related_works_to_seeds(
        related_works,
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
    )

    assert seeds == [
        PaperSeed(name="Direct Paper", url="https://arxiv.org/abs/2403.00001"),
        PaperSeed(name="Mapped Arxiv Title", url="https://arxiv.org/abs/2501.12345"),
    ]


@pytest.mark.anyio
async def test_normalize_related_work_candidates_to_seeds_matches_openalex_wrapper_behavior():
    from src.arxiv_relations.pipeline import (
        normalize_related_work_candidates_to_seeds,
        normalize_related_works_to_seeds,
    )

    candidates = [
        RelatedWorkCandidate(
            title="Direct Paper",
            direct_arxiv_url="https://arxiv.org/abs/2403.00001",
            doi_url=None,
            landing_page_url=None,
            source_url="https://openalex.org/W1",
        ),
        RelatedWorkCandidate(
            title="Original OpenAlex Title",
            direct_arxiv_url=None,
            doi_url=None,
            landing_page_url="https://publisher.example/mapped",
            source_url="https://openalex.org/W2",
        ),
    ]

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            mapping = {"R1": candidates[0], "R2": candidates[1]}
            return mapping[work["id"]]

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            if title == "Original OpenAlex Title":
                return "2501.12345", "title_search_exact", None
            raise AssertionError(f"Unexpected title search: {title}")

        async def get_arxiv_match_by_title_from_api(self, title: str):
            return None, None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Optimized relation normalization should use the title-search helper result directly")

        async def get_title(self, arxiv_identifier: str):
            if arxiv_identifier in {"2501.12345", "https://arxiv.org/abs/2501.12345"}:
                return "Mapped Arxiv Title", None
            raise AssertionError(f"Unexpected arXiv title lookup: {arxiv_identifier}")

    from_candidates = await normalize_related_work_candidates_to_seeds(
        candidates,
        arxiv_client=FakeArxivClient(),
    )
    from_openalex_rows = await normalize_related_works_to_seeds(
        [{"id": "R1"}, {"id": "R2"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
    )

    assert from_candidates == from_openalex_rows == [
        PaperSeed(name="Direct Paper", url="https://arxiv.org/abs/2403.00001"),
        PaperSeed(name="Mapped Arxiv Title", url="https://arxiv.org/abs/2501.12345"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_uses_shared_resolver_instead_of_relation_local_ladder(monkeypatch):
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    resolve_calls: list[dict] = []

    async def fake_shared_resolver(
        title: str,
        raw_url: str,
        *,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        discovery_client=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        allow_title_search=True,
        allow_openalex_preprint_crosswalk=True,
        allow_huggingface_fallback=True,
        extra_identifiers=None,
    ):
        resolve_calls.append(
            {
                "title": title,
                "raw_url": raw_url,
                "crossref_client": crossref_client,
                "datacite_client": datacite_client,
                "discovery_client": discovery_client,
                "relation_resolution_cache": relation_resolution_cache,
                "arxiv_relation_no_arxiv_recheck_days": arxiv_relation_no_arxiv_recheck_days,
                "allow_title_search": allow_title_search,
                "allow_openalex_preprint_crosswalk": allow_openalex_preprint_crosswalk,
                "allow_huggingface_fallback": allow_huggingface_fallback,
                "extra_identifiers": extra_identifiers,
            }
        )
        return SimpleNamespace(
            resolved_url="https://arxiv.org/abs/2501.12345",
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            resolved_title="Mapped Arxiv Title",
            source="shared_resolver",
            script_derived=True,
        )

    monkeypatch.setattr(
        "src.arxiv_relations.pipeline.resolve_arxiv_url",
        fake_shared_resolver,
        raising=False,
    )

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Published Paper",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/paper",
                source_url="https://openalex.org/W123",
            )

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("Resolved titles from the shared resolver should not need a fallback lookup")

    crossref_client = SimpleNamespace(name="crossref")
    datacite_client = SimpleNamespace(name="datacite")
    discovery_client = SimpleNamespace(name="discovery")
    relation_resolution_cache = SimpleNamespace(name="cache")
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=17,
    )

    from src.arxiv_relations import pipeline as relations_pipeline

    assert not hasattr(relations_pipeline, "resolve_related_work_title_to_arxiv")
    assert seeds == [PaperSeed(name="Mapped Arxiv Title", url="https://arxiv.org/abs/2501.12345")]
    assert resolve_calls == [
        {
            "title": "Published Paper",
            "raw_url": "https://doi.org/10.1007/978-3-031-72933-1_9",
            "crossref_client": crossref_client,
            "datacite_client": datacite_client,
            "discovery_client": discovery_client,
            "relation_resolution_cache": relation_resolution_cache,
            "arxiv_relation_no_arxiv_recheck_days": 17,
            "allow_title_search": True,
            "allow_openalex_preprint_crosswalk": True,
            "allow_huggingface_fallback": True,
            "extra_identifiers": [
                "https://openalex.org/W123",
                "https://doi.org/10.1007/978-3-031-72933-1_9",
            ],
        }
    ]


@pytest.mark.anyio
async def test_normalize_related_works_uses_cached_resolved_title_before_get_title_fallback():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    recent = datetime.now(timezone.utc).isoformat()
    cache = FakeRelationResolutionCache(
        {
            ("openalex_work", "https://openalex.org/W9"): SimpleNamespace(
                key_type="openalex_work",
                key_value="https://openalex.org/W9",
                arxiv_url="https://arxiv.org/abs/2312.00451",
                resolved_title="Cached Arxiv Title",
                checked_at=recent,
            ),
            ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9"): SimpleNamespace(
                key_type="doi",
                key_value="https://doi.org/10.1007/978-3-031-72933-1_9",
                arxiv_url=None,
                resolved_title=None,
                checked_at=recent,
            ),
        }
    )

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Cached Candidate Title",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/cached",
                source_url="https://openalex.org/W9",
            )

    class FakeArxivClient:
        def __init__(self):
            self.title_lookups: list[str] = []

        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("HTML title search should not run when cache already has an arXiv URL")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("API title search should not run when cache already has an arXiv URL")

        async def get_title(self, arxiv_identifier: str):
            self.title_lookups.append(arxiv_identifier)
            raise AssertionError("Positive cache hits with resolved_title should not do an extra arXiv title lookup")

    arxiv_client = FakeArxivClient()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R9"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=arxiv_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [PaperSeed(name="Cached Arxiv Title", url="https://arxiv.org/abs/2312.00451")]
    assert cache.record_calls == []
    assert arxiv_client.title_lookups == []


@pytest.mark.anyio
async def test_normalize_related_works_falls_back_to_get_title_for_legacy_positive_cache_entries():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    recent = datetime.now(timezone.utc).isoformat()
    cache = FakeRelationResolutionCache(
        {
            ("openalex_work", "https://openalex.org/W9"): SimpleNamespace(
                key_type="openalex_work",
                key_value="https://openalex.org/W9",
                arxiv_url="https://arxiv.org/abs/2312.00451",
                checked_at=recent,
            ),
            ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9"): SimpleNamespace(
                key_type="doi",
                key_value="https://doi.org/10.1007/978-3-031-72933-1_9",
                arxiv_url=None,
                checked_at=recent,
            ),
        }
    )

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Cached Candidate Title",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/cached",
                source_url="https://openalex.org/W9",
            )

    class FakeArxivClient:
        def __init__(self):
            self.title_lookups: list[str] = []

        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("HTML title search should not run when cache already has an arXiv URL")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("API title search should not run when cache already has an arXiv URL")

        async def get_title(self, arxiv_identifier: str):
            self.title_lookups.append(arxiv_identifier)
            if arxiv_identifier in {"2312.00451", "https://arxiv.org/abs/2312.00451"}:
                return "Legacy Cached Arxiv Title", None
            raise AssertionError(f"Unexpected arXiv title lookup: {arxiv_identifier}")

    arxiv_client = FakeArxivClient()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R9"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=arxiv_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [PaperSeed(name="Legacy Cached Arxiv Title", url="https://arxiv.org/abs/2312.00451")]
    assert cache.record_calls == []
    assert arxiv_client.title_lookups == ["https://arxiv.org/abs/2312.00451"]


@pytest.mark.anyio
async def test_normalize_related_works_retains_unresolved_non_arxiv_rows_with_url_priority():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R3": RelatedWorkCandidate(
                    title="With DOI",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1145/example",
                    landing_page_url="https://publisher.example/doi",
                    source_url="https://openalex.org/W3",
                ),
                "R4": RelatedWorkCandidate(
                    title="With Landing",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/paper",
                    source_url="https://openalex.org/W4",
                ),
                "R5": RelatedWorkCandidate(
                    title="OpenAlex Only",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/W5",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

    related_works = [{"id": "R3"}, {"id": "R4"}, {"id": "R5"}]
    seeds = await normalize_related_works_to_seeds(
        related_works,
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
    )

    assert seeds == [
        PaperSeed(name="With DOI", url="https://doi.org/10.1145/example"),
        PaperSeed(name="With Landing", url="https://publisher.example/paper"),
        PaperSeed(name="OpenAlex Only", url="https://openalex.org/W5"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_skips_api_when_negative_cache_is_fresh():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    recent = datetime.now(timezone.utc).isoformat()
    cache = FakeRelationResolutionCache(
        {
            ("openalex_work", "https://openalex.org/W10"): SimpleNamespace(
                key_type="openalex_work",
                key_value="https://openalex.org/W10",
                arxiv_url=None,
                checked_at=recent,
            ),
            ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9"): SimpleNamespace(
                key_type="doi",
                key_value="https://doi.org/10.1007/978-3-031-72933-1_9",
                arxiv_url=None,
                checked_at=recent,
            )
        }
    )

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Fallback Only",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fallback",
                source_url="https://openalex.org/W10",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("HTML title search should not run for a fresh negative cache entry")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("API title search should not run for a fresh negative cache entry")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("arXiv title lookup should not run for a retained fallback row")

    seeds = await normalize_related_works_to_seeds(
        [{"id": "R10"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [PaperSeed(name="Fallback Only", url="https://doi.org/10.1007/978-3-031-72933-1_9")]
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_normalize_related_works_does_not_negative_cache_api_request_failures():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    cache = FakeRelationResolutionCache()

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Transient Failure Paper",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/transient",
                source_url="https://openalex.org/W10",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "arXiv search timeout"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("Title lookup should not run when API search fails")

    seeds = await normalize_related_works_to_seeds(
        [{"id": "R10"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(name="Transient Failure Paper", url="https://doi.org/10.1007/978-3-031-72933-1_9")
    ]
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_normalize_related_works_runs_openalex_crosswalk_before_arxiv_title_and_hf_fallbacks():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    events: list[tuple] = []

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Example Published Paper",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1145/example",
                landing_page_url="https://publisher.example/paper",
                source_url="https://openalex.org/WX1",
            )

        async def find_preprint_match_by_identifier(self, identifier: str, *, title: str | None = None):
            events.append(("openalex_crosswalk", title))
            assert identifier in {
                "https://openalex.org/WX1",
                "https://doi.org/10.1145/example",
            }
            return "https://arxiv.org/abs/2401.12345", "Example Preprint Title"

        async def find_related_work_preprint_arxiv_url(self, work: dict, *, title: str):
            raise AssertionError("Shared relation normalization should use identifier-based OpenAlex crosswalk lookup")

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            events.append(("arxiv_title_search", title))
            return None, None, "No arXiv ID found from title search"

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("OpenAlex crosswalk hits should not do an extra arXiv title lookup")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            raise AssertionError("OpenAlex crosswalk hits should bypass Hugging Face fallback")

    class TrackingRelationResolutionCache(FakeRelationResolutionCache):
        def record_resolution(
            self,
            *,
            key_type: str,
            key_value: str,
            arxiv_url: str | None,
            resolved_title: str | None = None,
        ) -> None:
            events.append(("cache_record", key_type, key_value, arxiv_url))
            super().record_resolution(
                key_type=key_type,
                key_value=key_value,
                arxiv_url=arxiv_url,
                resolved_title=resolved_title,
            )

    cache = TrackingRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R-openalex-crosswalk"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="Example Preprint Title",
            url="https://arxiv.org/abs/2401.12345",
        )
    ]
    assert cache.record_calls == [
        ("openalex_work", "https://openalex.org/WX1", "https://arxiv.org/abs/2401.12345"),
        ("doi", "https://doi.org/10.1145/example", "https://arxiv.org/abs/2401.12345"),
    ]
    assert cache.entries[("openalex_work", "https://openalex.org/WX1")].resolved_title == "Example Preprint Title"
    assert cache.entries[("doi", "https://doi.org/10.1145/example")].resolved_title == "Example Preprint Title"
    assert events == [
        ("openalex_crosswalk", "Example Published Paper"),
        ("cache_record", "openalex_work", "https://openalex.org/WX1", "https://arxiv.org/abs/2401.12345"),
        ("cache_record", "doi", "https://doi.org/10.1145/example", "https://arxiv.org/abs/2401.12345"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_openalex_crosswalk_miss_runs_before_arxiv_and_hf_fallback():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    events: list[tuple] = []

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Example Published Paper",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1145/example",
                landing_page_url="https://publisher.example/paper",
                source_url="https://openalex.org/WX2",
            )

        async def find_related_work_preprint_arxiv_url(self, work: dict, *, title: str):
            raise AssertionError("Shared relation normalization should use identifier-based OpenAlex crosswalk lookup")

        async def find_preprint_match_by_identifier(self, identifier: str, *, title: str | None = None):
            events.append(("openalex_crosswalk", title))
            assert identifier in {
                "https://openalex.org/WX2",
                "https://doi.org/10.1145/example",
            }
            return None, None

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            events.append(("arxiv_title_search", title))
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after a full miss")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            events.append(("hf_search_json", title, limit))
            return [], None

    class TrackingRelationResolutionCache(FakeRelationResolutionCache):
        def record_resolution(
            self,
            *,
            key_type: str,
            key_value: str,
            arxiv_url: str | None,
            resolved_title: str | None = None,
        ) -> None:
            events.append(("cache_record", key_type, key_value, arxiv_url))
            super().record_resolution(
                key_type=key_type,
                key_value=key_value,
                arxiv_url=arxiv_url,
                resolved_title=resolved_title,
            )

    cache = TrackingRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R-openalex-crosswalk-miss"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [PaperSeed(name="Example Published Paper", url="https://doi.org/10.1145/example")]
    assert cache.record_calls == [
        ("openalex_work", "https://openalex.org/WX2", None),
        ("doi", "https://doi.org/10.1145/example", None),
    ]
    assert events == [
        ("openalex_crosswalk", "Example Published Paper"),
        ("openalex_crosswalk", "Example Published Paper"),
        ("arxiv_title_search", "Example Published Paper"),
        ("hf_search_json", "Example Published Paper", 1),
        ("cache_record", "openalex_work", "https://openalex.org/WX2", None),
        ("cache_record", "doi", "https://doi.org/10.1145/example", None),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_does_not_negative_cache_when_openalex_crosswalk_errors_and_other_fallbacks_miss():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Example Published Paper",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1145/example",
                landing_page_url="https://publisher.example/paper",
                source_url="https://openalex.org/WX3",
            )

        async def find_related_work_preprint_arxiv_url(self, work: dict, *, title: str):
            raise AssertionError("Shared relation normalization should use identifier-based OpenAlex crosswalk lookup")

        async def find_preprint_match_by_identifier(self, identifier: str, *, title: str | None = None):
            raise RuntimeError("OpenAlex API error (429)")

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after a full miss")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            return [], None

    cache = FakeRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R-openalex-crosswalk-error"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [PaperSeed(name="Example Published Paper", url="https://doi.org/10.1145/example")]
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_normalize_related_works_uses_hf_fallback_after_arxiv_api_miss():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    events: list[tuple] = []

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            events.append(("arxiv_title_miss", title))
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            events.append(("title_lookup", arxiv_identifier))
            assert arxiv_identifier == "2312.00451"
            return "FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting", None

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            events.append(("hf_search_json", title, limit))
            return (
                [
                    {
                        "paper": {
                            "id": "2312.00451",
                            "title": "FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting",
                        }
                    }
                ],
                None,
            )

    class TrackingRelationResolutionCache(FakeRelationResolutionCache):
        def record_resolution(
            self,
            *,
            key_type: str,
            key_value: str,
            arxiv_url: str | None,
            resolved_title: str | None = None,
        ) -> None:
            events.append(("cache_record", key_type, key_value, arxiv_url))
            super().record_resolution(
                key_type=key_type,
                key_value=key_value,
                arxiv_url=arxiv_url,
                resolved_title=resolved_title,
            )

    cache = TrackingRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting",
            url="https://arxiv.org/abs/2312.00451",
        )
    ]
    assert cache.record_calls == [
        ("openalex_work", "https://openalex.org/WFSGS", "https://arxiv.org/abs/2312.00451"),
        ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9", "https://arxiv.org/abs/2312.00451"),
    ]
    assert (
        cache.entries[("openalex_work", "https://openalex.org/WFSGS")].resolved_title
        == "FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting"
    )
    assert (
        cache.entries[("doi", "https://doi.org/10.1007/978-3-031-72933-1_9")].resolved_title
        == "FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting"
    )
    assert events == [
        ("arxiv_title_miss", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting"),
        ("hf_search_json", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting", 1),
        ("title_lookup", "2312.00451"),
        ("cache_record", "openalex_work", "https://openalex.org/WFSGS", "https://arxiv.org/abs/2312.00451"),
        ("cache_record", "doi", "https://doi.org/10.1007/978-3-031-72933-1_9", "https://arxiv.org/abs/2312.00451"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_uses_hf_fallback_after_arxiv_api_transient_error():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    events: list[tuple] = []

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            events.append(("arxiv_title_error", title))
            return None, None, "arXiv search error (429)"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            events.append(("title_lookup", arxiv_identifier))
            assert arxiv_identifier == "2312.00451"
            return "FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting", None

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            events.append(("hf_search_json", title, limit))
            return (
                [
                    {
                        "paper": {
                            "id": "2312.00451",
                            "title": "FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting",
                        }
                    }
                ],
                None,
            )

    class TrackingRelationResolutionCache(FakeRelationResolutionCache):
        def record_resolution(
            self,
            *,
            key_type: str,
            key_value: str,
            arxiv_url: str | None,
            resolved_title: str | None = None,
        ) -> None:
            events.append(("cache_record", key_type, key_value, arxiv_url))
            super().record_resolution(
                key_type=key_type,
                key_value=key_value,
                arxiv_url=arxiv_url,
                resolved_title=resolved_title,
            )

    cache = TrackingRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting",
            url="https://arxiv.org/abs/2312.00451",
        )
    ]
    assert cache.record_calls == [
        ("openalex_work", "https://openalex.org/WFSGS", "https://arxiv.org/abs/2312.00451"),
        ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9", "https://arxiv.org/abs/2312.00451"),
    ]
    assert events == [
        ("arxiv_title_error", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting"),
        ("hf_search_json", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting", 1),
        ("title_lookup", "2312.00451"),
        ("cache_record", "openalex_work", "https://openalex.org/WFSGS", "https://arxiv.org/abs/2312.00451"),
        ("cache_record", "doi", "https://doi.org/10.1007/978-3-031-72933-1_9", "https://arxiv.org/abs/2312.00451"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_negative_caches_stable_miss_when_hf_token_is_missing():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after a full miss")

    class FakeDiscoveryClient:
        huggingface_token = ""

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            raise AssertionError("HF fallback should be skipped when token is missing")

    cache = FakeRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeNoMatchArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
            url="https://doi.org/10.1007/978-3-031-72933-1_9",
        )
    ]
    assert cache.record_calls == [
        ("openalex_work", "https://openalex.org/WFSGS", None),
        ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9", None),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_does_not_negative_cache_transient_hf_failures():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after a full miss")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            return None, "Hugging Face Papers timeout"

    cache = FakeRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeNoMatchArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
            url="https://doi.org/10.1007/978-3-031-72933-1_9",
        )
    ]
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_normalize_related_works_does_not_negative_cache_when_arxiv_transient_error_and_hf_misses():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "arXiv search timeout"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after a full miss")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            return [], None

    cache = FakeRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
            url="https://doi.org/10.1007/978-3-031-72933-1_9",
        )
    ]
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_normalize_related_works_does_not_negative_cache_unparseable_hf_payload():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after an unparseable HF payload")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            return {"unexpected": "payload"}, None

    cache = FakeRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeNoMatchArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
            url="https://doi.org/10.1007/978-3-031-72933-1_9",
        )
    ]
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_normalize_related_works_does_not_negative_cache_malformed_hf_search_items():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after malformed HF search items")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            return [
                {"paper": {"title": "FSGS: Real-Time Few-shot View Synthesis using Gaussian Splatting"}},
                {"paper": {"id": "2312.00451"}},
            ], None

    cache = FakeRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeNoMatchArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
            url="https://doi.org/10.1007/978-3-031-72933-1_9",
        )
    ]
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_normalize_related_works_does_not_negative_cache_when_hf_title_payload_shape_is_invalid():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after malformed HF title payloads")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            return [
                {
                    "paper": {
                        "id": "2312.00451",
                        "title": {"unexpected": "shape"},
                    }
                }
            ], None

    cache = FakeRelationResolutionCache()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeNoMatchArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [
        PaperSeed(
            name="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
            url="https://doi.org/10.1007/978-3-031-72933-1_9",
        )
    ]
    assert cache.record_calls == []


@pytest.mark.anyio
async def test_normalize_related_works_negative_caches_after_stable_miss_with_hf_enabled():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    events: list[tuple] = []

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                source_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            events.append(("arxiv_title_miss", title))
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after a full miss")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 3):
            events.append(("hf_search_json_miss", title, limit))
            return [], None

    class TrackingRelationResolutionCache(FakeRelationResolutionCache):
        def record_resolution(
            self,
            *,
            key_type: str,
            key_value: str,
            arxiv_url: str | None,
            resolved_title: str | None = None,
        ) -> None:
            events.append(("cache_record", key_type, key_value, arxiv_url))
            super().record_resolution(
                key_type=key_type,
                key_value=key_value,
                arxiv_url=arxiv_url,
                resolved_title=resolved_title,
            )

    cache = TrackingRelationResolutionCache()
    await normalize_related_works_to_seeds(
        [{"id": "R1"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeNoMatchArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert cache.record_calls == [
        ("openalex_work", "https://openalex.org/WFSGS", None),
        ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9", None),
    ]
    assert events == [
        ("arxiv_title_miss", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting"),
        ("hf_search_json_miss", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting", 1),
        ("cache_record", "openalex_work", "https://openalex.org/WFSGS", None),
        ("cache_record", "doi", "https://doi.org/10.1007/978-3-031-72933-1_9", None),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_rechecks_stale_negative_and_backfills_all_keys():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    stale = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    cache = FakeRelationResolutionCache(
        {
            ("openalex_work", "https://openalex.org/W11"): SimpleNamespace(
                key_type="openalex_work",
                key_value="https://openalex.org/W11",
                arxiv_url=None,
                checked_at=stale,
            )
        }
    )

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Backfilled Paper",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/backfilled",
                source_url="https://openalex.org/W11",
            )

    class FakeArxivClient:
        def __init__(self):
            self.html_title_searches: list[str] = []
            self.title_lookups: list[str] = []

        async def get_arxiv_id_by_title(self, title: str):
            self.html_title_searches.append(title)
            return "2312.00451", "title_search_exact", None

        async def get_arxiv_match_by_title_from_api(self, title: str):
            if title == "Publisher Reference":
                return None, None, None, "No arXiv ID found from title search"
            raise AssertionError(f"Unexpected API title search: {title}")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Optimized backfill should use the title-search helper result directly")

        async def get_title(self, arxiv_identifier: str):
            self.title_lookups.append(arxiv_identifier)
            if arxiv_identifier == "2312.00451":
                return "Mapped Arxiv Title", None
            raise AssertionError(f"Unexpected arXiv title lookup: {arxiv_identifier}")

    arxiv_client = FakeArxivClient()
    seeds = await normalize_related_works_to_seeds(
        [{"id": "R11"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=arxiv_client,
        relation_resolution_cache=cache,
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [PaperSeed(name="Mapped Arxiv Title", url="https://arxiv.org/abs/2312.00451")]
    assert arxiv_client.html_title_searches == ["Backfilled Paper"]
    assert arxiv_client.title_lookups == ["2312.00451"]
    assert cache.record_calls == [
        ("openalex_work", "https://openalex.org/W11", "https://arxiv.org/abs/2312.00451"),
        ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9", "https://arxiv.org/abs/2312.00451"),
    ]
    assert cache.entries[("openalex_work", "https://openalex.org/W11")].resolved_title == "Mapped Arxiv Title"
    assert (
        cache.entries[("doi", "https://doi.org/10.1007/978-3-031-72933-1_9")].resolved_title
        == "Mapped Arxiv Title"
    )


@pytest.mark.anyio
async def test_normalize_related_works_direct_arxiv_rows_bypass_cache_and_search_path():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class ExplodingRelationResolutionCache:
        def get(self, key_type: str, key_value: str):
            raise AssertionError("Direct arXiv rows should not consult the relation-resolution cache")

        def record_resolution(
            self,
            *,
            key_type: str,
            key_value: str,
            arxiv_url: str | None,
            resolved_title: str | None = None,
        ) -> None:
            raise AssertionError("Direct arXiv rows should not update the relation-resolution cache")

        @staticmethod
        def is_negative_cache_fresh(checked_at: str | None, recheck_days: int) -> bool:
            raise AssertionError("Direct arXiv rows should not check negative-cache freshness")

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Direct Paper",
                direct_arxiv_url="https://arxiv.org/abs/2501.00001",
                doi_url="https://doi.org/10.1000/direct",
                landing_page_url="https://publisher.example/direct",
                source_url="https://openalex.org/W12",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Direct arXiv rows should not invoke legacy title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Direct arXiv rows should not invoke API title search")

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("Direct arXiv rows should not need extra title lookups")

    seeds = await normalize_related_works_to_seeds(
        [{"id": "R12"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
        relation_resolution_cache=ExplodingRelationResolutionCache(),
        arxiv_relation_no_arxiv_recheck_days=30,
    )

    assert seeds == [PaperSeed(name="Direct Paper", url="https://arxiv.org/abs/2501.00001")]


@pytest.mark.anyio
async def test_normalize_related_works_resolves_non_direct_rows_concurrently():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R6": RelatedWorkCandidate(
                    title="Concurrent Paper A",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/a",
                    source_url="https://openalex.org/W6",
                ),
                "R7": RelatedWorkCandidate(
                    title="Concurrent Paper B",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/b",
                    source_url="https://openalex.org/W7",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        def __init__(self):
            self.search_started: list[str] = []
            self.release_searches = asyncio.Event()

        async def get_arxiv_id_by_title(self, title: str):
            self.search_started.append(title)
            if len(self.search_started) == 2:
                self.release_searches.set()
            await self.release_searches.wait()

            mapping = {
                "Concurrent Paper A": ("2601.00001", "title_search_exact", None),
                "Concurrent Paper B": ("2601.00002", "title_search_exact", None),
            }
            return mapping[title]

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

        async def get_title(self, arxiv_identifier: str):
            mapping = {
                "2601.00001": ("Concurrent Match A", None),
                "2601.00002": ("Concurrent Match B", None),
            }
            return mapping[arxiv_identifier]

    seeds = await asyncio.wait_for(
        normalize_related_works_to_seeds(
            [{"id": "R6"}, {"id": "R7"}],
            openalex_client=FakeOpenAlexClient(),
            arxiv_client=FakeArxivClient(),
        ),
        timeout=0.2,
    )

    assert seeds == [
        PaperSeed(name="Concurrent Match A", url="https://arxiv.org/abs/2601.00001"),
        PaperSeed(name="Concurrent Match B", url="https://arxiv.org/abs/2601.00002"),
    ]


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_exports_mixed_direct_mapped_and_retained_rows(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import NormalizationStrength, export_arxiv_relations_to_csv

    recent = datetime.now(timezone.utc).isoformat()
    relation_resolution_cache = FakeRelationResolutionCache(
        {
            ("doi", "https://doi.org/10.1145/example"): SimpleNamespace(
                key_type="doi",
                key_value="https://doi.org/10.1145/example",
                arxiv_url=None,
                checked_at=recent,
            )
        }
    )

    class FakeArxivClient:
        def __init__(self):
            self.calls: list[str] = []
            self.api_title_searches: list[str] = []

        async def get_title(self, arxiv_identifier: str):
            self.calls.append(arxiv_identifier)
            title_mapping = {
                "https://arxiv.org/abs/2603.23502": "Target Paper",
                "2501.00002": "Mapped Reference",
            }
            return title_mapping[arxiv_identifier], None

        async def get_arxiv_id_by_title(self, title: str):
            self.api_title_searches.append(title)
            if title == "Reference Needs Mapping":
                return "2501.00002", "title_search_exact", None
            if title == "Publisher Reference":
                return None, None, "No arXiv ID found from title search"
            raise AssertionError(f"Unexpected title search: {title}")

        async def get_arxiv_match_by_title_from_api(self, title: str):
            if title == "Publisher Reference":
                return None, None, None, "No arXiv ID found from title search"
            raise AssertionError(f"Unexpected API title search: {title}")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Optimized relation export should use the title-search helper result directly")

    class FakeOpenAlexClient:
        def __init__(self):
            self.title_queries: list[str] = []
            self.reference_work_queries: list[dict] = []
            self.citation_work_queries: list[dict] = []

        async def search_first_work(self, title: str):
            self.title_queries.append(title)
            return {"id": "https://openalex.org/W0"}

        async def fetch_referenced_works(self, work: dict):
            self.reference_work_queries.append(work)
            return [
                {"id": "R1"},
                {"id": "R2"},
                {"id": "R3"},
            ]

        async def fetch_citations(self, work: dict):
            self.citation_work_queries.append(work)
            return [
                {"id": "C1"},
                {"id": "C2"},
            ]

        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R1": RelatedWorkCandidate(
                    title="Direct Reference",
                    direct_arxiv_url="https://arxiv.org/abs/2501.00001",
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/WR1",
                ),
                "R2": RelatedWorkCandidate(
                    title="Reference Needs Mapping",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/mapped",
                    source_url="https://openalex.org/WR2",
                ),
                "R3": RelatedWorkCandidate(
                    title="Publisher Reference",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1145/example",
                    landing_page_url="https://publisher.example/doi",
                    source_url="https://openalex.org/WR3",
                ),
                "C1": RelatedWorkCandidate(
                    title="Citation A",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00002",
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/WC1",
                ),
                "C2": RelatedWorkCandidate(
                    title="Citation A Duplicate",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00002",
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/WC2",
                ),
            }
            return mapping[work["id"]]

    arxiv_client = FakeArxivClient()
    openalex_client = FakeOpenAlexClient()
    discovery_client = object()
    github_client = object()
    export_calls = []
    statuses = []
    normalization_progress_events = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append(
            {
                "seeds": seeds,
                "csv_path": csv_path,
                "discovery_client": discovery_client,
                "github_client": github_client,
                "content_cache": content_cache,
                "relation_resolution_cache": relation_resolution_cache,
                "arxiv_relation_no_arxiv_recheck_days": arxiv_relation_no_arxiv_recheck_days,
            }
        )
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/pdf/2603.23502v4.pdf?download=1",
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        discovery_client=discovery_client,
        github_client=github_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=30,
        output_dir=tmp_path,
        status_callback=statuses.append,
        normalization_progress_callback=lambda outcome, total: normalization_progress_events.append(
            (
                outcome.index,
                total,
                outcome.row.title,
                outcome.row.url,
                outcome.row.strength,
                outcome.row.resolution_source,
            )
        ),
    )

    assert arxiv_client.calls == [
        "https://arxiv.org/abs/2603.23502",
        "2501.00002",
    ]
    assert arxiv_client.api_title_searches == [
        "Reference Needs Mapping",
        "Publisher Reference",
    ]
    assert openalex_client.title_queries == ["Target Paper"]
    assert openalex_client.reference_work_queries == [{"id": "https://openalex.org/W0"}]
    assert openalex_client.citation_work_queries == [{"id": "https://openalex.org/W0"}]

    assert len(export_calls) == 2
    assert [call["csv_path"].name for call in export_calls] == [
        "arxiv-2603.23502-references-20260326113045.csv",
        "arxiv-2603.23502-citations-20260326113045.csv",
    ]

    reference_seeds = export_calls[0]["seeds"]
    citation_seeds = export_calls[1]["seeds"]
    assert reference_seeds == [
        PaperSeed(name="Direct Reference", url="https://arxiv.org/abs/2501.00001"),
        PaperSeed(name="Mapped Reference", url="https://arxiv.org/abs/2501.00002"),
        PaperSeed(name="Publisher Reference", url="https://doi.org/10.1145/example"),
    ]
    assert citation_seeds == [PaperSeed(name="Citation A", url="https://arxiv.org/abs/2502.00002")]
    assert all(isinstance(seed, PaperSeed) for seed in reference_seeds + citation_seeds)

    assert export_calls[0]["discovery_client"] is discovery_client
    assert export_calls[0]["github_client"] is github_client
    assert export_calls[0]["relation_resolution_cache"] is relation_resolution_cache
    assert export_calls[0]["arxiv_relation_no_arxiv_recheck_days"] == 30
    assert export_calls[1]["discovery_client"] is discovery_client
    assert export_calls[1]["github_client"] is github_client
    assert export_calls[1]["relation_resolution_cache"] is relation_resolution_cache
    assert export_calls[1]["arxiv_relation_no_arxiv_recheck_days"] == 30

    assert result.references.csv_path.name == "arxiv-2603.23502-references-20260326113045.csv"
    assert result.citations.csv_path.name == "arxiv-2603.23502-citations-20260326113045.csv"
    assert any("Fetching OpenAlex referenced works" in message for message in statuses)
    assert any("Fetching OpenAlex citations" in message for message in statuses)
    assert "🔎 Normalizing referenced works to arXiv-backed seeds" in statuses
    assert "🧭 Kept 3/3 referenced works after arXiv normalization" in statuses
    assert "🔎 Normalizing citation works to arXiv-backed seeds" in statuses
    assert "🧭 Kept 1/2 citation works after arXiv normalization" in statuses
    assert sorted(normalization_progress_events) == [
        (1, 2, "Citation A", "https://arxiv.org/abs/2502.00002", NormalizationStrength.DIRECT_ARXIV, "direct_arxiv_url"),
        (1, 3, "Direct Reference", "https://arxiv.org/abs/2501.00001", NormalizationStrength.DIRECT_ARXIV, "direct_arxiv_url"),
        (2, 2, "Citation A Duplicate", "https://arxiv.org/abs/2502.00002", NormalizationStrength.DIRECT_ARXIV, "direct_arxiv_url"),
        (2, 3, "Mapped Reference", "https://arxiv.org/abs/2501.00002", NormalizationStrength.TITLE_SEARCH, "title_search"),
        (3, 3, "Publisher Reference", "https://doi.org/10.1145/example", NormalizationStrength.RETAINED_NON_ARXIV, "unresolved"),
    ]


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_prefers_exact_openalex_work_lookup_over_title_search(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            assert arxiv_identifier == "https://arxiv.org/abs/2510.22706"
            return "IGGT: Instance-Grounded Geometry Transformer for Semantic 3D Reconstruction", None

    class FakeOpenAlexClient:
        def __init__(self):
            self.identifier_queries: list[str] = []
            self.title_queries: list[str] = []
            self.reference_work_queries: list[dict] = []
            self.citation_work_queries: list[dict] = []

        async def fetch_work_by_identifier(self, identifier: str):
            self.identifier_queries.append(identifier)
            if identifier == "https://doi.org/10.48550/arxiv.2510.22706":
                return {
                    "id": "https://openalex.org/W7101583646",
                    "display_name": "IGGT: Instance-Grounded Geometry Transformer for Semantic 3D Reconstruction",
                    "doi": "https://doi.org/10.48550/arxiv.2510.22706",
                    "referenced_works": [],
                }
            return None

        async def search_first_work(self, title: str):
            self.title_queries.append(title)
            return {"id": "https://openalex.org/W7139144094", "referenced_works": []}

        async def fetch_referenced_works(self, work: dict):
            self.reference_work_queries.append(work)
            return []

        async def fetch_citations(self, work: dict):
            self.citation_work_queries.append(work)
            return []

    export_calls = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append((seeds, csv_path))
        return ConversionResult(csv_path=csv_path, resolved=0, skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    openalex_client = FakeOpenAlexClient()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2510.22706",
        arxiv_client=FakeArxivClient(),
        openalex_client=openalex_client,
        discovery_client=object(),
        github_client=object(),
        output_dir=tmp_path,
    )

    assert openalex_client.identifier_queries == ["https://doi.org/10.48550/arxiv.2510.22706"]
    assert openalex_client.title_queries == []
    assert openalex_client.reference_work_queries == [
        {
            "id": "https://openalex.org/W7101583646",
            "display_name": "IGGT: Instance-Grounded Geometry Transformer for Semantic 3D Reconstruction",
            "doi": "https://doi.org/10.48550/arxiv.2510.22706",
            "referenced_works": [],
        }
    ]
    assert openalex_client.citation_work_queries == [
        {
            "id": "https://openalex.org/W7101583646",
            "display_name": "IGGT: Instance-Grounded Geometry Transformer for Semantic 3D Reconstruction",
            "doi": "https://doi.org/10.48550/arxiv.2510.22706",
            "referenced_works": [],
        }
    ]
    assert len(export_calls) == 2
    assert result.arxiv_url == "https://arxiv.org/abs/2510.22706"


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_uses_semantic_scholar_before_openalex(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            assert arxiv_identifier == "https://arxiv.org/abs/2510.22706"
            return "Target Paper", None

    class FakeSemanticScholarGraphClient:
        def __init__(self):
            self.identifier_queries: list[str] = []
            self.title_queries: list[str] = []
            self.reference_queries: list[dict] = []
            self.citation_queries: list[dict] = []

        async def fetch_paper_by_identifier(self, identifier: str):
            self.identifier_queries.append(identifier)
            if identifier == "DOI:10.48550/arXiv.2510.22706":
                return {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
            return None

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            self.title_queries.append(title)
            raise AssertionError("Semantic Scholar title fallback should not run when DOI lookup succeeds")

        async def fetch_references(self, paper: dict):
            self.reference_queries.append(paper)
            return [{"paperId": "ss-ref", "title": "Reference A", "externalIds": {"ArXiv": "2501.00001"}}]

        async def fetch_citations(self, paper: dict):
            self.citation_queries.append(paper)
            return [{"paperId": "ss-cite", "title": "Citation A", "externalIds": {"ArXiv": "2502.00001"}}]

        def build_related_work_candidate(self, paper: dict):
            mapping = {
                "ss-ref": RelatedWorkCandidate(
                    title="Reference A",
                    direct_arxiv_url="https://arxiv.org/abs/2501.00001",
                    doi_url=None,
                    landing_page_url="https://www.semanticscholar.org/paper/ss-ref",
                    source_url="https://www.semanticscholar.org/paper/ss-ref",
                ),
                "ss-cite": RelatedWorkCandidate(
                    title="Citation A",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00001",
                    doi_url=None,
                    landing_page_url="https://www.semanticscholar.org/paper/ss-cite",
                    source_url="https://www.semanticscholar.org/paper/ss-cite",
                ),
            }
            return mapping[paper["paperId"]]

    class FakeOpenAlexClient:
        async def fetch_work_by_identifier(self, identifier: str):
            raise AssertionError("OpenAlex target lookup should not run when Semantic Scholar succeeds")

        async def search_first_work(self, title: str):
            raise AssertionError("OpenAlex title lookup should not run when Semantic Scholar succeeds")

        async def fetch_referenced_works(self, work: dict):
            raise AssertionError("OpenAlex references should not run when Semantic Scholar returns references")

        async def fetch_citations(self, work: dict):
            raise AssertionError("OpenAlex citations should not run when Semantic Scholar returns citations")

    export_calls = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append({"seeds": seeds, "csv_path": csv_path})
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    semanticscholar_graph_client = FakeSemanticScholarGraphClient()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2510.22706",
        arxiv_client=FakeArxivClient(),
        openalex_client=FakeOpenAlexClient(),
        semanticscholar_graph_client=semanticscholar_graph_client,
        discovery_client=object(),
        github_client=object(),
        output_dir=tmp_path,
    )

    assert semanticscholar_graph_client.identifier_queries == ["DOI:10.48550/arXiv.2510.22706"]
    assert semanticscholar_graph_client.title_queries == []
    assert semanticscholar_graph_client.reference_queries == [
        {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
    ]
    assert semanticscholar_graph_client.citation_queries == [
        {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
    ]
    assert [call["seeds"] for call in export_calls] == [
        [PaperSeed(name="Reference A", url="https://arxiv.org/abs/2501.00001")],
        [PaperSeed(name="Citation A", url="https://arxiv.org/abs/2502.00001")],
    ]
    assert result.arxiv_url == "https://arxiv.org/abs/2510.22706"


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_falls_back_to_openalex_when_semantic_scholar_title_match_fails(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            assert arxiv_identifier == "https://arxiv.org/abs/2510.22706"
            return "Target Paper", None

    class FakeSemanticScholarGraphClient:
        def __init__(self):
            self.identifier_queries: list[str] = []
            self.title_queries: list[str] = []

        async def fetch_paper_by_identifier(self, identifier: str):
            self.identifier_queries.append(identifier)
            return None

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            self.title_queries.append(title)
            return [{"paperId": "wrong-paper", "title": "Different Paper", "externalIds": {}}]

    class FakeOpenAlexClient:
        def __init__(self):
            self.identifier_queries: list[str] = []
            self.title_queries: list[str] = []
            self.reference_queries: list[dict] = []
            self.citation_queries: list[dict] = []

        async def fetch_work_by_identifier(self, identifier: str):
            self.identifier_queries.append(identifier)
            if identifier == "https://doi.org/10.48550/arxiv.2510.22706":
                return {
                    "id": "https://openalex.org/W1",
                    "display_name": "Target Paper",
                    "doi": "https://doi.org/10.48550/arxiv.2510.22706",
                }
            return None

        async def search_first_work(self, title: str):
            self.title_queries.append(title)
            raise AssertionError("OpenAlex title search should not run when exact DOI lookup succeeds")

        async def fetch_referenced_works(self, work: dict):
            self.reference_queries.append(work)
            return [{"id": "OA-REF"}]

        async def fetch_citations(self, work: dict):
            self.citation_queries.append(work)
            return [{"id": "OA-CITE"}]

        def build_related_work_candidate(self, work: dict):
            mapping = {
                "OA-REF": RelatedWorkCandidate(
                    title="OpenAlex Reference",
                    direct_arxiv_url="https://arxiv.org/abs/2501.00010",
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/OA-REF",
                ),
                "OA-CITE": RelatedWorkCandidate(
                    title="OpenAlex Citation",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00010",
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/OA-CITE",
                ),
            }
            return mapping[work["id"]]

    export_calls = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append({"seeds": seeds, "csv_path": csv_path})
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    semanticscholar_graph_client = FakeSemanticScholarGraphClient()
    openalex_client = FakeOpenAlexClient()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2510.22706",
        arxiv_client=FakeArxivClient(),
        openalex_client=openalex_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        discovery_client=object(),
        github_client=object(),
        output_dir=tmp_path,
    )

    assert semanticscholar_graph_client.identifier_queries == [
        "DOI:10.48550/arXiv.2510.22706",
        "ARXIV:2510.22706",
    ]
    assert semanticscholar_graph_client.title_queries == ["Target Paper"]
    assert openalex_client.identifier_queries == ["https://doi.org/10.48550/arxiv.2510.22706"]
    assert openalex_client.title_queries == []
    assert openalex_client.reference_queries == [
        {
            "id": "https://openalex.org/W1",
            "display_name": "Target Paper",
            "doi": "https://doi.org/10.48550/arxiv.2510.22706",
        }
    ]
    assert openalex_client.citation_queries == [
        {
            "id": "https://openalex.org/W1",
            "display_name": "Target Paper",
            "doi": "https://doi.org/10.48550/arxiv.2510.22706",
        }
    ]
    assert [call["seeds"] for call in export_calls] == [
        [PaperSeed(name="OpenAlex Reference", url="https://arxiv.org/abs/2501.00010")],
        [PaperSeed(name="OpenAlex Citation", url="https://arxiv.org/abs/2502.00010")],
    ]
    assert result.arxiv_url == "https://arxiv.org/abs/2510.22706"


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_falls_back_per_side_when_semantic_scholar_returns_empty_side(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            assert arxiv_identifier == "https://arxiv.org/abs/2510.22706"
            return "Target Paper", None

    class FakeSemanticScholarGraphClient:
        def __init__(self):
            self.reference_queries: list[dict] = []
            self.citation_queries: list[dict] = []

        async def fetch_paper_by_identifier(self, identifier: str):
            if identifier == "DOI:10.48550/arXiv.2510.22706":
                return {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
            return None

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            raise AssertionError("Semantic Scholar title search should not run when DOI lookup succeeds")

        async def fetch_references(self, paper: dict):
            self.reference_queries.append(paper)
            return []

        async def fetch_citations(self, paper: dict):
            self.citation_queries.append(paper)
            return [{"paperId": "ss-cite", "title": "Semantic Citation", "externalIds": {"ArXiv": "2502.00020"}}]

        def build_related_work_candidate(self, paper: dict):
            return RelatedWorkCandidate(
                title="Semantic Citation",
                direct_arxiv_url="https://arxiv.org/abs/2502.00020",
                doi_url=None,
                landing_page_url="https://www.semanticscholar.org/paper/ss-cite",
                source_url="https://www.semanticscholar.org/paper/ss-cite",
            )

    class FakeOpenAlexClient:
        def __init__(self):
            self.identifier_queries: list[str] = []
            self.reference_queries: list[dict] = []
            self.citation_queries: list[dict] = []

        async def fetch_work_by_identifier(self, identifier: str):
            self.identifier_queries.append(identifier)
            if identifier == "https://doi.org/10.48550/arxiv.2510.22706":
                return {
                    "id": "https://openalex.org/W1",
                    "display_name": "Target Paper",
                    "doi": "https://doi.org/10.48550/arxiv.2510.22706",
                }
            return None

        async def search_first_work(self, title: str):
            raise AssertionError("OpenAlex title search should not run when exact DOI lookup succeeds")

        async def fetch_referenced_works(self, work: dict):
            self.reference_queries.append(work)
            return [{"id": "OA-REF"}]

        async def fetch_citations(self, work: dict):
            self.citation_queries.append(work)
            raise AssertionError("OpenAlex citations should not run when Semantic Scholar citations are non-empty")

        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="OpenAlex Reference",
                direct_arxiv_url="https://arxiv.org/abs/2501.00020",
                doi_url=None,
                landing_page_url=None,
                source_url="https://openalex.org/OA-REF",
            )

    export_calls = []
    statuses = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append({"seeds": seeds, "csv_path": csv_path})
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    openalex_client = FakeOpenAlexClient()
    semanticscholar_graph_client = FakeSemanticScholarGraphClient()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2510.22706",
        arxiv_client=FakeArxivClient(),
        openalex_client=openalex_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        discovery_client=object(),
        github_client=object(),
        output_dir=tmp_path,
        status_callback=statuses.append,
    )

    assert semanticscholar_graph_client.reference_queries == [
        {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
    ]
    assert semanticscholar_graph_client.citation_queries == [
        {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
    ]
    assert openalex_client.identifier_queries == ["https://doi.org/10.48550/arxiv.2510.22706"]
    assert openalex_client.reference_queries == [
        {
            "id": "https://openalex.org/W1",
            "display_name": "Target Paper",
            "doi": "https://doi.org/10.48550/arxiv.2510.22706",
        }
    ]
    assert openalex_client.citation_queries == []
    assert [call["seeds"] for call in export_calls] == [
        [PaperSeed(name="OpenAlex Reference", url="https://arxiv.org/abs/2501.00020")],
        [PaperSeed(name="Semantic Citation", url="https://arxiv.org/abs/2502.00020")],
    ]
    assert "📚 Semantic Scholar references empty; falling back to OpenAlex" in statuses
    assert result.arxiv_url == "https://arxiv.org/abs/2510.22706"


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_raises_when_side_fallback_cannot_resolve_openalex_target(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            assert arxiv_identifier == "https://arxiv.org/abs/2510.22706"
            return "Target Paper", None

    class FakeSemanticScholarGraphClient:
        def __init__(self):
            self.reference_queries: list[dict] = []
            self.citation_queries: list[dict] = []

        async def fetch_paper_by_identifier(self, identifier: str):
            if identifier == "DOI:10.48550/arXiv.2510.22706":
                return {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
            return None

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            raise AssertionError("Semantic Scholar title search should not run when DOI lookup succeeds")

        async def fetch_references(self, paper: dict):
            self.reference_queries.append(paper)
            raise RuntimeError("Semantic Scholar references timed out")

        async def fetch_citations(self, paper: dict):
            self.citation_queries.append(paper)
            raise AssertionError("Export should fail before attempting citation-side export")

    class FakeOpenAlexClient:
        def __init__(self):
            self.identifier_queries: list[str] = []
            self.title_queries: list[str] = []
            self.reference_queries: list[dict] = []
            self.citation_queries: list[dict] = []

        async def fetch_work_by_identifier(self, identifier: str):
            self.identifier_queries.append(identifier)
            return None

        async def search_first_work(self, title: str):
            self.title_queries.append(title)
            return None

        async def fetch_referenced_works(self, work: dict):
            self.reference_queries.append(work)
            raise AssertionError("OpenAlex side fetch should not run without a resolved target work")

        async def fetch_citations(self, work: dict):
            self.citation_queries.append(work)
            raise AssertionError("OpenAlex side fetch should not run without a resolved target work")

    export_calls = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append({"seeds": seeds, "csv_path": csv_path})
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    openalex_client = FakeOpenAlexClient()
    semanticscholar_graph_client = FakeSemanticScholarGraphClient()

    with pytest.raises(ValueError, match="OpenAlex target work"):
        await export_arxiv_relations_to_csv(
            "https://arxiv.org/abs/2510.22706",
            arxiv_client=FakeArxivClient(),
            openalex_client=openalex_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            discovery_client=object(),
            github_client=object(),
            output_dir=tmp_path,
        )

    assert semanticscholar_graph_client.reference_queries == [
        {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
    ]
    assert semanticscholar_graph_client.citation_queries == []
    assert openalex_client.identifier_queries == ["https://doi.org/10.48550/arxiv.2510.22706"]
    assert openalex_client.title_queries == ["Target Paper"]
    assert openalex_client.reference_queries == []
    assert openalex_client.citation_queries == []
    assert export_calls == []


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_keeps_empty_semantic_side_when_openalex_target_misses(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            assert arxiv_identifier == "https://arxiv.org/abs/2510.22706"
            return "Target Paper", None

    class FakeSemanticScholarGraphClient:
        def __init__(self):
            self.reference_queries: list[dict] = []
            self.citation_queries: list[dict] = []

        async def fetch_paper_by_identifier(self, identifier: str):
            if identifier == "DOI:10.48550/arXiv.2510.22706":
                return {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
            return None

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            raise AssertionError("Semantic Scholar title search should not run when DOI lookup succeeds")

        async def fetch_references(self, paper: dict):
            self.reference_queries.append(paper)
            return []

        async def fetch_citations(self, paper: dict):
            self.citation_queries.append(paper)
            return [{"paperId": "ss-cite", "title": "Semantic Citation", "externalIds": {"ArXiv": "2502.00020"}}]

        def build_related_work_candidate(self, paper: dict):
            return RelatedWorkCandidate(
                title="Semantic Citation",
                direct_arxiv_url="https://arxiv.org/abs/2502.00020",
                doi_url=None,
                landing_page_url="https://www.semanticscholar.org/paper/ss-cite",
                source_url="https://www.semanticscholar.org/paper/ss-cite",
            )

    class FakeOpenAlexClient:
        def __init__(self):
            self.identifier_queries: list[str] = []
            self.title_queries: list[str] = []
            self.reference_queries: list[dict] = []
            self.citation_queries: list[dict] = []

        async def fetch_work_by_identifier(self, identifier: str):
            self.identifier_queries.append(identifier)
            return None

        async def search_first_work(self, title: str):
            self.title_queries.append(title)
            return None

        async def fetch_referenced_works(self, work: dict):
            self.reference_queries.append(work)
            raise AssertionError("OpenAlex references should not run without a resolved target work")

        async def fetch_citations(self, work: dict):
            self.citation_queries.append(work)
            raise AssertionError("OpenAlex citations should not run when Semantic Scholar citations are non-empty")

    export_calls = []
    statuses = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append({"seeds": seeds, "csv_path": csv_path})
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    openalex_client = FakeOpenAlexClient()
    semanticscholar_graph_client = FakeSemanticScholarGraphClient()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2510.22706",
        arxiv_client=FakeArxivClient(),
        openalex_client=openalex_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        discovery_client=object(),
        github_client=object(),
        output_dir=tmp_path,
        status_callback=statuses.append,
    )

    assert semanticscholar_graph_client.reference_queries == [
        {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
    ]
    assert semanticscholar_graph_client.citation_queries == [
        {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
    ]
    assert openalex_client.identifier_queries == ["https://doi.org/10.48550/arxiv.2510.22706"]
    assert openalex_client.title_queries == ["Target Paper"]
    assert openalex_client.reference_queries == []
    assert openalex_client.citation_queries == []
    assert [call["seeds"] for call in export_calls] == [
        [],
        [PaperSeed(name="Semantic Citation", url="https://arxiv.org/abs/2502.00020")],
    ]
    assert "📚 Semantic Scholar references empty; falling back to OpenAlex" in statuses
    assert "⚠️ OpenAlex target lookup missed; keeping empty references" in statuses
    assert result.arxiv_url == "https://arxiv.org/abs/2510.22706"


@pytest.mark.anyio
async def test_resolve_target_semantic_scholar_paper_falls_through_after_doi_lookup_error():
    from src.arxiv_relations.pipeline import _resolve_target_semantic_scholar_paper

    class FakeSemanticScholarGraphClient:
        def __init__(self):
            self.identifier_queries: list[str] = []
            self.title_queries: list[str] = []

        async def fetch_paper_by_identifier(self, identifier: str):
            self.identifier_queries.append(identifier)
            if identifier == "DOI:10.48550/arXiv.2510.22706":
                raise RuntimeError("Semantic Scholar DOI lookup timed out")
            if identifier == "ARXIV:2510.22706":
                return {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}
            return None

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            self.title_queries.append(title)
            raise AssertionError("Semantic Scholar title search should not run when ARXIV lookup succeeds")

    semanticscholar_graph_client = FakeSemanticScholarGraphClient()
    paper = await _resolve_target_semantic_scholar_paper(
        "https://arxiv.org/abs/2510.22706",
        "Target Paper",
        semanticscholar_graph_client,
    )

    assert paper == {
        "paperId": "ss-target",
        "title": "Target Paper",
        "externalIds": {"ArXiv": "2510.22706"},
    }
    assert semanticscholar_graph_client.identifier_queries == [
        "DOI:10.48550/arXiv.2510.22706",
        "ARXIV:2510.22706",
    ]
    assert semanticscholar_graph_client.title_queries == []


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_uses_hf_fallback_for_unresolved_relations(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    events: list[tuple] = []

    class FakeArxivClient:
        def __init__(self):
            self.title_lookups: list[str] = []
            self.api_title_searches: list[str] = []

        async def get_title(self, arxiv_identifier: str):
            self.title_lookups.append(arxiv_identifier)
            title_mapping = {
                "https://arxiv.org/abs/2603.23502": "Target Paper",
                "2312.00451": "Mapped Reference",
                "2312.00452": "Mapped Citation",
            }
            return title_mapping[arxiv_identifier], None

        async def get_arxiv_id_by_title(self, title: str):
            self.api_title_searches.append(title)
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_id_by_title_from_api(self, title: str):
            raise AssertionError("Shared relation normalization should use the common HTML title search entrypoint")

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            assert title == "Target Paper"
            return {"id": "https://openalex.org/W0"}

        async def fetch_referenced_works(self, work: dict):
            assert work == {"id": "https://openalex.org/W0"}
            return [{"id": "R1"}]

        async def fetch_citations(self, work: dict):
            assert work == {"id": "https://openalex.org/W0"}
            return [{"id": "C1"}]

        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R1": RelatedWorkCandidate(
                    title="Reference Needs HF Mapping",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                    landing_page_url="https://publisher.example/reference",
                    source_url="https://openalex.org/WR1",
                ),
                "C1": RelatedWorkCandidate(
                    title="Citation Needs HF Mapping",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1007/978-3-031-72933-1_10",
                    landing_page_url="https://publisher.example/citation",
                    source_url="https://openalex.org/WC1",
                ),
            }
            return mapping[work["id"]]

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 1):
            events.append(("hf_search_json", title, limit))
            payload_by_title = {
                "Reference Needs HF Mapping": [
                    {
                        "paper": {
                            "id": "2312.00451",
                            "title": "Reference Needs HF Mapping",
                        }
                    }
                ],
                "Citation Needs HF Mapping": [
                    {
                        "paper": {
                            "id": "2312.00452",
                            "title": "Citation Needs HF Mapping",
                        }
                    }
                ],
            }
            return payload_by_title[title], None

    arxiv_client = FakeArxivClient()
    export_calls = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append({"seeds": seeds, "csv_path": csv_path, "content_cache": content_cache})
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=arxiv_client,
        openalex_client=FakeOpenAlexClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=object(),
        output_dir=tmp_path,
    )

    assert arxiv_client.api_title_searches == [
        "Reference Needs HF Mapping",
        "Citation Needs HF Mapping",
    ]
    assert arxiv_client.title_lookups == [
        "https://arxiv.org/abs/2603.23502",
        "2312.00451",
        "2312.00452",
    ]
    assert events == [
        ("hf_search_json", "Reference Needs HF Mapping", 1),
        ("hf_search_json", "Citation Needs HF Mapping", 1),
    ]

    assert [call["seeds"] for call in export_calls] == [
        [PaperSeed(name="Mapped Reference", url="https://arxiv.org/abs/2312.00451")],
        [PaperSeed(name="Mapped Citation", url="https://arxiv.org/abs/2312.00452")],
    ]
    assert result.references.resolved == 1
    assert result.citations.resolved == 1


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_uses_shared_openalex_retry_after_handling(
    tmp_path: Path,
    monkeypatch,
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv
    from src.shared.openalex import OpenAlexClient

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr("src.shared.openalex.asyncio.sleep", fake_sleep)

    class FakeResponse:
        def __init__(self, json_data=None, status=200, headers=None):
            self.status = status
            self._json_data = json_data or {}
            self.headers = dict(headers or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self, *args, **kwargs):
            return self._json_data

    class FakeSession:
        def __init__(self, responses):
            self._responses = list(responses)
            self.calls: list[dict] = []

        def get(self, url, *, headers=None, params=None):
            self.calls.append(
                {
                    "url": url,
                    "headers": dict(headers or {}),
                    "params": dict(params or {}),
                }
            )
            if not self._responses:
                raise RuntimeError("No fake response configured")
            return self._responses.pop(0)

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            return "Target Paper", None

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    session = FakeSession(
        [
            FakeResponse(
                {"error": "Rate limit exceeded"},
                status=429,
                headers={"Retry-After": "3", "X-RateLimit-Remaining": "0"},
            ),
            FakeResponse(
                {
                    "id": "https://openalex.org/W0",
                    "display_name": "Target Paper",
                    "doi": "https://doi.org/10.48550/arxiv.2603.23502",
                    "referenced_works": [],
                }
            ),
            FakeResponse({"results": [], "meta": {"next_cursor": None}}),
        ]
    )
    openalex_client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=FakeArxivClient(),
        openalex_client=openalex_client,
        discovery_client=object(),
        github_client=object(),
        output_dir=tmp_path,
    )

    assert sleep_calls == [3.0]
    assert len(session.calls) == 3
    assert result.references.resolved == 0
    assert result.citations.resolved == 0


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_rejects_invalid_single_paper_input():
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("Should not request arXiv title for invalid input")

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            raise AssertionError("Should not query OpenAlex for invalid input")

    with pytest.raises(ValueError, match="Invalid single-paper arXiv URL"):
        await export_arxiv_relations_to_csv(
            "https://arxiv.org/list/cs.CV/recent",
            arxiv_client=FakeArxivClient(),
            openalex_client=FakeOpenAlexClient(),
            discovery_client=object(),
            github_client=object(),
        )


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_warms_content_for_arxiv_rows_and_preserves_retained_non_arxiv_rows(
    tmp_path: Path,
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class RecordingContentCache:
        def __init__(self):
            self.calls: list[str] = []

        async def ensure_local_content_cache(self, canonical_arxiv_url: str) -> None:
            self.calls.append(canonical_arxiv_url)

    class FakeArxivClient:
        def __init__(self):
            self.html_title_searches: list[str] = []
            self.api_title_searches: list[str] = []

        async def get_title(self, arxiv_identifier: str):
            if arxiv_identifier == "https://arxiv.org/abs/2603.23502":
                return "Target Paper", None
            raise AssertionError(f"Unexpected arXiv title lookup: {arxiv_identifier}")

        async def get_arxiv_id_by_title(self, title: str):
            self.html_title_searches.append(title)
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_match_by_title_from_api(self, title: str):
            self.api_title_searches.append(title)
            return None, None, None, "No arXiv ID found from title search"

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            assert title == "Target Paper"
            return {"id": "https://openalex.org/W0"}

        async def fetch_referenced_works(self, work: dict):
            assert work == {"id": "https://openalex.org/W0"}
            return [{"id": "R1"}, {"id": "R2"}]

        async def fetch_citations(self, work: dict):
            assert work == {"id": "https://openalex.org/W0"}
            return [{"id": "C1"}]

        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R1": RelatedWorkCandidate(
                    title="Direct Reference",
                    direct_arxiv_url="https://arxiv.org/abs/2501.00001",
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/WR1",
                ),
                "R2": RelatedWorkCandidate(
                    title="Retained DOI Reference",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1145/example",
                    landing_page_url="https://publisher.example/reference",
                    source_url="https://openalex.org/WR2",
                ),
                "C1": RelatedWorkCandidate(
                    title="Citation With Missing Stars",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00002",
                    doi_url=None,
                    landing_page_url=None,
                    source_url="https://openalex.org/WC1",
                ),
            }
            return mapping[work["id"]]

    class FakeDiscoveryClient:
        huggingface_token = ""

        async def resolve_github_url(self, seed):
            mapping = {
                "https://arxiv.org/abs/2501.00001": "https://github.com/foo/reference",
                "https://arxiv.org/abs/2502.00002": "https://github.com/foo/citation",
            }
            return mapping.get(seed.url)

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            mapping = {
                ("foo", "reference"): (12, None),
                ("foo", "citation"): (None, "GitHub API error (503)"),
            }
            return mapping[(owner, repo)]

    arxiv_client = FakeArxivClient()
    content_cache = RecordingContentCache()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=arxiv_client,
        openalex_client=FakeOpenAlexClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=content_cache,
        output_dir=tmp_path,
    )

    with result.references.csv_path.open(newline="", encoding="utf-8") as handle:
        reference_rows = list(csv.DictReader(handle))
    with result.citations.csv_path.open(newline="", encoding="utf-8") as handle:
        citation_rows = list(csv.DictReader(handle))

    assert reference_rows == [
        {
            "Name": "Direct Reference",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/reference",
            "Stars": "12",
        },
        {
            "Name": "Retained DOI Reference",
            "Url": "https://doi.org/10.1145/example",
            "Github": "",
            "Stars": "",
        },
    ]
    assert citation_rows == [
        {
            "Name": "Citation With Missing Stars",
            "Url": "https://arxiv.org/abs/2502.00002",
            "Github": "https://github.com/foo/citation",
            "Stars": "",
        }
    ]
    assert sorted(content_cache.calls) == [
        "https://arxiv.org/abs/2501.00001",
        "https://arxiv.org/abs/2502.00002",
    ]
    assert arxiv_client.html_title_searches == ["Retained DOI Reference"]
    assert arxiv_client.api_title_searches == ["Retained DOI Reference"]
    assert result.references.resolved == 1
    assert result.references.skipped == [
        {
            "title": "Retained DOI Reference",
            "github_url": None,
            "detail_url": "https://doi.org/10.1145/example",
            "reason": "No valid arXiv URL found",
        }
    ]
    assert result.citations.resolved == 0
    assert result.citations.skipped == [
        {
            "title": "Citation With Missing Stars",
            "github_url": "https://github.com/foo/citation",
            "detail_url": "https://arxiv.org/abs/2502.00002",
            "reason": "GitHub API error (503)",
        }
    ]


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_fails_when_arxiv_title_lookup_fails():
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            return None, "metadata lookup timeout"

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            raise AssertionError("OpenAlex search should not run when title lookup fails")

    with pytest.raises(ValueError, match="Failed to resolve arXiv title: metadata lookup timeout"):
        await export_arxiv_relations_to_csv(
            "https://arxiv.org/abs/2603.23502",
            arxiv_client=FakeArxivClient(),
            openalex_client=FakeOpenAlexClient(),
            discovery_client=object(),
            github_client=object(),
        )


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_fails_when_no_openalex_work_found():
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            return "Target Paper", None

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            return None

    with pytest.raises(ValueError, match="No OpenAlex work found for title: Target Paper"):
        await export_arxiv_relations_to_csv(
            "https://arxiv.org/abs/2603.23502",
            arxiv_client=FakeArxivClient(),
            openalex_client=FakeOpenAlexClient(),
            discovery_client=object(),
            github_client=object(),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exc_type", "message"),
    [
        (ValueError, "Invalid single-paper arXiv URL: bad-input"),
        (RuntimeError, "OpenAlex API error (503)"),
        (aiohttp.ClientError, "connection reset by peer"),
    ],
)
async def test_run_arxiv_relations_mode_prints_concise_stderr_and_returns_nonzero_on_expected_errors(
    monkeypatch, capsys, exc_type, message
):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeOpenAlexClient:
        def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(
            self,
            session,
            *,
            huggingface_token="",
            repo_cache=None,
            hf_exact_no_repo_recheck_days=0,
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    async def fake_export(*args, **kwargs):
        raise exc_type(message)

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    exit_code = await run_arxiv_relations_mode(
        "https://arxiv.org/abs/2603.23502",
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        openalex_client_cls=FakeOpenAlexClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == f"ArXiv relation export failed: {message}"


@pytest.mark.anyio
async def test_run_arxiv_relations_mode_returns_nonzero_on_unexpected_hard_failure(monkeypatch, capsys):
    class HardFailure(Exception):
        pass

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeOpenAlexClient:
        def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(
            self,
            session,
            *,
            huggingface_token="",
            repo_cache=None,
            hf_exact_no_repo_recheck_days=0,
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    async def fake_export(*args, **kwargs):
        raise HardFailure("unhandled export branch")

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    exit_code = await run_arxiv_relations_mode(
        "https://arxiv.org/abs/2603.23502",
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        openalex_client_cls=FakeOpenAlexClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == "ArXiv relation export failed: unhandled export branch"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure_stage", "message"),
    [
        ("runtime", "runtime setup exploded"),
        ("build", "arxiv client construction exploded"),
    ],
)
async def test_run_arxiv_relations_mode_returns_nonzero_on_pre_export_setup_failures(
    monkeypatch, capsys, failure_stage, message
):
    async def fake_export(*args, **kwargs):
        raise AssertionError("export should not run when setup fails")

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    if failure_stage == "runtime":

        @asynccontextmanager
        async def fake_open_runtime_clients(*args, **kwargs):
            raise RuntimeError(message)
            yield

        monkeypatch.setattr("src.arxiv_relations.runner.open_runtime_clients", fake_open_runtime_clients)

        exit_code = await run_arxiv_relations_mode("https://arxiv.org/abs/2603.23502")
    else:

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FailingArxivClient:
            def __init__(self, session, *, max_concurrent=0, min_interval=0):
                raise RuntimeError(message)

        class FakeOpenAlexClient:
            def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
                self.session = session

        class FakeDiscoveryClient:
            def __init__(
                self,
                session,
                *,
                huggingface_token="",
                repo_cache=None,
                hf_exact_no_repo_recheck_days=0,
                max_concurrent=0,
                min_interval=0,
            ):
                self.session = session

        class FakeGitHubClient:
            def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
                self.session = session

        exit_code = await run_arxiv_relations_mode(
            "https://arxiv.org/abs/2603.23502",
            session_factory=lambda **kwargs: FakeSession(),
            arxiv_client_cls=FailingArxivClient,
            openalex_client_cls=FakeOpenAlexClient,
            discovery_client_cls=FakeDiscoveryClient,
            github_client_cls=FakeGitHubClient,
        )

    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == f"ArXiv relation export failed: {message}"


@pytest.mark.anyio
async def test_run_arxiv_relations_mode_successfully_wires_clients_callbacks_and_summary_output(
    tmp_path: Path, monkeypatch, capsys
):
    from src.arxiv_relations.pipeline import NormalizationStrength, NormalizedRelatedRow

    monkeypatch.setenv("ALPHAXIV_TOKEN", "ax_token")
    references_csv_path = tmp_path / "arxiv-2603.23502-references-20260326113045.csv"
    citations_csv_path = tmp_path / "arxiv-2603.23502-citations-20260326113045.csv"
    constructed = {}
    export_calls = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            self.max_concurrent = max_concurrent
            self.min_interval = min_interval
            constructed["arxiv_client"] = self

    class FakeOpenAlexClient:
        def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
            self.session = session
            self.openalex_api_key = openalex_api_key
            self.max_concurrent = max_concurrent
            self.min_interval = min_interval
            constructed["openalex_client"] = self

    class FakeCrossrefClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            constructed["crossref_client"] = self

    class FakeDataCiteClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            constructed["datacite_client"] = self

    class FakeDiscoveryClient:
        def __init__(
            self,
            session,
            *,
            huggingface_token="",
            repo_cache=None,
            hf_exact_no_repo_recheck_days=0,
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session
            constructed["discovery_client"] = self

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            constructed["github_client"] = self

    class FakeContentClient:
        def __init__(self, session, *, alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.alphaxiv_token = alphaxiv_token
            constructed["content_client"] = self

    async def fake_export(
        arxiv_input: str,
        *,
        output_dir: Path | None = None,
        arxiv_client,
        openalex_client,
        crossref_client,
        datacite_client,
        discovery_client,
        github_client,
        semanticscholar_graph_client,
        content_cache,
        relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days,
        status_callback=None,
        normalization_progress_callback=None,
        progress_callback=None,
    ):
        export_calls.append(
            {
                "arxiv_input": arxiv_input,
                "output_dir": output_dir,
                "arxiv_client": arxiv_client,
                "openalex_client": openalex_client,
                "crossref_client": crossref_client,
                "datacite_client": datacite_client,
                "discovery_client": discovery_client,
                "github_client": github_client,
                "semanticscholar_graph_client": semanticscholar_graph_client,
                "content_cache": content_cache,
                "relation_resolution_cache": relation_resolution_cache,
                "arxiv_relation_no_arxiv_recheck_days": arxiv_relation_no_arxiv_recheck_days,
                "status_callback": status_callback,
                "normalization_progress_callback": normalization_progress_callback,
                "progress_callback": progress_callback,
            }
        )
        assert callable(status_callback)
        assert callable(normalization_progress_callback)
        assert callable(progress_callback)

        status_callback("Starting relation export")
        normalization_progress_callback(
            SimpleNamespace(
                index=1,
                row=NormalizedRelatedRow(
                    title="Direct Reference",
                    url="https://arxiv.org/abs/2501.00001",
                    strength=NormalizationStrength.DIRECT_ARXIV,
                    resolution_source="direct_arxiv_url",
                ),
            ),
            2,
        )
        normalization_progress_callback(
            SimpleNamespace(
                index=2,
                row=NormalizedRelatedRow(
                    title="Retained DOI Reference",
                    url="https://doi.org/10.1145/example",
                    strength=NormalizationStrength.RETAINED_NON_ARXIV,
                    resolution_source="relation_resolution_cache_negative",
                ),
            ),
            2,
        )
        normalization_progress_callback(
            SimpleNamespace(
                index=1,
                row=NormalizedRelatedRow(
                    title="Citation Paper",
                    url="https://arxiv.org/abs/2502.00001",
                    strength=NormalizationStrength.TITLE_SEARCH,
                    resolution_source="title_search",
                ),
            ),
            1,
        )
        progress_callback(
            SimpleNamespace(
                index=1,
                record=PaperRecord(
                    name="Reference Paper",
                    url="https://arxiv.org/abs/2501.00001",
                    github="https://github.com/foo/bar",
                    stars=12,
                ),
                reason=None,
                current_stars=10,
            ),
            1,
        )

        return ArxivRelationsExportResult(
            arxiv_url="https://arxiv.org/abs/2603.23502",
            title="Target Paper",
            references=ConversionResult(csv_path=references_csv_path, resolved=1, skipped=[]),
            citations=ConversionResult(csv_path=citations_csv_path, resolved=2, skipped=[]),
        )

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    exit_code = await run_arxiv_relations_mode(
        "https://arxiv.org/abs/2603.23502",
        output_dir=tmp_path,
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        openalex_client_cls=FakeOpenAlexClient,
        crossref_client_cls=FakeCrossrefClient,
        datacite_client_cls=FakeDataCiteClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        content_client_cls=FakeContentClient,
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert len(export_calls) == 1
    assert export_calls[0]["arxiv_input"] == "https://arxiv.org/abs/2603.23502"
    assert export_calls[0]["output_dir"] == tmp_path
    assert export_calls[0]["arxiv_client"] is constructed["arxiv_client"]
    assert export_calls[0]["openalex_client"] is constructed["openalex_client"]
    assert export_calls[0]["crossref_client"] is constructed["crossref_client"]
    assert export_calls[0]["datacite_client"] is constructed["datacite_client"]
    assert export_calls[0]["discovery_client"] is constructed["discovery_client"]
    assert export_calls[0]["github_client"] is constructed["github_client"]
    assert export_calls[0]["semanticscholar_graph_client"] is not None
    assert export_calls[0]["content_cache"] is not None
    assert export_calls[0]["content_cache"].content_client is constructed["content_client"]
    assert constructed["content_client"].alphaxiv_token == "ax_token"
    assert export_calls[0]["relation_resolution_cache"] is not None
    assert export_calls[0]["arxiv_relation_no_arxiv_recheck_days"] == 30
    assert "Starting relation export" in captured.out
    assert "[1/2] Direct Reference" in captured.out
    assert "Source: Direct arXiv URL" in captured.out
    assert "Url set to: https://arxiv.org/abs/2501.00001" in captured.out
    assert "[2/2] Retained DOI Reference" in captured.out
    assert "Source: Resolution cache negative" in captured.out
    assert "Retained: https://doi.org/10.1145/example" in captured.out
    assert "[1/1] Citation Paper" in captured.out
    assert "Source: Title search" in captured.out
    assert "Url set to: https://arxiv.org/abs/2502.00001" in captured.out
    assert "[1/1] Reference Paper" in captured.out
    assert "foo/bar" in captured.out
    assert "Updated: 10 → 12" in captured.out
    assert "References resolved: 1" in captured.out
    assert "Citations resolved: 2" in captured.out
    assert f"Wrote references CSV: {references_csv_path}" in captured.out
    assert f"Wrote citations CSV: {citations_csv_path}" in captured.out


@pytest.mark.anyio
async def test_run_arxiv_relations_mode_wires_semantic_scholar_graph_client_from_runtime_config(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "ss_key")

    references_csv_path = tmp_path / "arxiv-2603.23502-references-20260326113045.csv"
    citations_csv_path = tmp_path / "arxiv-2603.23502-citations-20260326113045.csv"
    constructed = {}
    export_calls = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeOpenAlexClient:
        def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeCrossrefClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDataCiteClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(
            self,
            session,
            *,
            huggingface_token="",
            repo_cache=None,
            hf_exact_no_repo_recheck_days=0,
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeContentClient:
        def __init__(self, session, *, alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeSemanticScholarGraphClient:
        def __init__(
            self,
            session,
            *,
            semantic_scholar_api_key="",
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session
            self.semantic_scholar_api_key = semantic_scholar_api_key
            self.max_concurrent = max_concurrent
            self.min_interval = min_interval
            constructed["semantic_scholar_graph_client"] = self

    async def fake_export(
        arxiv_input: str,
        *,
        semanticscholar_graph_client=None,
        **kwargs,
    ):
        export_calls.append(
            {
                "arxiv_input": arxiv_input,
                "semanticscholar_graph_client": semanticscholar_graph_client,
            }
        )
        return ArxivRelationsExportResult(
            arxiv_url="https://arxiv.org/abs/2603.23502",
            title="Target Paper",
            references=ConversionResult(csv_path=references_csv_path, resolved=0, skipped=[]),
            citations=ConversionResult(csv_path=citations_csv_path, resolved=0, skipped=[]),
        )

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    exit_code = await run_arxiv_relations_mode(
        "https://arxiv.org/abs/2603.23502",
        output_dir=tmp_path,
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        openalex_client_cls=FakeOpenAlexClient,
        crossref_client_cls=FakeCrossrefClient,
        datacite_client_cls=FakeDataCiteClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        content_client_cls=FakeContentClient,
        semanticscholar_graph_client_cls=FakeSemanticScholarGraphClient,
    )

    assert exit_code == 0
    assert len(export_calls) == 1
    assert export_calls[0]["arxiv_input"] == "https://arxiv.org/abs/2603.23502"
    assert (
        export_calls[0]["semanticscholar_graph_client"]
        is constructed["semantic_scholar_graph_client"]
    )
    assert (
        constructed["semantic_scholar_graph_client"].semantic_scholar_api_key
        == "ss_key"
    )
    assert constructed["semantic_scholar_graph_client"].min_interval == 1.0


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_threads_metadata_clients_to_shared_export(monkeypatch, tmp_path: Path):
    export_calls = []
    normalize_calls = []

    class FakeArxivClient:
        async def get_title(self, arxiv_url: str):
            assert arxiv_url == "https://arxiv.org/abs/2603.23502"
            return "Target Paper", None

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            assert title == "Target Paper"
            return {"id": "https://openalex.org/W1", "display_name": title}

        async def fetch_referenced_works(self, work: dict):
            return []

        async def fetch_citations(self, work: dict):
            return []

    async def fake_normalize_related_work_candidates_to_seeds(*args, **kwargs):
        normalize_calls.append(kwargs)
        return [PaperSeed(name="Mapped Related", url="https://doi.org/10.1145/example")]

    async def fake_export_paper_seeds_to_csv(
        seeds,
        csv_path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append(
            {
                "seeds": seeds,
                "csv_path": csv_path,
                "arxiv_client": arxiv_client,
                "crossref_client": crossref_client,
                "datacite_client": datacite_client,
                "relation_resolution_cache": relation_resolution_cache,
                "arxiv_relation_no_arxiv_recheck_days": arxiv_relation_no_arxiv_recheck_days,
            }
        )
        return ConversionResult(csv_path=csv_path, resolved=0, skipped=[])

    monkeypatch.setattr(
        "src.arxiv_relations.pipeline.normalize_related_work_candidates_to_seeds",
        fake_normalize_related_work_candidates_to_seeds,
    )
    monkeypatch.setattr(
        "src.arxiv_relations.pipeline.export_paper_seeds_to_csv",
        fake_export_paper_seeds_to_csv,
    )

    crossref_client = SimpleNamespace(name="crossref")
    datacite_client = SimpleNamespace(name="datacite")
    relation_resolution_cache = SimpleNamespace(name="relation-cache")
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=FakeArxivClient(),
        openalex_client=FakeOpenAlexClient(),
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=17,
        output_dir=tmp_path,
    )

    assert result.references.csv_path.parent == tmp_path
    assert len(normalize_calls) == 2
    assert normalize_calls[0]["crossref_client"] is crossref_client
    assert normalize_calls[0]["datacite_client"] is datacite_client
    assert normalize_calls[1]["crossref_client"] is crossref_client
    assert normalize_calls[1]["datacite_client"] is datacite_client
    assert len(export_calls) == 2
    assert export_calls[0]["arxiv_client"] is not None
    assert export_calls[0]["crossref_client"] is crossref_client
    assert export_calls[0]["datacite_client"] is datacite_client
    assert export_calls[0]["relation_resolution_cache"] is relation_resolution_cache
    assert export_calls[0]["arxiv_relation_no_arxiv_recheck_days"] == 17
    assert export_calls[1]["arxiv_client"] is not None
    assert export_calls[1]["crossref_client"] is crossref_client
    assert export_calls[1]["datacite_client"] is datacite_client
    assert export_calls[1]["relation_resolution_cache"] is relation_resolution_cache
    assert export_calls[1]["arxiv_relation_no_arxiv_recheck_days"] == 17
