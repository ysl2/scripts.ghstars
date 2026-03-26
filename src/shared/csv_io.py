import csv
import tempfile
from pathlib import Path

from src.shared.papers import PaperRecord, sort_records


CSV_HEADERS = ["Name", "Url", "Github", "Stars"]


def write_records_to_csv_path(records: list[PaperRecord], csv_path: Path) -> Path:
    sorted_records = sort_records(records)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=csv_path.parent) as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for record in sorted_records:
            writer.writerow(
                {
                    "Name": record.name,
                    "Url": record.url,
                    "Github": record.github,
                    "Stars": "" if record.stars in (None, "") else str(record.stars),
                }
            )
        temp_path = Path(handle.name)

    temp_path.replace(csv_path)
    return csv_path
