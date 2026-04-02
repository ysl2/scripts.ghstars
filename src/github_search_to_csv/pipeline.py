import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.core.input_adapters import GithubSearchInputAdapter
from src.github_search_to_csv.search import parse_github_search_url
from src.shared.csv_io import write_rows_to_csv_path
from src.shared.csv_rows import CsvRow
from src.shared.papers import ConversionResult


def current_run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def build_github_search_csv_path(
    search_url: str,
    *,
    output_dir: Path | None = None,
    timestamp: str | None = None,
) -> Path:
    parse_github_search_url(search_url)
    directory = Path(output_dir) if output_dir is not None else Path("output")
    stem = build_github_search_output_stem(search_url)
    suffix = timestamp or current_run_timestamp()
    return directory / f"{stem}-{suffix}.csv"


def build_github_search_output_stem(search_url: str) -> str:
    params = parse_qs(urlparse(search_url).query)
    parts = ["github-search"]

    query = params.get("q", [""])[0]
    parts.append(_slugify_filename_part(query))

    for key in sorted(params):
        if key == "q":
            continue
        for value in params[key]:
            key_slug = _slugify_filename_part(key)
            value_slug = _slugify_filename_part(value)
            if key_slug:
                parts.append(key_slug)
            if value_slug:
                parts.append(value_slug)

    return "-".join(part for part in parts if part)[:200].rstrip("-") or "github-search"


async def export_github_search_to_csv(
    input_url: str,
    *,
    search_client,
    output_dir: Path | None = None,
    timestamp: str | None = None,
    status_callback=None,
) -> ConversionResult:
    if search_client is None:
        raise ValueError("Missing GitHub search client")

    request = parse_github_search_url(input_url)
    if callable(status_callback):
        status_callback(f"🔎 Collecting repositories for query: {request.query}")

    repositories = await search_client.collect_repositories(request)
    adapter = GithubSearchInputAdapter()
    rows = [
        _csv_row_from_record(adapter.to_record(row))
        for row in sorted(repositories, key=lambda row: row.created, reverse=True)
    ]
    csv_path = build_github_search_csv_path(
        input_url,
        output_dir=output_dir,
        timestamp=timestamp,
    )

    return ConversionResult(
        csv_path=write_rows_to_csv_path(rows, csv_path),
        resolved=len(rows),
        skipped=[],
    )


def _slugify_filename_part(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or ""


def _csv_row_from_record(record) -> CsvRow:
    return CsvRow(
        name=_string_value(record.name.value),
        url=_string_value(record.url.value),
        github=_string_value(record.github.value),
        stars="" if record.stars.value is None else record.stars.value,
        created=_string_value(record.created.value),
        about=_string_value(record.about.value),
    )


def _string_value(value) -> str:
    if value is None:
        return ""
    return str(value)
