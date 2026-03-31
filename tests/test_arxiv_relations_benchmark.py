from types import SimpleNamespace

import pytest

from src.shared.openalex import RelatedWorkCandidate


@pytest.mark.anyio
async def test_normalize_related_works_to_rows_preserves_resolution_sources():
    from src.arxiv_relations.pipeline import normalize_related_works_to_rows

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            mapping = {
                "direct": RelatedWorkCandidate(
                    title="Direct Row",
                    direct_arxiv_url="https://arxiv.org/abs/2401.00001",
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/W1",
                ),
                "mapped": RelatedWorkCandidate(
                    title="Mapped Row",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1145/example",
                    landing_page_url="https://publisher.example/mapped",
                    openalex_url="https://openalex.org/W2",
                ),
                "retained": RelatedWorkCandidate(
                    title="Retained Row",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1145/unresolved",
                    landing_page_url="https://publisher.example/retained",
                    openalex_url="https://openalex.org/W3",
                ),
            }
            return mapping[work["id"]]

    async def fake_resolver(
        title: str,
        raw_url: str,
        **kwargs,
    ):
        if raw_url == "https://doi.org/10.1145/example":
            return SimpleNamespace(
                resolved_url="https://arxiv.org/abs/2501.12345",
                canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
                resolved_title="Mapped Arxiv Title",
                source="title_search",
                script_derived=True,
            )
        return SimpleNamespace(
            resolved_url=None,
            canonical_arxiv_url=None,
            resolved_title=None,
            source=None,
            script_derived=False,
        )

    rows = await normalize_related_works_to_rows(
        [{"id": "direct"}, {"id": "mapped"}, {"id": "retained"}],
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=SimpleNamespace(),
        resolve_arxiv_url_fn=fake_resolver,
    )

    assert [(row.title, row.resolution_source, row.input_url, row.url) for row in rows] == [
        (
            "Direct Row",
            "direct_arxiv_url",
            "https://arxiv.org/abs/2401.00001",
            "https://arxiv.org/abs/2401.00001",
        ),
        (
            "Mapped Arxiv Title",
            "title_search",
            "https://doi.org/10.1145/example",
            "https://arxiv.org/abs/2501.12345",
        ),
        (
            "Retained Row",
            "unresolved",
            "https://doi.org/10.1145/unresolved",
            "https://doi.org/10.1145/unresolved",
        ),
    ]


def test_build_resolution_stage_summary_counts_zero_hit_stages():
    from src.arxiv_relations.benchmark import STAGE_ORDER, build_resolution_stage_summary
    from src.arxiv_relations.pipeline import NormalizationStrength, NormalizedRelatedRow

    rows_by_kind = {
        "references": [
            NormalizedRelatedRow(
                title="Direct Row",
                url="https://arxiv.org/abs/2401.00001",
                input_url="https://arxiv.org/abs/2401.00001",
                strength=NormalizationStrength.DIRECT_ARXIV,
                resolution_source="direct_arxiv_url",
            ),
            NormalizedRelatedRow(
                title="Mapped Row",
                url="https://arxiv.org/abs/2501.12345",
                input_url="https://doi.org/10.1145/example",
                strength=NormalizationStrength.TITLE_SEARCH,
                resolution_source="title_search",
            ),
        ],
        "citations": [
            NormalizedRelatedRow(
                title="Retained Row",
                url="https://doi.org/10.1145/unresolved",
                input_url="https://doi.org/10.1145/unresolved",
                strength=NormalizationStrength.RETAINED_NON_ARXIV,
                resolution_source="unresolved",
            )
        ],
    }

    summary = build_resolution_stage_summary(rows_by_kind)

    assert list(summary["overall"].keys()) == STAGE_ORDER
    assert summary["references"]["direct_arxiv_url"] == 1
    assert summary["references"]["title_search"] == 1
    assert summary["citations"]["unresolved"] == 1
    assert summary["overall"]["openalex_exact_doi"] == 0
    assert summary["overall"]["huggingface_title_search"] == 0
