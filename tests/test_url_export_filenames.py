from pathlib import Path

import pytest


def test_build_url_export_csv_path_joins_parts_and_appends_timestamp(tmp_path: Path):
    try:
        from src.url_to_csv.filenames import build_url_export_csv_path
    except ModuleNotFoundError:
        pytest.fail("src.url_to_csv.filenames is missing")

    csv_path = build_url_export_csv_path(
        ["arxiv", "cs.CV", "new"],
        output_dir=tmp_path,
        timestamp="20260326113045",
    )

    assert csv_path == tmp_path / "arxiv-cs.CV-new-20260326113045.csv"


def test_build_url_export_csv_path_defaults_to_output_directory():
    try:
        from src.url_to_csv.filenames import build_url_export_csv_path
    except ModuleNotFoundError:
        pytest.fail("src.url_to_csv.filenames is missing")

    csv_path = build_url_export_csv_path(
        ["arxiv", "cs.CV", "new"],
        timestamp="20260326113045",
    )

    assert csv_path == Path("output") / "arxiv-cs.CV-new-20260326113045.csv"
