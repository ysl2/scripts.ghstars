from dataclasses import dataclass
from pathlib import Path

from src.shared.paper_export import export_paper_seeds_to_csv
from src.shared.paper_identity import build_arxiv_abs_url, extract_arxiv_id, extract_arxiv_id_from_single_paper_url
from src.shared.papers import ConversionResult, PaperSeed
from src.url_to_csv import filenames as url_export_filenames


@dataclass(frozen=True)
class ArxivRelationsExportResult:
    arxiv_url: str
    title: str
    references: ConversionResult
    citations: ConversionResult


@dataclass(frozen=True)
class NormalizedRelatedRow:
    title: str
    url: str


def normalize_single_arxiv_input(arxiv_input: str) -> str:
    arxiv_id = extract_arxiv_id_from_single_paper_url(arxiv_input)
    if not arxiv_id:
        raise ValueError(f"Invalid single-paper arXiv URL: {arxiv_input}")
    return build_arxiv_abs_url(arxiv_id)


def build_relations_csv_paths(
    arxiv_url: str,
    *,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    arxiv_id = extract_arxiv_id(arxiv_url)
    if not arxiv_id:
        raise ValueError(f"Invalid arXiv URL: {arxiv_url}")

    timestamp = url_export_filenames.current_run_timestamp()
    references_csv_path = url_export_filenames.build_url_export_csv_path(
        ["arxiv", arxiv_id, "references"],
        output_dir=output_dir,
        timestamp=timestamp,
    )
    citations_csv_path = url_export_filenames.build_url_export_csv_path(
        ["arxiv", arxiv_id, "citations"],
        output_dir=output_dir,
        timestamp=timestamp,
    )
    return references_csv_path, citations_csv_path


def _fallback_related_work_url(candidate) -> str:
    return candidate.doi_url or candidate.landing_page_url or candidate.openalex_url


async def _resolve_related_work_rows(candidates: list, *, arxiv_client) -> list[NormalizedRelatedRow]:
    rows: list[NormalizedRelatedRow] = []
    for candidate in candidates:
        if candidate.direct_arxiv_url:
            rows.append(
                NormalizedRelatedRow(
                    title=candidate.title or candidate.direct_arxiv_url,
                    url=candidate.direct_arxiv_url,
                )
            )
            continue

        matched_arxiv_id, _, _ = await arxiv_client.get_arxiv_id_by_title(candidate.title)
        if matched_arxiv_id:
            matched_title, _ = await arxiv_client.get_title(matched_arxiv_id)
            matched_url = build_arxiv_abs_url(matched_arxiv_id)
            rows.append(
                NormalizedRelatedRow(
                    title=matched_title or candidate.title or matched_url,
                    url=matched_url,
                )
            )
            continue

        fallback_url = _fallback_related_work_url(candidate)
        rows.append(
            NormalizedRelatedRow(
                title=candidate.title or fallback_url,
                url=fallback_url,
            )
        )

    return rows


def _dedupe_normalized_rows(rows: list[NormalizedRelatedRow]) -> list[NormalizedRelatedRow]:
    deduped_rows: list[NormalizedRelatedRow] = []
    seen_urls: set[str] = set()
    for row in rows:
        if row.url in seen_urls:
            continue
        seen_urls.add(row.url)
        deduped_rows.append(row)
    return deduped_rows


async def normalize_related_works_to_seeds(
    related_works: list[dict],
    *,
    openalex_client,
    arxiv_client,
) -> list[PaperSeed]:
    candidates = [openalex_client.build_related_work_candidate(work) for work in related_works]
    normalized_rows = await _resolve_related_work_rows(candidates, arxiv_client=arxiv_client)
    deduped_rows = _dedupe_normalized_rows(normalized_rows)
    return [PaperSeed(name=row.title, url=row.url) for row in deduped_rows]


async def export_arxiv_relations_to_csv(
    arxiv_input: str,
    *,
    arxiv_client,
    openalex_client,
    discovery_client,
    github_client,
    output_dir: Path | None = None,
    status_callback=None,
    progress_callback=None,
) -> ArxivRelationsExportResult:
    arxiv_url = normalize_single_arxiv_input(arxiv_input)
    if callable(status_callback):
        status_callback(f"🎯 Resolving arXiv paper: {arxiv_url}")

    title, error = await arxiv_client.get_title(arxiv_url)
    if error or not title:
        raise ValueError(f"Failed to resolve arXiv title: {error or 'No title found'}")
    if callable(status_callback):
        status_callback(f"📄 Resolved title: {title}")

    target_work = await openalex_client.search_first_work(title)
    if not target_work:
        raise ValueError(f"No OpenAlex work found for title: {title}")
    if callable(status_callback):
        status_callback("🔎 Fetching OpenAlex referenced works")
    referenced_works = await openalex_client.fetch_referenced_works(target_work)
    if callable(status_callback):
        status_callback(f"📚 Retrieved {len(referenced_works)} referenced works")
        status_callback("🔎 Fetching OpenAlex citations")
    citation_works = await openalex_client.fetch_citations(target_work)
    if callable(status_callback):
        status_callback(f"📚 Retrieved {len(citation_works)} citation works")

    reference_seeds = await normalize_related_works_to_seeds(
        referenced_works,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
    )
    citation_seeds = await normalize_related_works_to_seeds(
        citation_works,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
    )

    references_csv_path, citations_csv_path = build_relations_csv_paths(arxiv_url, output_dir=output_dir)

    references_result = await export_paper_seeds_to_csv(
        reference_seeds,
        references_csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )
    citations_result = await export_paper_seeds_to_csv(
        citation_seeds,
        citations_csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )

    return ArxivRelationsExportResult(
        arxiv_url=arxiv_url,
        title=title,
        references=references_result,
        citations=citations_result,
    )
