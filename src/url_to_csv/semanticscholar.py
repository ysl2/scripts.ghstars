import asyncio
import html as html_lib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.shared.headless_browser import dump_rendered_html
from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import normalize_semanticscholar_paper_url
from src.shared.papers import PaperSeed
from src.url_to_csv.models import FetchedSeedsResult


SEMANTIC_SCHOLAR_HOSTS = {"semanticscholar.org", "www.semanticscholar.org"}
INDEXED_FILTER_PATTERN = re.compile(r"^(?P<name>year|fos|venue)\[(?P<index>[0-9]+)\]$")
TOTAL_PAGES_PATTERN = re.compile(r'<[^>]*data-test-id="result-page-pagination"[^>]*>', re.IGNORECASE)
TITLE_LINK_PATTERN = re.compile(
    r'<a[^>]*data-test-id="title-link"[^>]*href="([^"]+)"[^>]*>\s*<h2[^>]*class="cl-paper-title"[^>]*>(.*?)</h2>\s*</a>',
    re.IGNORECASE | re.S,
)


@dataclass(frozen=True)
class SemanticScholarSearchSpec:
    search_text: str
    years: tuple[str, ...]
    fields_of_study: tuple[str, ...]
    venues: tuple[str, ...]
    sort: str


def is_supported_semanticscholar_url(raw_url: str) -> bool:
    if not raw_url or not isinstance(raw_url, str):
        return False

    parsed = urlparse(raw_url)
    host = (parsed.netloc or parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    return parsed.scheme in {"http", "https"} and host in SEMANTIC_SCHOLAR_HOSTS and path == "/search"


def is_supported_semanticscholar_paper_url(raw_url: str) -> bool:
    return normalize_semanticscholar_paper_url(raw_url) is not None


def parse_semanticscholar_url(raw_url: str) -> SemanticScholarSearchSpec:
    if not is_supported_semanticscholar_url(raw_url):
        raise ValueError(f"Unsupported Semantic Scholar URL: {raw_url}")

    search_text = ""
    sort = ""
    years: list[str] = []
    fields_of_study: list[str] = []
    venues: list[str] = []

    for key, value in parse_qsl(urlparse(raw_url).query, keep_blank_values=False):
        normalized_value = " ".join(value.replace("+", " ").split()).strip()
        if not normalized_value:
            continue

        if key == "q":
            search_text = normalized_value
            continue
        if key == "sort":
            sort = normalized_value
            continue

        match = INDEXED_FILTER_PATTERN.match(key)
        if not match:
            continue

        filter_name = match.group("name")
        if filter_name == "year":
            years.append(normalized_value)
        elif filter_name == "fos":
            fields_of_study.append(normalized_value)
        elif filter_name == "venue":
            venues.append(normalized_value)

    if not search_text:
        raise ValueError("Semantic Scholar URL must include a non-empty q parameter")

    return SemanticScholarSearchSpec(
        search_text=search_text,
        years=tuple(years),
        fields_of_study=tuple(fields_of_study),
        venues=tuple(venues),
        sort=sort,
    )


def output_csv_path_for_semanticscholar_url(raw_url: str, *, output_dir: Path | None = None) -> Path:
    spec = parse_semanticscholar_url(raw_url)
    directory = Path(output_dir) if output_dir is not None else Path.cwd()

    parts = [
        "semanticscholar",
        _slugify(spec.search_text),
        *(_sanitize_filename_part(year) for year in spec.years),
        *(_sanitize_filename_part(field) for field in spec.fields_of_study),
        *(_sanitize_filename_part(venue) for venue in spec.venues),
    ]
    stem = "-".join(part for part in parts if part)[:200].rstrip("-")
    return directory / f"{stem}.csv"


def extract_total_pages_from_semanticscholar_html(html_text: str) -> int:
    if not html_text or not isinstance(html_text, str):
        return 1

    container_match = TOTAL_PAGES_PATTERN.search(html_text)
    if not container_match:
        return 1

    page_match = re.search(r'data-total-pages="([0-9]+)"', container_match.group(0), flags=re.IGNORECASE)
    if not page_match:
        return 1

    return max(int(page_match.group(1)), 1)


def extract_paper_seeds_from_semanticscholar_html(html_text: str) -> list[PaperSeed]:
    if not html_text or not isinstance(html_text, str):
        return []

    seeds: list[PaperSeed] = []
    seen_urls: set[str] = set()
    for href, title_html in TITLE_LINK_PATTERN.findall(html_text):
        normalized_url = normalize_semanticscholar_paper_url(_make_absolute_semanticscholar_url(href))
        if not normalized_url or normalized_url in seen_urls:
            continue

        title = _normalize_html_text(title_html)
        if not title:
            continue

        seeds.append(PaperSeed(name=title, url=normalized_url))
        seen_urls.add(normalized_url)

    return seeds


async def fetch_paper_seeds_from_semanticscholar_url(
    input_url: str,
    *,
    semanticscholar_client,
    output_dir: Path | None = None,
    status_callback=None,
) -> FetchedSeedsResult:
    parse_semanticscholar_url(input_url)
    csv_path = output_csv_path_for_semanticscholar_url(input_url, output_dir=output_dir)

    seeds: list[PaperSeed] = []
    seen_urls: set[str] = set()

    first_page_seeds, total_pages = await _fetch_search_page(
        semanticscholar_client,
        build_semanticscholar_search_page_url(input_url, page=1),
        page=1,
        status_callback=status_callback,
    )
    _append_unique_seeds(seeds, seen_urls, first_page_seeds)

    if total_pages > 1:
        if callable(status_callback):
            status_callback(f"📚 Found {total_pages} Semantic Scholar result pages")
            status_callback("🔄 Starting concurrent page crawl")

        tasks = [
            asyncio.create_task(
                _fetch_search_page(
                    semanticscholar_client,
                    build_semanticscholar_search_page_url(input_url, page=page),
                    page=page,
                    status_callback=status_callback,
                )
            )
            for page in range(2, total_pages + 1)
        ]

        for task in asyncio.as_completed(tasks):
            page_seeds, _ = await task
            _append_unique_seeds(seeds, seen_urls, page_seeds)

    return FetchedSeedsResult(seeds=seeds, csv_path=csv_path)


class SemanticScholarSearchClient:
    def __init__(self, _session=None, max_concurrent: int = 5, min_interval: float = 0.2):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def fetch_search_page_html(self, url: str) -> str:
        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    return await dump_rendered_html(
                        url,
                        virtual_time_budget_ms=8000,
                        timeout_seconds=20.0,
                    )
                except asyncio.TimeoutError:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ValueError("Semantic Scholar search timeout") from None
                except Exception as exc:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ValueError(f"Semantic Scholar search failed: {exc}") from exc

        raise ValueError("Semantic Scholar search error")


def build_semanticscholar_search_page_url(raw_url: str, *, page: int) -> str:
    parsed = urlparse(raw_url)
    params = parse_qsl(parsed.query, keep_blank_values=False)
    if page <= 1 and all(key != "page" for key, _ in params):
        return raw_url

    params = [(key, value) for key, value in params if key != "page"]
    if page > 1:
        params.append(("page", str(page)))
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


async def _fetch_search_page(client, url: str, *, page: int, status_callback=None) -> tuple[list[PaperSeed], int]:
    if callable(status_callback):
        status_callback(f"🔎 Fetching Semantic Scholar search results page {page}")

    html_text = await client.fetch_search_page_html(url)
    page_seeds = extract_paper_seeds_from_semanticscholar_html(html_text)

    if callable(status_callback):
        status_callback(f"📄 Fetched page {page}: {len(page_seeds)} results")

    return page_seeds, extract_total_pages_from_semanticscholar_html(html_text)


def _append_unique_seeds(target: list[PaperSeed], seen_urls: set[str], page_seeds: list[PaperSeed]) -> None:
    for seed in page_seeds:
        if seed.url in seen_urls:
            continue
        target.append(seed)
        seen_urls.add(seed.url)


def _make_absolute_semanticscholar_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"https://www.semanticscholar.org{url}"


def _normalize_html_text(inner_html: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", " ", inner_html, flags=re.S)
    without_tags = re.sub(r"<[^>]+>", " ", without_comments)
    return html_lib.unescape(" ".join(without_tags.split())).strip()


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower() or "search"


def _sanitize_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
