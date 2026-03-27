import asyncio
import html as html_lib
import json
import re

import aiohttp

from src.shared.arxiv import normalize_title_for_matching
from src.shared.github import normalize_github_url
from src.shared.headless_browser import dump_rendered_html
from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import extract_arxiv_id, normalize_arxiv_url, normalize_semanticscholar_paper_url
from src.shared.settings import HF_EXACT_NO_REPO_THRESHOLD


HUGGINGFACE_PAPER_ID_PATTERN = re.compile(r"^[0-9]{4}\.[0-9]{4,5}$")
HUGGINGFACE_API_MIN_INTERVAL = 0.5
HUGGINGFACE_SEARCH_MAX_CONCURRENT = 1


class _DiscoveryRequestGate:
    def __init__(self, max_concurrent: int, min_interval: float):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)


def resolve_huggingface_min_interval(requested_min_interval: float) -> float:
    return max(requested_min_interval, HUGGINGFACE_API_MIN_INTERVAL)


def find_github_url_in_text(text: str) -> str | None:
    if not text or not isinstance(text, str):
        return None

    pattern = r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+(?:\.git)?/?[),.;:!?]*"
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    for match in matches:
        cleaned = match.rstrip("),.;:!?")
        normalized = normalize_github_url(cleaned)
        if normalized:
            return normalized
    return None


def find_github_url_in_json_payload(payload) -> str | None:
    if isinstance(payload, str):
        return find_github_url_in_text(payload)
    if isinstance(payload, list):
        for item in payload:
            result = find_github_url_in_json_payload(item)
            if result:
                return result
        return None
    if isinstance(payload, dict):
        for value in payload.values():
            result = find_github_url_in_json_payload(value)
            if result:
                return result
        return None
    return None


def find_github_url_in_huggingface_paper_html(html: str) -> str | None:
    if not html or not isinstance(html, str):
        return None

    candidates = [html]
    decoded_html = html_lib.unescape(html)
    if decoded_html != html:
        candidates.insert(0, decoded_html)

    patterns = (
        r'"githubRepo"\s*:\s*"(https://github\.com/[^"]+)"',
        r'<a[^>]*href="(https://github\.com/[^"]+)"[^>]*\b(?:aria-label|title)="GitHub"[^>]*>',
        r'<a[^>]*\b(?:aria-label|title)="GitHub"[^>]*href="(https://github\.com/[^"]+)"[^>]*>',
        r'href="(https://github\.com/[^"]+)"[^>]*>\s*GitHub\s*<',
        r'GitHub\s*</[^>]+>\s*<[^>]+href="(https://github\.com/[^"]+)"',
    )
    for candidate in candidates:
        for pattern in patterns:
            match = re.search(pattern, candidate, flags=re.IGNORECASE)
            if match:
                github_url = normalize_github_url(match.group(1).replace("\\/", "/"))
                if github_url:
                    return github_url

    return None


def find_github_url_in_huggingface_paper_payload(payload) -> str | None:
    if not isinstance(payload, dict):
        return None

    github_url = payload.get("githubRepo")
    if not github_url or not isinstance(github_url, str):
        return None

    return normalize_github_url(github_url)


def find_github_url_in_semanticscholar_paper_html(html: str) -> str | None:
    if not html or not isinstance(html, str):
        return None

    description_patterns = (
        r'<meta[^>]*name="description"[^>]*content="([^"]+)"',
        r'<meta[^>]*name="twitter:description"[^>]*content="([^"]+)"',
        r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"',
    )
    for pattern in description_patterns:
        for match in re.findall(pattern, html, flags=re.IGNORECASE):
            github_url = find_github_url_in_text(html_lib.unescape(match))
            if github_url:
                return github_url

    for match in re.findall(r'<script[^>]*class="schema-data"[^>]*>(.*?)</script>', html, flags=re.IGNORECASE | re.S):
        github_url = find_github_url_in_text(match)
        if github_url:
            return github_url

    return find_github_url_in_text(html)


def find_huggingface_paper_id_in_search_html(html: str, title_query: str | None = None) -> str | None:
    if not html or not isinstance(html, str):
        return None

    if title_query:
        paper_id, _source = extract_best_huggingface_paper_id_from_search_html(html, title_query)
        if paper_id:
            return paper_id

    match = re.search(r"/papers/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", html)
    if match:
        return match.group(1)
    return None


def extract_best_huggingface_paper_id_from_search_html(html: str, title_query: str) -> tuple[str | None, str | None]:
    if not html or not title_query:
        return None, None

    title_query_norm = normalize_title_for_matching(title_query)
    best_id = None
    best_score = -1
    best_source = None

    for item in _iter_huggingface_search_items(html):
        paper_id = str(item.get("paper_id") or "").strip()
        title = normalize_title_for_matching(str(item.get("title") or ""))
        if not HUGGINGFACE_PAPER_ID_PATTERN.match(paper_id) or not title:
            continue

        score = 0
        source = None
        if title == title_query_norm:
            score = 100
            source = "title_search_huggingface_exact"
        elif title_query_norm in title:
            score = 80
            source = "title_search_huggingface_contained"
        elif title in title_query_norm:
            score = 60
            source = "title_search_huggingface_contains_entry"

        if score > 0 and score > best_score:
            best_score = score
            best_id = paper_id
            best_source = source

    return best_id, best_source


def _iter_huggingface_search_items(html: str):
    match = re.search(r'data-target="DailyPapers"[^>]*data-props="([^"]*)"', html)
    if not match:
        return []

    try:
        payload = json.loads(html_lib.unescape(match.group(1)))
    except json.JSONDecodeError:
        return []

    items = payload.get("searchResults")
    if not isinstance(items, list) or not items:
        items = payload.get("dailyPapers")
    if not isinstance(items, list):
        return []

    output = []
    for item in items:
        if not isinstance(item, dict):
            continue
        paper = item.get("paper", {})
        if not isinstance(paper, dict):
            continue

        output.append(
            {
                "paper_id": str(paper.get("id") or "").strip(),
                "title": " ".join(str(item.get("title") or paper.get("title") or "").split()).strip(),
            }
        )

    return output


def find_github_url_in_alphaxiv_legacy_payload(payload) -> str | None:
    if not isinstance(payload, dict):
        return None

    paper = payload.get("paper", {}) if isinstance(payload.get("paper"), dict) else {}
    candidates = [
        paper.get("implementation"),
        paper.get("marimo_implementation"),
        paper.get("paper_group", {}).get("resources") if isinstance(paper.get("paper_group"), dict) else None,
        paper.get("resources"),
    ]

    for candidate in candidates:
        github_url = find_github_url_in_json_payload(candidate)
        if github_url:
            return github_url

    return find_github_url_in_json_payload(payload)


class DiscoveryClient:
    """Discovery client for GitHub repo lookup and paper identity helpers."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        huggingface_token: str = "",
        alphaxiv_token: str = "",
        repo_cache=None,
        hf_exact_no_repo_threshold: int = HF_EXACT_NO_REPO_THRESHOLD,
        max_concurrent: int = 5,
        min_interval: float = 0.2,
    ):
        self.session = session
        self.huggingface_token = huggingface_token
        self.alphaxiv_token = alphaxiv_token
        self.repo_cache = repo_cache
        self.hf_exact_no_repo_threshold = hf_exact_no_repo_threshold
        self._huggingface_gate = _DiscoveryRequestGate(max_concurrent, resolve_huggingface_min_interval(min_interval))
        self._huggingface_search_semaphore = asyncio.Semaphore(HUGGINGFACE_SEARCH_MAX_CONCURRENT)
        self._alphaxiv_gate = _DiscoveryRequestGate(max_concurrent, min_interval)
        self._semanticscholar_gate = _DiscoveryRequestGate(max_concurrent, min_interval)
        self.semaphore = self._huggingface_gate.semaphore
        self.rate_limiter = self._huggingface_gate.rate_limiter
        self._github_resolution_cache: dict[str, str] = {}
        self._github_resolution_tasks: dict[str, asyncio.Task[str | None]] = {}
        self._github_resolution_lock = asyncio.Lock()

    async def _request(
        self,
        url: str,
        *,
        headers=None,
        params=None,
        expect: str = "text",
        retry_prefix: str = "Request",
        gate: _DiscoveryRequestGate,
        allow_statuses: set[int] | None = None,
    ):
        allow_statuses = allow_statuses or set()
        for attempt in range(MAX_RETRIES + 1):
            async with gate.semaphore:
                await gate.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers=headers, params=params) as response:
                        if response.status == 200:
                            if expect == "json":
                                return await response.json(), None
                            return await response.text(), None
                        if response.status in allow_statuses:
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

    def _build_huggingface_headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "scripts.ghstars",
        }
        if self.huggingface_token:
            headers["Authorization"] = f"Bearer {self.huggingface_token}"
        return headers

    async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id: str):
        return await self._request(
            f"https://huggingface.co/api/papers/{arxiv_id}",
            headers=self._build_huggingface_headers("application/json"),
            expect="json",
            retry_prefix="Hugging Face Papers API",
            gate=self._huggingface_gate,
            allow_statuses={404},
        )

    async def get_huggingface_paper_html_by_arxiv_id(self, arxiv_id: str):
        if not self.huggingface_token:
            return None, "Missing HUGGINGFACE_TOKEN"
        return await self._request(
            f"https://huggingface.co/papers/{arxiv_id}",
            headers=self._build_huggingface_headers("text/html,application/json"),
            expect="text",
            retry_prefix="Hugging Face Papers",
            gate=self._huggingface_gate,
        )

    async def get_huggingface_search_html(self, title: str):
        if not self.huggingface_token:
            return None, "Missing HUGGINGFACE_TOKEN"
        async with self._huggingface_search_semaphore:
            return await self._request(
                "https://huggingface.co/papers",
                headers=self._build_huggingface_headers("text/html,application/json"),
                params={"q": title},
                expect="text",
                retry_prefix="Hugging Face Papers",
                gate=self._huggingface_gate,
            )

    async def get_alphaxiv_paper_legacy(self, arxiv_id: str):
        if not self.alphaxiv_token:
            return None, "Missing ALPHAXIV_TOKEN"
        headers = {"Accept": "application/json", "User-Agent": "scripts.ghstars", "Authorization": f"Bearer {self.alphaxiv_token}"}
        return await self._request(
            f"https://api.alphaxiv.org/papers/v3/legacy/{arxiv_id}",
            headers=headers,
            expect="json",
            retry_prefix="AlphaXiv API",
            gate=self._alphaxiv_gate,
        )

    async def get_semanticscholar_paper_html(self, url: str):
        normalized_url = normalize_semanticscholar_paper_url(url)
        if not normalized_url:
            return None, "Unsupported Semantic Scholar paper URL"

        async with self._semanticscholar_gate.semaphore:
            await self._semanticscholar_gate.rate_limiter.acquire()
            try:
                html = await dump_rendered_html(
                    normalized_url,
                    virtual_time_budget_ms=3000,
                    timeout_seconds=8.0,
                )
                return html, None
            except asyncio.TimeoutError:
                return None, "Semantic Scholar paper timeout"
            except Exception as exc:
                return None, f"Semantic Scholar paper request failed: {exc}"

    async def resolve_github_url(self, seed) -> str | None:
        cache_key = _discovery_cache_key(seed)
        if cache_key is None:
            return await resolve_github_url(seed, self)

        async with self._github_resolution_lock:
            cached = self._github_resolution_cache.get(cache_key)
            if cached is not None:
                return cached

            task = self._github_resolution_tasks.get(cache_key)
            if task is None:
                task = asyncio.create_task(resolve_github_url(seed, self))
                self._github_resolution_tasks[cache_key] = task

        try:
            github_url = await task
        finally:
            async with self._github_resolution_lock:
                if self._github_resolution_tasks.get(cache_key) is task:
                    self._github_resolution_tasks.pop(cache_key, None)

        if github_url:
            async with self._github_resolution_lock:
                self._github_resolution_cache[cache_key] = github_url

        return github_url


async def resolve_github_url(seed, client) -> str | None:
    url = getattr(seed, "url", "")
    arxiv_id = extract_arxiv_id(url)
    if not arxiv_id:
        normalized_semanticscholar_url = normalize_semanticscholar_paper_url(url)
        if not normalized_semanticscholar_url:
            return None

        fetcher = getattr(client, "get_semanticscholar_paper_html", None)
        if not callable(fetcher):
            return None

        html, error = await fetcher(normalized_semanticscholar_url)
        if error:
            return None
        return find_github_url_in_semanticscholar_paper_html(html)

    normalized_arxiv_url = normalize_arxiv_url(url)
    repo_cache = getattr(client, "repo_cache", None)
    if repo_cache is not None and normalized_arxiv_url:
        cached = repo_cache.get(normalized_arxiv_url)
        if cached is not None:
            if cached.github_url:
                return cached.github_url

            threshold = getattr(client, "hf_exact_no_repo_threshold", HF_EXACT_NO_REPO_THRESHOLD)
            if cached.hf_exact_no_repo_count >= threshold:
                return None

    exact_fetcher = getattr(client, "get_huggingface_paper_payload_by_arxiv_id", None)
    if callable(exact_fetcher):
        payload, error = await exact_fetcher(arxiv_id)
        if not error:
            github_url = find_github_url_in_huggingface_paper_payload(payload)
            if github_url:
                if repo_cache is not None and normalized_arxiv_url:
                    repo_cache.record_found_repo(normalized_arxiv_url, github_url)
                return github_url
            if repo_cache is not None and normalized_arxiv_url:
                repo_cache.record_exact_no_repo(normalized_arxiv_url)
        return None

    return None


async def resolve_arxiv_id_by_title(
    title: str,
    *,
    discovery_client=None,
    arxiv_client=None,
) -> tuple[str | None, str | None, str | None]:
    if not title:
        return None, None, "Missing title"

    if (
        discovery_client is not None
        and getattr(discovery_client, "huggingface_token", "")
        and callable(getattr(discovery_client, "get_huggingface_search_html", None))
    ):
        search_html, error = await discovery_client.get_huggingface_search_html(title)
        if not error:
            arxiv_id, source = extract_best_huggingface_paper_id_from_search_html(search_html, title)
            if arxiv_id:
                return arxiv_id, source, None

    if arxiv_client is not None:
        return await arxiv_client.get_arxiv_id_by_title(title)

    return None, None, "No arXiv ID found from title search"


def _discovery_cache_key(seed) -> str | None:
    url = getattr(seed, "url", "")
    normalized_url = normalize_arxiv_url(url) or normalize_semanticscholar_paper_url(url)
    if normalized_url:
        return normalized_url
    return None
