import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter

if TYPE_CHECKING:
    from src.core.repositories import RepoMetadataRepository
    from src.shared.repo_metadata_cache import RepoMetadataCacheStore


GITHUB_UNAUTHENTICATED_MIN_INTERVAL = 60.0


def is_valid_github_repo_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False

    pattern = r"^(https?://)?(www\.)?github\.com/[\w.-]+/[\w.-]+(?:\.git)?/?$"
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


def resolve_github_min_interval(github_token: str, requested_min_interval: float) -> float:
    if github_token.strip():
        return requested_min_interval
    return max(requested_min_interval, GITHUB_UNAUTHENTICATED_MIN_INTERVAL)


@dataclass(frozen=True)
class RepoMetadata:
    stars: int | None
    created: str | None
    about: str | None


class GitHubClient:
    """GitHub API client for repo metadata lookup."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        github_token: str = "",
        repo_metadata_cache: "RepoMetadataCacheStore | None" = None,
        repo_metadata_repository: "RepoMetadataRepository | None" = None,
        max_concurrent: int = 5,
        min_interval: float = 0.2,
    ):
        self.session = session
        self.github_token = github_token
        self.repo_metadata_repository = repo_metadata_repository
        self.repo_metadata_cache = repo_metadata_cache
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(resolve_github_min_interval(github_token, min_interval))
        self._repo_metadata_cache: dict[tuple[str, str], tuple[RepoMetadata | None, str | None]] = {}
        self._repo_metadata_tasks: dict[
            tuple[str, str],
            asyncio.Task[tuple[RepoMetadata | None, str | None]],
        ] = {}
        self._repo_metadata_cache_lock = asyncio.Lock()

    async def get_repo_metadata(self, owner: str, repo: str) -> tuple[RepoMetadata | None, str | None]:
        cache_key = (owner.strip().lower(), repo.strip().lower())
        async with self._repo_metadata_cache_lock:
            cached = self._repo_metadata_cache.get(cache_key)
            if cached is not None:
                return cached

            task = self._repo_metadata_tasks.get(cache_key)
            if task is None:
                task = asyncio.create_task(self._fetch_repo_metadata(owner, repo))
                self._repo_metadata_tasks[cache_key] = task

        try:
            result = await task
        finally:
            async with self._repo_metadata_cache_lock:
                if self._repo_metadata_tasks.get(cache_key) is task:
                    self._repo_metadata_tasks.pop(cache_key, None)

        metadata, error = result

        if metadata is not None and metadata.created:
            self._record_created(owner, repo, metadata.created)

        if _should_cache_repo_metadata_result(metadata, error):
            async with self._repo_metadata_cache_lock:
                self._repo_metadata_cache[cache_key] = result

        return result

    async def get_star_count(self, owner: str, repo: str) -> tuple[int | None, str | None]:
        metadata, error = await self.get_repo_metadata(owner, repo)
        if metadata is None:
            return None, error
        return metadata.stars, error

    async def _fetch_repo_metadata(self, owner: str, repo: str) -> tuple[RepoMetadata | None, str | None]:
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "scripts.ghstars"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"

        url = f"https://api.github.com/repos/{owner}/{repo}"
        cached_created = self._load_cached_created(owner, repo)
        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers=headers) as response:
                        if response.status == 200:
                            payload = await response.json()
                            created = payload.get("created_at") or cached_created
                            about = payload.get("description")
                            if about is None:
                                about = ""
                            return (
                                RepoMetadata(
                                    stars=payload.get("stargazers_count"),
                                    created=created,
                                    about=about,
                                ),
                                None,
                            )
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

    def _load_cached_created(self, owner: str, repo: str) -> str | None:
        github_url = normalize_github_url(f"https://github.com/{owner}/{repo}")
        if github_url is None:
            return None

        try:
            if self.repo_metadata_repository is not None:
                entry = self.repo_metadata_repository.get(github_url)
            elif self.repo_metadata_cache is not None:
                entry = self.repo_metadata_cache.get(github_url)
            else:
                entry = None
        except Exception:
            return None
        return None if entry is None else entry.created

    def _record_created(self, owner: str, repo: str, created: str) -> None:
        github_url = normalize_github_url(f"https://github.com/{owner}/{repo}")
        if github_url is None:
            return

        try:
            if self.repo_metadata_repository is not None:
                self.repo_metadata_repository.record_created(github_url, created)
            elif self.repo_metadata_cache is not None:
                self.repo_metadata_cache.record_created(github_url, created)
        except Exception:
            return


def _should_cache_repo_metadata_result(metadata: RepoMetadata | None, error: str | None) -> bool:
    return error is None or error == "Repository not found"
