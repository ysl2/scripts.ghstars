from dataclasses import dataclass, field
from pathlib import Path

from src.shared.csv_rows import CsvRow
from src.shared.paper_identity import arxiv_url_sort_key, extract_arxiv_id


@dataclass(frozen=True)
class PaperSeed:
    name: str
    url: str
    canonical_arxiv_url: str | None = field(default=None, compare=False)
    url_resolution_authoritative: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class PaperRecord:
    name: str
    url: str
    github: str
    stars: int | str | None
    sort_index: int = 0


@dataclass(frozen=True)
class PaperOutcome:
    index: int
    record: CsvRow
    reason: str | None


@dataclass(frozen=True)
class ConversionResult:
    csv_path: Path
    resolved: int
    skipped: list[dict]


def paper_record_to_csv_row(record: PaperRecord) -> CsvRow:
    return CsvRow(
        name=record.name,
        url=record.url,
        github=record.github,
        stars=record.stars,
        created="",
        about="",
        sort_index=record.sort_index,
    )


def sort_records(records: list[PaperRecord]) -> list[PaperRecord]:
    if all(extract_arxiv_id(record.url) for record in records):
        return sorted(records, key=lambda record: arxiv_url_sort_key(record.url), reverse=True)

    if any(record.sort_index for record in records):
        return sorted(records, key=lambda record: record.sort_index)

    return sorted(records, key=lambda record: arxiv_url_sort_key(record.url), reverse=True)


def sort_paper_export_rows(rows: list[CsvRow]) -> list[CsvRow]:
    if all(extract_arxiv_id(row.url) for row in rows):
        return sorted(rows, key=lambda row: arxiv_url_sort_key(row.url), reverse=True)

    if any(row.sort_index for row in rows):
        return sorted(rows, key=lambda row: row.sort_index)

    return sorted(rows, key=lambda row: arxiv_url_sort_key(row.url), reverse=True)
