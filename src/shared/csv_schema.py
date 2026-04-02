NAME_COLUMN = "Name"
URL_COLUMN = "Url"
GITHUB_COLUMN = "Github"
STARS_COLUMN = "Stars"
CREATED_COLUMN = "Created"
ABOUT_COLUMN = "About"

CSV_HEADERS = [NAME_COLUMN, URL_COLUMN, GITHUB_COLUMN, STARS_COLUMN, CREATED_COLUMN, ABOUT_COLUMN]
LEGACY_RECORD_HEADERS = [NAME_COLUMN, URL_COLUMN, GITHUB_COLUMN, STARS_COLUMN]
CSV_UPDATE_COLUMNS = [GITHUB_COLUMN, STARS_COLUMN, CREATED_COLUMN, ABOUT_COLUMN]


def append_missing_property_columns(existing_columns: list[str], required_columns: list[str]) -> list[str]:
    ordered_required = [column for column in CSV_HEADERS if column in required_columns]
    normalized = list(existing_columns)
    for column in ordered_required:
        if column not in normalized:
            normalized.append(column)
    return normalized
