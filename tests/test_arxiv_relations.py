import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from src.arxiv_relations.pipeline import ArxivRelationsExportResult
from src.shared.openalex import RelatedWorkCandidate
from src.shared.papers import ConversionResult, PaperSeed
from src.shared.papers import PaperRecord
from src.arxiv_relations.runner import run_arxiv_relations_mode


class FakeRelationResolutionCache:
    def __init__(self, entries: dict[tuple[str, str], object] | None = None):
        self.entries = entries or {}
        self.get_calls: list[tuple[str, str]] = []
        self.record_calls: list[tuple[str, str, str | None]] = []

    def get(self, key_type: str, key_value: str):
        self.get_calls.append((key_type, key_value))
        return self.entries.get((key_type, key_value))

    def record_resolution(self, *, key_type: str, key_value: str, arxiv_url: str | None) -> None:
        self.record_calls.append((key_type, key_value, arxiv_url))
        self.entries[(key_type, key_value)] = SimpleNamespace(
            key_type=key_type,
            key_value=key_value,
            arxiv_url=arxiv_url,
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
                    openalex_url="https://openalex.org/W1",
                ),
                "R2": RelatedWorkCandidate(
                    title="Original OpenAlex Title",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/mapped",
                    openalex_url="https://openalex.org/W2",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation normalization should use the arXiv API title search path")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            if title == "Original OpenAlex Title":
                return "2501.12345", "title_search_exact", None
            raise AssertionError(f"Unexpected title search: {title}")

        async def get_title(self, arxiv_identifier: str):
            if arxiv_identifier == "2501.12345":
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
async def test_normalize_related_works_uses_positive_cache_before_title_search():
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
                openalex_url="https://openalex.org/W9",
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
                return "Cached Arxiv Title", None
            raise AssertionError(f"Unexpected arXiv title lookup: {arxiv_identifier}")

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
                    openalex_url="https://openalex.org/W3",
                ),
                "R4": RelatedWorkCandidate(
                    title="With Landing",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/paper",
                    openalex_url="https://openalex.org/W4",
                ),
                "R5": RelatedWorkCandidate(
                    title="OpenAlex Only",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/W5",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation normalization should use the arXiv API title search path")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            return None, None, "No arXiv ID found from title search"

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
                openalex_url="https://openalex.org/W10",
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
                openalex_url="https://openalex.org/W10",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("HTML title search should not run in relation mode")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            return None, None, "arXiv metadata query timeout"

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
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            events.append(("arxiv_api_miss", title))
            return None, None, "No arXiv ID found from title search"

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
        def record_resolution(self, *, key_type: str, key_value: str, arxiv_url: str | None) -> None:
            events.append(("cache_record", key_type, key_value, arxiv_url))
            super().record_resolution(key_type=key_type, key_value=key_value, arxiv_url=arxiv_url)

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
        ("arxiv_api_miss", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting"),
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
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            events.append(("arxiv_api_429", title))
            return None, None, "arXiv metadata query error (429)"

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
        def record_resolution(self, *, key_type: str, key_value: str, arxiv_url: str | None) -> None:
            events.append(("cache_record", key_type, key_value, arxiv_url))
            super().record_resolution(key_type=key_type, key_value=key_value, arxiv_url=arxiv_url)

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
        ("arxiv_api_429", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting"),
        ("hf_search_json", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting", 1),
        ("title_lookup", "2312.00451"),
        ("cache_record", "openalex_work", "https://openalex.org/WFSGS", "https://arxiv.org/abs/2312.00451"),
        ("cache_record", "doi", "https://doi.org/10.1007/978-3-031-72933-1_9", "https://arxiv.org/abs/2312.00451"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_skips_hf_fallback_when_token_missing():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            return None, None, "No arXiv ID found from title search"

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
    assert cache.record_calls == []


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
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            return None, None, "No arXiv ID found from title search"

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
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            return None, None, "arXiv metadata query timeout"

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
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            return None, None, "No arXiv ID found from title search"

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
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            return None, None, "No arXiv ID found from title search"

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
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            return None, None, "No arXiv ID found from title search"

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
async def test_normalize_related_works_negative_caches_only_after_arxiv_and_hf_both_miss():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    events: list[tuple] = []

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting",
                direct_arxiv_url=None,
                doi_url="https://doi.org/10.1007/978-3-031-72933-1_9",
                landing_page_url="https://publisher.example/fsgs",
                openalex_url="https://openalex.org/WFSGS",
            )

    class FakeNoMatchArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation mode should not use legacy HTML title search")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            events.append(("arxiv_api_miss", title))
            return None, None, "No arXiv ID found from title search"

        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("No title lookup should run after a full miss")

    class FakeDiscoveryClient:
        huggingface_token = "hf-token"

        async def get_huggingface_paper_search_results(self, title: str, *, limit: int = 3):
            events.append(("hf_search_json_miss", title, limit))
            return [], None

    class TrackingRelationResolutionCache(FakeRelationResolutionCache):
        def record_resolution(self, *, key_type: str, key_value: str, arxiv_url: str | None) -> None:
            events.append(("cache_record", key_type, key_value, arxiv_url))
            super().record_resolution(key_type=key_type, key_value=key_value, arxiv_url=arxiv_url)

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
        ("arxiv_api_miss", "FSGS: Real-Time Few-Shot View Synthesis Using Gaussian Splatting"),
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
                openalex_url="https://openalex.org/W11",
            )

    class FakeArxivClient:
        def __init__(self):
            self.api_title_searches: list[str] = []
            self.title_lookups: list[str] = []

        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Legacy HTML title search should not run in the relation cache pipeline")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            self.api_title_searches.append(title)
            return "2312.00451", "title_search_exact", None

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
    assert arxiv_client.api_title_searches == ["Backfilled Paper"]
    assert arxiv_client.title_lookups == ["2312.00451"]
    assert cache.record_calls == [
        ("openalex_work", "https://openalex.org/W11", "https://arxiv.org/abs/2312.00451"),
        ("doi", "https://doi.org/10.1007/978-3-031-72933-1_9", "https://arxiv.org/abs/2312.00451"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_direct_arxiv_rows_bypass_cache_and_search_path():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class ExplodingRelationResolutionCache:
        def get(self, key_type: str, key_value: str):
            raise AssertionError("Direct arXiv rows should not consult the relation-resolution cache")

        def record_resolution(self, *, key_type: str, key_value: str, arxiv_url: str | None) -> None:
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
                openalex_url="https://openalex.org/W12",
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
                    openalex_url="https://openalex.org/W6",
                ),
                "R7": RelatedWorkCandidate(
                    title="Concurrent Paper B",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/b",
                    openalex_url="https://openalex.org/W7",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        def __init__(self):
            self.search_started: list[str] = []
            self.release_searches = asyncio.Event()

        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Relation normalization should use the arXiv API title search path")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            self.search_started.append(title)
            if len(self.search_started) == 2:
                self.release_searches.set()
            await self.release_searches.wait()

            mapping = {
                "Concurrent Paper A": ("2601.00001", "title_search_exact", None),
                "Concurrent Paper B": ("2601.00002", "title_search_exact", None),
            }
            return mapping[title]

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
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

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
            raise AssertionError("Relation export should use the arXiv API title search path")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            self.api_title_searches.append(title)
            if title == "Reference Needs Mapping":
                return "2501.00002", "title_search_exact", None
            raise AssertionError(f"Unexpected title search: {title}")

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
                    openalex_url="https://openalex.org/WR1",
                ),
                "R2": RelatedWorkCandidate(
                    title="Reference Needs Mapping",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/mapped",
                    openalex_url="https://openalex.org/WR2",
                ),
                "R3": RelatedWorkCandidate(
                    title="Publisher Reference",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1145/example",
                    landing_page_url="https://publisher.example/doi",
                    openalex_url="https://openalex.org/WR3",
                ),
                "C1": RelatedWorkCandidate(
                    title="Citation A",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00002",
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/WC1",
                ),
                "C2": RelatedWorkCandidate(
                    title="Citation A Duplicate",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00002",
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/WC2",
                ),
            }
            return mapping[work["id"]]

    arxiv_client = FakeArxivClient()
    openalex_client = FakeOpenAlexClient()
    discovery_client = object()
    github_client = object()
    export_calls = []
    statuses = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append(
            {
                "seeds": seeds,
                "csv_path": csv_path,
                "discovery_client": discovery_client,
                "github_client": github_client,
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
    )

    assert arxiv_client.calls == ["https://arxiv.org/abs/2603.23502", "2501.00002"]
    assert arxiv_client.api_title_searches == ["Reference Needs Mapping"]
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
    assert export_calls[1]["discovery_client"] is discovery_client
    assert export_calls[1]["github_client"] is github_client

    assert result.references.csv_path.name == "arxiv-2603.23502-references-20260326113045.csv"
    assert result.citations.csv_path.name == "arxiv-2603.23502-citations-20260326113045.csv"
    assert any("Fetching OpenAlex referenced works" in message for message in statuses)
    assert any("Fetching OpenAlex citations" in message for message in statuses)


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
            raise AssertionError("Relation export should not use the legacy HTML title search path")

        async def get_arxiv_id_by_title_from_api(self, title: str):
            self.api_title_searches.append(title)
            return None, None, "No arXiv ID found from title search"

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
                    openalex_url="https://openalex.org/WR1",
                ),
                "C1": RelatedWorkCandidate(
                    title="Citation Needs HF Mapping",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1007/978-3-031-72933-1_10",
                    landing_page_url="https://publisher.example/citation",
                    openalex_url="https://openalex.org/WC1",
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
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append({"seeds": seeds, "csv_path": csv_path})
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

    async def fake_export(
        arxiv_input: str,
        *,
        output_dir: Path | None = None,
        arxiv_client,
        openalex_client,
        discovery_client,
        github_client,
        relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append(
            {
                "arxiv_input": arxiv_input,
                "output_dir": output_dir,
                "arxiv_client": arxiv_client,
                "openalex_client": openalex_client,
                "discovery_client": discovery_client,
                "github_client": github_client,
                "relation_resolution_cache": relation_resolution_cache,
                "arxiv_relation_no_arxiv_recheck_days": arxiv_relation_no_arxiv_recheck_days,
                "status_callback": status_callback,
                "progress_callback": progress_callback,
            }
        )
        assert callable(status_callback)
        assert callable(progress_callback)

        status_callback("Starting relation export")
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
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert len(export_calls) == 1
    assert export_calls[0]["arxiv_input"] == "https://arxiv.org/abs/2603.23502"
    assert export_calls[0]["output_dir"] == tmp_path
    assert export_calls[0]["arxiv_client"] is constructed["arxiv_client"]
    assert export_calls[0]["openalex_client"] is constructed["openalex_client"]
    assert export_calls[0]["discovery_client"] is constructed["discovery_client"]
    assert export_calls[0]["github_client"] is constructed["github_client"]
    assert export_calls[0]["relation_resolution_cache"] is not None
    assert export_calls[0]["arxiv_relation_no_arxiv_recheck_days"] == 30
    assert "Starting relation export" in captured.out
    assert "[1/1] Reference Paper" in captured.out
    assert "foo/bar" in captured.out
    assert "Updated: 10 → 12" in captured.out
    assert "References resolved: 1" in captured.out
    assert "Citations resolved: 2" in captured.out
    assert f"Wrote references CSV: {references_csv_path}" in captured.out
    assert f"Wrote citations CSV: {citations_csv_path}" in captured.out
