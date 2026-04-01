import csv
import tempfile
from pathlib import Path

from src.shared.csv_rows import CsvRow, sort_csv_rows
from src.shared.papers import PaperRecord


CSV_HEADERS = ["Name", "Url", "Github", "Stars", "Created", "About"]


def write_rows_to_csv_path(rows: list[CsvRow], csv_path: Path) -> Path:
    sorted_rows = sort_csv_rows(rows)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=csv_path.parent) as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in sorted_rows:
            writer.writerow(
                {
                    "Name": row.name,
                    "Url": row.url,
                    "Github": row.github,
                    "Stars": "" if row.stars in (None, "") else str(row.stars),
                    "Created": row.created,
                    "About": row.about,
                }
            )
        temp_path = Path(handle.name)

    temp_path.replace(csv_path)
    return csv_path


def write_records_to_csv_path(records: list[PaperRecord], csv_path: Path) -> Path:
    return write_rows_to_csv_path(
        [
            CsvRow(
                name=record.name,
                url=record.url,
                github=record.github,
                stars=record.stars,
                created="",
                about="",
                sort_index=record.sort_index,
            )
            for record in records
        ],
        csv_path,
    )
