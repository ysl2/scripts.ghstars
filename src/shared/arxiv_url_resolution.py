import asyncio
from dataclasses import dataclass

import aiohttp

from src.shared.paper_identity import (
    build_arxiv_abs_url,
    is_arxiv_hosted_url,
    normalize_arxiv_url,
    normalize_doi_url,
    normalize_openalex_work_url,
)


NO_MATCH_TITLE_SEARCH_ERROR = "No arXiv ID found from title search"


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


def _build_cache_keys(raw_url: str) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []

    normalized_openalex = normalize_openalex_work_url(raw_url)
    if normalized_openalex:
        keys.append(("openalex_work", normalized_openalex))

    normalized_doi = normalize_doi_url(raw_url)
    if normalized_doi:
        keys.append(("doi", normalized_doi))

    return keys


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
) -> ArxivUrlResolutionResult:
    normalized_title = " ".join((title or "").split()).strip()
    normalized_raw_url = (raw_url or "").strip()

    if is_arxiv_hosted_url(normalized_raw_url):
        return ArxivUrlResolutionResult(
            resolved_url=normalized_raw_url,
            canonical_arxiv_url=normalize_arxiv_url(normalized_raw_url),
            resolved_title=normalized_title or None,
            source="existing_arxiv_url",
            script_derived=False,
        )

    cache_keys = _build_cache_keys(normalized_raw_url)
    if relation_resolution_cache is not None and cache_keys:
        cached_entries = [relation_resolution_cache.get(key_type, key_value) for key_type, key_value in cache_keys]
        positive_entry = next((entry for entry in cached_entries if entry is not None and entry.arxiv_url), None)
        if positive_entry is not None:
            return ArxivUrlResolutionResult(
                resolved_url=positive_entry.arxiv_url,
                canonical_arxiv_url=positive_entry.arxiv_url,
                resolved_title=getattr(positive_entry, "resolved_title", None) or normalized_title or positive_entry.arxiv_url,
                source="relation_resolution_cache",
                script_derived=True,
            )

        has_fresh_negative = any(
            entry is not None
            and entry.arxiv_url is None
            and relation_resolution_cache.is_negative_cache_fresh(
                getattr(entry, "checked_at", None),
                arxiv_relation_no_arxiv_recheck_days,
            )
            for entry in cached_entries
        )
        if has_fresh_negative:
            return ArxivUrlResolutionResult(
                resolved_url=None,
                canonical_arxiv_url=None,
                resolved_title=None,
                source="relation_resolution_cache_negative",
                script_derived=False,
            )

    normalized_doi_key = next((key_value for key_type, key_value in cache_keys if key_type == "doi"), None)
    openalex_lookup = getattr(openalex_client, "find_exact_arxiv_match_by_identifier", None)
    metadata_transient_failure = False
    if callable(openalex_lookup):
        for key_type, key_value in cache_keys:
            try:
                arxiv_url, resolved_title = await openalex_lookup(key_value, title=normalized_title or None)
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

    should_record_negative = bool(cache_keys) and not metadata_transient_failure
    if allow_title_search:
        should_record_negative = should_record_negative and title_resolution.definitive_no_match

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

    arxiv_id = None
    matched_title = None
    error = None

    arxiv_title_match = getattr(arxiv_client, "get_arxiv_match_by_title_from_api", None)
    if callable(arxiv_title_match):
        arxiv_id, matched_title, _source, error = await arxiv_title_match(title)
    elif arxiv_client is not None:
        arxiv_id, _source, error = await arxiv_client.get_arxiv_id_by_title(title)

    if arxiv_id:
        canonical_arxiv_url = build_arxiv_abs_url(arxiv_id)
        if matched_title is None and callable(getattr(arxiv_client, "get_title", None)):
            matched_title, _ = await arxiv_client.get_title(arxiv_id)
        return _TitleResolutionResult(
            canonical_arxiv_url=canonical_arxiv_url,
            resolved_title=matched_title or title,
            definitive_no_match=False,
        )

    definitive_no_match = error == NO_MATCH_TITLE_SEARCH_ERROR

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
