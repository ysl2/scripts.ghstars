from pathlib import Path

import pytest

from src.shared.papers import PaperSeed
from src.url_to_csv.semanticscholar import (
    SemanticScholarSearchSpec,
    fetch_paper_seeds_from_semanticscholar_url,
    is_supported_semanticscholar_url,
    output_csv_path_for_semanticscholar_url,
    parse_semanticscholar_url,
)


def test_is_supported_semanticscholar_url_accepts_search_pages():
    assert is_supported_semanticscholar_url("https://www.semanticscholar.org/search?q=semantic")
    assert is_supported_semanticscholar_url(
        "https://www.semanticscholar.org/search?year%5B0%5D=2025&year%5B1%5D=2026&q=semantic"
    )


def test_is_supported_semanticscholar_url_rejects_non_search_pages():
    assert not is_supported_semanticscholar_url("https://www.semanticscholar.org/paper/Foo/123")
    assert not is_supported_semanticscholar_url("https://example.com/search?q=semantic")


def test_parse_semanticscholar_url_reads_query_filters_and_sort():
    spec = parse_semanticscholar_url(
        "https://www.semanticscholar.org/search"
        "?year%5B0%5D=2025"
        "&year%5B1%5D=2026"
        "&fos%5B0%5D=computer-science"
        "&venue%5B0%5D=Computer%20Vision%20and%20Pattern%20Recognition"
        "&q=semantic%203d%20reconstruction"
        "&sort=pub-date"
    )

    assert spec == SemanticScholarSearchSpec(
        search_text="semantic 3d reconstruction",
        years=("2025", "2026"),
        fields_of_study=("computer-science",),
        venues=("Computer Vision and Pattern Recognition",),
        sort="pub-date",
    )


def test_output_csv_path_for_semanticscholar_url_uses_query_terms_and_filters(tmp_path: Path):
    csv_path = output_csv_path_for_semanticscholar_url(
        "https://www.semanticscholar.org/search"
        "?year%5B0%5D=2025"
        "&year%5B1%5D=2026"
        "&fos%5B0%5D=computer-science"
        "&venue%5B0%5D=Computer%20Vision%20and%20Pattern%20Recognition"
        "&q=semantic%203d%20reconstruction"
        "&sort=pub-date",
        output_dir=tmp_path,
    )

    assert (
        csv_path
        == tmp_path
        / "semanticscholar-semantic-3d-reconstruction-2025-2026-computer-science-Computer-Vision-and-Pattern-Recognition-20260326113045.csv"
    )


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_semanticscholar_url_uses_bulk_graph_search_and_token_pagination(
    tmp_path: Path,
):
    class FakeSemanticScholarClient:
        def __init__(self):
            self.calls = []

        async def fetch_search_bulk_page(self, params: dict[str, str]):
            self.calls.append(dict(params))
            token = params.get("token")
            if token == "token-1":
                return {
                    "data": [
                        {
                            "paperId": "def456",
                            "title": "Duplicate Paper B",
                            "externalIds": {},
                            "url": "https://www.semanticscholar.org/paper/Paper-B/def456",
                        },
                        {
                            "paperId": "ghi789",
                            "title": "Paper C",
                            "externalIds": {},
                            "url": "",
                        },
                    ]
                }

            assert token is None
            return {
                "total": 3,
                "token": "token-1",
                "data": [
                    {
                        "paperId": "abc123",
                        "title": "Paper A",
                        "externalIds": {"ArXiv": "2501.00001"},
                        "url": "https://www.semanticscholar.org/paper/Paper-A/abc123",
                    },
                    {
                        "paperId": "def456",
                        "title": "Paper B",
                        "externalIds": {},
                        "url": "https://www.semanticscholar.org/paper/Paper-B/def456",
                    },
                ],
            }

    client = FakeSemanticScholarClient()
    messages = []
    result = await fetch_paper_seeds_from_semanticscholar_url(
        "https://www.semanticscholar.org/search"
        "?year%5B0%5D=2025"
        "&year%5B1%5D=2026"
        "&fos%5B0%5D=computer-science"
        "&venue%5B0%5D=Computer%20Vision%20and%20Pattern%20Recognition"
        "&q=semantic%203d%20reconstruction"
        "&sort=pub-date",
        semanticscholar_client=client,
        output_dir=tmp_path,
        status_callback=messages.append,
    )

    assert [(seed.name, seed.url) for seed in result.seeds] == [
        ("Paper A", "https://arxiv.org/abs/2501.00001"),
        ("Paper B", "https://www.semanticscholar.org/paper/Paper-B/def456"),
        ("Paper C", "https://www.semanticscholar.org/paper/ghi789"),
    ]
    assert client.calls == [
        {
            "query": "semantic 3d reconstruction",
            "year": "2025-2026",
            "fieldsOfStudy": "computer-science",
            "venue": "Computer Vision and Pattern Recognition",
            "sort": "publicationDate:desc",
            "fields": "paperId,title,externalIds,url",
        },
        {
            "query": "semantic 3d reconstruction",
            "year": "2025-2026",
            "fieldsOfStudy": "computer-science",
            "venue": "Computer Vision and Pattern Recognition",
            "sort": "publicationDate:desc",
            "fields": "paperId,title,externalIds,url",
            "token": "token-1",
        },
    ]
    assert result.csv_path == (
        tmp_path
        / "semanticscholar-semantic-3d-reconstruction-2025-2026-computer-science-Computer-Vision-and-Pattern-Recognition-20260326113045.csv"
    )
    assert any("Fetching Semantic Scholar bulk search batch 1" in message for message in messages)
    assert any("Estimated 3 Semantic Scholar matches" in message for message in messages)
    assert any("Fetched batch 2: 2 results" in message for message in messages)
