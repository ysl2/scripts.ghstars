import asyncio
import re
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import build_arxiv_abs_url, normalize_arxiv_url, normalize_doi_url


DATACITE_API_URL = "https://api.datacite.org/dois"
DATACITE_RETRY_STATUSES = {429, 500, 502, 503, 504}
ARXIV_DOI_PATTERN = re.compile(r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.IGNORECASE)


class DataCiteClient:
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
        payload = await self._get_json(f"{DATACITE_API_URL}/{quote(doi_path, safe='')}")
        attributes = ((payload.get("data") or {}).get("attributes") or {})
        if not isinstance(attributes, dict):
            return None, None

        return self._extract_arxiv_url(attributes), self._extract_title(attributes)

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

                        if response.status in DATACITE_RETRY_STATUSES and retry_attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**retry_attempt))
                            retry_attempt += 1
                            continue

                        raise RuntimeError(f"DataCite API error ({response.status})")
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if retry_attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**retry_attempt))
                        retry_attempt += 1
                        continue
                    raise

    @staticmethod
    def _extract_title(attributes: dict[str, Any]) -> str | None:
        titles = attributes.get("titles")
        if isinstance(titles, list):
            for item in titles:
                if isinstance(item, dict):
                    normalized = " ".join(str(item.get("title") or "").split()).strip()
                    if normalized:
                        return normalized
        return None

    def _extract_arxiv_url(self, attributes: dict[str, Any]) -> str | None:
        related_identifiers = attributes.get("relatedIdentifiers")
        if not isinstance(related_identifiers, list):
            return None

        for item in related_identifiers:
            if not isinstance(item, dict):
                continue

            arxiv_url = self._normalize_arxiv_candidate(item.get("relatedIdentifier"))
            if arxiv_url:
                return arxiv_url

        return None

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
