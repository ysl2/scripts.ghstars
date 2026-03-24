import csv
import tempfile
from pathlib import Path

from html_to_csv.models import PaperRecord, sort_records


CSV_HEADERS = ["Name", "Url", "Github", "Stars"]


def output_csv_path_for_html(html_path: Path) -> Path:
    return html_path.with_suffix(".csv")


def write_records_to_csv(records: list[PaperRecord], html_path: Path) -> Path:
    csv_path = output_csv_path_for_html(html_path)
    sorted_records = sort_records(records)

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
