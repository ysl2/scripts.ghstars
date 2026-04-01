import csv
from pathlib import Path

import pytest

from src.github_search_to_csv.models import RepositorySearchRow, SearchRequest
from src.github_search_to_csv.pipeline import (
    build_github_search_csv_path,
    export_github_search_to_csv,
)
from src.github_search_to_csv.search import (
    is_supported_github_search_url,
    parse_github_search_url,
)


def test_is_supported_github_search_url_accepts_repository_search_url():
    assert is_supported_github_search_url(
        "https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/search?type=repositories&s=stars&o=desc",
        "https://github.com/search?q=cvpr+2026&type=issues&s=stars&o=desc",
        "https://github.com/songliyu/scripts.ghstars",
    ],
)
def test_is_supported_github_search_url_rejects_unsupported_github_urls(url: str):
    assert not is_supported_github_search_url(url)


def test_parse_github_search_url_reads_query_sort_and_order():
    request = parse_github_search_url(
        "https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc"
    )

    assert request == SearchRequest(query="cvpr 2026", sort="stars", order="desc")


def test_build_github_search_csv_path_includes_query_and_sort_parts(tmp_path: Path):
    csv_path = build_github_search_csv_path(
        "https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc",
        output_dir=tmp_path,
        timestamp="20260326113045",
    )

    assert (
        csv_path
        == tmp_path
        / "github-search-cvpr-2026-o-desc-s-stars-type-repositories-20260326113045.csv"
    )


@pytest.mark.anyio
async def test_export_github_search_to_csv_writes_unified_rows_sorted_by_created_desc(
    tmp_path: Path,
):
    class FakeSearchClient:
        def __init__(self):
            self.requests = []

        async def collect_repositories(self, request):
            self.requests.append(request)
            return [
                RepositorySearchRow(
                    github="https://github.com/foo/older",
                    stars=2,
                    about="older",
                    created="2023-01-01T00:00:00Z",
                ),
                RepositorySearchRow(
                    github="https://github.com/foo/newer",
                    stars=5,
                    about="newer",
                    created="2024-01-01T00:00:00Z",
                ),
            ]

    search_client = FakeSearchClient()
    result = await export_github_search_to_csv(
        "https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc",
        search_client=search_client,
        output_dir=tmp_path,
        timestamp="20260326113045",
    )

    assert search_client.requests == [
        SearchRequest(query="cvpr 2026", sort="stars", order="desc")
    ]
    assert result.csv_path == (
        tmp_path
        / "github-search-cvpr-2026-o-desc-s-stars-type-repositories-20260326113045.csv"
    )
    assert result.resolved == 2
    assert result.skipped == []

    with result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "Name": "",
            "Url": "",
            "Github": "https://github.com/foo/newer",
            "Stars": "5",
            "Created": "2024-01-01T00:00:00Z",
            "About": "newer",
        },
        {
            "Name": "",
            "Url": "",
            "Github": "https://github.com/foo/older",
            "Stars": "2",
            "Created": "2023-01-01T00:00:00Z",
            "About": "older",
        },
    ]
