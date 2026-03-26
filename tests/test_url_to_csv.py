import csv
import html
import json
from pathlib import Path

import pytest

from src.url_to_csv.arxivxplorer import TooManyPagesError, output_csv_path_for_arxivxplorer_url, parse_arxivxplorer_url
from src.url_to_csv.pipeline import fetch_paper_seeds_from_url, export_url_to_csv
from src.url_to_csv.runner import run_url_mode


def test_parse_arxivxplorer_url_reads_query_categories_and_years():
    query = parse_arxivxplorer_url(
        "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&cats=cs.LG&year=2026&year=2025"
    )

    assert query.search_text == "streaming semantic 3d reconstruction"
    assert query.categories == ("cs.CV", "cs.LG")
    assert query.years == ("2026", "2025")


def test_output_csv_path_for_arxivxplorer_url_uses_current_working_directory(tmp_path: Path):
    csv_path = output_csv_path_for_arxivxplorer_url(
        "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026&year=2025&year=2024",
        output_dir=tmp_path,
    )

    assert csv_path == tmp_path / "arxivxplorer-streaming-semantic-3d-reconstruction-cs.CV-2026-2025-2024-20260326113045.csv"


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_url_pages_until_empty_and_deduplicates_arxiv_urls():
    class FakeSearchClient:
        def __init__(self):
            self.pages = []

        async def search(self, query, page: int):
            self.pages.append(page)
            data = {
                1: [
                    {"id": "2501.00001", "journal": "arxiv", "title": "Paper A"},
                    {"id": "10.1101/123", "journal": "biorxiv", "title": "Ignore Me"},
                ],
                2: [
                    {"id": "2501.00001", "journal": "arxiv", "title": "Duplicate Paper A"},
                    {"id": "2501.00002", "journal": "arxiv", "title": "Paper B"},
                ],
                3: [],
            }
            return data[page]

    search_client = FakeSearchClient()
    messages = []
    result = await fetch_paper_seeds_from_url(
        "https://arxivxplorer.com/?q=test&cats=cs.CV&year=2026",
        search_client=search_client,
        status_callback=messages.append,
    )

    assert [seed.name for seed in result.seeds] == ["Paper A", "Paper B"]
    assert [seed.url for seed in result.seeds] == [
        "https://arxiv.org/abs/2501.00001",
        "https://arxiv.org/abs/2501.00002",
    ]
    assert search_client.pages == [1, 2, 3]
    assert any("Fetching arXiv Xplorer page 1" in message for message in messages)
    assert any("Fetched page 2" in message for message in messages)


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_url_stops_on_too_many_pages_boundary():
    class FakeSearchClient:
        def __init__(self):
            self.pages = []

        async def search(self, query, page: int):
            self.pages.append(page)
            if page == 1:
                return [{"id": "2501.00001", "journal": "arxiv", "title": "Paper A"}]
            raise TooManyPagesError("Too many pages.")

    search_client = FakeSearchClient()
    messages = []
    result = await fetch_paper_seeds_from_url(
        "https://arxivxplorer.com/?q=test&cats=cs.CV&year=2026",
        search_client=search_client,
        status_callback=messages.append,
    )

    assert [seed.url for seed in result.seeds] == ["https://arxiv.org/abs/2501.00001"]
    assert search_client.pages == [1, 2]
    assert any("Reached arXiv Xplorer page limit" in message for message in messages)


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_url_reads_huggingface_collection_payload():
    payload = {
        "query": {"q": "semantic"},
        "searchResults": [
            {
                "paper": {"id": "2502.00002", "title": "Search Match"},
                "title": "Search Match",
            }
        ],
    }

    class FakeHuggingFacePapersClient:
        async def fetch_collection_html(self, url: str):
            return (
                '<div class="SVELTE_HYDRATER contents" '
                f'data-target="DailyPapers" data-props="{html.escape(json.dumps(payload))}"></div>'
            )

    messages = []
    result = await fetch_paper_seeds_from_url(
        "https://huggingface.co/papers/trending?q=semantic",
        huggingface_papers_client=FakeHuggingFacePapersClient(),
        status_callback=messages.append,
    )

    assert [seed.name for seed in result.seeds] == ["Search Match"]
    assert [seed.url for seed in result.seeds] == ["https://arxiv.org/abs/2502.00002"]
    assert any("Fetching Hugging Face Papers collection" in message for message in messages)


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_url_reads_arxiv_org_collection_results():
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            assert url == "https://arxiv.org/list/cs.CV/recent"
            return """
            <div class='paging'>Total of 2 entries : <span>1-2</span></div>
            <div class='morefewer'>Showing up to 25 entries per page:</div>
            <dl id="articles">
              <dt><a href="/abs/2502.00002">arXiv:2502.00002</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Search Match</div></dd>
              <dt><a href="/abs/2502.00001">arXiv:2502.00001</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Older Match</div></dd>
            </dl>
            """

    messages = []
    result = await fetch_paper_seeds_from_url(
        "https://arxiv.org/list/cs.CV/recent",
        arxiv_org_client=FakeArxivOrgClient(),
        status_callback=messages.append,
    )

    assert [seed.name for seed in result.seeds] == ["Search Match", "Older Match"]
    assert [seed.url for seed in result.seeds] == [
        "https://arxiv.org/abs/2502.00002",
        "https://arxiv.org/abs/2502.00001",
    ]
    assert any("Fetching arXiv.org list page 1" in message for message in messages)


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_url_reads_arxiv_org_catchup_results():
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            assert url == "https://arxiv.org/catchup/cs.CV/2026-03-26"
            return """
            <div class='paging'>Total of 2 entries for Thu, 26 Mar 2026</div>
            <dl id="articles">
              <dt><a href="/abs/2502.00002">arXiv:2502.00002</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Catchup Match</div></dd>
              <dt><a href="/abs/2502.00001">arXiv:2502.00001</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Older Catchup Match</div></dd>
            </dl>
            """

    messages = []
    result = await fetch_paper_seeds_from_url(
        "https://arxiv.org/catchup/cs.CV/2026-03-26",
        arxiv_org_client=FakeArxivOrgClient(),
        status_callback=messages.append,
    )

    assert [seed.name for seed in result.seeds] == ["Catchup Match", "Older Catchup Match"]
    assert [seed.url for seed in result.seeds] == [
        "https://arxiv.org/abs/2502.00002",
        "https://arxiv.org/abs/2502.00001",
    ]
    assert result.csv_path.name == "arxiv-cs.CV-catchup-2026-03-26-20260326113045.csv"
    assert any("Fetching arXiv.org list page 1" in message for message in messages)


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_url_fails_for_incomplete_arxiv_org_catchup_results():
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            assert url == "https://arxiv.org/catchup/cs.CV/2026-03-26"
            return """
            <div class='paging'>Total of 3 entries for Thu, 26 Mar 2026</div>
            <dl id="articles">
              <dt><a href="/abs/2502.00002">arXiv:2502.00002</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Catchup Match</div></dd>
              <dt><a href="/abs/2502.00001">arXiv:2502.00001</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Older Catchup Match</div></dd>
            </dl>
            """

    with pytest.raises(ValueError, match="Cannot guarantee complete export for this arXiv catchup collection"):
        await fetch_paper_seeds_from_url(
            "https://arxiv.org/catchup/cs.CV/2026-03-26",
            arxiv_org_client=FakeArxivOrgClient(),
        )


@pytest.mark.anyio
async def test_fetch_paper_seeds_from_url_reads_semanticscholar_search_results():
    class FakeSemanticScholarClient:
        async def fetch_search_page_html(self, url: str):
            assert "q=semantic%203d%20reconstruction" in url
            return """
            <div class="cl-pager" data-total-pages="1" data-test-id="result-page-pagination"></div>
            <a data-test-id="title-link" href="/paper/Search-Match/abc123">
              <h2 class="cl-paper-title">Search Match</h2>
            </a>
            <a data-test-id="title-link" href="/paper/Missing/def456">
              <h2 class="cl-paper-title">Missing</h2>
            </a>
            """

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            if title == "Search Match":
                return "2502.00002", "title_search_exact", None
            return None, None, "No arXiv ID found from title search"

    messages = []
    result = await fetch_paper_seeds_from_url(
        "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date",
        semanticscholar_client=FakeSemanticScholarClient(),
        arxiv_client=FakeArxivClient(),
        status_callback=messages.append,
    )

    assert [seed.name for seed in result.seeds] == ["Search Match"]
    assert [seed.url for seed in result.seeds] == ["https://arxiv.org/abs/2502.00002"]
    assert any("Fetching Semantic Scholar search results page 1" in message for message in messages)
    assert any("Normalizing to arXiv-backed papers" in message for message in messages)
    assert any("Kept 1/2 arXiv-backed papers" in message for message in messages)


@pytest.mark.anyio
async def test_export_url_to_csv_writes_sorted_csv_in_output_dir(tmp_path: Path):
    class FakeSearchClient:
        async def search(self, query, page: int):
            data = {
                1: [
                    {"id": "2501.00001", "journal": "arxiv", "title": "Older"},
                    {"id": "2502.00002", "journal": "arxiv", "title": "Newer"},
                ],
                2: [],
            }
            return data[page]

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            mapping = {
                "https://arxiv.org/abs/2501.00001": "https://github.com/foo/old",
                "https://arxiv.org/abs/2502.00002": "https://github.com/foo/new",
            }
            return mapping[seed.url]

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            mapping = {
                ("foo", "old"): (10, None),
                ("foo", "new"): (20, None),
            }
            return mapping[(owner, repo)]

    result = await export_url_to_csv(
        "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026&year=2025&year=2024",
        output_dir=tmp_path,
        search_client=FakeSearchClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    with result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.csv_path == (
        tmp_path / "arxivxplorer-streaming-semantic-3d-reconstruction-cs.CV-2026-2025-2024-20260326113045.csv"
    )
    assert rows == [
        {
            "Name": "Newer",
            "Url": "https://arxiv.org/abs/2502.00002",
            "Github": "https://github.com/foo/new",
            "Stars": "20",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
        },
    ]


@pytest.mark.anyio
async def test_export_url_to_csv_writes_huggingface_results_in_output_dir(tmp_path: Path):
    payload = {
        "query": {"q": "semantic"},
        "searchResults": [
            {
                "paper": {"id": "2501.00001", "title": "Older"},
                "title": "Older",
            },
            {
                "paper": {"id": "2502.00002", "title": "Newer"},
                "title": "Newer",
            },
        ],
    }

    class FakeHuggingFacePapersClient:
        async def fetch_collection_html(self, url: str):
            return (
                '<div class="SVELTE_HYDRATER contents" '
                f'data-target="DailyPapers" data-props="{html.escape(json.dumps(payload))}"></div>'
            )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            mapping = {
                "https://arxiv.org/abs/2501.00001": "https://github.com/foo/old",
                "https://arxiv.org/abs/2502.00002": "https://github.com/foo/new",
            }
            return mapping[seed.url]

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            mapping = {
                ("foo", "old"): (10, None),
                ("foo", "new"): (20, None),
            }
            return mapping[(owner, repo)]

    result = await export_url_to_csv(
        "https://huggingface.co/papers/trending?q=semantic",
        output_dir=tmp_path,
        huggingface_papers_client=FakeHuggingFacePapersClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    with result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.csv_path == tmp_path / "huggingface-papers-trending-semantic-20260326113045.csv"
    assert rows == [
        {
            "Name": "Newer",
            "Url": "https://arxiv.org/abs/2502.00002",
            "Github": "https://github.com/foo/new",
            "Stars": "20",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
        },
    ]


@pytest.mark.anyio
async def test_export_url_to_csv_writes_arxiv_org_results_in_output_dir(tmp_path: Path):
    class FakeArxivOrgClient:
        async def fetch_page_html(self, url: str):
            return """
            <div class='paging'>Total of 2 entries : <span>1-2</span></div>
            <div class='morefewer'>Showing up to 25 entries per page:</div>
            <dl id="articles">
              <dt><a href="/abs/2501.00001">arXiv:2501.00001</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Older</div></dd>
              <dt><a href="/abs/2502.00002">arXiv:2502.00002</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Newer</div></dd>
            </dl>
            """

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            mapping = {
                "https://arxiv.org/abs/2501.00001": "https://github.com/foo/old",
                "https://arxiv.org/abs/2502.00002": "https://github.com/foo/new",
            }
            return mapping[seed.url]

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            mapping = {
                ("foo", "old"): (10, None),
                ("foo", "new"): (20, None),
            }
            return mapping[(owner, repo)]

    result = await export_url_to_csv(
        "https://arxiv.org/list/cs.CV/recent",
        output_dir=tmp_path,
        arxiv_org_client=FakeArxivOrgClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    with result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.csv_path == tmp_path / "arxiv-cs.CV-recent-20260326113045.csv"
    assert rows == [
        {
            "Name": "Newer",
            "Url": "https://arxiv.org/abs/2502.00002",
            "Github": "https://github.com/foo/new",
            "Stars": "20",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
        },
    ]


@pytest.mark.anyio
async def test_export_url_to_csv_writes_semanticscholar_results_in_output_dir(tmp_path: Path):
    class FakeSemanticScholarClient:
        async def fetch_search_page_html(self, url: str):
            return """
            <div class="cl-pager" data-total-pages="1" data-test-id="result-page-pagination"></div>
            <a data-test-id="title-link" href="/paper/Newer/def456">
              <h2 class="cl-paper-title">Newer</h2>
            </a>
            <a data-test-id="title-link" href="/paper/Older/abc123">
              <h2 class="cl-paper-title">Older</h2>
            </a>
            <a data-test-id="title-link" href="/paper/Missing/ghi789">
              <h2 class="cl-paper-title">Missing</h2>
            </a>
            """

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            mapping = {
                "Older": ("2501.00001", "title_search_exact", None),
                "Newer": ("2502.00002", "title_search_exact", None),
                "Missing": (None, None, "No arXiv ID found from title search"),
            }
            return mapping[title]

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            mapping = {
                "https://arxiv.org/abs/2501.00001": "https://github.com/foo/old",
                "https://arxiv.org/abs/2502.00002": "https://github.com/foo/new",
            }
            return mapping[seed.url]

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            mapping = {
                ("foo", "old"): (10, None),
                ("foo", "new"): (20, None),
            }
            return mapping[(owner, repo)]

    result = await export_url_to_csv(
        "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date",
        output_dir=tmp_path,
        semanticscholar_client=FakeSemanticScholarClient(),
        arxiv_client=FakeArxivClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    with result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.csv_path == tmp_path / "semanticscholar-semantic-3d-reconstruction-20260326113045.csv"
    assert rows == [
        {
            "Name": "Newer",
            "Url": "https://arxiv.org/abs/2502.00002",
            "Github": "https://github.com/foo/new",
            "Stars": "20",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
        },
    ]


@pytest.mark.anyio
async def test_run_url_mode_supports_arxiv_org_url(tmp_path: Path, capsys):
    input_url = "https://arxiv.org/list/cs.CV/recent"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSearchClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def search(self, query, page: int):
            raise AssertionError("arXiv Xplorer client should not be used for arXiv.org URLs")

    class FakeArxivOrgClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def fetch_page_html(self, url: str):
            return """
            <div class='paging'>Total of 1 entries : <span>1-1</span></div>
            <div class='morefewer'>Showing up to 25 entries per page:</div>
            <dl id="articles">
              <dt><a href="/abs/2501.00001">arXiv:2501.00001</a></dt>
              <dd><div class="list-title mathjax"><span class="descriptor">Title:</span> Paper A</div></dd>
            </dl>
            """

    class FakeHuggingFacePapersClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def fetch_collection_html(self, url: str):
            raise AssertionError("Hugging Face client should not be used for arXiv.org URLs")

    class FakeSemanticScholarClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def fetch_search_page_html(self, url: str):
            raise AssertionError("Semantic Scholar client should not be used for arXiv.org URLs")

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token
            self.alphaxiv_token = alphaxiv_token

        async def resolve_github_url(self, seed):
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        def __init__(self, session, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.github_token = github_token

        async def get_star_count(self, owner, repo):
            return 11, None

    exit_code = await run_url_mode(
        input_url,
        output_dir=tmp_path,
        session_factory=lambda **kwargs: FakeSession(),
        search_client_cls=FakeSearchClient,
        arxiv_org_client_cls=FakeArxivOrgClient,
        huggingface_papers_client_cls=FakeHuggingFacePapersClient,
        semanticscholar_client_cls=FakeSemanticScholarClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Fetching arXiv.org list page 1" in captured.out
    assert "Found 1 papers" in captured.out
    assert "[1/1] Paper A" in captured.out
    assert "foo/bar" in captured.out
    assert "Wrote CSV:" in captured.out


@pytest.mark.anyio
async def test_run_url_mode_prints_fetch_and_paper_progress(tmp_path: Path, capsys):
    input_url = "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSearchClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def search(self, query, page: int):
            data = {
                1: [
                    {"id": "2501.00001", "journal": "arxiv", "title": "Paper A"},
                ],
                2: [],
            }
            return data[page]

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token
            self.alphaxiv_token = alphaxiv_token

        async def resolve_github_url(self, seed):
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        def __init__(self, session, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.github_token = github_token

        async def get_star_count(self, owner, repo):
            return 11, None

    exit_code = await run_url_mode(
        input_url,
        output_dir=tmp_path,
        session_factory=lambda **kwargs: FakeSession(),
        search_client_cls=FakeSearchClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Fetching arXiv Xplorer page 1" in captured.out
    assert "Found 1 papers" in captured.out
    assert "Starting concurrent enrichment (10 workers)" in captured.out
    assert "[1/1] Paper A" in captured.out
    assert "foo/bar" in captured.out
    assert "Wrote CSV:" in captured.out


@pytest.mark.anyio
async def test_run_url_mode_supports_huggingface_papers_collection_url(tmp_path: Path, capsys):
    input_url = "https://huggingface.co/papers/trending?q=semantic"
    payload = {
        "query": {"q": "semantic"},
        "searchResults": [
            {
                "paper": {"id": "2501.00001", "title": "Paper A"},
                "title": "Paper A",
            }
        ],
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSearchClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def search(self, query, page: int):
            raise AssertionError("arXiv Xplorer client should not be used for Hugging Face URLs")

    class FakeHuggingFacePapersClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def fetch_collection_html(self, url: str):
            return (
                '<div class="SVELTE_HYDRATER contents" '
                f'data-target="DailyPapers" data-props="{html.escape(json.dumps(payload))}"></div>'
            )

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token
            self.alphaxiv_token = alphaxiv_token

        async def resolve_github_url(self, seed):
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        def __init__(self, session, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.github_token = github_token

        async def get_star_count(self, owner, repo):
            return 11, None

    exit_code = await run_url_mode(
        input_url,
        output_dir=tmp_path,
        session_factory=lambda **kwargs: FakeSession(),
        search_client_cls=FakeSearchClient,
        huggingface_papers_client_cls=FakeHuggingFacePapersClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Fetching Hugging Face Papers collection" in captured.out
    assert "Found 1 papers" in captured.out
    assert "[1/1] Paper A" in captured.out
    assert "foo/bar" in captured.out
    assert "Wrote CSV:" in captured.out


@pytest.mark.anyio
async def test_run_url_mode_supports_semanticscholar_url(tmp_path: Path, capsys):
    input_url = "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSearchClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def search(self, query, page: int):
            raise AssertionError("arXiv Xplorer client should not be used for Semantic Scholar URLs")

    class FakeHuggingFacePapersClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def fetch_collection_html(self, url: str):
            raise AssertionError("Hugging Face client should not be used for Semantic Scholar URLs")

    class FakeSemanticScholarClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def fetch_search_page_html(self, url: str):
            return """
            <div class="cl-pager" data-total-pages="1" data-test-id="result-page-pagination"></div>
            <a data-test-id="title-link" href="/paper/Paper-A/abc123">
              <h2 class="cl-paper-title">Paper A</h2>
            </a>
            """

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def get_arxiv_id_by_title(self, title: str):
            assert title == "Paper A"
            return "2501.00001", "title_search_exact", None

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token
            self.alphaxiv_token = alphaxiv_token

        async def resolve_github_url(self, seed):
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        def __init__(self, session, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.github_token = github_token

        async def get_star_count(self, owner, repo):
            return 11, None

    exit_code = await run_url_mode(
        input_url,
        output_dir=tmp_path,
        session_factory=lambda **kwargs: FakeSession(),
        search_client_cls=FakeSearchClient,
        huggingface_papers_client_cls=FakeHuggingFacePapersClient,
        semanticscholar_client_cls=FakeSemanticScholarClient,
        arxiv_client_cls=FakeArxivClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Fetching Semantic Scholar search results page 1" in captured.out
    assert "Normalizing to arXiv-backed papers" in captured.out
    assert "Found 1 papers" in captured.out
    assert "[1/1] Paper A" in captured.out
    assert "foo/bar" in captured.out
    assert "Wrote CSV:" in captured.out
