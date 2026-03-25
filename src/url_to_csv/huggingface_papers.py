import asyncio
import html as html_lib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import normalize_arxiv_url
from src.shared.papers import PaperSeed
from src.url_to_csv.models import FetchedSeedsResult


HUGGINGFACE_PAPERS_HOSTS = {"huggingface.co", "www.huggingface.co"}
ARXIV_ID_PATTERN = re.compile(r"^[0-9]{4}\.[0-9]{4,5}$")


def is_supported_huggingface_papers_url(raw_url: str) -> bool:
    if not raw_url or not isinstance(raw_url, str):
        return False

    parsed = urlparse(raw_url)
    host = (parsed.netloc or parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in HUGGINGFACE_PAPERS_HOSTS:
        return False

    path = parsed.path.rstrip("/")
    if not path.startswith("/papers/"):
        return False

    suffix = path.removeprefix("/papers/")
    if not suffix or suffix.count("/") > 1:
        return False

    return suffix == "trending" or suffix.startswith("month/")


def output_csv_path_for_huggingface_papers_url(raw_url: str, *, output_dir: Path | None = None) -> Path:
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query, keep_blank_values=False)
    directory = Path(output_dir) if output_dir is not None else Path.cwd()

    parts = ["huggingface", "papers"]
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) >= 2:
        parts.extend(path_parts[1:])

    search_text = (query.get("q", [""])[0] or "").strip()
    if search_text:
        parts.append(_slugify(search_text))

    stem = "-".join(_sanitize_filename_part(part) for part in parts if part)[:200].rstrip("-")
    return directory / f"{stem}.csv"


def extract_paper_seeds_from_huggingface_html(html_text: str) -> list[PaperSeed]:
    payload = _extract_daily_papers_payload(html_text)
    items = _select_paper_items(payload)

    seeds: list[PaperSeed] = []
    seen_urls: set[str] = set()
    for item in items:
        seed = _paper_seed_from_huggingface_item(item)
        if seed and seed.url not in seen_urls:
            seeds.append(seed)
            seen_urls.add(seed.url)
    return seeds


async def fetch_paper_seeds_from_huggingface_papers_url(
    input_url: str,
    *,
    huggingface_papers_client,
    output_dir: Path | None = None,
    status_callback=None,
) -> FetchedSeedsResult:
    if callable(status_callback):
        status_callback("🔎 Fetching Hugging Face Papers collection")

    html_text = await huggingface_papers_client.fetch_collection_html(input_url)
    seeds = extract_paper_seeds_from_huggingface_html(html_text)
    csv_path = output_csv_path_for_huggingface_papers_url(input_url, output_dir=output_dir)
    return FetchedSeedsResult(seeds=seeds, csv_path=csv_path)


class HuggingFacePapersClient:
    def __init__(self, session: aiohttp.ClientSession, max_concurrent: int = 5, min_interval: float = 0.2):
        self.session = session
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def fetch_collection_html(self, url: str) -> str:
        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.get(url, headers={"User-Agent": "ghstars"}) as response:
                        if response.status == 200:
                            return await response.text()
                        if response.status in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue
                        raise ValueError(f"Hugging Face Papers page error ({response.status})")
                except asyncio.TimeoutError:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ValueError("Hugging Face Papers page timeout") from None
                except aiohttp.ClientError as exc:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ValueError(f"Hugging Face Papers page failed: {exc}") from exc

        raise ValueError("Hugging Face Papers page error")


def _extract_daily_papers_payload(html_text: str) -> dict:
    match = re.search(r'data-target="DailyPapers"[^>]*data-props="([^"]*)"', html_text)
    if not match:
        raise ValueError("Hugging Face Papers payload not found in page")

    raw_payload = html_lib.unescape(match.group(1))
    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise ValueError("Hugging Face Papers payload is not a JSON object")
    return payload


def _select_paper_items(payload: dict) -> list[dict]:
    if _has_search_query(payload):
        search_results = _filter_paper_items(payload.get("searchResults"))
        if _is_month_payload(payload):
            return _intersect_paper_items(search_results, _filter_paper_items(payload.get("dailyPapers")))
        if search_results:
            return search_results

    return _filter_paper_items(payload.get("dailyPapers"))


def _has_search_query(payload: dict) -> bool:
    query = payload.get("query")
    return isinstance(query, dict) and bool((query.get("q") or "").strip())


def _is_month_payload(payload: dict) -> bool:
    return str(payload.get("periodType") or "").strip().lower() == "month"


def _filter_paper_items(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _intersect_paper_items(search_results: list[dict], daily_papers: list[dict]) -> list[dict]:
    if not search_results or not daily_papers:
        return []

    daily_ids = {
        paper_id
        for item in daily_papers
        if (paper_id := _extract_paper_id(item)) is not None
    }
    return [
        item
        for item in search_results
        if (paper_id := _extract_paper_id(item)) is not None and paper_id in daily_ids
    ]


def _extract_paper_id(item: dict) -> str | None:
    paper = item.get("paper")
    if not isinstance(paper, dict):
        return None

    paper_id = str(paper.get("id", "")).strip()
    if not ARXIV_ID_PATTERN.match(paper_id):
        return None
    return paper_id


def _paper_seed_from_huggingface_item(item: dict) -> PaperSeed | None:
    paper = item.get("paper")
    if not isinstance(paper, dict):
        return None

    paper_id = str(paper.get("id", "")).strip()
    if not ARXIV_ID_PATTERN.match(paper_id):
        return None

    title = " ".join(str(item.get("title") or paper.get("title") or "").split()).strip()
    if not title:
        return None

    normalized_url = normalize_arxiv_url(f"https://arxiv.org/abs/{paper_id}")
    if not normalized_url:
        return None

    return PaperSeed(name=title, url=normalized_url)


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower() or "search"


def _sanitize_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
