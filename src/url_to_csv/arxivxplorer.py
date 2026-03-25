import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import normalize_arxiv_url
from src.shared.papers import PaperSeed
from src.url_to_csv.models import FetchedSeedsResult


ARXIVXPLORER_HOSTS = {"arxivxplorer.com", "www.arxivxplorer.com"}
ARXIV_ID_ONLY_PATTERN = re.compile(r"^[0-9]{4}\.[0-9]{4,5}$")


@dataclass(frozen=True)
class ArxivXplorerQuery:
    search_text: str
    categories: tuple[str, ...]
    years: tuple[str, ...]


class TooManyPagesError(ValueError):
    """Raised when the arXiv Xplorer API rejects further page access."""


def is_supported_arxivxplorer_url(raw_url: str) -> bool:
    if not raw_url or not isinstance(raw_url, str):
        return False

    parsed = urlparse(raw_url)
    return parsed.scheme in {"http", "https"} and (parsed.netloc or parsed.hostname or "").lower() in ARXIVXPLORER_HOSTS


def parse_arxivxplorer_url(raw_url: str) -> ArxivXplorerQuery:
    parsed = urlparse(raw_url)
    if not is_supported_arxivxplorer_url(raw_url):
        raise ValueError(f"Unsupported arXiv Xplorer URL: {raw_url}")

    query = parse_qs(parsed.query, keep_blank_values=False)
    search_text = (query.get("q", [""])[0] or "").replace("+", " ").strip()
    if not search_text:
        raise ValueError("arXiv Xplorer URL must include a non-empty q parameter")

    return ArxivXplorerQuery(
        search_text=search_text,
        categories=tuple(value.strip() for value in query.get("cats", []) if value.strip()),
        years=tuple(value.strip() for value in query.get("year", []) if value.strip()),
    )


def output_csv_path_for_arxivxplorer_url(raw_url: str, *, output_dir: Path | None = None) -> Path:
    query = parse_arxivxplorer_url(raw_url)
    directory = Path(output_dir) if output_dir is not None else Path.cwd()
    parts = [
        "arxivxplorer",
        _slugify_search_text(query.search_text),
        *(_sanitize_filename_part(category) for category in query.categories),
        *(_sanitize_filename_part(year) for year in query.years),
    ]
    stem = "-".join(part for part in parts if part)[:200].rstrip("-")
    return directory / f"{stem}.csv"


def build_search_params(query: ArxivXplorerQuery, page: int) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [("q", query.search_text)]
    params.extend(("cats", category) for category in query.categories)
    params.extend(("year", year) for year in query.years)
    if page > 1:
        params.append(("page", str(page)))
    return params


def paper_seed_from_search_result(result: dict) -> PaperSeed | None:
    if not isinstance(result, dict):
        return None

    if result.get("journal") != "arxiv":
        return None

    paper_id = (result.get("id") or "").strip()
    if not ARXIV_ID_ONLY_PATTERN.match(paper_id):
        return None

    title = " ".join(str(result.get("title", "")).split()).strip()
    if not title:
        return None

    normalized_url = normalize_arxiv_url(f"https://arxiv.org/abs/{paper_id}")
    if not normalized_url:
        return None

    return PaperSeed(name=title, url=normalized_url)


async def fetch_paper_seeds_from_arxivxplorer_url(
    input_url: str,
    *,
    search_client,
    output_dir: Path | None = None,
    status_callback=None,
) -> FetchedSeedsResult:
    query = parse_arxivxplorer_url(input_url)
    csv_path = output_csv_path_for_arxivxplorer_url(input_url, output_dir=output_dir)

    seeds = []
    seen_urls: set[str] = set()
    page = 1
    while True:
        if callable(status_callback):
            status_callback(f"🔎 Fetching arXiv Xplorer page {page}")

        try:
            results = await search_client.search(query, page)
        except TooManyPagesError:
            if callable(status_callback):
                status_callback(f"📄 Reached arXiv Xplorer page limit at page {page - 1}")
            break

        if callable(status_callback):
            status_callback(f"📄 Fetched page {page}: {len(results)} results")

        if not results:
            break

        for result in results:
            seed = paper_seed_from_search_result(result)
            if seed and seed.url not in seen_urls:
                seeds.append(seed)
                seen_urls.add(seed.url)

        page += 1

    return FetchedSeedsResult(seeds=seeds, csv_path=csv_path)


class ArxivXplorerSearchClient:
    def __init__(self, session: aiohttp.ClientSession, max_concurrent: int = 5, min_interval: float = 0.2):
        self.session = session
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def search(self, query: ArxivXplorerQuery, page: int) -> list[dict]:
        params = build_search_params(query, page)
        url = "https://search.arxivxplorer.com"

        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, params=params, headers={"User-Agent": "scripts.ghstars"}) as response:
                        if response.status == 200:
                            payload = await response.json()
                            return payload if isinstance(payload, list) else []
                        if response.status == 400:
                            payload = await response.json()
                            detail = str(payload.get("detail", "")) if isinstance(payload, dict) else ""
                            if "too many pages" in detail.lower():
                                raise TooManyPagesError(detail)
                            raise ValueError(detail or "arXiv Xplorer search error (400)")
                        if response.status in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue
                        raise ValueError(f"arXiv Xplorer search error ({response.status})")
                except asyncio.TimeoutError:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ValueError("arXiv Xplorer search timeout") from None
                except aiohttp.ClientError as exc:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ValueError(f"arXiv Xplorer search failed: {exc}") from exc

        raise ValueError("arXiv Xplorer search error")


def _slugify_search_text(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return normalized or "search"


def _sanitize_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
