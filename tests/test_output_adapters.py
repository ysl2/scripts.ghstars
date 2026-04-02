from src.core.output_adapters import FreshCsvExportAdapter
from src.core.record_model import Record


def test_fresh_csv_export_adapter_serializes_record_into_shared_six_column_row():
    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345",
        github="https://github.com/foo/bar",
        stars=42,
        created="2020-01-01T00:00:00Z",
        about="repo",
        source="paper_export",
    )

    row = FreshCsvExportAdapter().to_csv_row(record, sort_index=3)

    assert row.name == "Paper A"
    assert row.url == "https://arxiv.org/abs/2501.12345"
    assert row.github == "https://github.com/foo/bar"
    assert row.stars == 42
    assert row.created == "2020-01-01T00:00:00Z"
    assert row.about == "repo"
    assert row.sort_index == 3
