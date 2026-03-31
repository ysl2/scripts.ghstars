import asyncio
from dataclasses import dataclass
import re

import aiohttp

from src.shared.arxiv import normalize_title_for_matching, sanitize_title_for_lookup
from src.shared.paper_identity import (
    build_arxiv_abs_url,
    is_arxiv_hosted_url,
    normalize_arxiv_url,
    normalize_doi_url,
    normalize_openalex_work_url,
)


NO_MATCH_TITLE_SEARCH_ERROR = "No arXiv ID found from title search"
HUGGINGFACE_PAPER_ID_PATTERN = re.compile(r"^[0-9]{4}\.[0-9]{4,5}$")


@dataclass(frozen=True)
class ArxivUrlResolutionResult:
    resolved_url: str | None
    canonical_arxiv_url: str | None
    resolved_title: str | None
    source: str | None
    script_derived: bool


@dataclass(frozen=True)
class _TitleResolutionResult:
    canonical_arxiv_url: str | None
    resolved_title: str | None
    definitive_no_match: bool


def _build_cache_keys(identifiers: list[str]) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw_identifier in identifiers:
        normalized_openalex = normalize_openalex_work_url(raw_identifier)
        if normalized_openalex:
            key = ("openalex_work", normalized_openalex)
            if key not in seen:
                keys.append(key)
                seen.add(key)

    for raw_identifier in identifiers:
        normalized_doi = normalize_doi_url(raw_identifier)
        if normalized_doi:
            key = ("doi", normalized_doi)
            if key not in seen:
                keys.append(key)
                seen.add(key)

    return keys


def _uses_full_shared_resolution_policy(
    *,
    allow_title_search: bool,
    allow_openalex_preprint_crosswalk: bool,
    allow_huggingface_fallback: bool,
) -> bool:
    return allow_title_search and allow_openalex_preprint_crosswalk and allow_huggingface_fallback


async def resolve_arxiv_url(
    title: str,
    raw_url: str,
    *,
    arxiv_client=None,
    openalex_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    allow_title_search: bool = True,
    allow_openalex_preprint_crosswalk: bool = True,
    allow_huggingface_fallback: bool = True,
    extra_identifiers: list[str] | None = None,
) -> ArxivUrlResolutionResult:
    normalized_title = sanitize_title_for_lookup(title)
    normalized_raw_url = (raw_url or "").strip()
    identifiers = [normalized_raw_url]
    if extra_identifiers:
        identifiers.extend(" ".join(str(item or "").split()).strip() for item in extra_identifiers if str(item or "").strip())

    if is_arxiv_hosted_url(normalized_raw_url):
        return ArxivUrlResolutionResult(
            resolved_url=normalized_raw_url,
            canonical_arxiv_url=normalize_arxiv_url(normalized_raw_url),
            resolved_title=normalized_title or None,
            source="existing_arxiv_url",
            script_derived=False,
        )

    cache_keys = _build_cache_keys(identifiers)
    uses_full_shared_resolution_policy = _uses_full_shared_resolution_policy(
        allow_title_search=allow_title_search,
        allow_openalex_preprint_crosswalk=allow_openalex_preprint_crosswalk,
        allow_huggingface_fallback=allow_huggingface_fallback,
    )

    if relation_resolution_cache is not None and cache_keys:
        cached_entries = [relation_resolution_cache.get(key_type, key_value) for key_type, key_value in cache_keys]
        positive_entry = next((entry for entry in cached_entries if entry is not None and entry.arxiv_url), None)
        if positive_entry is not None:
            cached_title = getattr(positive_entry, "resolved_title", None)
            if not cached_title and callable(getattr(arxiv_client, "get_title", None)):
                cached_title, _ = await arxiv_client.get_title(positive_entry.arxiv_url)
            return ArxivUrlResolutionResult(
                resolved_url=positive_entry.arxiv_url,
                canonical_arxiv_url=positive_entry.arxiv_url,
                resolved_title=cached_title or normalized_title or positive_entry.arxiv_url,
                source="relation_resolution_cache",
                script_derived=True,
            )

        has_fresh_negative_for_all_keys = uses_full_shared_resolution_policy and all(
            entry is not None
            and entry.arxiv_url is None
            and relation_resolution_cache.is_negative_cache_fresh(
                getattr(entry, "checked_at", None),
                arxiv_relation_no_arxiv_recheck_days,
            )
            for entry in cached_entries
        )
        if has_fresh_negative_for_all_keys:
            return ArxivUrlResolutionResult(
                resolved_url=None,
                canonical_arxiv_url=None,
                resolved_title=None,
                source="relation_resolution_cache_negative",
                script_derived=False,
            )

    normalized_doi_key = next((key_value for key_type, key_value in cache_keys if key_type == "doi"), None)
    openalex_exact_lookup = getattr(openalex_client, "find_exact_arxiv_match_by_identifier", None)
    openalex_preprint_lookup = getattr(openalex_client, "find_preprint_match_by_identifier", None)
    metadata_transient_failure = False
    if callable(openalex_exact_lookup):
        for key_type, key_value in cache_keys:
            try:
                arxiv_url, resolved_title = await openalex_exact_lookup(key_value, title=normalized_title or None)
            except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
                metadata_transient_failure = True
                continue

            if arxiv_url:
                _record_positive_resolution(
                    relation_resolution_cache,
                    cache_keys,
                    arxiv_url=arxiv_url,
                    resolved_title=resolved_title or normalized_title or None,
                )
                return ArxivUrlResolutionResult(
                    resolved_url=arxiv_url,
                    canonical_arxiv_url=arxiv_url,
                    resolved_title=resolved_title or normalized_title or arxiv_url,
                    source=f"openalex_exact_{key_type}",
                    script_derived=True,
                )

    if allow_openalex_preprint_crosswalk and callable(openalex_preprint_lookup):
        for key_type, key_value in cache_keys:
            try:
                arxiv_url, resolved_title = await openalex_preprint_lookup(key_value, title=normalized_title or None)
            except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
                metadata_transient_failure = True
                continue

            if arxiv_url:
                _record_positive_resolution(
                    relation_resolution_cache,
                    cache_keys,
                    arxiv_url=arxiv_url,
                    resolved_title=resolved_title or normalized_title or None,
                )
                return ArxivUrlResolutionResult(
                    resolved_url=arxiv_url,
                    canonical_arxiv_url=arxiv_url,
                    resolved_title=resolved_title or normalized_title or arxiv_url,
                    source=f"openalex_preprint_{key_type}",
                    script_derived=True,
                )

    title_resolution = _TitleResolutionResult(canonical_arxiv_url=None, resolved_title=None, definitive_no_match=False)
    if allow_title_search:
        title_resolution = await _resolve_by_title(
            normalized_title,
            arxiv_client=arxiv_client,
        )
        if title_resolution.canonical_arxiv_url:
            _record_positive_resolution(
                relation_resolution_cache,
                cache_keys,
                arxiv_url=title_resolution.canonical_arxiv_url,
                resolved_title=title_resolution.resolved_title or normalized_title or None,
            )
            return ArxivUrlResolutionResult(
                resolved_url=title_resolution.canonical_arxiv_url,
                canonical_arxiv_url=title_resolution.canonical_arxiv_url,
                resolved_title=title_resolution.resolved_title or normalized_title or title_resolution.canonical_arxiv_url,
                source="title_search",
                script_derived=True,
            )

    crossref_lookup = getattr(crossref_client, "find_arxiv_match_by_doi", None)
    if callable(crossref_lookup) and normalized_doi_key:
        try:
            arxiv_url, resolved_title = await crossref_lookup(normalized_doi_key)
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
            metadata_transient_failure = True
        else:
            if arxiv_url:
                _record_positive_resolution(
                    relation_resolution_cache,
                    cache_keys,
                    arxiv_url=arxiv_url,
                    resolved_title=resolved_title or normalized_title or None,
                )
                return ArxivUrlResolutionResult(
                    resolved_url=arxiv_url,
                    canonical_arxiv_url=arxiv_url,
                    resolved_title=resolved_title or normalized_title or arxiv_url,
                    source="crossref",
                    script_derived=True,
                )

    datacite_lookup = getattr(datacite_client, "find_arxiv_match_by_doi", None)
    if callable(datacite_lookup) and normalized_doi_key:
        try:
            arxiv_url, resolved_title = await datacite_lookup(normalized_doi_key)
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
            metadata_transient_failure = True
        else:
            if arxiv_url:
                _record_positive_resolution(
                    relation_resolution_cache,
                    cache_keys,
                    arxiv_url=arxiv_url,
                    resolved_title=resolved_title or normalized_title or None,
                )
                return ArxivUrlResolutionResult(
                    resolved_url=arxiv_url,
                    canonical_arxiv_url=arxiv_url,
                    resolved_title=resolved_title or normalized_title or arxiv_url,
                    source="datacite",
                    script_derived=True,
                )

    huggingface_search = getattr(discovery_client, "get_huggingface_paper_search_results", None)
    huggingface_stage_available = bool(
        allow_huggingface_fallback
        and getattr(discovery_client, "huggingface_token", "")
        and callable(huggingface_search)
    )
    huggingface_definitive_no_match = not allow_huggingface_fallback
    if allow_huggingface_fallback and huggingface_stage_available:
        search_results, hf_error = await huggingface_search(normalized_title, limit=1)
        if hf_error or search_results is None:
            huggingface_definitive_no_match = False
        else:
            hf_arxiv_id, _hf_title, huggingface_definitive_no_match = _extract_best_huggingface_paper_id_from_search_results(
                search_results,
                normalized_title,
            )
            if hf_arxiv_id:
                resolved_title = normalized_title or None
                if callable(getattr(arxiv_client, "get_title", None)):
                    matched_title, _ = await arxiv_client.get_title(hf_arxiv_id)
                    resolved_title = matched_title or resolved_title
                canonical_arxiv_url = build_arxiv_abs_url(hf_arxiv_id)
                _record_positive_resolution(
                    relation_resolution_cache,
                    cache_keys,
                    arxiv_url=canonical_arxiv_url,
                    resolved_title=resolved_title,
                )
                return ArxivUrlResolutionResult(
                    resolved_url=canonical_arxiv_url,
                    canonical_arxiv_url=canonical_arxiv_url,
                    resolved_title=resolved_title or canonical_arxiv_url,
                    source="huggingface_title_search",
                    script_derived=True,
                )

    should_record_negative = uses_full_shared_resolution_policy and bool(cache_keys) and not metadata_transient_failure
    if allow_title_search:
        should_record_negative = should_record_negative and title_resolution.definitive_no_match
    if allow_huggingface_fallback:
        should_record_negative = should_record_negative and huggingface_stage_available and huggingface_definitive_no_match

    if should_record_negative:
        _record_negative_resolution(relation_resolution_cache, cache_keys)

    return ArxivUrlResolutionResult(
        resolved_url=None,
        canonical_arxiv_url=None,
        resolved_title=None,
        source=None,
        script_derived=False,
    )


async def _resolve_by_title(
    title: str,
    *,
    arxiv_client=None,
) -> _TitleResolutionResult:
    if not title:
        return _TitleResolutionResult(canonical_arxiv_url=None, resolved_title=None, definitive_no_match=False)

    arxiv_title_search = getattr(arxiv_client, "get_arxiv_id_by_title", None)
    html_definitive_no_match = False
    if callable(arxiv_title_search):
        arxiv_id, _source, error = await arxiv_title_search(title)
        if arxiv_id:
            matched_title = None
            if callable(getattr(arxiv_client, "get_title", None)):
                matched_title, _ = await arxiv_client.get_title(arxiv_id)
            return _TitleResolutionResult(
                canonical_arxiv_url=build_arxiv_abs_url(arxiv_id),
                resolved_title=matched_title or title,
                definitive_no_match=False,
            )
        html_definitive_no_match = error == NO_MATCH_TITLE_SEARCH_ERROR

    arxiv_title_match = getattr(arxiv_client, "get_arxiv_match_by_title_from_api", None)
    api_definitive_no_match = False
    if callable(arxiv_title_match):
        arxiv_id, matched_title, _source, error = await arxiv_title_match(title)
        if arxiv_id:
            canonical_arxiv_url = build_arxiv_abs_url(arxiv_id)
            if matched_title is None and callable(getattr(arxiv_client, "get_title", None)):
                matched_title, _ = await arxiv_client.get_title(arxiv_id)
            return _TitleResolutionResult(
                canonical_arxiv_url=canonical_arxiv_url,
                resolved_title=matched_title or title,
                definitive_no_match=False,
            )
        api_definitive_no_match = error == NO_MATCH_TITLE_SEARCH_ERROR

    definitive_no_match = False
    if callable(arxiv_title_match):
        definitive_no_match = api_definitive_no_match and (
            not callable(arxiv_title_search) or html_definitive_no_match
        )
    elif callable(arxiv_title_search):
        definitive_no_match = html_definitive_no_match

    return _TitleResolutionResult(
        canonical_arxiv_url=None,
        resolved_title=None,
        definitive_no_match=definitive_no_match,
    )


def _record_positive_resolution(relation_resolution_cache, cache_keys, *, arxiv_url: str, resolved_title: str | None) -> None:
    if relation_resolution_cache is None:
        return

    for key_type, key_value in cache_keys:
        relation_resolution_cache.record_resolution(
            key_type=key_type,
            key_value=key_value,
            arxiv_url=arxiv_url,
            resolved_title=resolved_title,
        )


def _record_negative_resolution(relation_resolution_cache, cache_keys) -> None:
    if relation_resolution_cache is None:
        return

    for key_type, key_value in cache_keys:
        relation_resolution_cache.record_resolution(
            key_type=key_type,
            key_value=key_value,
            arxiv_url=None,
        )


def _extract_best_huggingface_paper_id_from_search_results(
    search_results,
    title_query: str,
) -> tuple[str | None, str | None, bool]:
    if not isinstance(search_results, list) or not title_query:
        return None, None, False

    if not search_results:
        return None, None, True

    title_query_norm = normalize_title_for_matching(title_query)
    best_id = None
    best_title = None
    best_score = -1
    saw_interpretable_candidate = False

    for item in search_results:
        if not isinstance(item, dict):
            continue

        paper = item.get("paper", {})
        if not isinstance(paper, dict):
            continue

        raw_paper_id = paper.get("id")
        if not isinstance(raw_paper_id, str):
            continue

        raw_title = item.get("title")
        if not isinstance(raw_title, str):
            raw_title = paper.get("title")
        if not isinstance(raw_title, str):
            continue

        paper_id = raw_paper_id.strip()
        title_text = " ".join(raw_title.split()).strip()

        title = normalize_title_for_matching(title_text)
        if not HUGGINGFACE_PAPER_ID_PATTERN.match(paper_id) or not title:
            continue

        saw_interpretable_candidate = True

        score = 0
        if title == title_query_norm:
            score = 100
        elif title_query_norm in title:
            score = 80
        elif title in title_query_norm:
            score = 60

        if score > 0 and score > best_score:
            best_score = score
            best_id = paper_id
            best_title = title_text

    if best_id:
        return best_id, best_title, False
    return None, None, saw_interpretable_candidate
