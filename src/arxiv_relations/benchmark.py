import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from src.arxiv_relations.pipeline import normalize_related_works_to_rows, normalize_single_arxiv_input
from src.shared.arxiv import ArxivClient
from src.shared.crossref import CrossrefClient
from src.shared.datacite import DataCiteClient
from src.shared.discovery import DiscoveryClient
from src.shared.openalex import OpenAlexClient
from src.shared.runtime import build_client, load_runtime_config, open_runtime_clients
from src.url_to_csv import filenames as url_export_filenames


STAGE_ORDER = [
    "direct_arxiv_url",
    "relation_resolution_cache",
    "openalex_exact_openalex_work",
    "openalex_exact_doi",
    "title_search",
    "openalex_preprint_openalex_work",
    "openalex_preprint_doi",
    "crossref",
    "datacite",
    "huggingface_title_search",
    "relation_resolution_cache_negative",
    "unresolved",
]


@dataclass(frozen=True)
class ArxivRelationsBenchmarkResult:
    arxiv_url: str
    title: str
    detail_csv_path: Path
    summary_json_path: Path
    rows_by_kind: dict[str, list]
    summary: dict[str, dict[str, int]]


def build_resolution_stage_summary(rows_by_kind: dict[str, list]) -> dict[str, dict[str, int]]:
    extra_stages = sorted(
        {
            row.resolution_source
            for rows in rows_by_kind.values()
            for row in rows
            if row.resolution_source and row.resolution_source not in STAGE_ORDER
        }
    )
    stage_order = [*STAGE_ORDER, *extra_stages]

    summary: dict[str, dict[str, int]] = {}
    overall_counts = {stage: 0 for stage in stage_order}
    for kind, rows in rows_by_kind.items():
        counts = {stage: 0 for stage in stage_order}
        for row in rows:
            stage = row.resolution_source or "unresolved"
            if stage not in counts:
                counts[stage] = 0
                overall_counts[stage] = overall_counts.get(stage, 0)
            counts[stage] += 1
            overall_counts[stage] += 1
        summary[kind] = counts

    summary["overall"] = {stage: overall_counts.get(stage, 0) for stage in stage_order}
    return summary


def build_resolution_detail_records(rows_by_kind: dict[str, list]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for kind, rows in rows_by_kind.items():
        for row in rows:
            records.append(
                {
                    "Category": kind,
                    "Title": row.title,
                    "OriginalTitle": row.original_title or row.title,
                    "InputUrl": row.input_url or row.url,
                    "ResolvedUrl": row.url,
                    "Stage": row.resolution_source or "unresolved",
                }
            )
    return records


def build_benchmark_output_paths(
    arxiv_url: str,
    *,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    arxiv_id = normalize_single_arxiv_input(arxiv_url).rsplit("/", 1)[-1]
    timestamp = url_export_filenames.current_run_timestamp()
    directory = Path(output_dir) if output_dir is not None else Path("output")
    detail_csv_path = directory / f"arxiv-{arxiv_id}-resolution-benchmark-{timestamp}.csv"
    summary_json_path = directory / f"arxiv-{arxiv_id}-resolution-benchmark-{timestamp}.json"
    return detail_csv_path, summary_json_path


async def run_arxiv_relations_benchmark(
    arxiv_input: str,
    *,
    output_dir: Path | None = None,
    session_factory=aiohttp.ClientSession,
    arxiv_client_cls=ArxivClient,
    openalex_client_cls=OpenAlexClient,
    crossref_client_cls=CrossrefClient,
    datacite_client_cls=DataCiteClient,
    discovery_client_cls=DiscoveryClient,
    use_relation_resolution_cache: bool = False,
) -> ArxivRelationsBenchmarkResult:
    config = load_runtime_config(dict(os.environ))
    async with open_runtime_clients(
        config,
        session_factory=session_factory,
        discovery_client_cls=discovery_client_cls,
        github_client_cls=lambda *args, **kwargs: None,
        concurrent_limit=4,
        request_delay=0.2,
        enable_relation_resolution_cache=use_relation_resolution_cache,
    ) as runtime:
        arxiv_client = build_client(arxiv_client_cls, runtime.session, max_concurrent=4, min_interval=0.2)
        openalex_client = build_client(
            openalex_client_cls,
            runtime.session,
            openalex_api_key=config["openalex_api_key"],
            max_concurrent=4,
            min_interval=0.2,
        )
        crossref_client = build_client(crossref_client_cls, runtime.session, max_concurrent=4, min_interval=0.2)
        datacite_client = build_client(datacite_client_cls, runtime.session, max_concurrent=4, min_interval=0.2)

        arxiv_url = normalize_single_arxiv_input(arxiv_input)
        title, error = await arxiv_client.get_title(arxiv_url)
        if error or not title:
            raise ValueError(f"Failed to resolve arXiv title: {error or 'No title found'}")

        target_work = await openalex_client.search_first_work(title)
        if not target_work:
            raise ValueError(f"No OpenAlex work found for title: {title}")

        referenced_works = await openalex_client.fetch_referenced_works(target_work)
        citation_works = await openalex_client.fetch_citations(target_work)
        rows_by_kind = {
            "references": await normalize_related_works_to_rows(
                referenced_works,
                openalex_client=openalex_client,
                arxiv_client=arxiv_client,
                crossref_client=crossref_client,
                datacite_client=datacite_client,
                discovery_client=runtime.discovery_client,
                relation_resolution_cache=runtime.relation_resolution_cache,
                arxiv_relation_no_arxiv_recheck_days=config["arxiv_relation_no_arxiv_recheck_days"],
            ),
            "citations": await normalize_related_works_to_rows(
                citation_works,
                openalex_client=openalex_client,
                arxiv_client=arxiv_client,
                crossref_client=crossref_client,
                datacite_client=datacite_client,
                discovery_client=runtime.discovery_client,
                relation_resolution_cache=runtime.relation_resolution_cache,
                arxiv_relation_no_arxiv_recheck_days=config["arxiv_relation_no_arxiv_recheck_days"],
            ),
        }

    summary = build_resolution_stage_summary(rows_by_kind)
    detail_csv_path, summary_json_path = build_benchmark_output_paths(arxiv_input, output_dir=output_dir)
    detail_csv_path.parent.mkdir(parents=True, exist_ok=True)

    records = build_resolution_detail_records(rows_by_kind)
    with detail_csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Category", "Title", "OriginalTitle", "InputUrl", "ResolvedUrl", "Stage"])
        writer.writeheader()
        writer.writerows(records)

    with summary_json_path.open("w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=False)
        handle.write("\n")

    return ArxivRelationsBenchmarkResult(
        arxiv_url=normalize_single_arxiv_input(arxiv_input),
        title=title,
        detail_csv_path=detail_csv_path,
        summary_json_path=summary_json_path,
        rows_by_kind=rows_by_kind,
        summary=summary,
    )


async def async_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Expected exactly 1 arXiv single-paper URL argument", file=sys.stderr)
        return 2

    try:
        result = await run_arxiv_relations_benchmark(args[0])
    except Exception as exc:
        print(f"ArXiv resolution benchmark failed: {exc}", file=sys.stderr)
        return 1

    print(f"Benchmark target: {result.arxiv_url}")
    print(f"Resolved title: {result.title}")
    print("Stage hits:")
    for stage, count in result.summary["overall"].items():
        print(f"  {stage}: {count}")
    print(f"Wrote detail CSV: {result.detail_csv_path}")
    print(f"Wrote summary JSON: {result.summary_json_path}")
    return 0
