import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

import aiohttp

from src.shared.arxiv import normalize_title_for_matching
from src.shared.arxiv_url_resolution import resolve_arxiv_url
from src.shared.paper_export import export_paper_seeds_to_csv
from src.shared.paper_identity import (
    build_arxiv_abs_url,
    extract_arxiv_id,
    extract_arxiv_id_from_single_paper_url,
    normalize_doi_url,
)
from src.shared.papers import ConversionResult, PaperSeed
from src.shared.relation_candidates import RelatedWorkCandidate
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
    input_url: str = field(default="", compare=False)
    resolution_source: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class RelationNormalizationProgressOutcome:
    index: int
    row: NormalizedRelatedRow


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


def _build_arxiv_doi_url(arxiv_url: str) -> str | None:
    arxiv_id = extract_arxiv_id(arxiv_url)
    if not arxiv_id:
        return None
    return f"https://doi.org/10.48550/arxiv.{arxiv_id}"


def _extract_openalex_work_title(work: dict) -> str:
    return " ".join(str(work.get("display_name") or work.get("title") or "").split()).strip()


def _matches_target_openalex_work(work: dict, *, arxiv_url: str, title: str, expected_doi: str | None) -> bool:
    candidate_doi = normalize_doi_url(str(work.get("doi") or ""))
    if expected_doi and candidate_doi == expected_doi:
        return True

    ids = work.get("ids") or {}
    candidate_arxiv_id = str(ids.get("arxiv") or "").strip()
    if candidate_arxiv_id and build_arxiv_abs_url(candidate_arxiv_id) == arxiv_url:
        return True

    candidate_title = _extract_openalex_work_title(work)
    if candidate_title and normalize_title_for_matching(candidate_title) == normalize_title_for_matching(title):
        return True

    return False


async def _resolve_target_openalex_work(arxiv_url: str, title: str, openalex_client) -> dict | None:
    expected_doi = _build_arxiv_doi_url(arxiv_url)
    exact_lookup = getattr(openalex_client, "fetch_work_by_identifier", None)
    if expected_doi and callable(exact_lookup):
        exact_work = await exact_lookup(expected_doi)
        if isinstance(exact_work, dict) and _matches_target_openalex_work(
            exact_work,
            arxiv_url=arxiv_url,
            title=title,
            expected_doi=expected_doi,
        ):
            return exact_work

    search_first_work = getattr(openalex_client, "search_first_work", None)
    if not callable(search_first_work):
        return None

    fallback_work = await search_first_work(title)
    if not isinstance(fallback_work, dict):
        return None

    if not callable(exact_lookup):
        return fallback_work

    fallback_identifier = str(fallback_work.get("id") or "").strip()
    hydrated_work = await exact_lookup(fallback_identifier) if fallback_identifier else None
    candidate = hydrated_work if isinstance(hydrated_work, dict) else fallback_work
    if _matches_target_openalex_work(candidate, arxiv_url=arxiv_url, title=title, expected_doi=expected_doi):
        return candidate

    return None


async def _resolve_target_semantic_scholar_paper(
    arxiv_url: str,
    title: str,
    semanticscholar_graph_client,
) -> dict | None:
    arxiv_id = extract_arxiv_id(arxiv_url)
    if not arxiv_id:
        return None

    last_stage_error: RuntimeError | aiohttp.ClientError | asyncio.TimeoutError | None = None
    completed_stage_without_error = False

    for identifier in [f"DOI:10.48550/arXiv.{arxiv_id}", f"ARXIV:{arxiv_id}"]:
        try:
            paper = await semanticscholar_graph_client.fetch_paper_by_identifier(identifier)
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_stage_error = exc
            continue

        completed_stage_without_error = True
        if isinstance(paper, dict) and paper.get("paperId"):
            return paper

    normalized_title = normalize_title_for_matching(title)
    try:
        matches = await semanticscholar_graph_client.search_papers_by_title(title)
    except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        last_stage_error = exc
    else:
        completed_stage_without_error = True
        for paper in matches:
            candidate_title = " ".join(str(paper.get("title") or "").split()).strip()
            if candidate_title and normalize_title_for_matching(candidate_title) == normalized_title:
                return paper

    if last_stage_error is not None and not completed_stage_without_error:
        raise last_stage_error

    return None


async def _fetch_primary_relation_candidates(
    *,
    relation_label: str,
    semanticscholar_graph_client=None,
    semantic_scholar_target_paper: dict | None = None,
    openalex_client=None,
    get_openalex_target_work,
    status_callback=None,
) -> list[RelatedWorkCandidate]:
    is_references = relation_label == "references"
    semantic_fetch_failed = False

    if semanticscholar_graph_client is not None and semantic_scholar_target_paper is not None:
        semantic_fetcher = (
            semanticscholar_graph_client.fetch_references
            if is_references
            else semanticscholar_graph_client.fetch_citations
        )
        if callable(status_callback):
            status_callback(f"🔎 Fetching Semantic Scholar {relation_label}")
        try:
            semantic_rows = await semantic_fetcher(semantic_scholar_target_paper)
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            semantic_fetch_failed = True
            if callable(status_callback):
                status_callback(f"⚠️ Semantic Scholar {relation_label} failed ({exc}); falling back to OpenAlex")
        else:
            if semantic_rows:
                if callable(status_callback):
                    status_callback(f"📚 Semantic Scholar returned {len(semantic_rows)} {relation_label}")
                return [
                    semanticscholar_graph_client.build_related_work_candidate(row)
                    for row in semantic_rows
                ]
            if callable(status_callback):
                status_callback(f"📚 Semantic Scholar {relation_label} empty; falling back to OpenAlex")

    openalex_target_work = await get_openalex_target_work()
    if openalex_target_work is None:
        if semantic_fetch_failed:
            raise ValueError(
                f"Semantic Scholar {relation_label} fallback could not resolve OpenAlex target work"
            )
        if callable(status_callback):
            if openalex_client is None:
                status_callback(f"⚠️ OpenAlex fallback unavailable; keeping empty {relation_label}")
            else:
                status_callback(f"⚠️ OpenAlex target lookup missed; keeping empty {relation_label}")
        return []

    openalex_label = "referenced works" if is_references else "citations"
    openalex_fetcher = (
        openalex_client.fetch_referenced_works
        if is_references
        else openalex_client.fetch_citations
    )
    if callable(status_callback):
        status_callback(f"🔎 Fetching OpenAlex {openalex_label}")
    openalex_rows = await openalex_fetcher(openalex_target_work)
    if callable(status_callback):
        status_callback(f"📚 Retrieved {len(openalex_rows)} {openalex_label}")
    return [openalex_client.build_related_work_candidate(row) for row in openalex_rows]


def _fallback_related_work_url(candidate) -> str:
    return candidate.doi_url or candidate.landing_page_url or candidate.openalex_url


def _build_retained_related_row(candidate, *, resolution_source: str | None = None) -> NormalizedRelatedRow:
    fallback_url = _fallback_related_work_url(candidate)
    original_title = candidate.title or fallback_url
    return NormalizedRelatedRow(
        title=original_title,
        url=fallback_url,
        strength=NormalizationStrength.RETAINED_NON_ARXIV,
        original_title=original_title,
        input_url=fallback_url,
        resolution_source=resolution_source or "unresolved",
    )


async def _resolve_related_work_row(
    candidate,
    *,
    arxiv_client,
    semanticscholar_graph_client=None,
    openalex_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    resolve_arxiv_url_fn=None,
) -> NormalizedRelatedRow:
    resolve_arxiv_url_fn = resolve_arxiv_url if resolve_arxiv_url_fn is None else resolve_arxiv_url_fn
    if candidate.direct_arxiv_url:
        resolved_title = candidate.title or candidate.direct_arxiv_url
        return NormalizedRelatedRow(
            title=resolved_title,
            url=candidate.direct_arxiv_url,
            strength=NormalizationStrength.DIRECT_ARXIV,
            original_title=resolved_title,
            input_url=candidate.direct_arxiv_url,
            resolution_source="direct_arxiv_url",
        )

    fallback_url = _fallback_related_work_url(candidate)
    resolution = await resolve_arxiv_url_fn(
        title=candidate.title,
        raw_url=fallback_url,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        extra_identifiers=[candidate.openalex_url, candidate.doi_url],
    )
    if resolution.canonical_arxiv_url:
        resolved_title = resolution.resolved_title or candidate.title or resolution.canonical_arxiv_url
        original_title = candidate.title or resolution.resolved_title or resolution.canonical_arxiv_url
        return NormalizedRelatedRow(
            title=resolved_title,
            url=resolution.canonical_arxiv_url,
            strength=NormalizationStrength.TITLE_SEARCH,
            original_title=original_title,
            input_url=fallback_url,
            resolution_source=resolution.source,
        )

    return _build_retained_related_row(candidate, resolution_source=resolution.source)


async def _resolve_related_work_rows(
    candidates: list,
    *,
    arxiv_client,
    semanticscholar_graph_client=None,
    openalex_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    resolve_arxiv_url_fn=None,
    progress_callback=None,
) -> list[NormalizedRelatedRow]:
    total = len(candidates)

    async def resolve_candidate(item: tuple[int, object]) -> NormalizedRelatedRow:
        index, candidate = item
        row = await _resolve_related_work_row(
            candidate,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            openalex_client=openalex_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            discovery_client=discovery_client,
            relation_resolution_cache=relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
            resolve_arxiv_url_fn=resolve_arxiv_url_fn,
        )
        if callable(progress_callback):
            progress_callback(RelationNormalizationProgressOutcome(index=index, row=row), total)
        return row

    return await asyncio.gather(
        *[
            resolve_candidate((index, candidate))
            for index, candidate in enumerate(candidates, 1)
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


async def normalize_related_works_to_rows(
    related_works: list[dict],
    *,
    openalex_client,
    arxiv_client,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    resolve_arxiv_url_fn=None,
    progress_callback=None,
) -> list[NormalizedRelatedRow]:
    candidates = [openalex_client.build_related_work_candidate(work) for work in related_works]
    return await normalize_related_work_candidates_to_rows(
        candidates,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        resolve_arxiv_url_fn=resolve_arxiv_url_fn,
        progress_callback=progress_callback,
    )


async def normalize_related_work_candidates_to_rows(
    related_work_candidates: list[RelatedWorkCandidate],
    *,
    arxiv_client,
    semanticscholar_graph_client=None,
    openalex_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    resolve_arxiv_url_fn=None,
    progress_callback=None,
) -> list[NormalizedRelatedRow]:
    normalized_rows = await _resolve_related_work_rows(
        related_work_candidates,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        resolve_arxiv_url_fn=resolve_arxiv_url_fn,
        progress_callback=progress_callback,
    )
    return _dedupe_normalized_rows(normalized_rows)


async def normalize_related_work_candidates_to_seeds(
    related_work_candidates: list[RelatedWorkCandidate],
    *,
    arxiv_client,
    semanticscholar_graph_client=None,
    openalex_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    progress_callback=None,
) -> list[PaperSeed]:
    deduped_rows = await normalize_related_work_candidates_to_rows(
        related_work_candidates,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        progress_callback=progress_callback,
    )
    return [
        PaperSeed(
            name=row.title,
            url=row.url,
            canonical_arxiv_url=row.url if extract_arxiv_id(row.url) else None,
            url_resolution_authoritative=True,
        )
        for row in deduped_rows
    ]


async def normalize_related_works_to_seeds(
    related_works: list[dict],
    *,
    openalex_client,
    arxiv_client,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    progress_callback=None,
) -> list[PaperSeed]:
    related_work_candidates = [
        openalex_client.build_related_work_candidate(work)
        for work in related_works
    ]
    return await normalize_related_work_candidates_to_seeds(
        related_work_candidates,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        progress_callback=progress_callback,
    )


async def export_arxiv_relations_to_csv(
    arxiv_input: str,
    *,
    arxiv_client,
    openalex_client,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client,
    github_client,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    output_dir: Path | None = None,
    status_callback=None,
    normalization_progress_callback=None,
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

    semantic_scholar_target_paper = None
    if semanticscholar_graph_client is not None:
        if callable(status_callback):
            status_callback("🔎 Resolving Semantic Scholar target paper")
        try:
            semantic_scholar_target_paper = await _resolve_target_semantic_scholar_paper(
                arxiv_url,
                title,
                semanticscholar_graph_client,
            )
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if callable(status_callback):
                status_callback(f"⚠️ Semantic Scholar target lookup failed ({exc}); falling back to OpenAlex")
        else:
            if callable(status_callback):
                if semantic_scholar_target_paper is not None:
                    status_callback("📄 Resolved Semantic Scholar target paper")
                else:
                    status_callback("📄 Semantic Scholar target lookup missed; falling back to OpenAlex")

    openalex_target_work: dict | None = None
    openalex_target_work_resolved = False

    async def get_openalex_target_work() -> dict | None:
        nonlocal openalex_target_work
        nonlocal openalex_target_work_resolved
        if not openalex_target_work_resolved:
            if openalex_client is None:
                openalex_target_work = None
            else:
                openalex_target_work = await _resolve_target_openalex_work(arxiv_url, title, openalex_client)
            openalex_target_work_resolved = True
        return openalex_target_work

    if semantic_scholar_target_paper is None:
        target_work = await get_openalex_target_work()
        if not target_work:
            if semanticscholar_graph_client is None:
                raise ValueError(f"No OpenAlex work found for title: {title}")
            raise ValueError(f"No relation target found for title: {title}")

    reference_candidates = await _fetch_primary_relation_candidates(
        relation_label="references",
        semanticscholar_graph_client=semanticscholar_graph_client,
        semantic_scholar_target_paper=semantic_scholar_target_paper,
        openalex_client=openalex_client,
        get_openalex_target_work=get_openalex_target_work,
        status_callback=status_callback,
    )
    citation_candidates = await _fetch_primary_relation_candidates(
        relation_label="citations",
        semanticscholar_graph_client=semanticscholar_graph_client,
        semantic_scholar_target_paper=semantic_scholar_target_paper,
        openalex_client=openalex_client,
        get_openalex_target_work=get_openalex_target_work,
        status_callback=status_callback,
    )

    if callable(status_callback):
        status_callback("🔎 Normalizing referenced works to arXiv-backed seeds")
    reference_seeds = await normalize_related_work_candidates_to_seeds(
        reference_candidates,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        progress_callback=normalization_progress_callback,
    )
    if callable(status_callback):
        status_callback(
            f"🧭 Kept {len(reference_seeds)}/{len(reference_candidates)} referenced works after arXiv normalization"
        )

    if callable(status_callback):
        status_callback("🔎 Normalizing citation works to arXiv-backed seeds")
    citation_seeds = await normalize_related_work_candidates_to_seeds(
        citation_candidates,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        progress_callback=normalization_progress_callback,
    )
    if callable(status_callback):
        status_callback(f"🧭 Kept {len(citation_seeds)}/{len(citation_candidates)} citation works after arXiv normalization")

    references_csv_path, citations_csv_path = build_relations_csv_paths(arxiv_url, output_dir=output_dir)

    references_result = await export_paper_seeds_to_csv(
        reference_seeds,
        references_csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )
    citations_result = await export_paper_seeds_to_csv(
        citation_seeds,
        citations_csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        status_callback=status_callback,
        progress_callback=progress_callback,
    )

    return ArxivRelationsExportResult(
        arxiv_url=arxiv_url,
        title=title,
        references=references_result,
        citations=citations_result,
    )
