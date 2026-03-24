import csv
from pathlib import Path

import pytest

from html_to_csv.arxiv import extract_published_date_from_feed, extract_submitted_date_from_abs_html
from html_to_csv.csv_writer import output_csv_path_for_html
from html_to_csv.html_parser import normalize_arxiv_url, parse_paper_seeds_from_html
from html_to_csv.models import PaperRecord, PaperSeed, sort_records
from html_to_csv.pipeline import convert_html_to_csv


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


def test_sort_records_orders_newer_dates_first_and_empty_dates_last():
    records = [
        PaperRecord(
            name="Missing Date",
            date="",
            github="",
            stars="",
            url="https://arxiv.org/abs/2603.99999",
        ),
        PaperRecord(
            name="Same Date B",
            date="2026-03-02",
            github="",
            stars="",
            url="https://arxiv.org/abs/2603.20000",
        ),
        PaperRecord(
            name="Newest",
            date="2026-03-03",
            github="",
            stars="",
            url="https://arxiv.org/abs/2603.30000",
        ),
        PaperRecord(
            name="Same Date A",
            date="2026-03-02",
            github="",
            stars="",
            url="https://arxiv.org/abs/2603.10000",
        ),
    ]

    assert [record.url for record in sort_records(records)] == [
        "https://arxiv.org/abs/2603.30000",
        "https://arxiv.org/abs/2603.10000",
        "https://arxiv.org/abs/2603.20000",
        "https://arxiv.org/abs/2603.99999",
    ]


def test_extract_published_date_from_feed_uses_exact_arxiv_entry():
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://arxiv.org/abs/2603.18493v2</id>
        <published>2026-03-24T12:34:56Z</published>
      </entry>
      <entry>
        <id>https://arxiv.org/abs/2603.17519v1</id>
        <published>2026-03-22T00:00:00Z</published>
      </entry>
    </feed>
    """

    assert (
        extract_published_date_from_feed(feed_xml, "https://arxiv.org/abs/2603.18493")
        == "2026-03-24"
    )


def test_extract_submitted_date_from_abs_html_returns_iso_date():
    html = """
    <div class="dateline">
      [Submitted on 15 Jan 2025]
    </div>
    """

    assert extract_submitted_date_from_abs_html(html) == "2025-01-15"


def test_extract_submitted_date_from_abs_html_handles_revised_versions():
    html = """
    <div class="dateline">
      [Submitted on 23 Jan 2025 (v1), last revised 19 Mar 2025 (this version, v2)]
    </div>
    """

    assert extract_submitted_date_from_abs_html(html) == "2025-01-23"


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

    class FakeArxivClient:
        async def get_published_date(self, url):
            mapping = {
                "https://arxiv.org/abs/2603.10000": "2026-03-20",
                "https://arxiv.org/abs/2603.20000": "2026-03-24",
            }
            return mapping.get(url), None

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

    csv_path = await convert_html_to_csv(
        html_path,
        arxiv_client=FakeArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert csv_path == tmp_path / "papers.csv"
    assert rows == [
        {
            "Name": "Newer Paper",
            "Date": "2026-03-24",
            "Github": "https://github.com/foo/new",
            "Stars": "20",
            "Url": "https://arxiv.org/abs/2603.20000",
        },
        {
            "Name": "Older Paper",
            "Date": "2026-03-20",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
            "Url": "https://arxiv.org/abs/2603.10000",
        },
    ]


@pytest.mark.anyio
async def test_convert_html_to_csv_prefers_batch_date_lookup_when_available(tmp_path: Path):
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

    class FakeArxivClient:
        def __init__(self):
            self.batch_calls = []

        async def get_published_dates(self, urls):
            self.batch_calls.append(tuple(urls))
            return {
                "https://arxiv.org/abs/2603.10000": "2026-03-20",
                "https://arxiv.org/abs/2603.20000": "2026-03-24",
            }, {}

        async def get_published_date(self, url):
            raise AssertionError("single-date lookup should not be used")

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            return ""

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            raise AssertionError("star lookup should not be reached")

    arxiv_client = FakeArxivClient()
    await convert_html_to_csv(
        html_path,
        arxiv_client=arxiv_client,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    assert arxiv_client.batch_calls == [
        (
            "https://arxiv.org/abs/2603.10000",
            "https://arxiv.org/abs/2603.20000",
        )
    ]
