import csv
from datetime import date
from pathlib import Path

import pytest

from src.github_search_to_csv.models import RepositorySearchRow, SearchPartition, SearchRequest
from src.github_search_to_csv.pipeline import (
    build_github_search_csv_path,
    export_github_search_to_csv,
)
from src.github_search_to_csv.search import (
    GitHubRepositorySearchClient,
    collect_repositories,
    is_supported_github_search_url,
    parse_github_search_url,
    resolve_github_search_min_interval,
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


def test_resolve_github_search_min_interval_respects_documented_search_quotas():
    assert resolve_github_search_min_interval("", 0.2) == 6.0
    assert resolve_github_search_min_interval("gh_token", 0.2) == 2.0
    assert resolve_github_search_min_interval("", 10.0) == 10.0
    assert resolve_github_search_min_interval("gh_token", 3.0) == 3.0


class FakeResponse:
    def __init__(self, payload, *, status=200, headers=None, text=""):
        self.payload = payload
        self.status = status
        self.headers = headers or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params=None, headers=None):
        self.calls.append(
            {
                "url": url,
                "params": dict(params or {}),
                "headers": dict(headers or {}),
            }
        )
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_count_results_fails_when_github_search_reports_incomplete_results():
    client = GitHubRepositorySearchClient(
        FakeSession(
            [
                FakeResponse(
                    {"total_count": 1200, "incomplete_results": True, "items": []},
                )
            ]
        ),
        github_token="gh_token",
        max_concurrent=1,
        min_interval=0,
    )

    with pytest.raises(RuntimeError, match="incomplete_results"):
        await client.count_results(SearchPartition(request=SearchRequest(query="cvpr 2026")))


@pytest.mark.anyio
async def test_fetch_partition_fails_when_github_search_reports_incomplete_results():
    client = GitHubRepositorySearchClient(
        FakeSession(
            [
                FakeResponse(
                    {
                        "total_count": 100,
                        "incomplete_results": True,
                        "items": [
                            {
                                "html_url": "https://github.com/foo/bar",
                                "stargazers_count": 42,
                                "description": "partial",
                                "created_at": "2024-01-01T00:00:00Z",
                            }
                        ],
                    },
                )
            ]
        ),
        github_token="gh_token",
        max_concurrent=1,
        min_interval=0,
    )

    with pytest.raises(RuntimeError, match="incomplete_results"):
        await client.fetch_partition(SearchPartition(request=SearchRequest(query="cvpr 2026")))


@pytest.mark.anyio
async def test_collect_repositories_splits_created_range_before_deeper_star_splits():
    start = date(2020, 1, 1)
    end = date(2020, 1, 10)
    request = SearchRequest(query="cvpr 2026")

    class FakeSearchClient:
        def __init__(self):
            self.count_calls = []

        async def count_results(self, partition):
            key = (
                partition.stars_min,
                partition.stars_max,
                partition.created_after,
                partition.created_before,
            )
            self.count_calls.append(key)
            if key == (0, 100, start, end):
                return 2001
            if key == (0, 50, start, end):
                return 2001
            return 10

        async def fetch_partition(self, partition):
            suffix = (
                f"{partition.stars_min}-{partition.stars_max}-"
                f"{partition.created_after.isoformat()}-{partition.created_before.isoformat()}"
            )
            return [
                RepositorySearchRow(
                    github=f"https://github.com/example/{suffix}",
                    stars=1,
                    about="",
                    created="2024-01-01T00:00:00Z",
                )
            ]

    client = FakeSearchClient()
    rows = await collect_repositories(
        client,
        request,
        default_created_after=start,
        default_created_before=end,
        default_stars_min=0,
        default_stars_max=100,
    )

    assert rows
    assert client.count_calls[:3] == [
        (0, 100, start, end),
        (0, 50, start, end),
        (0, 50, start, date(2020, 1, 5)),
    ]


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
