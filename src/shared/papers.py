from dataclasses import dataclass
from pathlib import Path

from src.shared.paper_identity import arxiv_url_sort_key, extract_arxiv_id


@dataclass(frozen=True)
class PaperSeed:
    name: str
    url: str


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
    record: PaperRecord
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
