import asyncio
from pathlib import Path

from html_to_csv.csv_writer import write_records_to_csv
from html_to_csv.models import ConversionResult, PaperOutcome, PaperRecord, PaperSeed
from html_to_csv.html_parser import parse_paper_seeds_from_html
from shared.discovery import resolve_github_url
from shared.github import extract_owner_repo


async def build_paper_outcome(
    index: int,
    seed: PaperSeed,
    *,
    discovery_client,
    github_client,
) -> PaperOutcome:
    github_task = asyncio.create_task(_resolve_github(seed, discovery_client))

    github_url = await github_task

    reason = None
    stars = ""
    if github_url:
        owner_repo = extract_owner_repo(github_url)
        if not owner_repo:
            reason = "Discovered URL is not a valid GitHub repository"
            github_url = ""
        else:
            stars_result, _stars_error = await github_client.get_star_count(*owner_repo)
            if _stars_error:
                reason = _stars_error
            elif stars_result is not None:
                stars = stars_result
    else:
        reason = "No Github URL found from discovery"

    return PaperOutcome(
        index=index,
        record=PaperRecord(
            name=seed.name,
            url=seed.url,
            github=github_url or "",
            stars=stars,
        ),
        reason=reason,
    )


async def _resolve_github(seed: PaperSeed, discovery_client) -> str | None:
    resolver = getattr(discovery_client, "resolve_github_url", None)
    if callable(resolver):
        return await resolver(seed)
    return await resolve_github_url(seed, discovery_client)


async def convert_html_to_csv(
    html_path: Path,
    *,
    discovery_client,
    github_client,
    status_callback=None,
    progress_callback=None,
) -> ConversionResult:
    html_path = Path(html_path)
    seeds = parse_paper_seeds_from_html(html_path.read_text(encoding="utf-8"))
    total = len(seeds)
    if callable(status_callback):
        status_callback(f"📝 Found {total} papers")

    tasks = [
        asyncio.create_task(
            build_paper_outcome(
                index,
                seed,
                discovery_client=discovery_client,
                github_client=github_client,
            )
        )
        for index, seed in enumerate(seeds, 1)
    ]

    records = []
    resolved = 0
    skipped = []
    for task in asyncio.as_completed(tasks):
        outcome = await task
        records.append(outcome.record)
        if outcome.reason is None:
            resolved += 1
        else:
            skipped.append(
                {
                    "title": outcome.record.name,
                    "github_url": outcome.record.github or None,
                    "detail_url": outcome.record.url,
                    "reason": outcome.reason,
                }
            )
        if callable(progress_callback):
            progress_callback(outcome, total)

    return ConversionResult(
        csv_path=write_records_to_csv(records, html_path),
        resolved=resolved,
        skipped=skipped,
    )
