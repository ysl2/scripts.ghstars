import csv
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.input_adapters import CsvRowInputAdapter
from src.core.output_adapters import CsvUpdateAdapter
from src.core.record_model import PropertyState
from src.core.record_sync_workflow import RecordSyncPolicy, sync_record_with_policy
from src.shared.async_batch import iter_bounded_as_completed, resolve_worker_count
from src.shared.csv_schema import (
    ABOUT_COLUMN,
    CREATED_COLUMN,
    GITHUB_COLUMN,
    NAME_COLUMN,
    STARS_COLUMN,
    URL_COLUMN,
)
from src.shared.papers import PaperRecord


MANAGED_COLUMNS = (GITHUB_COLUMN, STARS_COLUMN, CREATED_COLUMN, ABOUT_COLUMN)


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
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    status_callback=None,
    progress_callback=None,
) -> CsvUpdateResult:
    csv_path = Path(csv_path)
    rows, fieldnames = _read_csv_rows(csv_path)
    total = len(rows)
    worker_count = resolve_worker_count(discovery_client, github_client)
    if callable(status_callback):
        status_callback(f"📝 Found {total} rows")

    updated_rows: list[dict[str, str] | None] = [None] * total
    updated = 0
    skipped = []

    async def build_outcome(item: tuple[int, dict[str, str]]) -> tuple[int, dict[str, str], CsvRowOutcome]:
        index, row = item
        return await build_csv_row_outcome(
            index,
            row,
            discovery_client=discovery_client,
            github_client=github_client,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            content_cache=content_cache,
            relation_resolution_cache=relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
            csv_dir=csv_path.parent,
        )

    async for row_index, updated_row, outcome in iter_bounded_as_completed(
        enumerate(rows, 1),
        build_outcome,
        max_concurrent=worker_count,
    ):
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
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    csv_dir: Path,
) -> tuple[int, dict[str, str], CsvRowOutcome]:
    updated_row = dict(row)
    csv_update_adapter = CsvUpdateAdapter()
    record = CsvRowInputAdapter().to_record(index, updated_row)
    name = _string_value(record.name.value).strip() or f"Row {index}"
    url = _string_value(record.url.value).strip()
    existing_github = _string_value(record.github.value).strip()
    current_stars = parse_current_stars(record.stars.value)
    if record.name.value is None:
        record = record.with_property("name", PropertyState.present(name, source="csv_update"))

    if not existing_github and not url:
        outcome = CsvRowOutcome(
            index=index,
            record=PaperRecord(
                name=name,
                url="",
                github="",
                stars=updated_row.get(STARS_COLUMN, ""),
            ),
            current_stars=current_stars,
            reason="Row has neither Github nor Url",
            source_label=None,
            github_url_set=None,
        )
        return index - 1, updated_row, outcome

    workflow_result = await sync_record_with_policy(
        record,
        policy=RecordSyncPolicy(
            allow_title_search=bool(url),
            allow_github_discovery=not bool(existing_github),
            trust_existing_github=bool(existing_github),
            apply_normalized_url=not bool(existing_github),
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    synced_record = workflow_result.record
    updated_row = csv_update_adapter.apply(updated_row, synced_record)

    reason = workflow_result.reason

    github_url_set = None
    source_label = None
    github_source = synced_record.facts.github_source
    github_url = _string_value(synced_record.github.value).strip() or None
    if github_source == "existing":
        source_label = "existing Github"
    elif github_source == "discovered":
        source_label = "Discovered Github"
        if not existing_github.strip():
            github_url_set = github_url

    outcome = CsvRowOutcome(
        index=index,
        record=PaperRecord(
            name=name,
            url=updated_row.get(URL_COLUMN, "") or "",
            github=updated_row.get(GITHUB_COLUMN, "") or "",
            stars=synced_record.stars.value if reason is None else updated_row.get(STARS_COLUMN, ""),
        ),
        current_stars=current_stars,
        reason=reason,
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


def _string_value(value) -> str:
    if value is None:
        return ""
    return str(value)


def _read_csv_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV file must include a header row")
        fieldnames = _normalize_fieldnames(list(reader.fieldnames))

        rows = [{field: raw_row.get(field, "") or "" for field in fieldnames} for raw_row in reader]
        return rows, fieldnames


def _write_csv_rows(csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=csv_path.parent) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(handle.name)

    temp_path.replace(csv_path)


def _normalize_fieldnames(fieldnames: list[str]) -> list[str]:
    return CsvUpdateAdapter().normalize_fieldnames(list(fieldnames))
