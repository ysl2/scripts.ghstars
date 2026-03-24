from dataclasses import dataclass


@dataclass(frozen=True)
class PaperSeed:
    name: str
    url: str


@dataclass(frozen=True)
class PaperRecord:
    name: str
    date: str
    github: str
    stars: int | str | None
    url: str


def sort_records(records: list[PaperRecord]) -> list[PaperRecord]:
    """Sort by date descending, then URL ascending, with empty dates last."""

    def sort_key(record: PaperRecord) -> tuple[int, int, str]:
        if record.date:
            return (0, -int(record.date.replace("-", "")), record.url)
        return (1, 0, record.url)

    return sorted(records, key=sort_key)
