import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import build_arxiv_abs_url, normalize_arxiv_url, normalize_doi_url
from src.shared.relation_candidates import RelatedWorkCandidate

SEMANTIC_SCHOLAR_GRAPH_URL = "https://api.semanticscholar.org/graph/v1"
SEMANTIC_SCHOLAR_SEARCH_LIMIT = 5
SEMANTIC_SCHOLAR_RETRY_STATUSES = {500, 502, 503, 504}
SEMANTIC_SCHOLAR_MAX_RETRY_AFTER_SECONDS = 15.0
SEMANTIC_SCHOLAR_AUTHENTICATED_MIN_INTERVAL = 1.0


def resolve_semantic_scholar_min_interval(
    semantic_scholar_api_key: str,
    requested_min_interval: float,
) -> float:
    if semantic_scholar_api_key.strip():
        return max(requested_min_interval, SEMANTIC_SCHOLAR_AUTHENTICATED_MIN_INTERVAL)
    return requested_min_interval


class SemanticScholarGraphClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        semantic_scholar_api_key: str = "",
        max_concurrent: int = 4,
        min_interval: float = 0.2,
    ):
        self.session = session
        self.semantic_scholar_api_key = semantic_scholar_api_key.strip()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(
            resolve_semantic_scholar_min_interval(self.semantic_scholar_api_key, min_interval)
        )

    async def fetch_paper_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        try:
            payload = await self._get_json(
                f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/{identifier}",
                params={"fields": "paperId,title,externalIds"},
            )
        except RuntimeError as exc:
            if "(404)" in str(exc):
                return None
            raise
        return payload if isinstance(payload, dict) and payload.get("paperId") else None

    async def search_papers_by_title(
        self,
        title: str,
        *,
        limit: int = SEMANTIC_SCHOLAR_SEARCH_LIMIT,
    ) -> list[dict[str, Any]]:
        payload = await self._get_json(
            f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/search",
            params={"query": title, "limit": limit, "fields": "paperId,title,externalIds"},
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def fetch_references(self, paper: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._fetch_relation_rows(paper, relation_path="references", row_key="citedPaper")

    async def fetch_citations(self, paper: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._fetch_relation_rows(paper, relation_path="citations", row_key="citingPaper")

    def build_related_work_candidate(self, paper: dict[str, Any]) -> RelatedWorkCandidate:
        external_ids = paper.get("externalIds") or {}
        arxiv_url = self._build_arxiv_url(external_ids.get("ArXiv"))
        doi_url = normalize_doi_url(external_ids.get("DOI"))
        paper_url = self._build_paper_url(paper)
        landing_page_url = arxiv_url or doi_url or paper_url or None
        return RelatedWorkCandidate(
            title=str(paper.get("title") or ""),
            direct_arxiv_url=arxiv_url,
            doi_url=doi_url,
            landing_page_url=landing_page_url,
            source_url=paper_url,
        )

    async def _fetch_relation_rows(
        self,
        paper: dict[str, Any],
        *,
        relation_path: str,
        row_key: str,
    ) -> list[dict[str, Any]]:
        paper_id = str(paper.get("paperId") or "").strip()
        if not paper_id:
            return []

        fields = ",".join(
            [
                f"{row_key}.paperId",
                f"{row_key}.title",
                f"{row_key}.externalIds",
            ]
        )
        unwrapped: list[dict[str, Any]] = []
        next_offset: int | None = 0

        while next_offset is not None:
            payload = await self._get_json(
                f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/{paper_id}/{relation_path}",
                params={"fields": fields, "offset": next_offset},
            )
            rows = payload.get("data")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    related_paper = row.get(row_key)
                    if self._has_usable_related_paper_data(related_paper):
                        unwrapped.append(related_paper)

            maybe_next = payload.get("next")
            if not isinstance(maybe_next, int) or maybe_next < 0 or maybe_next == next_offset:
                next_offset = None
            else:
                next_offset = maybe_next
        return unwrapped

    async def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        retry_attempt = 0

        while True:
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers=self._build_headers(), params=dict(params or {})) as response:
                        if response.status == 200:
                            return await response.json()

                        if response.status == 429:
                            retry_after = self._parse_retry_after_header(response.headers.get("Retry-After"))
                            if retry_after is None:
                                retry_after = 0.5 * (2**retry_attempt)
                            if retry_after > SEMANTIC_SCHOLAR_MAX_RETRY_AFTER_SECONDS or retry_attempt >= MAX_RETRIES:
                                raise RuntimeError(f"Semantic Scholar Graph API error ({response.status})")
                            retry_attempt += 1
                            await asyncio.sleep(retry_after)
                            continue

                        if response.status in SEMANTIC_SCHOLAR_RETRY_STATUSES and retry_attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**retry_attempt))
                            retry_attempt += 1
                            continue

                        raise RuntimeError(f"Semantic Scholar Graph API error ({response.status})")
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if retry_attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**retry_attempt))
                        retry_attempt += 1
                        continue
                    raise

        raise RuntimeError("Semantic Scholar Graph API request failed")

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

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": "scripts.ghstars"}
        if self.semantic_scholar_api_key:
            headers["x-api-key"] = self.semantic_scholar_api_key
        return headers

    @classmethod
    def _has_usable_related_paper_data(cls, paper: Any) -> bool:
        if not isinstance(paper, dict):
            return False

        paper_id = str(paper.get("paperId") or "").strip()
        title = " ".join(str(paper.get("title") or "").split()).strip()
        external_ids = paper.get("externalIds")
        if not isinstance(external_ids, dict):
            external_ids = {}
        arxiv_url = cls._build_arxiv_url(external_ids.get("ArXiv"))
        doi_url = normalize_doi_url(external_ids.get("DOI"))
        return bool(paper_id or title or arxiv_url or doi_url)

    def _build_paper_url(self, paper: dict[str, Any]) -> str:
        paper_id = str(paper.get("paperId") or "").strip()
        if not paper_id:
            return ""
        return f"https://www.semanticscholar.org/paper/{paper_id}"

    @staticmethod
    def _build_arxiv_url(arxiv_id: Any) -> str | None:
        if not isinstance(arxiv_id, str):
            return None
        normalized_arxiv_id = arxiv_id.strip()
        if not normalized_arxiv_id:
            return None
        return normalize_arxiv_url(build_arxiv_abs_url(normalized_arxiv_id))
