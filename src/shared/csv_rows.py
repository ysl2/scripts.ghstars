from dataclasses import dataclass


@dataclass(frozen=True)
class CsvRow:
    name: str
    url: str
    github: str
    stars: int | str | None
    created: str
    about: str
    sort_index: int = 0


def sort_csv_rows(rows: list[CsvRow]) -> list[CsvRow]:
    if any(row.sort_index for row in rows):
        return sorted(rows, key=lambda row: row.sort_index)

    return rows
