import csv
from pathlib import Path

import pytest

from html_to_csv.csv_writer import output_csv_path_for_html
from html_to_csv.html_parser import normalize_arxiv_url, parse_paper_seeds_from_html
from html_to_csv.models import PaperRecord, PaperSeed, sort_records
from html_to_csv.pipeline import build_paper_outcome, convert_html_to_csv


def test_normalize_arxiv_url_strips_versions_from_abs_and_pdf_urls():
    assert normalize_arxiv_url("https://arxiv.org/abs/2603.18493v2") == "https://arxiv.org/abs/2603.18493"
    assert normalize_arxiv_url("https://arxiv.org/pdf/2603.18493v1.pdf") == "https://arxiv.org/abs/2603.18493"


def test_parse_paper_seeds_deduplicates_by_canonical_arxiv_url():
    html = """
    <div class="chakra-card__root">
      <h2 class="chakra-heading">First Title</h2>
      <a href="https://arxiv.org/abs/2603.18493v2">View</a>
    </div>
    <div class="chakra-card__root">
      <h2 class="chakra-heading">Changed Title</h2>
      <a href="https://arxiv.org/pdf/2603.18493v1.pdf">View</a>
    </div>
    <div class="chakra-card__root">
      <h2 class="chakra-heading">Second Title</h2>
      <a href="https://arxiv.org/abs/2603.17519">View</a>
    </div>
    """

    assert parse_paper_seeds_from_html(html) == [
        PaperSeed(name="First Title", url="https://arxiv.org/abs/2603.18493"),
        PaperSeed(name="Second Title", url="https://arxiv.org/abs/2603.17519"),
    ]


def test_parse_paper_seeds_collapses_title_whitespace():
    html = """
    <div class="chakra-card__root">
      <h2 class="chakra-heading">A Title With
          Multiple   Spaces</h2>
      <a href="https://arxiv.org/abs/2603.18493v2">View</a>
    </div>
    """

    assert parse_paper_seeds_from_html(html) == [
        PaperSeed(name="A Title With Multiple Spaces", url="https://arxiv.org/abs/2603.18493"),
    ]


def test_sort_records_orders_newer_urls_first():
    records = [
        PaperRecord(
            name="Middle",
            github="",
            stars="",
            url="https://arxiv.org/abs/2603.20000",
        ),
        PaperRecord(
            name="Newest",
            github="",
            stars="",
            url="https://arxiv.org/abs/2603.30000",
        ),
        PaperRecord(
            name="Oldest",
            github="",
            stars="",
            url="https://arxiv.org/abs/2603.10000",
        ),
    ]

    assert [record.url for record in sort_records(records)] == [
        "https://arxiv.org/abs/2603.30000",
        "https://arxiv.org/abs/2603.20000",
        "https://arxiv.org/abs/2603.10000",
    ]


def test_output_csv_path_for_html_reuses_directory_and_stem(tmp_path: Path):
    html_path = tmp_path / "nested" / "papers.html"
    html_path.parent.mkdir()
    html_path.write_text("<html></html>", encoding="utf-8")

    assert output_csv_path_for_html(html_path) == tmp_path / "nested" / "papers.csv"


@pytest.mark.anyio
async def test_convert_html_to_csv_generates_sorted_csv_from_html(tmp_path: Path):
    html_path = tmp_path / "papers.html"
    html_path.write_text(
        """
        <div class="chakra-card__root">
          <h2 class="chakra-heading">Older Paper</h2>
          <a href="https://arxiv.org/abs/2603.10000v1">View</a>
        </div>
        <div class="chakra-card__root">
          <h2 class="chakra-heading">Newer Paper</h2>
          <a href="https://arxiv.org/abs/2603.20000v2">View</a>
        </div>
        <div class="chakra-card__root">
          <h2 class="chakra-heading">Duplicate Newer Paper</h2>
          <a href="https://arxiv.org/pdf/2603.20000v1.pdf">View</a>
        </div>
        """,
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            mapping = {
                "https://arxiv.org/abs/2603.10000": "https://github.com/foo/old",
                "https://arxiv.org/abs/2603.20000": "https://github.com/foo/new",
            }
            return mapping.get(seed.url)

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            mapping = {
                ("foo", "old"): (10, None),
                ("foo", "new"): (20, None),
            }
            return mapping[(owner, repo)]

    result = await convert_html_to_csv(
        html_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    with result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.csv_path == tmp_path / "papers.csv"
    assert result.resolved == 2
    assert result.skipped == []
    assert rows == [
        {
            "Name": "Newer Paper",
            "Url": "https://arxiv.org/abs/2603.20000",
            "Github": "https://github.com/foo/new",
            "Stars": "20",
        },
        {
            "Name": "Older Paper",
            "Url": "https://arxiv.org/abs/2603.10000",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
        },
    ]


@pytest.mark.anyio
async def test_convert_html_to_csv_tracks_skipped_rows_when_github_resolution_fails(tmp_path: Path):
    html_path = tmp_path / "papers.html"
    html_path.write_text(
        """
        <div class="chakra-card__root">
          <h2 class="chakra-heading">Paper A</h2>
          <a href="https://arxiv.org/abs/2603.10000v1">View</a>
        </div>
        <div class="chakra-card__root">
          <h2 class="chakra-heading">Paper B</h2>
          <a href="https://arxiv.org/abs/2603.20000v2">View</a>
        </div>
        """,
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            if seed.url.endswith("2603.10000"):
                return ""
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            return 42, None

    result = await convert_html_to_csv(
        html_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    assert result.resolved == 1
    assert len(result.skipped) == 1
    assert result.skipped[0]["title"] == "Paper A"
    assert result.skipped[0]["reason"] == "No Github URL found from discovery"


@pytest.mark.anyio
async def test_build_paper_outcome_does_not_create_nested_asyncio_task(monkeypatch):
    def fail_create_task(*args, **kwargs):
        raise AssertionError("nested create_task should not be used here")

    monkeypatch.setattr("html_to_csv.pipeline.asyncio.create_task", fail_create_task)

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            return 42, None

    outcome = await build_paper_outcome(
        1,
        PaperSeed(name="Test Paper", url="https://arxiv.org/abs/2603.20000"),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    assert outcome.record.github == "https://github.com/foo/bar"
    assert outcome.record.stars == 42
