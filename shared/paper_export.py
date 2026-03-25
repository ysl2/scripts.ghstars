import asyncio
from pathlib import Path

from shared.csv_io import write_records_to_csv_path
from shared.paper_enrichment import enrich_paper
from shared.papers import ConversionResult, PaperOutcome, PaperRecord, PaperSeed
from shared.settings import DEFAULT_CONCURRENT_LIMIT


async def build_paper_outcome(
    index: int,
    seed: PaperSeed,
    *,
    discovery_client,
    github_client,
) -> PaperOutcome:
    enrichment = await enrich_paper(
        name=seed.name,
        url=seed.url,
        discovery_client=discovery_client,
        github_client=github_client,
    )

    return PaperOutcome(
        index=index,
        record=PaperRecord(
            name=enrichment.name,
            url=enrichment.url,
            github=enrichment.github_url or "",
            stars=enrichment.stars if enrichment.reason is None else "",
        ),
        reason=enrichment.reason,
    )


async def export_paper_seeds_to_csv(
    seeds: list[PaperSeed],
    csv_path: Path,
    *,
    discovery_client,
    github_client,
    status_callback=None,
    progress_callback=None,
) -> ConversionResult:
    total = len(seeds)
    if callable(status_callback):
        status_callback(f"📝 Found {total} papers")
        status_callback(
            f"🔄 Starting concurrent enrichment ({_resolve_worker_count(discovery_client, github_client)} workers)"
        )

    tasks = [
        asyncio.create_task(
            build_paper_outcome(
                index,
                seed,
                discovery_client=discovery_client,
                github_client=github_client,
            )
        )
        for index, seed in enumerate(seeds, 1)
    ]

    records = []
    resolved = 0
    skipped = []
    for task in asyncio.as_completed(tasks):
        outcome = await task
        records.append(outcome.record)
        if outcome.reason is None:
            resolved += 1
        else:
            skipped.append(
                {
                    "title": outcome.record.name,
                    "github_url": outcome.record.github or None,
                    "detail_url": outcome.record.url,
                    "reason": outcome.reason,
                }
            )
        if callable(progress_callback):
            progress_callback(outcome, total)

    return ConversionResult(
        csv_path=write_records_to_csv_path(records, csv_path),
        resolved=resolved,
        skipped=skipped,
    )


def _resolve_worker_count(discovery_client, github_client) -> int:
    for client in (discovery_client, github_client):
        semaphore = getattr(client, "semaphore", None)
        value = getattr(semaphore, "_value", None)
        if isinstance(value, int) and value > 0:
            return value
    return DEFAULT_CONCURRENT_LIMIT
