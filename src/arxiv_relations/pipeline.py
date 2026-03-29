import asyncio
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from src.arxiv_relations.title_resolution import resolve_related_work_title_to_arxiv
from src.shared.arxiv import normalize_title_for_matching
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


class NormalizationStrength(IntEnum):
    DIRECT_ARXIV = 0
    TITLE_SEARCH = 1
    RETAINED_NON_ARXIV = 2


@dataclass(frozen=True)
class NormalizedRelatedRow:
    title: str
    url: str
    strength: NormalizationStrength
    original_title: str = ""


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


def _build_retained_related_row(candidate) -> NormalizedRelatedRow:
    fallback_url = _fallback_related_work_url(candidate)
    original_title = candidate.title or fallback_url
    return NormalizedRelatedRow(
        title=original_title,
        url=fallback_url,
        strength=NormalizationStrength.RETAINED_NON_ARXIV,
        original_title=original_title,
    )


def _relation_cache_keys(candidate) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if candidate.openalex_url:
        keys.append(("openalex_work", candidate.openalex_url))
    if candidate.doi_url:
        keys.append(("doi", candidate.doi_url))
    return keys


async def _resolve_related_work_row(
    candidate,
    *,
    arxiv_client,
    openalex_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> NormalizedRelatedRow:
    if candidate.direct_arxiv_url:
        resolved_title = candidate.title or candidate.direct_arxiv_url
        return NormalizedRelatedRow(
            title=resolved_title,
            url=candidate.direct_arxiv_url,
            strength=NormalizationStrength.DIRECT_ARXIV,
            original_title=resolved_title,
        )

    cache_keys = _relation_cache_keys(candidate)
    cached_entries = (
        [relation_resolution_cache.get(key_type, key_value) for key_type, key_value in cache_keys]
        if relation_resolution_cache is not None
        else []
    )
    positive_entry = next((entry for entry in cached_entries if entry and entry.arxiv_url), None)
    if positive_entry is not None:
        cached_title = getattr(positive_entry, "resolved_title", None)
        if not cached_title:
            cached_title, _ = await arxiv_client.get_title(positive_entry.arxiv_url)
        original_title = candidate.title or cached_title or positive_entry.arxiv_url
        return NormalizedRelatedRow(
            title=cached_title or candidate.title or positive_entry.arxiv_url,
            url=positive_entry.arxiv_url,
            strength=NormalizationStrength.TITLE_SEARCH,
            original_title=original_title,
        )

    has_fresh_negative = any(
        entry is not None
        and entry.arxiv_url is None
        and relation_resolution_cache.is_negative_cache_fresh(
            entry.checked_at,
            arxiv_relation_no_arxiv_recheck_days,
        )
        for entry in cached_entries
    )
    if has_fresh_negative:
        return _build_retained_related_row(candidate)

    resolution = await resolve_related_work_title_to_arxiv(
        candidate.title,
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        openalex_work=(
            {
                "id": candidate.openalex_url,
                "display_name": candidate.title,
            }
            if candidate.openalex_url
            else None
        ),
        discovery_client=discovery_client,
    )
    if resolution.arxiv_url:
        if relation_resolution_cache is not None:
            for key_type, key_value in cache_keys:
                relation_resolution_cache.record_resolution(
                    key_type=key_type,
                    key_value=key_value,
                    arxiv_url=resolution.arxiv_url,
                    resolved_title=resolution.resolved_title,
                )
        resolved_title = resolution.resolved_title or candidate.title or resolution.arxiv_url
        original_title = candidate.title or resolution.resolved_title or resolution.arxiv_url
        return NormalizedRelatedRow(
            title=resolved_title,
            url=resolution.arxiv_url,
            strength=NormalizationStrength.TITLE_SEARCH,
            original_title=original_title,
        )

    if relation_resolution_cache is not None and resolution.negative_cacheable:
        for key_type, key_value in cache_keys:
            relation_resolution_cache.record_resolution(
                key_type=key_type,
                key_value=key_value,
                arxiv_url=None,
            )
    return _build_retained_related_row(candidate)


async def _resolve_related_work_rows(
    candidates: list,
    *,
    arxiv_client,
    openalex_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> list[NormalizedRelatedRow]:
    return await asyncio.gather(
        *[
            _resolve_related_work_row(
                candidate,
                arxiv_client=arxiv_client,
                openalex_client=openalex_client,
                discovery_client=discovery_client,
                relation_resolution_cache=relation_resolution_cache,
                arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
            )
            for candidate in candidates
        ]
    )


def _normalized_row_ordering(row: NormalizedRelatedRow) -> tuple[int, str, str, str, str]:
    original_title = row.original_title or row.title
    return (
        int(row.strength),
        normalize_title_for_matching(row.title),
        row.title,
        normalize_title_for_matching(original_title),
        original_title,
    )


def _dedupe_normalized_rows(rows: list[NormalizedRelatedRow]) -> list[NormalizedRelatedRow]:
    winners_by_url: dict[str, NormalizedRelatedRow] = {}
    for row in rows:
        current_winner = winners_by_url.get(row.url)
        if current_winner is None or _normalized_row_ordering(row) < _normalized_row_ordering(current_winner):
            winners_by_url[row.url] = row
    return list(winners_by_url.values())


async def normalize_related_works_to_seeds(
    related_works: list[dict],
    *,
    openalex_client,
    arxiv_client,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> list[PaperSeed]:
    candidates = [openalex_client.build_related_work_candidate(work) for work in related_works]
    normalized_rows = await _resolve_related_work_rows(
        candidates,
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    deduped_rows = _dedupe_normalized_rows(normalized_rows)
    return [PaperSeed(name=row.title, url=row.url) for row in deduped_rows]


async def export_arxiv_relations_to_csv(
    arxiv_input: str,
    *,
    arxiv_client,
    openalex_client,
    discovery_client,
    github_client,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
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
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    citation_seeds = await normalize_related_works_to_seeds(
        citation_works,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )

    references_csv_path, citations_csv_path = build_relations_csv_paths(arxiv_url, output_dir=output_dir)

    references_result = await export_paper_seeds_to_csv(
        reference_seeds,
        references_csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=content_cache,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )
    citations_result = await export_paper_seeds_to_csv(
        citation_seeds,
        citations_csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=content_cache,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )

    return ArxivRelationsExportResult(
        arxiv_url=arxiv_url,
        title=title,
        references=references_result,
        citations=citations_result,
    )
