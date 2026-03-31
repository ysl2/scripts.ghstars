import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib.parse import urlparse

import aiohttp

from src.shared.arxiv import normalize_title_for_matching
from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import (
    build_arxiv_abs_url,
    normalize_arxiv_url,
    normalize_doi_url,
    normalize_openalex_work_url,
)
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_SEARCH_PAGE_SIZE = 5
OPENALEX_CITED_BY_PAGE_SIZE = 200
OPENALEX_REFERENCED_WORKS_CHUNK_SIZE = 50
OPENALEX_RETRY_STATUSES = {500, 502, 503, 504}
OPENALEX_TARGET_WORK_SELECT = "id,referenced_works"
OPENALEX_RELATION_WORK_SELECT = "id,display_name,title,ids,doi,locations"
OPENALEX_429_MAX_RETRIES = 3
OPENALEX_429_FALLBACK_BACKOFF_SECONDS = 2.0
OPENALEX_429_MAX_RETRY_AFTER_SECONDS = 15.0
ARXIV_DOI_PATTERN = re.compile(r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.IGNORECASE)


@dataclass(frozen=True)
class RelatedWorkCandidate:
    title: str
    direct_arxiv_url: str | None
    doi_url: str | None
    landing_page_url: str | None
    openalex_url: str


def _normalize_doi_url(doi: Any) -> str | None:
    if not isinstance(doi, str):
        return None

    normalized = doi.strip()
    return normalized or None


class OpenAlexClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        openalex_api_key: str = "",
        max_concurrent: int = 4,
        min_interval: float = 0.2,
    ):
        self.session = session
        self.openalex_api_key = openalex_api_key.strip()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)
        self._work_cache: dict[str, dict[str, Any] | None] = {}

    async def search_first_work(self, title: str) -> dict[str, Any] | None:
        params = {
            "search": title,
            "per_page": OPENALEX_SEARCH_PAGE_SIZE,
            "select": OPENALEX_TARGET_WORK_SELECT,
        }
        payload = await self._get_json(OPENALEX_WORKS_URL, params=params)
        results = payload.get("results") or []
        return results[0] if results else None

    async def fetch_work_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        normalized_openalex_url = normalize_openalex_work_url(identifier)
        normalized_doi_url = normalize_doi_url(identifier)
        cache_keys = [key for key in [normalized_openalex_url, normalized_doi_url] if key]

        for cache_key in cache_keys:
            if cache_key in self._work_cache:
                return self._work_cache[cache_key]

        if normalized_openalex_url:
            work_id = self._extract_work_id(normalized_openalex_url)
            if not work_id:
                return None
            payload = await self._get_json(
                f"{OPENALEX_WORKS_URL}/{work_id}",
                params={"select": OPENALEX_RELATION_WORK_SELECT},
            )
            work = payload if isinstance(payload, dict) else None
            self._cache_work_lookup(work, cache_keys)
            return work

        if normalized_doi_url:
            payload = await self._get_json(
                f"{OPENALEX_WORKS_URL}/{normalized_doi_url}",
                params={"select": OPENALEX_RELATION_WORK_SELECT},
            )
            work = payload if isinstance(payload, dict) else None
            self._cache_work_lookup(work, cache_keys)
            return work

        return None

    def _cache_work_lookup(self, work: dict[str, Any] | None, cache_keys: list[str]) -> None:
        alias_keys = {key for key in cache_keys if key}
        if isinstance(work, dict):
            normalized_work_openalex_url = normalize_openalex_work_url(work.get("id"))
            normalized_work_doi_url = normalize_doi_url(work.get("doi"))
            if normalized_work_openalex_url:
                alias_keys.add(normalized_work_openalex_url)
            if normalized_work_doi_url:
                alias_keys.add(normalized_work_doi_url)

        for cache_key in alias_keys:
            self._work_cache[cache_key] = work

    async def find_exact_arxiv_match_by_identifier(
        self,
        identifier: str,
        *,
        title: str | None = None,
    ) -> tuple[str | None, str | None]:
        work = await self.fetch_work_by_identifier(identifier)
        if not isinstance(work, dict):
            return None, None

        return self._resolve_exact_match(work, title=title)

    async def find_preprint_match_by_identifier(
        self,
        identifier: str,
        *,
        title: str | None = None,
    ) -> tuple[str | None, str | None]:
        work = await self.fetch_work_by_identifier(identifier)
        if not isinstance(work, dict):
            return None, None

        direct_arxiv_url, resolved_title = self._resolve_exact_match(work, title=title)
        if direct_arxiv_url:
            return direct_arxiv_url, resolved_title or title

        search_title = " ".join(str(title or resolved_title or "").split()).strip()
        if not search_title:
            return None, resolved_title or None

        return await self.find_related_work_preprint_match(work, title=search_title)

    async def find_related_work_preprint_match(
        self,
        work: dict[str, Any],
        *,
        title: str,
    ) -> tuple[str | None, str | None]:
        current_work_id = self._extract_work_id(work.get("id"))
        search_title = " ".join(title.split()).strip()
        normalized_title = normalize_title_for_matching(search_title)
        if not current_work_id or not normalized_title:
            return None, None

        payload = await self._get_json(
            OPENALEX_WORKS_URL,
            params={
                "search": search_title,
                "per_page": OPENALEX_SEARCH_PAGE_SIZE,
                "select": OPENALEX_RELATION_WORK_SELECT,
            },
        )
        results = payload.get("results")
        if not isinstance(results, list):
            return None, None

        for candidate in results:
            if not isinstance(candidate, dict):
                continue

            candidate_work_id = self._extract_work_id(candidate.get("id"))
            if current_work_id and candidate_work_id == current_work_id:
                continue

            candidate_title = " ".join(str(candidate.get("display_name") or candidate.get("title") or "").split()).strip()
            if normalize_title_for_matching(candidate_title) != normalized_title:
                continue

            canonical_arxiv_url = self._canonical_arxiv_url(candidate)
            if canonical_arxiv_url:
                return canonical_arxiv_url, candidate_title or search_title

        return None, None

    async def find_related_work_preprint_arxiv_url(
        self,
        work: dict[str, Any],
        *,
        title: str,
    ) -> str | None:
        arxiv_url, _resolved_title = await self.find_related_work_preprint_match(work, title=title)
        return arxiv_url

    async def fetch_referenced_works(self, work: dict[str, Any]) -> list[dict[str, Any]]:
        referenced = work.get("referenced_works") or []
        work_ids = self._unique_work_ids(referenced)
        if not work_ids:
            return []

        hydrated_by_id: dict[str, dict[str, Any]] = {}
        for work_id_chunk in self._chunk_values(work_ids, OPENALEX_REFERENCED_WORKS_CHUNK_SIZE):
            payload = await self._get_json(
                OPENALEX_WORKS_URL,
                params={
                    "filter": f"openalex:{'|'.join(work_id_chunk)}",
                    "per_page": len(work_id_chunk),
                    "select": OPENALEX_RELATION_WORK_SELECT,
                },
            )
            results = payload.get("results")
            if not isinstance(results, list):
                continue

            for related_work in results:
                if not isinstance(related_work, dict):
                    continue
                related_work_id = self._extract_work_id(related_work.get("id"))
                if not related_work_id or related_work_id in hydrated_by_id:
                    continue
                hydrated_by_id[related_work_id] = related_work

        return [hydrated_by_id[work_id] for work_id in work_ids if work_id in hydrated_by_id]

    async def fetch_citations(self, work: dict[str, Any]) -> list[dict[str, Any]]:
        work_id = self._extract_work_id(work.get("id"))
        if not work_id:
            return []

        cursor: str | None = "*"
        citations: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {
                "filter": f"cites:{work_id}",
                "per_page": OPENALEX_CITED_BY_PAGE_SIZE,
                "select": OPENALEX_RELATION_WORK_SELECT,
            }
            if cursor:
                params["cursor"] = cursor

            payload = await self._get_json(OPENALEX_WORKS_URL, params=params)
            citations.extend(payload.get("results") or [])

            meta = payload.get("meta") or {}
            cursor = meta.get("next_cursor")
            if not cursor:
                break

        return citations

    def build_related_work_candidate(self, work: dict[str, Any]) -> RelatedWorkCandidate:
        return RelatedWorkCandidate(
            title=work.get("display_name") or work.get("title") or "",
            direct_arxiv_url=self._canonical_arxiv_url(work),
            doi_url=_normalize_doi_url(work.get("doi")),
            landing_page_url=self._extract_landing_page_url(work),
            openalex_url=work.get("id") or "",
        )

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        retry_attempt = 0
        rate_limit_attempt = 0

        while True:
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    request_params = dict(params or {})
                    if self.openalex_api_key:
                        request_params["api_key"] = self.openalex_api_key

                    async with self.session.get(
                        url, headers=self._build_headers(), params=request_params
                    ) as response:
                        if response.status == 200:
                            return await response.json()

                        if response.status == 429:
                            retry_delay = await self._get_rate_limit_retry_delay(
                                response,
                                attempt=rate_limit_attempt,
                            )
                            if retry_delay is None or rate_limit_attempt >= OPENALEX_429_MAX_RETRIES:
                                raise RuntimeError(f"OpenAlex API error ({response.status})")
                            rate_limit_attempt += 1
                            await asyncio.sleep(retry_delay)
                            continue

                        if response.status in OPENALEX_RETRY_STATUSES and retry_attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**retry_attempt))
                            retry_attempt += 1
                            continue

                        raise RuntimeError(f"OpenAlex API error ({response.status})")
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if retry_attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**retry_attempt))
                        retry_attempt += 1
                        continue
                    raise

        raise RuntimeError("OpenAlex API request failed")

    async def _get_rate_limit_retry_delay(
        self,
        response: aiohttp.ClientResponse,
        *,
        attempt: int,
    ) -> float | None:
        retry_after = self._parse_retry_after_header(response.headers.get("Retry-After"))
        if retry_after is None:
            retry_after = await self._extract_retry_after_from_payload(response)
        if retry_after is None:
            retry_after = OPENALEX_429_FALLBACK_BACKOFF_SECONDS * (2**attempt)
        if retry_after > OPENALEX_429_MAX_RETRY_AFTER_SECONDS:
            return None
        return retry_after

    @staticmethod
    def _parse_retry_after_header(raw_value: str | None) -> float | None:
        text = str(raw_value or "").strip()
        if not text:
            return None

        try:
            return max(0.0, float(text))
        except ValueError:
            pass

        try:
            retry_at = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())

    async def _extract_retry_after_from_payload(self, response: aiohttp.ClientResponse) -> float | None:
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ClientError, ValueError, TypeError):
            return None

        if not isinstance(payload, dict):
            return None

        raw_retry_after = payload.get("retryAfter")
        if raw_retry_after is None:
            return None

        try:
            return max(0.0, float(raw_retry_after))
        except (TypeError, ValueError):
            return None

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": "scripts.ghstars"}
        return headers

    def _resolve_exact_match(
        self,
        work: dict[str, Any],
        *,
        title: str | None = None,
    ) -> tuple[str | None, str | None]:
        direct_arxiv_url = self._canonical_arxiv_url(work)
        resolved_title = " ".join(str(work.get("display_name") or work.get("title") or title or "").split()).strip()
        return direct_arxiv_url, resolved_title or title

    def _canonical_arxiv_url(self, work: dict[str, Any]) -> str | None:
        ids = work.get("ids") or {}
        arxiv_id = ids.get("arxiv")
        if isinstance(arxiv_id, str):
            canonical = normalize_arxiv_url(build_arxiv_abs_url(arxiv_id))
            if canonical:
                return canonical

        doi = work.get("doi")
        doi_arxiv_id = self._extract_arxiv_id_from_doi(doi)
        if doi_arxiv_id:
            canonical = normalize_arxiv_url(build_arxiv_abs_url(doi_arxiv_id))
            if canonical:
                return canonical

        locations = work.get("locations") or []
        for location in locations:
            location_url = None
            if isinstance(location, dict):
                for key in ("landing_page_url", "pdf_url", "url", "source", "source_id"):
                    location_url = location.get(key)
                    if location_url:
                        break
            elif isinstance(location, str):
                location_url = location

            canonical = normalize_arxiv_url(location_url or "")
            if canonical:
                return canonical

        return None

    @staticmethod
    def _extract_arxiv_id_from_doi(doi: str | None) -> str | None:
        if not doi or not isinstance(doi, str):
            return None
        match = ARXIV_DOI_PATTERN.search(doi.strip())
        if not match:
            return None
        return match.group(1)

    @staticmethod
    def _extract_landing_page_url(work: dict[str, Any]) -> str | None:
        locations = work.get("locations") or []
        for location in locations:
            if not isinstance(location, dict):
                continue

            landing_page_url = location.get("landing_page_url")
            if not isinstance(landing_page_url, str):
                continue

            normalized = landing_page_url.strip()
            if normalized:
                return normalized

        return None

    @staticmethod
    def _unique_work_ids(values: Iterable[str | None]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            work_id = OpenAlexClient._extract_work_id(value)
            if not work_id or work_id in seen:
                continue
            seen.add(work_id)
            normalized.append(work_id)
        return normalized

    @staticmethod
    def _chunk_values(values: list[str], chunk_size: int) -> list[list[str]]:
        if chunk_size <= 0:
            return [values]
        return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]

    @staticmethod
    def _extract_work_id(identifier: str | None) -> str | None:
        if not identifier:
            return None

        candidate = identifier.strip()
        if not candidate:
            return None

        if candidate.startswith("http"):
            parsed = urlparse(candidate)
            path = (parsed.path or "").strip("/")
        else:
            path = candidate

        if not path:
            return None

        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return None

        return segments[-1]
