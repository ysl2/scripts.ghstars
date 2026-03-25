import csv

from src.shared.csv_io import write_records_to_csv_path
from src.shared.papers import PaperRecord, sort_records


def test_sort_records_orders_newer_arxiv_urls_first():
    records = [
        PaperRecord(name="Middle", github="", stars="", url="https://arxiv.org/abs/2603.20000"),
        PaperRecord(name="Newest", github="", stars="", url="https://arxiv.org/abs/2603.30000"),
        PaperRecord(name="Oldest", github="", stars="", url="https://arxiv.org/abs/2603.10000"),
    ]

    assert [record.url for record in sort_records(records)] == [
        "https://arxiv.org/abs/2603.30000",
        "https://arxiv.org/abs/2603.20000",
        "https://arxiv.org/abs/2603.10000",
    ]


def test_write_records_to_csv_path_sorts_and_serializes_stars(tmp_path):
    csv_path = tmp_path / "papers.csv"
    records = [
        PaperRecord(name="Older", github="https://github.com/foo/old", stars=10, url="https://arxiv.org/abs/2603.10000"),
        PaperRecord(name="Newer", github="https://github.com/foo/new", stars=20, url="https://arxiv.org/abs/2603.20000"),
    ]

    write_records_to_csv_path(records, csv_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "Name": "Newer",
            "Url": "https://arxiv.org/abs/2603.20000",
            "Github": "https://github.com/foo/new",
            "Stars": "20",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2603.10000",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
        },
    ]


def test_sort_records_preserves_source_order_for_non_arxiv_urls():
    records = [
        PaperRecord(
            name="First",
            github="",
            stars="",
            url="https://www.semanticscholar.org/paper/first/1",
            sort_index=1,
        ),
        PaperRecord(
            name="Second",
            github="",
            stars="",
            url="https://www.semanticscholar.org/paper/second/2",
            sort_index=2,
        ),
    ]

    assert [record.url for record in sort_records(records)] == [
        "https://www.semanticscholar.org/paper/first/1",
        "https://www.semanticscholar.org/paper/second/2",
    ]
