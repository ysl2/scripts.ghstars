import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.shared.paper_export as paper_export
from src.shared.papers import PaperOutcome, PaperRecord, PaperSeed


@pytest.mark.anyio
async def test_export_paper_seeds_to_csv_limits_started_tasks_to_worker_count(tmp_path: Path, monkeypatch):
    release = asyncio.Event()
    started: list[int] = []

    async def fake_build_paper_outcome(index, seed, **kwargs):
        started.append(index)
        await release.wait()
        return PaperOutcome(
            index=index,
            record=PaperRecord(
                name=seed.name,
                url=seed.url,
                github="",
                stars="",
                sort_index=index,
            ),
            reason=None,
        )

    monkeypatch.setattr(paper_export, "build_paper_outcome", fake_build_paper_outcome)

    seeds = [PaperSeed(name=f"Paper {index}", url=f"https://arxiv.org/abs/2501.0000{index}") for index in range(5)]
    client = SimpleNamespace(semaphore=asyncio.Semaphore(2))
    export_task = asyncio.create_task(
        paper_export.export_paper_seeds_to_csv(
            seeds,
            tmp_path / "papers.csv",
            discovery_client=client,
            github_client=client,
        )
    )

    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == [1, 2]
    finally:
        release.set()

    result = await export_task
    assert result.resolved == 5


@pytest.mark.anyio
async def test_build_paper_outcome_threads_metadata_clients_to_process_single_paper(monkeypatch):
    received = {}

    async def fake_process_single_paper(request, **kwargs):
        received["arxiv_client"] = kwargs.get("arxiv_client")
        received["crossref_client"] = kwargs.get("crossref_client")
        received["datacite_client"] = kwargs.get("datacite_client")
        received["relation_resolution_cache"] = kwargs.get("relation_resolution_cache")
        received["arxiv_relation_no_arxiv_recheck_days"] = kwargs.get("arxiv_relation_no_arxiv_recheck_days")
        received["allow_title_search"] = request.allow_title_search
        return SimpleNamespace(
            title=request.title,
            raw_url=request.raw_url,
            normalized_url="https://arxiv.org/abs/2501.00001",
            canonical_arxiv_url="https://arxiv.org/abs/2501.00001",
            github_url="https://github.com/foo/bar",
            github_source="discovered",
            stars=12,
            reason=None,
        )

    monkeypatch.setattr(paper_export, "process_single_paper", fake_process_single_paper)

    arxiv_client = SimpleNamespace()
    crossref_client = SimpleNamespace()
    datacite_client = SimpleNamespace()
    relation_resolution_cache = SimpleNamespace(name="relation-cache")
    outcome = await paper_export.build_paper_outcome(
        1,
        PaperSeed(name="Paper A", url="https://doi.org/10.1145/example"),
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=17,
    )

    assert outcome.record.url == "https://arxiv.org/abs/2501.00001"
    assert received["arxiv_client"] is arxiv_client
    assert received["crossref_client"] is crossref_client
    assert received["datacite_client"] is datacite_client
    assert received["relation_resolution_cache"] is relation_resolution_cache
    assert received["arxiv_relation_no_arxiv_recheck_days"] == 17
    assert received["allow_title_search"] is True


@pytest.mark.anyio
async def test_build_paper_outcome_uses_arxiv_title_api_after_openalex_exact_miss():
    arxiv_client = SimpleNamespace(
        get_arxiv_match_by_title_from_api=AsyncMock(
            return_value=("2501.54321", "Paper A On arXiv", "title_search_exact", None)
        )
    )
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(return_value=(None, "Paper A"))
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            assert seed.url == "https://arxiv.org/abs/2501.54321"
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 12, None

    outcome = await paper_export.build_paper_outcome(
        1,
        PaperSeed(name="Paper A", url="https://doi.org/10.1145/example"),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
    )

    assert outcome.reason is None
    assert outcome.record.url == "https://arxiv.org/abs/2501.54321"
    assert outcome.record.github == "https://github.com/foo/bar"
    assert outcome.record.stars == 12
    arxiv_client.get_arxiv_match_by_title_from_api.assert_awaited_once_with("Paper A")
    crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()
