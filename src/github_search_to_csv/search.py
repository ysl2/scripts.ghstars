import asyncio
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Protocol
from urllib.parse import parse_qs, urlparse

import aiohttp

from src.github_search_to_csv.models import (
    RepositorySearchRow,
    SearchPartition,
    SearchRequest,
)
from src.shared.http import MAX_RETRIES, RateLimiter


GITHUB_SEARCH_HOSTS = {"github.com", "www.github.com"}
GITHUB_SEARCH_UNAUTHENTICATED_MIN_INTERVAL = 6.0


class SearchClient(Protocol):
    async def count_results(self, partition: SearchPartition) -> int: ...

    async def fetch_partition(self, partition: SearchPartition) -> list[RepositorySearchRow]: ...


def parse_github_search_url(url: str) -> SearchRequest:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Expected a GitHub search URL")

    host = (parsed.hostname or "").lower()
    if host not in GITHUB_SEARCH_HOSTS:
        raise ValueError("Expected a GitHub search URL")

    if parsed.path.rstrip("/") != "/search":
        raise ValueError("Expected a GitHub search URL")

    params = parse_qs(parsed.query)
    if params.get("type", ["repositories"])[0] != "repositories":
        raise ValueError("Expected a repository search URL")

    query = params.get("q", [""])[0].strip()
    if not query:
        raise ValueError("Missing q parameter")

    return SearchRequest(
        query=query,
        sort=(params.get("s", ["stars"])[0] or "stars").strip() or "stars",
        order=(params.get("o", ["desc"])[0] or "desc").strip() or "desc",
    )


def is_supported_github_search_url(url: str) -> bool:
    try:
        parse_github_search_url(url)
    except ValueError:
        return False
    return True


def render_query(partition: SearchPartition) -> str:
    parts = [partition.request.query]
    if partition.stars_min is not None and partition.stars_max is not None:
        parts.append(f"stars:{partition.stars_min}..{partition.stars_max}")
    if partition.created_after is not None and partition.created_before is not None:
        parts.append(
            "created:"
            f"{partition.created_after.isoformat()}..{partition.created_before.isoformat()}"
        )
    return " ".join(parts)


def split_star_range(
    partition: SearchPartition,
) -> tuple[SearchPartition, SearchPartition] | None:
    if partition.stars_min is None or partition.stars_max is None:
        return None
    if partition.stars_min >= partition.stars_max:
        return None

    midpoint = (partition.stars_min + partition.stars_max) // 2
    return (
        SearchPartition(
            request=partition.request,
            stars_min=partition.stars_min,
            stars_max=midpoint,
            created_after=partition.created_after,
            created_before=partition.created_before,
        ),
        SearchPartition(
            request=partition.request,
            stars_min=midpoint + 1,
            stars_max=partition.stars_max,
            created_after=partition.created_after,
            created_before=partition.created_before,
        ),
    )


def split_created_range(
    partition: SearchPartition,
) -> tuple[SearchPartition, SearchPartition] | None:
    if partition.created_after is None or partition.created_before is None:
        return None
    if partition.created_after >= partition.created_before:
        return None

    span_days = (partition.created_before - partition.created_after).days
    midpoint = partition.created_after + timedelta(days=span_days // 2)
    return (
        SearchPartition(
            request=partition.request,
            stars_min=partition.stars_min,
            stars_max=partition.stars_max,
            created_after=partition.created_after,
            created_before=midpoint,
        ),
        SearchPartition(
            request=partition.request,
            stars_min=partition.stars_min,
            stars_max=partition.stars_max,
            created_after=midpoint + timedelta(days=1),
            created_before=partition.created_before,
        ),
    )


def resolve_github_search_min_interval(
    github_token: str,
    requested_min_interval: float,
) -> float:
    if github_token.strip():
        return requested_min_interval
    return max(requested_min_interval, GITHUB_SEARCH_UNAUTHENTICATED_MIN_INTERVAL)


class GitHubRepositorySearchClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        github_token: str = "",
        max_concurrent: int = 1,
        min_interval: float = 0.2,
        progress: Callable[[str], None] | None = None,
    ):
        self.session = session
        self.github_token = github_token
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(
            resolve_github_search_min_interval(github_token, min_interval)
        )
        self.progress = progress

    async def _request_search(self, params: dict[str, str | int]) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "scripts.ghstars",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"

        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(
                        "https://api.github.com/search/repositories",
                        params=params,
                        headers=headers,
                    ) as response:
                        if response.status == 200:
                            return await response.json()

                        if (
                            response.status == 403
                            and response.headers.get("x-ratelimit-remaining") == "0"
                        ):
                            reset_at = float(response.headers.get("x-ratelimit-reset", "0"))
                            await asyncio.sleep(max(0.0, reset_at - time.time()))
                            continue

                        if response.status in {429, 502, 503, 504} and attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue

                        text = await response.text()
                except asyncio.TimeoutError as exc:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise RuntimeError("GitHub search request timed out") from exc
                except aiohttp.ClientError as exc:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise RuntimeError(f"GitHub search request failed: {exc}") from exc

            raise RuntimeError(f"GitHub search API error ({response.status}): {text}")

        raise RuntimeError("GitHub search request exhausted retries")

    async def count_results(self, partition: SearchPartition) -> int:
        payload = await self._request_search(
            {
                "q": render_query(partition),
                "sort": partition.request.sort,
                "order": partition.request.order,
                "page": 1,
                "per_page": 1,
            }
        )
        return int(payload["total_count"])

    async def fetch_partition(self, partition: SearchPartition) -> list[RepositorySearchRow]:
        rows: list[RepositorySearchRow] = []
        page = 1
        while True:
            if self.progress is not None:
                self.progress(f"Fetching page {page}: {render_query(partition)}")

            payload = await self._request_search(
                {
                    "q": render_query(partition),
                    "sort": partition.request.sort,
                    "order": partition.request.order,
                    "page": page,
                    "per_page": 100,
                }
            )
            items = payload["items"]
            if not items:
                break

            rows.extend(
                RepositorySearchRow(
                    github=item["html_url"],
                    stars=item["stargazers_count"],
                    about=item.get("description") or "",
                    created=item.get("created_at") or "",
                )
                for item in items
            )

            if len(items) < 100 or len(rows) >= min(int(payload["total_count"]), 1000):
                break
            page += 1

        return rows

    async def collect_repositories(
        self,
        request: SearchRequest,
        *,
        default_created_after: date = date(2008, 1, 1),
        default_created_before: date | None = None,
        default_stars_min: int = 0,
        default_stars_max: int = 2_000_000,
        progress: Callable[[str], None] | None = None,
    ) -> list[RepositorySearchRow]:
        return await collect_repositories(
            client=self,
            request=request,
            default_created_after=default_created_after,
            default_created_before=default_created_before or date.today(),
            default_stars_min=default_stars_min,
            default_stars_max=default_stars_max,
            progress=progress or self.progress,
        )


async def collect_repositories(
    client: SearchClient,
    request: SearchRequest,
    *,
    default_created_after: date,
    default_created_before: date,
    default_stars_min: int,
    default_stars_max: int,
    progress: Callable[[str], None] | None = None,
) -> list[RepositorySearchRow]:
    pending = [
        SearchPartition(
            request=request,
            stars_min=default_stars_min,
            stars_max=default_stars_max,
            created_after=default_created_after,
            created_before=default_created_before,
        )
    ]
    rows_by_url: dict[str, RepositorySearchRow] = {}

    while pending:
        partition = pending.pop()
        if progress is not None:
            progress(f"Inspecting partition: {render_query(partition)}")

        total_count = await client.count_results(partition)
        if total_count <= 1000:
            if progress is not None:
                progress(
                    "Fetching leaf partition "
                    f"({total_count} results): {render_query(partition)}"
                )
            for row in await client.fetch_partition(partition):
                rows_by_url[row.github] = row
            if progress is not None:
                progress(f"Collected {len(rows_by_url)} unique repositories so far")
            continue

        star_children = split_star_range(partition)
        if star_children is not None:
            if progress is not None:
                progress(
                    "Splitting by stars "
                    f"({total_count} results): {render_query(partition)}"
                )
            pending.extend(reversed(star_children))
            continue

        created_children = split_created_range(partition)
        if created_children is not None:
            if progress is not None:
                progress(
                    "Splitting by created date "
                    f"({total_count} results): {render_query(partition)}"
                )
            pending.extend(reversed(created_children))
            continue

        raise RuntimeError(f"Could not reduce partition below API limit: {partition}")

    return sorted(rows_by_url.values(), key=lambda row: row.github)
