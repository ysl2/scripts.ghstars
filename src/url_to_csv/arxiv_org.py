import asyncio
import html as html_lib
from pathlib import Path
import re
from urllib.parse import parse_qsl, parse_qs, urlencode, urlparse, urlunparse

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import normalize_arxiv_url
from src.shared.papers import PaperSeed
from src.url_to_csv.models import FetchedSeedsResult


ARXIV_ORG_HOSTS = {"arxiv.org", "www.arxiv.org"}
LIST_ENTRY_PATTERN = re.compile(
    r"<dt\b.*?>.*?href\s*=\s*[\"'](?:https?://(?:www\.)?arxiv\.org)?/abs/([^\"']+)[\"'].*?</dt>\s*<dd\b.*?>(.*?)</dd>",
    re.IGNORECASE | re.S,
)
LIST_TITLE_PATTERN = re.compile(
    r"<div[^>]*class=[\"'][^\"']*list-title[^\"']*[\"'][^>]*>(.*?)</div>",
    re.IGNORECASE | re.S,
)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
SEARCH_RESULT_PATTERN = re.compile(
    r"<li[^>]*class=[\"'][^\"']*arxiv-result[^\"']*[\"'][^>]*>(.*?)</li>",
    re.IGNORECASE | re.S,
)
SEARCH_LINK_PATTERN = re.compile(
    r"<p[^>]*class=[\"'][^\"']*list-title[^\"']*[\"'][^>]*>.*?<a[^>]*href=[\"']((?:https?://(?:www\.)?arxiv\.org)?/abs/[^\"']+)[\"']",
    re.IGNORECASE | re.S,
)
SEARCH_TITLE_PATTERN = re.compile(
    r"<p[^>]*class=[\"'][^\"']*title is-5 mathjax[^\"']*[\"'][^>]*>(.*?)</p>",
    re.IGNORECASE | re.S,
)
LIST_TOTAL_PATTERN = re.compile(r"Total of\s+([\d,]+)\s+entries", re.IGNORECASE)
LIST_PAGE_SIZE_PATTERN = re.compile(r"Showing up to\s+([\d,]+)\s+entries per page", re.IGNORECASE)
SEARCH_TOTAL_PATTERN = re.compile(
    r"Showing\s+\d+\s*(?:&ndash;|&#8211;|–|-)\s*\d+\s+of\s+([\d,]+)\s+results",
    re.IGNORECASE,
)


def is_supported_arxiv_org_url(raw_url: str) -> bool:
    if not raw_url or not isinstance(raw_url, str):
        return False

    parsed = urlparse(raw_url)
    host = (parsed.netloc or parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if parsed.scheme not in {"http", "https"} or host not in ARXIV_ORG_HOSTS:
        return False

    if path.startswith("/list/"):
        return True

    return path == "/search"


def output_csv_path_for_arxiv_org_url(raw_url: str, *, output_dir: Path | None = None) -> Path:
    parsed = urlparse(raw_url)
    directory = Path(output_dir) if output_dir is not None else Path.cwd()
    path = parsed.path.rstrip("/")

    if path.startswith("/list/"):
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3:
            category = _sanitize_filename_part(parts[1])
            mode = _sanitize_filename_part(parts[2])
            return directory / f"arxiv-{category}-{mode}.csv"

    if path == "/search":
        query = parse_qs(parsed.query, keep_blank_values=False)
        search_text = _slugify(query.get("query", [""])[0] or "search")
        search_type = _sanitize_filename_part((query.get("searchtype", ["all"])[0] or "all").strip()) or "all"
        order = ((query.get("order", [""])[0] or "").strip().lstrip("-").replace("_", "-")) or "relevance"
        return directory / f"arxiv-search-{search_text}-{search_type}-{_sanitize_filename_part(order)}.csv"

    return directory / "arxiv-collection.csv"


def extract_paper_seeds_from_arxiv_list_html(html_text: str) -> list[PaperSeed]:
    if not html_text or not isinstance(html_text, str):
        return []

    seeds: list[PaperSeed] = []
    seen_urls: set[str] = set()
    for raw_id, dd_html in LIST_ENTRY_PATTERN.findall(html_text):
        title_match = LIST_TITLE_PATTERN.search(dd_html)
        if not title_match:
            continue

        title = _normalize_list_title(title_match.group(1))
        normalized_url = normalize_arxiv_url(f"https://arxiv.org/abs/{raw_id}")
        if not title or not normalized_url or normalized_url in seen_urls:
            continue

        seeds.append(PaperSeed(name=title, url=normalized_url))
        seen_urls.add(normalized_url)

    return seeds


def extract_paper_seeds_from_arxiv_search_html(html_text: str) -> list[PaperSeed]:
    if not html_text or not isinstance(html_text, str):
        return []

    seeds: list[PaperSeed] = []
    seen_urls: set[str] = set()
    for block in SEARCH_RESULT_PATTERN.findall(html_text):
        link_match = SEARCH_LINK_PATTERN.search(block)
        title_match = SEARCH_TITLE_PATTERN.search(block)
        if not link_match or not title_match:
            continue

        normalized_url = normalize_arxiv_url(link_match.group(1))
        title = _normalize_html_text(title_match.group(1))
        if not normalized_url or not title or normalized_url in seen_urls:
            continue

        seeds.append(PaperSeed(name=title, url=normalized_url))
        seen_urls.add(normalized_url)

    return seeds


async def fetch_paper_seeds_from_arxiv_org_url(
    input_url: str,
    *,
    arxiv_org_client,
    output_dir: Path | None = None,
    status_callback=None,
) -> FetchedSeedsResult:
    if not is_supported_arxiv_org_url(input_url):
        raise ValueError(f"Unsupported arXiv collection URL: {input_url}")

    parsed = urlparse(input_url)
    path = parsed.path.rstrip("/")
    csv_path = output_csv_path_for_arxiv_org_url(input_url, output_dir=output_dir)

    if path.startswith("/list/"):
        seeds = await _fetch_list_seeds(
            input_url,
            arxiv_org_client=arxiv_org_client,
            status_callback=status_callback,
        )
        return FetchedSeedsResult(seeds=seeds, csv_path=csv_path)

    if path == "/search":
        seeds = await _fetch_search_seeds(
            input_url,
            arxiv_org_client=arxiv_org_client,
            status_callback=status_callback,
        )
        return FetchedSeedsResult(seeds=seeds, csv_path=csv_path)

    raise ValueError(f"Unsupported arXiv collection URL: {input_url}")


class ArxivOrgClient:
    def __init__(self, session: aiohttp.ClientSession, max_concurrent: int = 5, min_interval: float = 0.2):
        self.session = session
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def fetch_page_html(self, url: str) -> str:
        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers={"User-Agent": "scripts.ghstars"}) as response:
                        if response.status == 200:
                            return await response.text()
                        if response.status in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue
                        raise ValueError(f"arXiv.org page error ({response.status})")
                except asyncio.TimeoutError:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ValueError("arXiv.org page timeout") from None
                except aiohttp.ClientError as exc:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ValueError(f"arXiv.org page failed: {exc}") from exc

        raise ValueError("arXiv.org page error")


async def _fetch_list_seeds(input_url: str, *, arxiv_org_client, status_callback=None) -> list[PaperSeed]:
    if callable(status_callback):
        status_callback("🔎 Fetching arXiv.org list page 1")

    first_html = await arxiv_org_client.fetch_page_html(input_url)
    first_page_seeds = extract_paper_seeds_from_arxiv_list_html(first_html)
    total_entries = _extract_list_total_entries(first_html)
    if total_entries is None:
        raise ValueError("Cannot determine total entries from arXiv list page")

    page_size = _extract_list_page_size(input_url, first_html, first_page_seeds)
    if page_size is None or page_size <= 0:
        raise ValueError("Cannot determine page size from arXiv list page")

    if callable(status_callback):
        status_callback(f"📄 Fetched arXiv.org list page 1: {len(first_page_seeds)} results")

    seeds = list(first_page_seeds)
    seen_urls = {seed.url for seed in seeds}
    if total_entries <= len(first_page_seeds):
        return seeds

    tasks = [
        asyncio.create_task(
            _fetch_list_page(
                arxiv_org_client,
                build_arxiv_list_page_url(input_url, skip=skip, show=page_size),
                page_number=index,
                status_callback=status_callback,
            )
        )
        for index, skip in enumerate(range(page_size, total_entries, page_size), start=2)
    ]

    for task in asyncio.as_completed(tasks):
        page_seeds = await task
        _append_unique_seeds(seeds, seen_urls, page_seeds)

    return seeds


async def _fetch_search_seeds(input_url: str, *, arxiv_org_client, status_callback=None) -> list[PaperSeed]:
    if callable(status_callback):
        status_callback("🔎 Fetching arXiv.org search page 1")

    first_html = await arxiv_org_client.fetch_page_html(input_url)
    first_page_seeds = extract_paper_seeds_from_arxiv_search_html(first_html)
    total_results = _extract_search_total_results(first_html)
    if total_results is None:
        raise ValueError("Cannot determine total results from arXiv search page")

    page_size = _extract_search_page_size(input_url, first_page_seeds)
    if page_size is None or page_size <= 0:
        raise ValueError("Cannot determine page size from arXiv search page")

    if callable(status_callback):
        status_callback(f"📄 Fetched arXiv.org search page 1: {len(first_page_seeds)} results")

    seeds = list(first_page_seeds)
    seen_urls = {seed.url for seed in seeds}
    if total_results <= len(first_page_seeds):
        return seeds

    tasks = [
        asyncio.create_task(
            _fetch_search_page(
                arxiv_org_client,
                build_arxiv_search_page_url(input_url, start=start),
                page_number=index,
                status_callback=status_callback,
            )
        )
        for index, start in enumerate(range(page_size, total_results, page_size), start=2)
    ]

    for task in asyncio.as_completed(tasks):
        page_seeds = await task
        _append_unique_seeds(seeds, seen_urls, page_seeds)

    return seeds


async def _fetch_list_page(client, url: str, *, page_number: int, status_callback=None) -> list[PaperSeed]:
    if callable(status_callback):
        status_callback(f"🔎 Fetching arXiv.org list page {page_number}")
    html_text = await client.fetch_page_html(url)
    seeds = extract_paper_seeds_from_arxiv_list_html(html_text)
    if callable(status_callback):
        status_callback(f"📄 Fetched arXiv.org list page {page_number}: {len(seeds)} results")
    return seeds


async def _fetch_search_page(client, url: str, *, page_number: int, status_callback=None) -> list[PaperSeed]:
    if callable(status_callback):
        status_callback(f"🔎 Fetching arXiv.org search page {page_number}")
    html_text = await client.fetch_page_html(url)
    seeds = extract_paper_seeds_from_arxiv_search_html(html_text)
    if callable(status_callback):
        status_callback(f"📄 Fetched arXiv.org search page {page_number}: {len(seeds)} results")
    return seeds


def build_arxiv_list_page_url(raw_url: str, *, skip: int, show: int) -> str:
    parsed = urlparse(raw_url)
    params = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=False) if key not in {"skip", "show"}]
    if skip <= 0 and not any(key in {"skip", "show"} for key, _value in parse_qsl(parsed.query, keep_blank_values=False)):
        return raw_url
    if skip > 0:
        params.append(("skip", str(skip)))
    params.append(("show", str(show)))
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def build_arxiv_search_page_url(raw_url: str, *, start: int) -> str:
    parsed = urlparse(raw_url)
    params = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=False) if key != "start"]
    if start <= 0 and not any(key == "start" for key, _value in parse_qsl(parsed.query, keep_blank_values=False)):
        return raw_url
    if start > 0:
        params.append(("start", str(start)))
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def _normalize_list_title(raw_html: str) -> str:
    text = _normalize_html_text(raw_html)
    text = re.sub(r"^Title:\s*", "", text)
    return text.strip()


def _normalize_html_text(raw_html: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", " ", raw_html, flags=re.S)
    without_tags = HTML_TAG_PATTERN.sub(" ", without_comments)
    return html_lib.unescape(" ".join(without_tags.split())).strip()


def _append_unique_seeds(target: list[PaperSeed], seen_urls: set[str], page_seeds: list[PaperSeed]) -> None:
    for seed in page_seeds:
        if seed.url in seen_urls:
            continue
        target.append(seed)
        seen_urls.add(seed.url)


def _extract_list_total_entries(html_text: str) -> int | None:
    match = LIST_TOTAL_PATTERN.search(html_text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _extract_list_page_size(input_url: str, html_text: str, current_seeds: list[PaperSeed]) -> int | None:
    query = parse_qs(urlparse(input_url).query, keep_blank_values=False)
    if "show" in query and query["show"]:
        try:
            return int(query["show"][0])
        except ValueError:
            return None

    match = LIST_PAGE_SIZE_PATTERN.search(html_text)
    if match:
        return int(match.group(1).replace(",", ""))

    if current_seeds:
        return len(current_seeds)
    return None


def _extract_search_total_results(html_text: str) -> int | None:
    match = SEARCH_TOTAL_PATTERN.search(html_text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _extract_search_page_size(input_url: str, current_seeds: list[PaperSeed]) -> int | None:
    query = parse_qs(urlparse(input_url).query, keep_blank_values=False)
    if "size" in query and query["size"]:
        try:
            return int(query["size"][0])
        except ValueError:
            return None

    if current_seeds:
        return len(current_seeds)
    return None


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower() or "search"


def _sanitize_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
