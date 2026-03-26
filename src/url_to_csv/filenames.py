from datetime import datetime
from pathlib import Path


def current_run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def build_url_export_csv_path(
    parts: list[str] | tuple[str, ...],
    *,
    output_dir: Path | None = None,
    timestamp: str | None = None,
) -> Path:
    directory = Path(output_dir) if output_dir is not None else Path("output")
    stem = "-".join(part for part in parts if part)[:200].rstrip("-") or "papers"
    suffix = timestamp or current_run_timestamp()
    return directory / f"{stem}-{suffix}.csv"
