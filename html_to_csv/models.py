from dataclasses import dataclass
from pathlib import Path
import re


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
    """Sort by canonical arXiv URL descending."""

    def sort_key(record: PaperRecord) -> tuple[int, int, str]:
        match = re.search(r"/abs/([0-9]{4})\.([0-9]{4,5})$", record.url)
        if not match:
            return (-1, -1, record.url)
        return (int(match.group(1)), int(match.group(2)), record.url)

    return sorted(records, key=sort_key, reverse=True)
