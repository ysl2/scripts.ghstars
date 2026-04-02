from types import SimpleNamespace

from src.core.input_adapters import (
    CsvRowInputAdapter,
    GithubSearchInputAdapter,
    NotionPageInputAdapter,
    PaperSeedInputAdapter,
)
from src.shared.papers import PaperSeed


def test_github_search_input_adapter_marks_repo_side_values_as_trusted():
    record = GithubSearchInputAdapter().to_record(
        SimpleNamespace(
            github="https://github.com/foo/bar",
            stars=99,
            created="2020-01-01T00:00:00Z",
            about="repo",
        )
    )

    assert record.github.trusted is True
    assert record.stars.trusted is True
    assert record.created.trusted is True
    assert record.about.trusted is True


def test_paper_seed_input_adapter_keeps_name_and_url_as_source_values():
    record = PaperSeedInputAdapter().to_record(
        PaperSeed(name="Paper A", url="https://arxiv.org/abs/2501.12345")
    )

    assert record.name.value == "Paper A"
    assert record.url.value == "https://arxiv.org/abs/2501.12345"
    assert record.github.value is None


def test_csv_row_input_adapter_attaches_row_index_context():
    record = CsvRowInputAdapter().to_record(
        7,
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2501.12345",
            "Github": "https://github.com/foo/bar",
            "Stars": "42",
            "Created": "2020-01-01T00:00:00Z",
            "About": "repo",
        },
    )

    assert record.context.csv_row_index == 7
    assert record.name.value == "Paper A"
    assert record.github.value == "https://github.com/foo/bar"


def test_notion_page_input_adapter_builds_record_from_existing_page_properties():
    record = NotionPageInputAdapter().to_record(
        {
            "id": "page-1",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Paper A"}]},
                "URL": {
                    "type": "url",
                    "url": "https://arxiv.org/abs/2501.12345",
                },
                "Github": {"type": "url", "url": "https://github.com/foo/bar"},
                "Stars": {"type": "number", "number": 42},
                "Created": {
                    "type": "date",
                    "date": {"start": "2020-01-01T00:00:00Z"},
                },
                "About": {
                    "type": "rich_text",
                    "rich_text": [{"plain_text": "Current about"}],
                },
            },
        }
    )

    assert record.context.notion_page_id == "page-1"
    assert record.github.trusted is True
    assert record.url.value == "https://arxiv.org/abs/2501.12345"
    assert record.about.value == "Current about"
