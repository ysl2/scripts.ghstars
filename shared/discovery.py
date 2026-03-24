import asyncio
import re

import aiohttp

from shared.github import normalize_github_url
from shared.http import MAX_RETRIES, RateLimiter


ARXIV_ID_PATTERN = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?", re.IGNORECASE)


def extract_arxiv_id(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    match = ARXIV_ID_PATTERN.search(url.strip())
    if not match:
        return None
    return match.group(1)


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

    patterns = (
        r'"githubRepo"\s*:\s*"(https://github\.com/[^"]+)"',
        r'href="(https://github\.com/[^"]+)"[^>]*>\s*GitHub\s*<',
        r'GitHub\s*</[^>]+>\s*<[^>]+href="(https://github\.com/[^"]+)"',
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            github_url = normalize_github_url(match.group(1).replace("\\/", "/"))
            if github_url:
                return github_url

    return find_github_url_in_text(html)


def find_huggingface_paper_id_in_search_html(html: str) -> str | None:
    if not html or not isinstance(html, str):
        return None

    match = re.search(r"/papers/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", html)
    if match:
        return match.group(1)
    return None


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
    """Hugging Face / AlphaXiv discovery client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        huggingface_token: str = "",
        alphaxiv_token: str = "",
        max_concurrent: int = 5,
        min_interval: float = 0.2,
    ):
        self.session = session
        self.huggingface_token = huggingface_token
        self.alphaxiv_token = alphaxiv_token
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def _request(self, url: str, *, headers=None, params=None, expect: str = "text", retry_prefix: str = "Request"):
        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers=headers, params=params) as response:
                        if response.status == 200:
                            if expect == "json":
                                return await response.json(), None
                            return await response.text(), None
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

    async def get_huggingface_paper_html_by_arxiv_id(self, arxiv_id: str):
        if not self.huggingface_token:
            return None, "Missing HUGGINGFACE_TOKEN"
        headers = {"Accept": "text/html,application/json", "User-Agent": "zotero-notion-ghstars", "Authorization": f"Bearer {self.huggingface_token}"}
        return await self._request(
            f"https://huggingface.co/papers/{arxiv_id}",
            headers=headers,
            expect="text",
            retry_prefix="Hugging Face Papers",
        )

    async def get_huggingface_search_html(self, title: str):
        if not self.huggingface_token:
            return None, "Missing HUGGINGFACE_TOKEN"
        headers = {"Accept": "text/html,application/json", "User-Agent": "zotero-notion-ghstars", "Authorization": f"Bearer {self.huggingface_token}"}
        return await self._request(
            "https://huggingface.co/papers",
            headers=headers,
            params={"q": title},
            expect="text",
            retry_prefix="Hugging Face Papers",
        )

    async def get_alphaxiv_paper_legacy(self, arxiv_id: str):
        if not self.alphaxiv_token:
            return None, "Missing ALPHAXIV_TOKEN"
        headers = {"Accept": "application/json", "User-Agent": "zotero-notion-ghstars", "Authorization": f"Bearer {self.alphaxiv_token}"}
        return await self._request(
            f"https://api.alphaxiv.org/papers/v3/legacy/{arxiv_id}",
            headers=headers,
            expect="json",
            retry_prefix="AlphaXiv API",
        )

    async def resolve_github_url(self, seed) -> str | None:
        return await resolve_github_url(seed, self)


async def resolve_github_url(seed, client) -> str | None:
    arxiv_id = extract_arxiv_id(getattr(seed, "url", ""))
    if not arxiv_id:
        return None

    if getattr(client, "huggingface_token", ""):
        html, error = await client.get_huggingface_paper_html_by_arxiv_id(arxiv_id)
        if not error:
            github_url = find_github_url_in_huggingface_paper_html(html)
            if github_url:
                return github_url

        search_html, search_error = await client.get_huggingface_search_html(getattr(seed, "name", ""))
        if not search_error:
            paper_id = find_huggingface_paper_id_in_search_html(search_html)
            if paper_id and paper_id == arxiv_id:
                html, page_error = await client.get_huggingface_paper_html_by_arxiv_id(paper_id)
                if not page_error:
                    github_url = find_github_url_in_huggingface_paper_html(html)
                    if github_url:
                        return github_url

    if getattr(client, "alphaxiv_token", ""):
        payload, error = await client.get_alphaxiv_paper_legacy(arxiv_id)
        if not error:
            return find_github_url_in_alphaxiv_legacy_payload(payload)

    return None
