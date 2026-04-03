from __future__ import annotations

from src.core.record_model import Record
from src.shared.csv_schema import (
    ABOUT_COLUMN,
    CREATED_COLUMN,
    CSV_UPDATE_COLUMNS,
    GITHUB_COLUMN,
    STARS_COLUMN,
    URL_COLUMN,
    append_missing_property_columns,
)
from src.shared.csv_rows import CsvRow


class FreshCsvExportAdapter:
    def to_csv_row(self, record: Record, *, sort_index: int = 0) -> CsvRow:
        return CsvRow(
            name=_string_value(record.name.value),
            url=_string_value(record.url.value),
            github=_string_value(record.github.value),
            stars="" if record.stars.value is None else record.stars.value,
            created=_string_value(record.created.value),
            about=_string_value(record.about.value),
            sort_index=sort_index,
        )


class CsvUpdateAdapter:
    def normalize_fieldnames(self, fieldnames: list[str]) -> list[str]:
        return append_missing_property_columns(list(fieldnames), list(CSV_UPDATE_COLUMNS))

    def apply(self, row: dict[str, str], record: Record) -> dict[str, str]:
        updated = dict(row)
        existing_github = _string_value(row.get(GITHUB_COLUMN)).strip()

        if not existing_github and record.url.value is not None:
            updated[URL_COLUMN] = _string_value(record.url.value)
        if not existing_github and record.github.value is not None:
            updated[GITHUB_COLUMN] = _string_value(record.github.value)
        if record.stars.value is not None:
            updated[STARS_COLUMN] = str(record.stars.value)
        if record.about.value is not None:
            updated[ABOUT_COLUMN] = _string_value(record.about.value)
        if not _string_value(updated.get(CREATED_COLUMN)).strip() and record.created.value is not None:
            updated[CREATED_COLUMN] = _string_value(record.created.value)

        return updated


class NotionUpdateAdapter:
    MANAGED_PROPERTY_TYPES = {
        "Github": "url",
        "Stars": "number",
        "Created": "date",
        "About": "rich_text",
    }
    MANAGED_NOTION_PROPERTIES = {
        "Github": {
            "type": "url",
            "url": {},
        },
        "Stars": {
            "type": "number",
            "number": {"format": "number"},
        },
        "Created": {
            "type": "date",
            "date": {},
        },
        "About": {
            "type": "rich_text",
            "rich_text": {},
        },
    }

    def validate_schema(self, properties: dict) -> None:
        for name, expected_type in self.MANAGED_PROPERTY_TYPES.items():
            property_value = properties.get(name)
            if property_value is None:
                continue
            actual_type = property_value.get("type")
            if actual_type != expected_type:
                raise ValueError(f"Notion property {name} must have type {expected_type}")

    def build_patch(self, page: dict, record: Record, *, update_github: bool) -> dict:
        patch = {}
        if record.stars.value is not None:
            patch["Stars"] = {"number": int(record.stars.value)}
        if record.about.value is not None:
            if _string_value(record.about.value):
                patch["About"] = {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": _string_value(record.about.value)},
                        }
                    ]
                }
            else:
                patch["About"] = {"rich_text": []}

        created_property = page.get("properties", {}).get("Created", {})
        has_created = bool((created_property.get("date") or {}).get("start"))
        if not has_created and record.created.value is not None:
            patch["Created"] = {"date": {"start": _string_value(record.created.value)}}

        if update_github and record.github.value is not None:
            patch["Github"] = {"url": _string_value(record.github.value)}

        return patch


def _string_value(value) -> str:
    if value is None:
        return ""
    return str(value)
