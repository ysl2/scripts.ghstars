from pathlib import Path

import pytest

from src.shared.papers import PaperSeed
from src.url_to_csv.semanticscholar import (
    SemanticScholarSearchSpec,
    extract_paper_seeds_from_semanticscholar_html,
    extract_total_pages_from_semanticscholar_html,
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
        / "semanticscholar-semantic-3d-reconstruction-2025-2026-computer-science-Computer-Vision-and-Pattern-Recognition.csv"
    )


def test_extract_total_pages_from_semanticscholar_html_reads_rendered_pager():
    html = (
        '<div class="cl-pager" data-curr-page-num="1" '
        'data-total-pages="22" data-test-id="result-page-pagination"></div>'
    )

    assert extract_total_pages_from_semanticscholar_html(html) == 22


def test_extract_paper_seeds_from_semanticscholar_html_reads_title_links():
    html = """
    <div class="cl-pager" data-total-pages="2" data-test-id="result-page-pagination"></div>
    <a data-test-id="title-link" href="/paper/Foo/abc123">
      <h2 class="cl-paper-title"><span>Foo </span><em>3D</em><span> Reconstruction</span></h2>
    </a>
    <a data-test-id="title-link" href="https://www.semanticscholar.org/paper/Bar/def456">
      <h2 class="cl-paper-title">Bar Paper</h2>
    </a>
    """

    assert extract_paper_seeds_from_semanticscholar_html(html) == [
        PaperSeed(name="Foo 3D Reconstruction", url="https://www.semanticscholar.org/paper/Foo/abc123"),
        PaperSeed(name="Bar Paper", url="https://www.semanticscholar.org/paper/Bar/def456"),
    ]


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_semanticscholar_url_fetches_raw_candidates(tmp_path: Path):
    class FakeSemanticScholarClient:
        def __init__(self):
            self.calls = []

        async def fetch_search_page_html(self, url: str):
            self.calls.append(url)
            if "page=2" in url:
                return """
                <div class="cl-pager" data-total-pages="2" data-test-id="result-page-pagination"></div>
                <a data-test-id="title-link" href="/paper/Paper-A/abc123">
                  <h2 class="cl-paper-title">Duplicate Paper A</h2>
                </a>
                <a data-test-id="title-link" href="/paper/Paper-B/def456">
                  <h2 class="cl-paper-title">Paper B</h2>
                </a>
                """

            return """
            <div class="cl-pager" data-total-pages="2" data-test-id="result-page-pagination"></div>
            <a data-test-id="title-link" href="/paper/Paper-A/abc123">
              <h2 class="cl-paper-title">Paper A</h2>
            </a>
            """

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
        ("Paper A", "https://www.semanticscholar.org/paper/Paper-A/abc123"),
        ("Paper B", "https://www.semanticscholar.org/paper/Paper-B/def456"),
    ]
    assert client.calls[0] == (
        "https://www.semanticscholar.org/search"
        "?year%5B0%5D=2025"
        "&year%5B1%5D=2026"
        "&fos%5B0%5D=computer-science"
        "&venue%5B0%5D=Computer%20Vision%20and%20Pattern%20Recognition"
        "&q=semantic%203d%20reconstruction"
        "&sort=pub-date"
    )
    assert client.calls[1].endswith("&page=2")
    assert result.csv_path == (
        tmp_path
        / "semanticscholar-semantic-3d-reconstruction-2025-2026-computer-science-Computer-Vision-and-Pattern-Recognition.csv"
    )
    assert any("Fetching Semantic Scholar search results page 1" in message for message in messages)
    assert any("Fetched page 2: 2 results" in message for message in messages)
