from src.core.output_adapters import CsvUpdateAdapter, FreshCsvExportAdapter, NotionUpdateAdapter
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


def test_notion_update_adapter_builds_patch_with_about_overwrite_and_created_backfill_only():
    adapter = NotionUpdateAdapter()
    page = {
        "id": "page-1",
        "properties": {
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 5},
            "Created": {"type": "date", "date": {"start": "2019-01-01"}},
            "About": {"type": "rich_text", "rich_text": [{"plain_text": "old"}]},
        },
    }
    record = (
        Record.from_source(
            github="https://github.com/foo/bar",
            stars=42,
            created="2020-01-01",
            about="old",
            source="notion_sync",
        )
        .with_property("about", PropertyState.resolved("", source="github_api"))
    )

    patch = adapter.build_patch(page, record, update_github=False)

    assert patch["Stars"]["number"] == 42
    assert patch["About"]["rich_text"] == []
    assert "Created" not in patch
