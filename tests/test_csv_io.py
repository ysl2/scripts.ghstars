from pathlib import Path

from src.shared.csv_io import CSV_HEADERS, write_rows_to_csv_path
from src.shared.csv_rows import CsvRow


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
