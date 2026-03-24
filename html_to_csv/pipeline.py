import asyncio
from pathlib import Path

from html_to_csv.csv_writer import write_records_to_csv
from html_to_csv.models import PaperRecord, PaperSeed
from html_to_csv.html_parser import parse_paper_seeds_from_html
from shared.discovery import resolve_github_url
from shared.github import extract_owner_repo


async def build_paper_record(
    seed: PaperSeed,
    *,
    date_map: dict[str, str],
    discovery_client,
    github_client,
) -> PaperRecord:
    github_task = asyncio.create_task(_resolve_github(seed, discovery_client))

    github_url = await github_task

    stars = ""
    if github_url:
        owner_repo = extract_owner_repo(github_url)
        if owner_repo:
            stars_result, _stars_error = await github_client.get_star_count(*owner_repo)
            if stars_result is not None:
                stars = stars_result

    return PaperRecord(
        name=seed.name,
        date=date_map.get(seed.url, ""),
        github=github_url or "",
        stars=stars,
        url=seed.url,
    )


async def _resolve_github(seed: PaperSeed, discovery_client) -> str | None:
    resolver = getattr(discovery_client, "resolve_github_url", None)
    if callable(resolver):
        return await resolver(seed)
    return await resolve_github_url(seed, discovery_client)


async def _prefetch_dates(seeds: list[PaperSeed], arxiv_client) -> dict[str, str]:
    urls = [seed.url for seed in seeds]
    batch_fetcher = getattr(arxiv_client, "get_published_dates", None)
    if callable(batch_fetcher):
        date_map, _errors = await batch_fetcher(urls)
        return date_map

    date_map: dict[str, str] = {}
    for url in urls:
        date, _error = await arxiv_client.get_published_date(url)
        if date:
            date_map[url] = date
    return date_map


async def convert_html_to_csv(
    html_path: Path,
    *,
    arxiv_client,
    discovery_client,
    github_client,
    status_callback=None,
    progress_callback=None,
) -> Path:
    html_path = Path(html_path)
    seeds = parse_paper_seeds_from_html(html_path.read_text(encoding="utf-8"))
    total = len(seeds)
    if callable(status_callback):
        status_callback(f"Parsed {total} unique papers")

    date_map = await _prefetch_dates(seeds, arxiv_client)
    if callable(status_callback):
        status_callback(f"Fetched {len(date_map)}/{total} arXiv dates")

    tasks = [
        asyncio.create_task(
            build_paper_record(
                seed,
                date_map=date_map,
                discovery_client=discovery_client,
                github_client=github_client,
            )
        )
        for seed in seeds
    ]

    records = []
    completed = 0
    for task in asyncio.as_completed(tasks):
        record = await task
        records.append(record)
        completed += 1
        if callable(progress_callback):
            progress_callback(completed, total, record)

    return write_records_to_csv(records, html_path)
