from dataclasses import dataclass

from src.shared.paper_identity import arxiv_url_sort_key, extract_arxiv_id


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
    if all(extract_arxiv_id(row.url) for row in rows):
        return sorted(rows, key=lambda row: arxiv_url_sort_key(row.url), reverse=True)

    if any(row.sort_index for row in rows):
        return sorted(rows, key=lambda row: row.sort_index)

    return rows
