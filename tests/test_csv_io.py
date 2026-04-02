from pathlib import Path

from src.shared.csv_io import CSV_HEADERS, write_records_to_csv_path, write_rows_to_csv_path
from src.shared.csv_rows import CsvRow
from src.shared.papers import PaperRecord


def test_write_rows_to_csv_path_uses_unified_header_order(tmp_path: Path):
    csv_path = tmp_path / "rows.csv"

    write_rows_to_csv_path(
        [
            CsvRow(
                name="Paper A",
                url="https://arxiv.org/abs/2501.00001",
                github="https://github.com/foo/bar",
                stars=7,
                created="",
                about="",
                sort_index=1,
            )
        ],
        csv_path,
    )

    assert CSV_HEADERS == ["Name", "Url", "Github", "Stars", "Created", "About"]
    assert csv_path.read_text(encoding="utf-8").splitlines() == [
        "Name,Url,Github,Stars,Created,About",
        "Paper A,https://arxiv.org/abs/2501.00001,https://github.com/foo/bar,7,,",
    ]


def test_write_rows_to_csv_path_uses_sort_index_even_for_arxiv_like_urls(tmp_path: Path):
    csv_path = tmp_path / "rows.csv"

    write_rows_to_csv_path(
        [
            CsvRow(
                name="Second",
                url="https://arxiv.org/abs/2603.20000",
                github="https://github.com/foo/second",
                stars=20,
                created="",
                about="",
                sort_index=2,
            ),
            CsvRow(
                name="First",
                url="https://arxiv.org/abs/2603.10000",
                github="https://github.com/foo/first",
                stars=10,
                created="",
                about="",
                sort_index=1,
            ),
        ],
        csv_path,
    )

    assert csv_path.read_text(encoding="utf-8").splitlines() == [
        "Name,Url,Github,Stars,Created,About",
        "First,https://arxiv.org/abs/2603.10000,https://github.com/foo/first,10,,",
        "Second,https://arxiv.org/abs/2603.20000,https://github.com/foo/second,20,,",
    ]


def test_write_records_to_csv_path_uses_unified_header_order(tmp_path: Path):
    csv_path = tmp_path / "records.csv"

    write_records_to_csv_path(
        [
            PaperRecord(
                name="Newer",
                url="https://arxiv.org/abs/2603.20000",
                github="https://github.com/foo/new",
                stars=20,
            )
        ],
        csv_path,
    )

    assert csv_path.read_text(encoding="utf-8").splitlines() == [
        "Name,Url,Github,Stars,Created,About",
        "Newer,https://arxiv.org/abs/2603.20000,https://github.com/foo/new,20,,",
    ]
