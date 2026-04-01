import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from src.shared.paper_identity import (
    build_arxiv_abs_url,
    normalize_arxiv_url,
    normalize_semanticscholar_paper_url,
)
from src.shared.papers import PaperSeed
from src.shared.semantic_scholar_graph import SemanticScholarGraphClient
from src.url_to_csv.filenames import build_url_export_csv_path
from src.url_to_csv.models import FetchedSeedsResult


SEMANTIC_SCHOLAR_HOSTS = {"semanticscholar.org", "www.semanticscholar.org"}
INDEXED_FILTER_PATTERN = re.compile(r"^(?P<name>year|fos|venue)\[(?P<index>[0-9]+)\]$")
SEMANTIC_SCHOLAR_BULK_SEARCH_FIELDS = "paperId,title,externalIds,url"
SEMANTIC_SCHOLAR_SORT_MAPPING = {
    "pub-date": "publicationDate:desc",
    "citation-count": "citationCount:desc",
    "total-citations": "citationCount:desc",
}


@dataclass(frozen=True)
class SemanticScholarSearchSpec:
    search_text: str
    years: tuple[str, ...]
    fields_of_study: tuple[str, ...]
    venues: tuple[str, ...]
    sort: str


class SemanticScholarSearchClient(SemanticScholarGraphClient):
    async def fetch_search_bulk_page(self, params: dict[str, str]) -> dict[str, Any]:
        return await self._get_json(f"{self.graph_url}/paper/search/bulk", params=params)


def is_supported_semanticscholar_url(raw_url: str) -> bool:
    if not raw_url or not isinstance(raw_url, str):
        return False

    parsed = urlparse(raw_url)
    host = (parsed.netloc or parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    return parsed.scheme in {"http", "https"} and host in SEMANTIC_SCHOLAR_HOSTS and path == "/search"


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

    parts = [
        "semanticscholar",
        _slugify(spec.search_text),
        *(_sanitize_filename_part(year) for year in spec.years),
        *(_sanitize_filename_part(field) for field in spec.fields_of_study),
        *(_sanitize_filename_part(venue) for venue in spec.venues),
    ]
    return build_url_export_csv_path(parts, output_dir=output_dir)


async def fetch_paper_seeds_from_semanticscholar_url(
    input_url: str,
    *,
    semanticscholar_client,
    output_dir: Path | None = None,
    status_callback=None,
) -> FetchedSeedsResult:
    spec = parse_semanticscholar_url(input_url)
    csv_path = output_csv_path_for_semanticscholar_url(input_url, output_dir=output_dir)

    seeds: list[PaperSeed] = []
    seen_urls: set[str] = set()
    next_token: str | None = None
    seen_tokens: set[str] = set()
    batch = 1

    while True:
        if callable(status_callback):
            status_callback(f"🔎 Fetching Semantic Scholar bulk search batch {batch}")

        payload = await _fetch_search_bulk_page(
            semanticscholar_client,
            _build_bulk_search_params(spec, token=next_token),
        )

        if batch == 1 and callable(status_callback):
            maybe_total = payload.get("total")
            if isinstance(maybe_total, int) and maybe_total >= 0:
                status_callback(f"📚 Estimated {maybe_total} Semantic Scholar matches")

        batch_seeds = _extract_paper_seeds_from_search_payload(payload)
        _append_unique_seeds(seeds, seen_urls, batch_seeds)

        if callable(status_callback):
            status_callback(f"📄 Fetched batch {batch}: {len(batch_seeds)} results")

        token = str(payload.get("token") or "").strip()
        if not token or token in seen_tokens:
            break

        seen_tokens.add(token)
        next_token = token
        batch += 1

    return FetchedSeedsResult(seeds=seeds, csv_path=csv_path)


async def _fetch_search_bulk_page(client, params: dict[str, str]) -> dict[str, Any]:
    fetch_page = getattr(client, "fetch_search_bulk_page", None)
    if callable(fetch_page):
        payload = await fetch_page(params)
    else:
        get_json = getattr(client, "_get_json", None)
        graph_url = str(getattr(client, "graph_url", "") or "").rstrip("/")
        if not callable(get_json) or not graph_url:
            raise ValueError("Missing Semantic Scholar bulk search client")
        payload = await get_json(f"{graph_url}/paper/search/bulk", params=params)

    return payload if isinstance(payload, dict) else {}


def _build_bulk_search_params(spec: SemanticScholarSearchSpec, *, token: str | None) -> dict[str, str]:
    params = {
        "query": spec.search_text,
        "fields": SEMANTIC_SCHOLAR_BULK_SEARCH_FIELDS,
    }

    year_filter = _build_year_filter(spec.years)
    if year_filter:
        params["year"] = year_filter

    fields_of_study_filter = _join_filter_values(spec.fields_of_study)
    if fields_of_study_filter:
        params["fieldsOfStudy"] = fields_of_study_filter

    venue_filter = _join_filter_values(spec.venues)
    if venue_filter:
        params["venue"] = venue_filter

    sort_filter = _map_sort(spec.sort)
    if sort_filter:
        params["sort"] = sort_filter

    if token:
        params["token"] = token

    return params


def _extract_paper_seeds_from_search_payload(payload: dict[str, Any]) -> list[PaperSeed]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []

    seeds: list[PaperSeed] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        title = " ".join(str(row.get("title") or "").split()).strip()
        if not title:
            continue

        seed_url = _resolve_seed_url(row)
        if not seed_url:
            continue

        seeds.append(PaperSeed(name=title, url=seed_url))
    return seeds


def _resolve_seed_url(row: dict[str, Any]) -> str | None:
    external_ids = row.get("externalIds")
    if not isinstance(external_ids, dict):
        external_ids = {}

    arxiv_url = _build_arxiv_url(external_ids.get("ArXiv"))
    if arxiv_url:
        return arxiv_url

    normalized_url = normalize_semanticscholar_paper_url(str(row.get("url") or "").strip())
    if normalized_url:
        return normalized_url

    paper_id = " ".join(str(row.get("paperId") or "").split()).strip()
    if not paper_id:
        return None
    return normalize_semanticscholar_paper_url(f"https://www.semanticscholar.org/paper/{paper_id}")


def _build_arxiv_url(arxiv_identifier: Any) -> str | None:
    candidate = " ".join(str(arxiv_identifier or "").split()).strip()
    if not candidate:
        return None

    normalized_url = normalize_arxiv_url(candidate)
    if normalized_url:
        return normalized_url
    return normalize_arxiv_url(build_arxiv_abs_url(candidate))


def _append_unique_seeds(target: list[PaperSeed], seen_urls: set[str], page_seeds: list[PaperSeed]) -> None:
    for seed in page_seeds:
        if seed.url in seen_urls:
            continue
        target.append(seed)
        seen_urls.add(seed.url)


def _build_year_filter(years: tuple[str, ...]) -> str:
    normalized_years = []
    for year in years:
        candidate = " ".join(str(year or "").split()).strip()
        if not re.fullmatch(r"[0-9]{4}", candidate):
            return ",".join(value for value in years if str(value).strip())
        normalized_years.append(int(candidate))

    if not normalized_years:
        return ""
    if len(normalized_years) == 1:
        return str(normalized_years[0])
    return f"{min(normalized_years)}-{max(normalized_years)}"


def _join_filter_values(values: tuple[str, ...]) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = " ".join(str(value or "").split()).strip()
        if not candidate or candidate in seen:
            continue
        output.append(candidate)
        seen.add(candidate)
    return ",".join(output)


def _map_sort(sort: str) -> str:
    normalized_sort = " ".join(str(sort or "").split()).strip()
    if not normalized_sort or normalized_sort == "relevance":
        return ""
    return SEMANTIC_SCHOLAR_SORT_MAPPING.get(normalized_sort, normalized_sort)


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower() or "search"


def _sanitize_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
