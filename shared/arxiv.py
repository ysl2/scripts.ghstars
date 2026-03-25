import asyncio
from datetime import datetime
import re
import xml.etree.ElementTree as ET

import aiohttp

from shared.http import MAX_RETRIES, RateLimiter
from shared.paper_identity import extract_arxiv_id


ARXIV_SUBMITTED_PATTERN = re.compile(r"\[Submitted on (\d{1,2} [A-Za-z]{3} \d{4})\b", re.IGNORECASE)


def normalize_title_for_matching(title: str) -> str:
    if not title or not isinstance(title, str):
        return ""
    return " ".join(title.split()).strip().lower()


def extract_best_arxiv_id_from_feed(feed_xml: str, title_query: str) -> tuple[str | None, str | None]:
    if not feed_xml or not title_query:
        return None, None

    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError:
        return None, None

    title_query_norm = normalize_title_for_matching(title_query)
    best_id = None
    best_score = -1
    best_source = None

    for entry in root.findall("a:entry", ns):
        title_el = entry.find("a:title", ns)
        id_el = entry.find("a:id", ns)
        if title_el is None or id_el is None or not title_el.text or not id_el.text:
            continue

        title = normalize_title_for_matching(title_el.text)
        entry_id = id_el.text.strip()
        match = re.search(r"/abs/([0-9]{4}\.[0-9]{4,5})(v\d+)?$", entry_id)
        if not match:
            continue

        arxiv_id = match.group(1)
        score = 0
        source = None
        if title == title_query_norm:
            score = 100
            source = "title_search_exact"
        elif title_query_norm in title:
            score = 80
            source = "title_search_contained"
        elif title in title_query_norm:
            score = 60
            source = "title_search_contains_entry"

        if score > best_score:
            best_score = score
            best_id = arxiv_id
            best_source = source

    return best_id, best_source


def extract_published_date_from_feed(feed_xml: str, arxiv_url: str) -> str | None:
    arxiv_id = extract_arxiv_id(arxiv_url)
    if not feed_xml or not arxiv_id:
        return None

    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError:
        return None

    for entry in root.findall("a:entry", ns):
        id_el = entry.find("a:id", ns)
        published_el = entry.find("a:published", ns)
        if id_el is None or published_el is None or not id_el.text or not published_el.text:
            continue

        entry_id = extract_arxiv_id(id_el.text.strip())
        if entry_id == arxiv_id:
            return published_el.text.strip()[:10]

    return None


def extract_submitted_date_from_abs_html(html: str) -> str | None:
    if not html or not isinstance(html, str):
        return None

    match = ARXIV_SUBMITTED_PATTERN.search(html)
    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


class ArxivClient:
    def __init__(self, session: aiohttp.ClientSession, max_concurrent: int = 5, min_interval: float = 0.2):
        self.session = session
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def _request_text(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        retry_prefix: str = "arXiv request",
    ) -> tuple[str | None, str | None]:
        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, params=params) as response:
                        if response.status == 200:
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

    async def get_published_date(self, arxiv_url: str) -> tuple[str | None, str | None]:
        arxiv_id = extract_arxiv_id(arxiv_url)
        if not arxiv_id:
            return None, "Invalid arXiv URL"

        html, error = await self._request_text(
            f"https://arxiv.org/abs/{arxiv_id}",
            retry_prefix="arXiv abs page",
        )
        if error:
            return None, error

        date = extract_submitted_date_from_abs_html(html)
        if not date:
            return None, "No submitted date found on arXiv abs page"
        return date, None

    async def get_published_dates(self, arxiv_urls: list[str]) -> tuple[dict[str, str], dict[str, str]]:
        unique_urls = []
        seen_urls: set[str] = set()
        for url in arxiv_urls:
            if url not in seen_urls:
                unique_urls.append(url)
                seen_urls.add(url)

        tasks = [asyncio.create_task(self.get_published_date(url)) for url in unique_urls]
        dates_by_url: dict[str, str] = {}
        errors: dict[str, str] = {}

        for url, task in zip(unique_urls, tasks, strict=True):
            date, error = await task
            if date:
                dates_by_url[url] = date
            elif error:
                errors[url] = error

        return dates_by_url, errors

    async def get_arxiv_id_by_title(self, title: str) -> tuple[str | None, str | None, str | None]:
        if not title:
            return None, None, "Missing title"

        feed_xml, error = await self._request_text(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": f'ti:"{title}"',
                "start": "0",
                "max_results": "10",
            },
            retry_prefix="arXiv API",
        )
        if error:
            return None, None, error

        arxiv_id, source = extract_best_arxiv_id_from_feed(feed_xml, title)
        if not arxiv_id:
            return None, None, "No arXiv ID found from title search"
        return arxiv_id, source, None
