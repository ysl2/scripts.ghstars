import asyncio

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter


class AlphaXivContentClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        alphaxiv_token: str = "",
        max_concurrent: int = 5,
        min_interval: float = 0.2,
    ):
        self.session = session
        self.alphaxiv_token = alphaxiv_token
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)
        self._paper_cache: dict[str, tuple[dict | None, str | None]] = {}
        self._paper_tasks: dict[str, asyncio.Task[tuple[dict | None, str | None]]] = {}
        self._overview_cache: dict[tuple[str, str], tuple[dict | None, str | None]] = {}
        self._overview_tasks: dict[tuple[str, str], asyncio.Task[tuple[dict | None, str | None]]] = {}
        self._cache_lock = asyncio.Lock()

    async def get_paper_payload_by_arxiv_id(self, arxiv_id: str) -> tuple[dict | None, str | None]:
        cache_key = arxiv_id.strip()
        return await self._get_cached_result(
            cache_key,
            cache=self._paper_cache,
            tasks=self._paper_tasks,
            fetcher=lambda: self._request_json(
                f"https://api.alphaxiv.org/papers/v3/{cache_key}",
                retry_prefix="AlphaXiv paper API",
            ),
        )

    async def get_overview_payload_by_version_id(
        self,
        version_id: str,
        *,
        language: str = "en",
    ) -> tuple[dict | None, str | None]:
        cache_key = (version_id.strip(), language.strip() or "en")
        return await self._get_cached_result(
            cache_key,
            cache=self._overview_cache,
            tasks=self._overview_tasks,
            fetcher=lambda: self._request_json(
                f"https://api.alphaxiv.org/papers/v3/{cache_key[0]}/overview/{cache_key[1]}",
                retry_prefix="AlphaXiv overview API",
            ),
        )

    async def _get_cached_result(self, cache_key, *, cache, tasks, fetcher):
        async with self._cache_lock:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

            task = tasks.get(cache_key)
            if task is None:
                task = asyncio.create_task(fetcher())
                tasks[cache_key] = task

        try:
            result = await task
        finally:
            async with self._cache_lock:
                if tasks.get(cache_key) is task:
                    tasks.pop(cache_key, None)

        if _should_cache_content_result(*result):
            async with self._cache_lock:
                cache[cache_key] = result

        return result

    async def _request_json(self, url: str, *, retry_prefix: str) -> tuple[dict | None, str | None]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "scripts.ghstars",
        }
        if self.alphaxiv_token:
            headers["Authorization"] = f"Bearer {self.alphaxiv_token}"

        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers=headers) as response:
                        if response.status == 200:
                            payload = await response.json()
                            if isinstance(payload, dict):
                                return payload, None
                            return None, f"{retry_prefix} returned unexpected payload"
                        if response.status == 404:
                            return None, None
                        if response.status in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue
                        return None, f"{retry_prefix} error ({response.status})"
                except asyncio.TimeoutError:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    return None, f"{retry_prefix} timeout"
                except Exception as exc:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    return None, f"{retry_prefix} request failed: {exc}"

        return None, f"{retry_prefix} error"


def _should_cache_content_result(payload: dict | None, error: str | None) -> bool:
    return error is None
