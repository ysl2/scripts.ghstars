import asyncio
import re
from typing import Any, Iterable
from urllib.parse import urlparse

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import build_arxiv_abs_url, normalize_arxiv_url
from src.shared.papers import PaperSeed


OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_SEARCH_PAGE_SIZE = 5
OPENALEX_CITED_BY_PAGE_SIZE = 200
OPENALEX_RETRY_STATUSES = {429, 500, 502, 503, 504}
ARXIV_DOI_PATTERN = re.compile(r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.IGNORECASE)


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

    async def fetch_referenced_works(self, work: dict[str, Any]) -> list[dict[str, Any]]:
        referenced = work.get("referenced_works") or []
        work_ids = self._unique_work_ids(referenced)
        if not work_ids:
            return []

        tasks = [self._get_json(f"{OPENALEX_WORKS_URL}/{work_id}") for work_id in work_ids]
        return await asyncio.gather(*tasks)

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

    def normalize_related_work(self, work: dict[str, Any]) -> PaperSeed | None:
        canonical = self._canonical_arxiv_url(work)
        if not canonical:
            return None

        name = work.get("display_name") or work.get("title") or canonical
        return PaperSeed(name=name, url=canonical)

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
                location_url = location.get("url")
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
