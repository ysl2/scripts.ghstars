import asyncio
import re

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter


def is_valid_github_repo_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False

    pattern = r"^(https?://)?(www\.)?github\.com/[\w.-]+/[\w.-]+/?(.git)?$"
    return bool(re.match(pattern, url.strip(), re.IGNORECASE))


def extract_owner_repo(github_url: str) -> tuple[str, str] | None:
    if not is_valid_github_repo_url(github_url):
        return None

    url = github_url.strip()
    url = re.sub(r"^(https?://)?(www\.)?", "", url, flags=re.IGNORECASE)
    url = re.sub(r"^github\.com/", "", url, flags=re.IGNORECASE)
    url = re.sub(r"(\.git)?/?$", "", url)
    parts = url.split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def normalize_github_url(url: str) -> str | None:
    result = extract_owner_repo(url)
    if not result:
        return None
    owner, repo = result
    return f"https://github.com/{owner}/{repo}"


class GitHubClient:
    """GitHub API client for stars lookup."""

    def __init__(self, session: aiohttp.ClientSession, github_token: str = "", max_concurrent: int = 5, min_interval: float = 0.2):
        self.session = session
        self.github_token = github_token
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)
        self._star_cache: dict[tuple[str, str], tuple[int | None, str | None]] = {}
        self._star_tasks: dict[tuple[str, str], asyncio.Task[tuple[int | None, str | None]]] = {}
        self._star_cache_lock = asyncio.Lock()

    async def get_star_count(self, owner: str, repo: str) -> tuple[int | None, str | None]:
        cache_key = (owner.strip().lower(), repo.strip().lower())
        async with self._star_cache_lock:
            cached = self._star_cache.get(cache_key)
            if cached is not None:
                return cached

            task = self._star_tasks.get(cache_key)
            if task is None:
                task = asyncio.create_task(self._fetch_star_count(owner, repo))
                self._star_tasks[cache_key] = task

        try:
            result = await task
        finally:
            async with self._star_cache_lock:
                if self._star_tasks.get(cache_key) is task:
                    self._star_tasks.pop(cache_key, None)

        if _should_cache_star_result(*result):
            async with self._star_cache_lock:
                self._star_cache[cache_key] = result

        return result

    async def _fetch_star_count(self, owner: str, repo: str) -> tuple[int | None, str | None]:
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "scripts.ghstars"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"

        url = f"https://api.github.com/repos/{owner}/{repo}"
        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers=headers) as response:
                        if response.status == 200:
                            payload = await response.json()
                            return payload.get("stargazers_count"), None
                        if response.status == 404:
                            return None, "Repository not found"
                        if response.status in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue
                        return None, f"GitHub API error ({response.status})"
                except asyncio.TimeoutError:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    return None, "Request timeout"
                except Exception as exc:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    return None, f"Request failed: {exc}"
        return None, "GitHub API error"


def _should_cache_star_result(stars: int | None, error: str | None) -> bool:
    return error is None or error == "Repository not found"
