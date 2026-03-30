import asyncio
import re
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import build_arxiv_abs_url, normalize_arxiv_url, normalize_doi_url


CROSSREF_API_URL = "https://api.crossref.org/works"
CROSSREF_RETRY_STATUSES = {429, 500, 502, 503, 504}
ARXIV_DOI_PATTERN = re.compile(r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.IGNORECASE)


class CrossrefClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        max_concurrent: int = 4,
        min_interval: float = 0.2,
    ):
        self.session = session
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def find_arxiv_match_by_doi(self, doi_url: str) -> tuple[str | None, str | None]:
        normalized_doi = normalize_doi_url(doi_url)
        if not normalized_doi:
            return None, None

        doi_path = urlparse(normalized_doi).path.lstrip("/")
        payload = await self._get_json(f"{CROSSREF_API_URL}/{quote(doi_path, safe='')}")
        message = payload.get("message") or {}
        if not isinstance(message, dict):
            return None, None

        return self._extract_arxiv_url(message), self._extract_title(message)

    async def _get_json(self, url: str) -> dict[str, Any]:
        retry_attempt = 0

        while True:
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers=self._build_headers()) as response:
                        if response.status == 200:
                            payload = await response.json()
                            return payload if isinstance(payload, dict) else {}

                        if response.status == 404:
                            return {}

                        if response.status in CROSSREF_RETRY_STATUSES and retry_attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**retry_attempt))
                            retry_attempt += 1
                            continue

                        raise RuntimeError(f"Crossref API error ({response.status})")
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if retry_attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**retry_attempt))
                        retry_attempt += 1
                        continue
                    raise

    @staticmethod
    def _extract_title(message: dict[str, Any]) -> str | None:
        titles = message.get("title")
        if isinstance(titles, list):
            for title in titles:
                normalized = " ".join(str(title or "").split()).strip()
                if normalized:
                    return normalized
        normalized = " ".join(str(message.get("title") or "").split()).strip()
        return normalized or None

    def _extract_arxiv_url(self, message: dict[str, Any]) -> str | None:
        relation = message.get("relation")
        if not isinstance(relation, dict):
            return None

        for relation_value in relation.values():
            for candidate in self._iter_relation_candidates(relation_value):
                arxiv_url = self._normalize_arxiv_candidate(candidate)
                if arxiv_url:
                    return arxiv_url

        return None

    def _iter_relation_candidates(self, relation_value: Any):
        if isinstance(relation_value, list):
            values = relation_value
        else:
            values = [relation_value]

        for item in values:
            if isinstance(item, dict):
                for key in ("id", "identifier", "url"):
                    value = item.get(key)
                    if isinstance(value, str):
                        yield value
            elif isinstance(item, str):
                yield item

    @staticmethod
    def _normalize_arxiv_candidate(candidate: str | None) -> str | None:
        text = str(candidate or "").strip()
        if not text:
            return None

        canonical_url = normalize_arxiv_url(text)
        if canonical_url:
            return canonical_url

        normalized_doi = normalize_doi_url(text)
        if normalized_doi:
            match = ARXIV_DOI_PATTERN.search(normalized_doi)
            if match:
                return normalize_arxiv_url(build_arxiv_abs_url(match.group(1)))

        return normalize_arxiv_url(build_arxiv_abs_url(text))

    @staticmethod
    def _build_headers() -> dict[str, str]:
        return {"User-Agent": "scripts.ghstars"}
