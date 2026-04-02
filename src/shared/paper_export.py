from dataclasses import replace
from pathlib import Path

from src.shared.async_batch import iter_bounded_as_completed, resolve_worker_count
from src.shared.csv_io import write_rows_to_csv_path
from src.shared.csv_rows import CsvRow
from src.shared.paper_enrichment import PaperEnrichmentRequest, process_single_paper
from src.shared.papers import ConversionResult, PaperOutcome, PaperSeed, sort_paper_export_rows


async def build_paper_outcome(
    index: int,
    seed: PaperSeed,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> PaperOutcome:
    enrichment = await process_single_paper(
        PaperEnrichmentRequest(
            title=seed.name,
            raw_url=seed.url,
            existing_github_url=None,
            allow_title_search=True,
            allow_github_discovery=True,
            precomputed_normalized_url=seed.url if seed.url_resolution_authoritative else None,
            precomputed_canonical_arxiv_url=seed.canonical_arxiv_url,
            url_resolution_authoritative=seed.url_resolution_authoritative,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )

    return PaperOutcome(
        index=index,
        record=CsvRow(
            name=enrichment.title,
            url=enrichment.normalized_url or enrichment.raw_url or "",
            github=enrichment.github_url or "",
            stars=enrichment.stars if enrichment.reason is None else "",
            created=enrichment.created or "",
            about=enrichment.about or "",
            sort_index=index,
        ),
        reason=enrichment.reason,
    )


async def export_paper_seeds_to_csv(
    seeds: list[PaperSeed],
    csv_path: Path,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    status_callback=None,
    progress_callback=None,
) -> ConversionResult:
    total = len(seeds)
    worker_count = resolve_worker_count(discovery_client, github_client)
    if callable(status_callback):
        status_callback(f"📝 Found {total} papers")
        status_callback(f"🔄 Starting concurrent enrichment ({worker_count} workers)")

    async def build_seed_outcome(item: tuple[int, PaperSeed]) -> PaperOutcome:
        index, seed = item
        return await build_paper_outcome(
            index,
            seed,
            discovery_client=discovery_client,
            github_client=github_client,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            content_cache=content_cache,
            relation_resolution_cache=relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        )

    rows = []
    resolved = 0
    skipped = []
    async for outcome in iter_bounded_as_completed(
        enumerate(seeds, 1),
        build_seed_outcome,
        max_concurrent=worker_count,
    ):
        rows.append(outcome.record)
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

    ordered_rows = [replace(row, sort_index=0) for row in sort_paper_export_rows(rows)]

    return ConversionResult(
        csv_path=write_rows_to_csv_path(ordered_rows, csv_path),
        resolved=resolved,
        skipped=skipped,
    )
