from __future__ import annotations

from src.core.record_model import Record
from src.shared.csv_rows import CsvRow


class FreshCsvExportAdapter:
    def to_csv_row(self, record: Record, *, sort_index: int = 0) -> CsvRow:
        return CsvRow(
            name="" if record.name.value is None else str(record.name.value),
            url="" if record.url.value is None else str(record.url.value),
            github="" if record.github.value is None else str(record.github.value),
            stars="" if record.stars.value is None else record.stars.value,
            created="" if record.created.value is None else str(record.created.value),
            about="" if record.about.value is None else str(record.about.value),
            sort_index=sort_index,
        )
