import asyncio
import csv
import tempfile
from dataclasses import dataclass
from pathlib import Path

from shared.paper_enrichment import enrich_paper
from shared.papers import PaperRecord


NAME_COLUMN = "Name"
URL_COLUMN = "Url"
GITHUB_COLUMN = "Github"
STARS_COLUMN = "Stars"
REQUIRED_COLUMNS = (GITHUB_COLUMN, STARS_COLUMN)


@dataclass(frozen=True)
class CsvRowOutcome:
    index: int
    record: PaperRecord
    current_stars: int | None
    reason: str | None
    source_label: str | None
    github_url_set: str | None


@dataclass(frozen=True)
class CsvUpdateResult:
    csv_path: Path
    updated: int
    skipped: list[dict]


async def update_csv_file(
    csv_path: Path,
    *,
    discovery_client,
    github_client,
    status_callback=None,
    progress_callback=None,
) -> CsvUpdateResult:
    csv_path = Path(csv_path)
    rows, fieldnames = _read_csv_rows(csv_path)
    total = len(rows)
    if callable(status_callback):
        status_callback(f"📝 Found {total} rows")

    tasks = [
        asyncio.create_task(
            build_csv_row_outcome(
                index,
                row,
                discovery_client=discovery_client,
                github_client=github_client,
            )
        )
        for index, row in enumerate(rows, 1)
    ]

    updated_rows: list[dict[str, str] | None] = [None] * total
    updated = 0
    skipped = []

    for task in asyncio.as_completed(tasks):
        row_index, updated_row, outcome = await task
        updated_rows[row_index] = updated_row
        if outcome.reason is None:
            updated += 1
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

    _write_csv_rows(csv_path, fieldnames, [row for row in updated_rows if row is not None])
    return CsvUpdateResult(csv_path=csv_path, updated=updated, skipped=skipped)


async def build_csv_row_outcome(
    index: int,
    row: dict[str, str],
    *,
    discovery_client,
    github_client,
) -> tuple[int, dict[str, str], CsvRowOutcome]:
    updated_row = dict(row)
    name = (updated_row.get(NAME_COLUMN) or "").strip() or f"Row {index}"
    url = updated_row.get(URL_COLUMN, "") or ""
    existing_github = updated_row.get(GITHUB_COLUMN, "") or ""
    current_stars = parse_current_stars(updated_row.get(STARS_COLUMN))

    enrichment = await enrich_paper(
        name=name,
        url=url,
        existing_github=existing_github,
        discovery_client=discovery_client,
        github_client=github_client,
    )

    if enrichment.url and enrichment.url != url and enrichment.reason != "No valid arXiv URL found":
        updated_row[URL_COLUMN] = enrichment.url

    if enrichment.github_url:
        updated_row[GITHUB_COLUMN] = enrichment.github_url

    if enrichment.reason is None and enrichment.stars is not None:
        updated_row[STARS_COLUMN] = str(enrichment.stars)

    github_url_set = None
    source_label = None
    if enrichment.source == "existing":
        source_label = "existing Github"
    elif enrichment.source == "discovered":
        source_label = "Discovered Github"
        if not existing_github.strip():
            github_url_set = enrichment.github_url

    outcome = CsvRowOutcome(
        index=index,
        record=PaperRecord(
            name=name,
            url=updated_row.get(URL_COLUMN, "") or enrichment.url or url,
            github=updated_row.get(GITHUB_COLUMN, "") or "",
            stars=enrichment.stars if enrichment.reason is None else updated_row.get(STARS_COLUMN, ""),
        ),
        current_stars=current_stars,
        reason=enrichment.reason,
        source_label=source_label,
        github_url_set=github_url_set,
    )
    return index - 1, updated_row, outcome


def parse_current_stars(value) -> int | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        return int(text)
    except ValueError:
        return None


def _read_csv_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV file must include a header row")
        fieldnames = list(reader.fieldnames)
        for column in REQUIRED_COLUMNS:
            if column not in fieldnames:
                fieldnames.append(column)

        rows = []
        for raw_row in reader:
            row = {field: raw_row.get(field, "") or "" for field in fieldnames}
            rows.append(row)
        return rows, fieldnames


def _write_csv_rows(csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=csv_path.parent) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(handle.name)

    temp_path.replace(csv_path)
