from src.core.output_adapters import CsvUpdateAdapter, FreshCsvExportAdapter
from src.core.record_model import PropertyState, Record


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


def test_csv_update_adapter_overwrites_stars_and_about_but_only_backfills_created():
    adapter = CsvUpdateAdapter()
    original = {
        "Name": "Paper A",
        "Url": "https://arxiv.org/abs/2501.12345",
        "Github": "https://github.com/foo/bar",
        "Stars": "5",
        "Created": "2019-01-01T00:00:00Z",
        "About": "old",
    }
    record = (
        Record.from_source(
            name="Paper A",
            url="https://arxiv.org/abs/2501.12345",
            github="https://github.com/foo/bar",
            stars=42,
            created="2020-01-01T00:00:00Z",
            about="old",
            source="csv_update",
        )
        .with_property("stars", PropertyState.resolved(42, source="github_api"))
        .with_property("created", PropertyState.resolved("2020-01-01T00:00:00Z", source="github_api"))
        .with_property("about", PropertyState.resolved("", source="github_api"))
    )

    updated = adapter.apply(original, record)

    assert updated["Stars"] == "42"
    assert updated["About"] == ""
    assert updated["Created"] == "2019-01-01T00:00:00Z"


def test_csv_update_adapter_appends_missing_managed_columns_without_reordering_existing_columns():
    adapter = CsvUpdateAdapter()

    assert adapter.normalize_fieldnames(["Url", "Name"]) == [
        "Url",
        "Name",
        "Github",
        "Stars",
        "Created",
        "About",
    ]
