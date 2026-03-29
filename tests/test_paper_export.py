import asyncio
from pathlib import Path
from types import SimpleNamespace

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
