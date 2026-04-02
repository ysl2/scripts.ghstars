import asyncio
import csv
import html
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.record_model import Record
from src.shared.paper_content import PaperContentCache
from src.shared.settings import ABS_CACHE_SUBDIR, OVERVIEW_CACHE_SUBDIR
from src.shared.papers import ConversionResult, PaperSeed
from src.url_to_csv.arxivxplorer import TooManyPagesError, output_csv_path_for_arxivxplorer_url, parse_arxivxplorer_url
import src.url_to_csv.pipeline as url_pipeline
from src.url_to_csv.pipeline import fetch_paper_seeds_from_url, export_url_to_csv, normalize_paper_seeds_to_arxiv
from src.url_to_csv.models import FetchedSeedsResult
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
    class FakeSemanticScholarGraphClient:
        def __init__(self):
            self.search_calls = []

        async def fetch_search_bulk_page(self, params: dict[str, str]):
            self.search_calls.append(dict(params))
            return {
                "data": [
                    {
                        "paperId": "abc123",
                        "title": "Search Match",
                        "externalIds": {},
                        "url": "https://www.semanticscholar.org/paper/Search-Match/abc123",
                    },
                    {
                        "paperId": "def456",
                        "title": "Missing",
                        "externalIds": {},
                        "url": "https://www.semanticscholar.org/paper/Missing/def456",
                    },
                ]
            }

    class FakeArxivClient:
        def __init__(self):
            self.calls = []

        async def get_arxiv_id_by_title(self, title: str):
            self.calls.append(title)
            return None, None, "No arXiv ID found from title search"

    class FakeExactMatchGraphClient(FakeSemanticScholarGraphClient):
        async def find_arxiv_match_by_identifier(self, identifier: str, *, title=None, allow_title_fallback=True):
            if identifier == "https://www.semanticscholar.org/paper/Search-Match/abc123":
                assert title == "Search Match"
                assert allow_title_fallback is False
                return "https://arxiv.org/abs/2502.00002", "Search Match", "semantic_scholar_exact_source_url"
            if identifier == "https://www.semanticscholar.org/paper/Missing/def456":
                return None, None, None
            raise AssertionError(f"Unexpected identifier: {identifier}")

        async def find_arxiv_match_by_title(self, title: str):
            if title == "Missing":
                return None, None, None
            raise AssertionError(f"Unexpected title lookup: {title}")

    messages = []
    client = FakeExactMatchGraphClient()
    arxiv_client = FakeArxivClient()
    result = await fetch_paper_seeds_from_url(
        "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date",
        semanticscholar_graph_client=client,
        arxiv_client=arxiv_client,
        status_callback=messages.append,
    )

    assert [seed.name for seed in result.seeds] == ["Search Match"]
    assert [seed.url for seed in result.seeds] == ["https://arxiv.org/abs/2502.00002"]
    assert arxiv_client.calls == ["Missing"]
    assert client.search_calls == [
        {
            "query": "semantic 3d reconstruction",
            "sort": "publicationDate:desc",
            "fields": "paperId,title,externalIds,url",
        }
    ]
    assert any("Fetching Semantic Scholar bulk search batch 1" in message for message in messages)
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
        async def get_repo_metadata(self, owner, repo):
            mapping = {
                ("foo", "old"): (
                    SimpleNamespace(
                        stars=10,
                        created="2024-01-01T00:00:00Z",
                        about="old repo",
                    ),
                    None,
                ),
                ("foo", "new"): (
                    SimpleNamespace(
                        stars=20,
                        created="2025-02-02T00:00:00Z",
                        about="new repo",
                    ),
                    None,
                ),
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
            "Created": "2025-02-02T00:00:00Z",
            "About": "new repo",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
            "Created": "2024-01-01T00:00:00Z",
            "About": "old repo",
        },
    ]


@pytest.mark.anyio
async def test_export_url_to_csv_defaults_to_output_directory_and_creates_it(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class FakeSearchClient:
        async def search(self, query, page: int):
            data = {
                1: [
                    {"id": "2501.00001", "journal": "arxiv", "title": "Paper A"},
                ],
                2: [],
            }
            return data[page]

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            assert seed.url == "https://arxiv.org/abs/2501.00001"
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 11, None

    result = await export_url_to_csv(
        "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026",
        search_client=FakeSearchClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
    )

    assert result.csv_path.parent == Path("output")
    assert result.csv_path == (
        Path("output") / "arxivxplorer-streaming-semantic-3d-reconstruction-cs.CV-2026-20260326113045.csv"
    )
    assert (tmp_path / result.csv_path).exists()

    with (tmp_path / result.csv_path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/bar",
            "Stars": "11",
            "Created": "",
            "About": "",
        }
    ]


@pytest.mark.anyio
async def test_export_url_to_csv_adapts_paper_seeds_before_shared_export(monkeypatch, tmp_path: Path):
    adapter_calls = []
    exported = {}

    class FakeAdapter:
        def to_record(self, seed):
            adapter_calls.append((seed.name, seed.url))
            return Record.from_source(
                name=f"{seed.name} adapted",
                url=f"{seed.url}?adapted",
                source="paper_seed",
            )

    async def fake_fetch_paper_seeds_from_url(*args, **kwargs):
        return FetchedSeedsResult(
            seeds=[PaperSeed(name="Paper A", url="https://arxiv.org/abs/2501.00001")],
            csv_path=tmp_path / "papers.csv",
        )

    async def fake_export_paper_seeds_to_csv(seeds, csv_path, **kwargs):
        exported["seeds"] = seeds
        return ConversionResult(csv_path=csv_path, resolved=1, skipped=[])

    monkeypatch.setattr(url_pipeline, "PaperSeedInputAdapter", FakeAdapter)
    monkeypatch.setattr(url_pipeline, "fetch_paper_seeds_from_url", fake_fetch_paper_seeds_from_url)
    monkeypatch.setattr(url_pipeline, "export_paper_seeds_to_csv", fake_export_paper_seeds_to_csv)

    await export_url_to_csv(
        "https://arxivxplorer.com/?q=test&cats=cs.CV&year=2026",
        search_client=SimpleNamespace(),
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
    )

    assert adapter_calls == [("Paper A", "https://arxiv.org/abs/2501.00001")]
    assert exported["seeds"] == [
        PaperSeed(name="Paper A adapted", url="https://arxiv.org/abs/2501.00001?adapted")
    ]


@pytest.mark.anyio
async def test_normalize_paper_seeds_to_arxiv_limits_started_tasks_to_worker_count(monkeypatch):
    release = asyncio.Event()
    started: list[int] = []

    async def fake_normalize_seed_to_arxiv(seed, **kwargs):
        started.append(int(seed.name.split()[-1]))
        await release.wait()
        url = f"https://arxiv.org/abs/2501.0000{seed.name.split()[-1]}"
        return PaperSeed(name=seed.name, url=url), url

    monkeypatch.setattr(url_pipeline, "_normalize_seed_to_arxiv", fake_normalize_seed_to_arxiv)

    seeds = [PaperSeed(name=f"Paper {index}", url=f"https://example.com/{index}") for index in range(1, 6)]
    client = SimpleNamespace(semaphore=asyncio.Semaphore(2))
    normalize_task = asyncio.create_task(
        normalize_paper_seeds_to_arxiv(
            seeds,
            discovery_client=client,
            arxiv_client=client,
        )
    )

    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == [1, 2]
    finally:
        release.set()

    resolved = await normalize_task
    assert [seed.url for seed in resolved] == [
        "https://arxiv.org/abs/2501.00001",
        "https://arxiv.org/abs/2501.00002",
        "https://arxiv.org/abs/2501.00003",
        "https://arxiv.org/abs/2501.00004",
        "https://arxiv.org/abs/2501.00005",
    ]


@pytest.mark.anyio
async def test_normalize_paper_seeds_to_arxiv_preserves_existing_arxiv_urls_exactly():
    seeds = [PaperSeed(name="Paper A", url="https://arxiv.org/pdf/2501.00001v2.pdf")]

    resolved = await normalize_paper_seeds_to_arxiv(
        seeds,
        discovery_client=SimpleNamespace(huggingface_token=""),
        arxiv_client=SimpleNamespace(get_arxiv_id_by_title=AsyncMock()),
    )

    assert resolved == [PaperSeed(name="Paper A", url="https://arxiv.org/pdf/2501.00001v2.pdf")]


@pytest.mark.anyio
async def test_normalize_paper_seeds_to_arxiv_rewrites_doi_via_semantic_scholar_exact_lookup():
    seeds = [PaperSeed(name="Published Paper", url="https://doi.org/10.1007/978-3-031-72933-1_9")]
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title", "semantic_scholar_exact_doi")
        )
    )

    resolved = await normalize_paper_seeds_to_arxiv(
        seeds,
        discovery_client=SimpleNamespace(huggingface_token=""),
        arxiv_client=SimpleNamespace(get_arxiv_id_by_title=AsyncMock()),
        semanticscholar_graph_client=semanticscholar_graph_client,
    )

    assert resolved == [PaperSeed(name="Published Paper", url="https://arxiv.org/abs/2501.12345")]
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1007/978-3-031-72933-1_9",
        title="Published Paper",
        allow_title_fallback=False,
    )


@pytest.mark.anyio
async def test_normalize_paper_seeds_to_arxiv_uses_datacite_after_semantic_scholar_and_crossref_misses():
    seeds = [PaperSeed(name="Published Paper", url="https://doi.org/10.1145/example")]
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(return_value=(None, None, None)),
        find_arxiv_match_by_title=AsyncMock(return_value=(None, None, None)),
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=(None, None, "No arXiv ID found from title search"))
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(return_value=(None, "Published Paper")))
    datacite_client = SimpleNamespace(
        find_arxiv_match_by_doi=AsyncMock(return_value=("https://arxiv.org/abs/2501.54321", "Published Paper"))
    )

    resolved = await normalize_paper_seeds_to_arxiv(
        seeds,
        discovery_client=SimpleNamespace(huggingface_token=""),
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
    )

    assert resolved == [PaperSeed(name="Published Paper", url="https://arxiv.org/abs/2501.54321")]
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1145/example",
        title="Published Paper",
        allow_title_fallback=False,
    )
    semanticscholar_graph_client.find_arxiv_match_by_title.assert_awaited_once_with("Published Paper")
    crossref_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")
    datacite_client.find_arxiv_match_by_doi.assert_awaited_once_with("https://doi.org/10.1145/example")


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
            "Created": "",
            "About": "",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
            "Created": "",
            "About": "",
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
            "Created": "",
            "About": "",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
            "Created": "",
            "About": "",
        },
    ]


@pytest.mark.anyio
async def test_export_url_to_csv_writes_semanticscholar_results_in_output_dir(tmp_path: Path):
    class FakeSemanticScholarGraphClient:
        async def fetch_search_bulk_page(self, params: dict[str, str]):
            return {
                "data": [
                    {
                        "paperId": "def456",
                        "title": "Newer",
                        "externalIds": {"ArXiv": "2502.00002"},
                        "url": "https://www.semanticscholar.org/paper/Newer/def456",
                    },
                    {
                        "paperId": "abc123",
                        "title": "Older",
                        "externalIds": {},
                        "url": "https://www.semanticscholar.org/paper/Older/abc123",
                    },
                    {
                        "paperId": "ghi789",
                        "title": "Missing",
                        "externalIds": {},
                        "url": "https://www.semanticscholar.org/paper/Missing/ghi789",
                    },
                ]
            }

        async def find_arxiv_match_by_identifier(self, identifier: str, *, title=None, allow_title_fallback=True):
            if identifier == "https://www.semanticscholar.org/paper/Older/abc123":
                assert title == "Older"
                assert allow_title_fallback is False
                return "https://arxiv.org/abs/2501.00001", "Older", "semantic_scholar_exact_source_url"
            if identifier == "https://www.semanticscholar.org/paper/Missing/ghi789":
                return None, None, None
            raise AssertionError(f"Unexpected identifier: {identifier}")

        async def find_arxiv_match_by_title(self, title: str):
            if title == "Missing":
                return None, None, None
            raise AssertionError(f"Unexpected title lookup: {title}")

    class FakeArxivClient:
        def __init__(self):
            self.calls = []

        async def get_arxiv_id_by_title(self, title: str):
            self.calls.append(title)
            return None, None, "No arXiv ID found from title search"

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

    arxiv_client = FakeArxivClient()
    result = await export_url_to_csv(
        "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date",
        output_dir=tmp_path,
        semanticscholar_graph_client=FakeSemanticScholarGraphClient(),
        arxiv_client=arxiv_client,
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
            "Created": "",
            "About": "",
        },
        {
            "Name": "Older",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/old",
            "Stars": "10",
            "Created": "",
            "About": "",
        },
    ]
    assert arxiv_client.calls == ["Missing"]


@pytest.mark.anyio
async def test_export_url_to_csv_warms_content_and_reuses_cached_files(tmp_path: Path):
    class FakeSearchClient:
        async def search(self, query, page: int):
            data = {
                1: [
                    {"id": "2501.00001", "journal": "arxiv", "title": "Paper A"},
                ],
                2: [],
            }
            return data[page]

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            assert seed.url == "https://arxiv.org/abs/2501.00001"
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 11, None

    class FakeAlphaXivContentClient:
        def __init__(self):
            self.paper_calls: list[str] = []
            self.overview_calls: list[tuple[str, str]] = []

        async def get_paper_payload_by_arxiv_id(self, arxiv_id: str):
            self.paper_calls.append(arxiv_id)
            return (
                {
                    "title": "Paper A",
                    "abstract": "Paper A abstract",
                    "sourceUrl": "https://arxiv.org/abs/2501.00001",
                    "versionId": "v2501.00001",
                },
                None,
            )

        async def get_overview_payload_by_version_id(self, version_id: str, *, language: str = "en"):
            self.overview_calls.append((version_id, language))
            return ({"overview": "Paper A overview"}, None)

    content_client = FakeAlphaXivContentClient()
    content_cache = PaperContentCache(cache_root=tmp_path / "cache", content_client=content_client)

    first_result = await export_url_to_csv(
        "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026",
        output_dir=tmp_path,
        search_client=FakeSearchClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=content_cache,
    )
    second_result = await export_url_to_csv(
        "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026",
        output_dir=tmp_path,
        search_client=FakeSearchClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=content_cache,
    )

    with second_result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert first_result.csv_path == tmp_path / "arxivxplorer-streaming-semantic-3d-reconstruction-cs.CV-2026-20260326113045.csv"
    assert second_result.csv_path == first_result.csv_path
    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/bar",
            "Stars": "11",
            "Created": "",
            "About": "",
        }
    ]
    assert (tmp_path / "cache" / OVERVIEW_CACHE_SUBDIR / "2501.00001.md").exists()
    assert (tmp_path / "cache" / ABS_CACHE_SUBDIR / "2501.00001.md").exists()
    assert content_client.paper_calls == ["2501.00001"]
    assert content_client.overview_calls == [("v2501.00001", "en")]


@pytest.mark.anyio
async def test_run_url_mode_builds_and_passes_content_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPHAXIV_TOKEN", "ax_token")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "ss_key")
    monkeypatch.setenv("AIFORSCHOLAR_TOKEN", "relay_token")
    received = {}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSearchClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeArxivOrgClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeHuggingFacePapersClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeSemanticScholarGraphClient:
        def __init__(
            self,
            session,
            *,
            semantic_scholar_api_key="",
            aiforscholar_token="",
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session
            self.semantic_scholar_api_key = semantic_scholar_api_key
            self.aiforscholar_token = aiforscholar_token
            received["semanticscholar_graph_client"] = self

    class FakeCrossrefClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            received["crossref_client"] = self

    class FakeDataCiteClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            received["datacite_client"] = self

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeContentClient:
        def __init__(self, session, *, alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.alphaxiv_token = alphaxiv_token
            received["content_client"] = self

    async def fake_export(
        input_url: str,
        *,
        search_client=None,
        arxiv_org_client=None,
        huggingface_papers_client=None,
        semanticscholar_client=None,
        arxiv_client=None,
        semanticscholar_graph_client=None,
        crossref_client=None,
        datacite_client=None,
        discovery_client,
        github_client,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        output_dir=None,
        status_callback=None,
        progress_callback=None,
    ):
        received["input_url"] = input_url
        received["content_cache"] = content_cache
        received["semanticscholar_arg"] = semanticscholar_graph_client
        received["crossref_arg"] = crossref_client
        received["datacite_arg"] = datacite_client
        received["output_dir"] = output_dir
        return SimpleNamespace(csv_path=tmp_path / "papers.csv", resolved=1, skipped=[])

    monkeypatch.setattr("src.url_to_csv.runner.export_url_to_csv", fake_export)

    exit_code = await run_url_mode(
        "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026",
        output_dir=tmp_path,
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        search_client_cls=FakeSearchClient,
        arxiv_org_client_cls=FakeArxivOrgClient,
        huggingface_papers_client_cls=FakeHuggingFacePapersClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        semanticscholar_graph_client_cls=FakeSemanticScholarGraphClient,
        crossref_client_cls=FakeCrossrefClient,
        datacite_client_cls=FakeDataCiteClient,
        content_client_cls=FakeContentClient,
        content_cache_root=tmp_path / "cache",
    )

    assert exit_code == 0
    assert received["input_url"] == (
        "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026"
    )
    assert received["output_dir"] == tmp_path
    assert received["content_cache"] is not None
    assert received["content_cache"].cache_root == tmp_path / "cache"
    assert received["content_cache"].content_client is received["content_client"]
    assert received["content_client"].alphaxiv_token == "ax_token"
    assert received["semanticscholar_arg"] is received["semanticscholar_graph_client"]
    assert received["semanticscholar_graph_client"].semantic_scholar_api_key == "ss_key"
    assert received["semanticscholar_graph_client"].aiforscholar_token == "relay_token"
    assert received["crossref_arg"] is received["crossref_client"]
    assert received["datacite_arg"] is received["datacite_client"]


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

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token

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
        def __init__(self, session, *, huggingface_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token

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
        def __init__(self, session, *, huggingface_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token

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
async def test_run_url_mode_supports_semanticscholar_url_without_constructing_extra_search_client(
    tmp_path: Path,
    capsys,
):
    input_url = "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date"
    assert "semanticscholar_client_cls" not in run_url_mode.__kwdefaults__

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

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

        async def get_arxiv_id_by_title(self, title: str):
            raise AssertionError("Semantic Scholar graph exact lookup should resolve Semantic Scholar paper URLs")

    class FakeSemanticScholarGraphClient:
        def __init__(
            self,
            session,
            *,
            semantic_scholar_api_key="",
            aiforscholar_token="",
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session
            self.search_calls = []
            self.graph_url = "https://api.semanticscholar.org/graph/v1"

        async def _get_json(self, url: str, *, params=None):
            assert url == f"{self.graph_url}/paper/search/bulk"
            self.search_calls.append(dict(params or {}))
            return {
                "data": [
                    {
                        "paperId": "abc123",
                        "title": "Paper A",
                        "externalIds": {},
                        "url": "https://www.semanticscholar.org/paper/Paper-A/abc123",
                    }
                ]
            }

        async def find_arxiv_match_by_identifier(self, identifier: str, *, title=None, allow_title_fallback=True):
            assert identifier == "https://www.semanticscholar.org/paper/Paper-A/abc123"
            assert title == "Paper A"
            assert allow_title_fallback is False
            return "https://arxiv.org/abs/2501.00001", "Paper A", "semantic_scholar_exact_source_url"

        async def find_arxiv_match_by_title(self, title: str):
            raise AssertionError("Semantic Scholar title lookup should not run after exact source-url resolution")

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token

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
        arxiv_client_cls=FakeArxivClient,
        semanticscholar_graph_client_cls=FakeSemanticScholarGraphClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Fetching Semantic Scholar bulk search batch 1" in captured.out
    assert "Normalizing to arXiv-backed papers" in captured.out
    assert "Found 1 papers" in captured.out
    assert "[1/1] Paper A" in captured.out
    assert "foo/bar" in captured.out
    assert "Wrote CSV:" in captured.out
