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
    record: PaperRecord | CsvRow
    reason: str | None


@dataclass(frozen=True)
class ConversionResult:
    csv_path: Path
    resolved: int
    skipped: list[dict]


def sort_records(records: list[PaperRecord]) -> list[PaperRecord]:
    if all(extract_arxiv_id(record.url) for record in records):
        return sorted(records, key=lambda record: arxiv_url_sort_key(record.url), reverse=True)

    if any(record.sort_index for record in records):
        return sorted(records, key=lambda record: record.sort_index)

    return sorted(records, key=lambda record: arxiv_url_sort_key(record.url), reverse=True)
