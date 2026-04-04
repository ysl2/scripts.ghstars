from types import SimpleNamespace

import pytest

from src.shared.arxiv_url_resolution import ArxivUrlResolutionResult
from src.shared.papers import PaperSeed
from src.core.paper_seed_normalization import normalize_paper_seed_to_arxiv


@pytest.mark.anyio
async def test_normalize_paper_seed_to_arxiv_returns_authoritative_seed_for_doi(monkeypatch):
    async def fake_resolve_arxiv_url(*args, **kwargs):
        return ArxivUrlResolutionResult(
            resolved_url="https://arxiv.org/pdf/2501.12345v2.pdf",
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            resolved_title="Mapped Arxiv Title",
            source="semantic_scholar_exact_doi",
            script_derived=True,
        )

    monkeypatch.setattr("src.core.paper_seed_normalization.resolve_arxiv_url", fake_resolve_arxiv_url)

    result = await normalize_paper_seed_to_arxiv(
        PaperSeed(name="Published Paper", url="https://doi.org/10.1007/978-3-031-72933-1_9"),
        discovery_client=SimpleNamespace(),
        arxiv_client=SimpleNamespace(),
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.normalized_seed == PaperSeed(
        name="Published Paper",
        url="https://arxiv.org/pdf/2501.12345v2.pdf",
    )
    assert result.normalized_seed.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.normalized_seed.url_resolution_authoritative is True


@pytest.mark.anyio
async def test_normalize_paper_seed_to_arxiv_returns_none_when_unresolved(monkeypatch):
    async def fake_resolve_arxiv_url(*args, **kwargs):
        return ArxivUrlResolutionResult(
            resolved_url=None,
            canonical_arxiv_url=None,
            resolved_title=None,
            source="none",
            script_derived=False,
        )

    monkeypatch.setattr("src.core.paper_seed_normalization.resolve_arxiv_url", fake_resolve_arxiv_url)

    result = await normalize_paper_seed_to_arxiv(
        PaperSeed(name="Unresolved Paper", url="https://example.com/no-match"),
        discovery_client=SimpleNamespace(),
        arxiv_client=SimpleNamespace(),
    )

    assert result.normalized_seed is None
    assert result.canonical_arxiv_url is None
