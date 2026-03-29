import asyncio
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

import aiohttp

from src.shared.arxiv import normalize_title_for_matching
from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import build_arxiv_abs_url, normalize_arxiv_url
from src.shared.papers import PaperSeed


OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_SEARCH_PAGE_SIZE = 5
OPENALEX_CITED_BY_PAGE_SIZE = 200
OPENALEX_RETRY_STATUSES = {429, 500, 502, 503, 504}
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

    async def search_first_work(self, title: str) -> dict[str, Any] | None:
        params = {"search": title, "per_page": OPENALEX_SEARCH_PAGE_SIZE}
        payload = await self._get_json(OPENALEX_WORKS_URL, params=params)
        results = payload.get("results") or []
        return results[0] if results else None

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

        tasks = [self._fetch_referenced_work(work_id) for work_id in work_ids]
        hydrated = await asyncio.gather(*tasks)
        return [work for work in hydrated if work is not None]

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

    def normalize_related_work(self, work: dict[str, Any]) -> PaperSeed | None:
        candidate = self.build_related_work_candidate(work)
        if not candidate.direct_arxiv_url:
            return None

        name = candidate.title or candidate.direct_arxiv_url
        return PaperSeed(name=name, url=candidate.direct_arxiv_url)

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(MAX_RETRIES + 1):
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

                        if response.status in OPENALEX_RETRY_STATUSES and attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue

                        raise RuntimeError(f"OpenAlex API error ({response.status})")
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise

        raise RuntimeError("OpenAlex API request failed")

    async def _fetch_referenced_work(self, work_id: str) -> dict[str, Any] | None:
        try:
            return await self._get_json(f"{OPENALEX_WORKS_URL}/{work_id}")
        except RuntimeError as exc:
            if self._is_openalex_not_found_error(exc):
                return None
            raise

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": "scripts.ghstars"}
        return headers

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

    @staticmethod
    def _is_openalex_not_found_error(exc: RuntimeError) -> bool:
        return str(exc) == "OpenAlex API error (404)"
