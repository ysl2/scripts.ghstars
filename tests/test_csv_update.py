import asyncio
import csv
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.csv_update.pipeline as csv_update_pipeline
from src.csv_update.pipeline import CsvRowOutcome, build_csv_row_outcome, update_csv_file
from src.csv_update.runner import run_csv_mode
from src.shared.paper_content import PaperContentCache
from src.shared.paper_identity import extract_arxiv_id
from src.shared.papers import PaperRecord


class FakeContentCache:
    def __init__(self):
        self.calls: list[str] = []

    async def ensure_local_content_cache(self, url: str) -> None:
        self.calls.append(url)
        arxiv_id = extract_arxiv_id(url)
        if not arxiv_id:
            return None
        return None


@pytest.mark.anyio
async def test_update_csv_file_updates_rows_in_place_preserving_columns_and_order(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Notes,Github,Stars,Tag",
                "Keep Github,https://arxiv.org/abs/2603.20000v2,note-1,https://github.com/foo/existing,1,A",
                "Discover Github,https://arxiv.org/pdf/2603.10000v1.pdf,note-2,,,B",
                "Invalid Url,https://example.com/not-arxiv,note-3,,9,C",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        def __init__(self):
            self.urls = []

        async def resolve_github_url(self, seed):
            self.urls.append(seed.url)
            if seed.url.endswith("2603.10000"):
                return "https://github.com/foo/discovered"
            raise AssertionError(f"unexpected discovery lookup for {seed.url}")

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            mapping = {
                ("foo", "existing"): (99, None),
                ("foo", "discovered"): (42, None),
            }
            return mapping[(owner, repo)]

    content_cache = FakeContentCache()
    discovery_client = FakeDiscoveryClient()
    result = await update_csv_file(
        csv_path,
        discovery_client=discovery_client,
        github_client=FakeGitHubClient(),
        content_cache=content_cache,
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert result.csv_path == csv_path
    assert result.updated == 2
    assert len(result.skipped) == 1
    assert result.skipped[0]["title"] == "Invalid Url"
    assert result.skipped[0]["reason"] == "No valid arXiv URL found"
    assert reader.fieldnames == ["Name", "Url", "Notes", "Github", "Stars", "Tag"]
    assert discovery_client.urls == ["https://arxiv.org/abs/2603.10000"]
    assert rows == [
        {
            "Name": "Keep Github",
            "Url": "https://arxiv.org/abs/2603.20000v2",
            "Notes": "note-1",
            "Github": "https://github.com/foo/existing",
            "Stars": "99",
            "Tag": "A",
        },
        {
            "Name": "Discover Github",
            "Url": "https://arxiv.org/pdf/2603.10000v1.pdf",
            "Notes": "note-2",
            "Github": "https://github.com/foo/discovered",
            "Stars": "42",
            "Tag": "B",
        },
        {
            "Name": "Invalid Url",
            "Url": "https://example.com/not-arxiv",
            "Notes": "note-3",
            "Github": "",
            "Stars": "9",
            "Tag": "C",
        },
    ]
    assert content_cache.calls == [
        "https://arxiv.org/abs/2603.20000",
        "https://arxiv.org/abs/2603.10000",
    ]


@pytest.mark.anyio
async def test_update_csv_file_rewrites_doi_to_arxiv_when_openalex_crosswalk_resolves_it(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Github,Stars",
                "DOI Paper,https://doi.org/10.1007/978-3-031-72933-1_9,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title")
        )
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            assert seed.url == "https://arxiv.org/abs/2501.12345"
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 7, None

    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        openalex_client=openalex_client,
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.updated == 1
    assert rows == [
        {
            "Name": "DOI Paper",
            "Url": "https://arxiv.org/abs/2501.12345",
            "Github": "https://github.com/foo/bar",
            "Stars": "7",
        }
    ]


@pytest.mark.anyio
async def test_update_csv_file_uses_arxiv_html_title_search_after_openalex_exact_miss(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Github,Stars",
                "DOI Paper,https://doi.org/10.1145/example,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(return_value=(None, "DOI Paper"))
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=("2501.54321", "title_search_exact", None)),
        get_arxiv_match_by_title_from_api=AsyncMock(
            return_value=("2999.99999", "Wrong API Match", "title_search_exact", None)
        ),
    )
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            assert seed.url == "https://arxiv.org/abs/2501.54321"
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 7, None

    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.updated == 1
    assert rows == [
        {
            "Name": "DOI Paper",
            "Url": "https://arxiv.org/abs/2501.54321",
            "Github": "https://github.com/foo/bar",
            "Stars": "7",
        }
    ]
    arxiv_client.get_arxiv_id_by_title.assert_awaited_once_with("DOI Paper")
    arxiv_client.get_arxiv_match_by_title_from_api.assert_not_awaited()
    crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()


@pytest.mark.anyio
async def test_run_csv_mode_builds_and_passes_metadata_clients(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPHAXIV_TOKEN", "ax_token")
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text("Name,Url,Github,Stars\nPaper A,https://arxiv.org/abs/2603.20000v2,,\n", encoding="utf-8")
    received = {}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeOpenAlexClient:
        def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            received["arxiv_client"] = self

    class FakeCrossrefClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            received["crossref_client"] = self

    class FakeDataCiteClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            received["datacite_client"] = self

    class FakeContentClient:
        def __init__(self, session, *, alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session

    async def fake_update_csv_file(
        path,
        *,
        discovery_client,
        github_client,
        arxiv_client=None,
        openalex_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        received["arxiv_arg"] = arxiv_client
        received["crossref_arg"] = crossref_client
        received["datacite_arg"] = datacite_client
        return SimpleNamespace(csv_path=path, updated=0, skipped=[])

    monkeypatch.setattr("src.csv_update.runner.update_csv_file", fake_update_csv_file)

    exit_code = await run_csv_mode(
        csv_path,
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        openalex_client_cls=FakeOpenAlexClient,
        crossref_client_cls=FakeCrossrefClient,
        datacite_client_cls=FakeDataCiteClient,
        content_client_cls=FakeContentClient,
        content_cache_root=tmp_path / "cache",
    )

    assert exit_code == 0
    assert received["arxiv_arg"] is received["arxiv_client"]
    assert received["crossref_arg"] is received["crossref_client"]
    assert received["datacite_arg"] is received["datacite_client"]


@pytest.mark.anyio
async def test_update_csv_file_appends_missing_github_and_stars_columns_at_the_end(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Notes",
                "Paper A,https://arxiv.org/abs/2603.30000v1,note-a",
                "Paper B,,note-b",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            assert seed.url == "https://arxiv.org/abs/2603.30000"
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 7, None

    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert result.updated == 1
    assert len(result.skipped) == 1
    assert result.skipped[0]["title"] == "Paper B"
    assert result.skipped[0]["reason"] == "No valid arXiv URL found"
    assert reader.fieldnames == ["Name", "Url", "Notes", "Github", "Stars"]
    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2603.30000v1",
            "Notes": "note-a",
            "Github": "https://github.com/foo/bar",
            "Stars": "7",
        },
        {
            "Name": "Paper B",
            "Url": "",
            "Notes": "note-b",
            "Github": "",
            "Stars": "",
        },
    ]


@pytest.mark.anyio
async def test_update_csv_file_requires_url_column(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Github,Stars",
                "Paper A,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            raise AssertionError("discovery should not run when Url column is missing")

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            raise AssertionError("GitHub should not run when Url column is missing")

    with pytest.raises(ValueError, match="CSV file must include Url column"):
        await update_csv_file(
            csv_path,
            discovery_client=FakeDiscoveryClient(),
            github_client=FakeGitHubClient(),
            content_cache=FakeContentCache(),
        )


@pytest.mark.anyio
async def test_update_csv_file_allows_missing_name_column_when_url_exists(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Url,Notes",
                "https://arxiv.org/abs/2603.30000v1,note-a",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            assert seed.name == "Row 1"
            assert seed.url == "https://arxiv.org/abs/2603.30000"
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 7, None

    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert result.updated == 1
    assert rows == [
        {
            "Url": "https://arxiv.org/abs/2603.30000v1",
            "Notes": "note-a",
            "Github": "https://github.com/foo/bar",
            "Stars": "7",
        }
    ]
    assert reader.fieldnames == ["Url", "Notes", "Github", "Stars"]


@pytest.mark.anyio
async def test_update_csv_file_fills_blank_stars_for_existing_github_without_adding_content_columns(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Github,Stars",
                "Paper A,https://arxiv.org/abs/2603.20000v2,https://github.com/foo/bar,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            raise AssertionError("discovery should not run when Github already exists")

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 11, None

    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.updated == 1
    assert result.skipped == []
    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2603.20000v2",
            "Github": "https://github.com/foo/bar",
            "Stars": "11",
        }
    ]


@pytest.mark.anyio
async def test_update_csv_file_skips_content_updates_when_github_discovery_misses(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Github,Stars",
                "Paper A,https://arxiv.org/abs/2603.20000v2,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            return None

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            raise AssertionError("GitHub should not run when discovery finds no repo")

    content_cache = FakeContentCache()
    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=content_cache,
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.updated == 0
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "No Github URL found from discovery"
    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2603.20000v2",
            "Github": "",
            "Stars": "",
        }
    ]
    assert content_cache.calls == []


@pytest.mark.anyio
async def test_update_csv_file_limits_started_tasks_to_worker_count(tmp_path: Path, monkeypatch):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url",
                "Paper 1,https://arxiv.org/abs/2501.00001",
                "Paper 2,https://arxiv.org/abs/2501.00002",
                "Paper 3,https://arxiv.org/abs/2501.00003",
                "Paper 4,https://arxiv.org/abs/2501.00004",
                "Paper 5,https://arxiv.org/abs/2501.00005",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    release = asyncio.Event()
    started: list[int] = []

    async def fake_build_csv_row_outcome(index, row, **kwargs):
        started.append(index)
        await release.wait()
        return (
            index - 1,
            dict(row),
            CsvRowOutcome(
                index=index,
                record=PaperRecord(
                    name=row["Name"],
                    url=row["Url"],
                    github="",
                    stars="",
                ),
                current_stars=None,
                reason=None,
                source_label=None,
                github_url_set=None,
            ),
        )

    monkeypatch.setattr(csv_update_pipeline, "build_csv_row_outcome", fake_build_csv_row_outcome)

    client = SimpleNamespace(semaphore=asyncio.Semaphore(2))
    update_task = asyncio.create_task(
        update_csv_file(
            csv_path,
            discovery_client=client,
            github_client=client,
            content_cache=FakeContentCache(),
        )
    )

    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == [1, 2]
    finally:
        release.set()

    result = await update_task
    assert result.updated == 5


@pytest.mark.anyio
async def test_build_csv_row_outcome_resolves_repo_then_warms_content_then_fetches_stars(tmp_path: Path):
    events: list[str] = []

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            events.append("discovery")
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            events.append("stars")
            return 5, None

    class OrderedContentCache:
        async def ensure_local_content_cache(self, url: str) -> None:
            events.append("content")

    _, updated_row, outcome = await build_csv_row_outcome(
        1,
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2603.30000v1",
            "Github": "",
            "Stars": "",
        },
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=OrderedContentCache(),
        csv_dir=tmp_path,
    )

    assert outcome.reason is None
    assert events == ["discovery", "content", "stars"]
    assert "Overview" not in updated_row
    assert "Abs" not in updated_row


@pytest.mark.anyio
async def test_update_csv_file_leaves_preexisting_overview_and_abs_columns_unchanged(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Overview,Abs",
                "Paper A,https://arxiv.org/abs/2603.30000v1,old-overview.md,old-abs.md",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            return 7, None

    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert result.updated == 1
    assert reader.fieldnames == ["Name", "Url", "Overview", "Abs", "Github", "Stars"]
    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2603.30000v1",
            "Overview": "old-overview.md",
            "Abs": "old-abs.md",
            "Github": "https://github.com/foo/bar",
            "Stars": "7",
        }
    ]


@pytest.mark.anyio
async def test_paper_content_cache_writes_files_once_and_returns_paths_relative_to_csv_directory(tmp_path: Path):
    cache_root = tmp_path / "cache"
    csv_dir = tmp_path / "output"
    csv_dir.mkdir()

    class FakeAlphaXivContentClient:
        def __init__(self):
            self.paper_calls = []
            self.overview_calls = []

        async def get_paper_payload_by_arxiv_id(self, arxiv_id: str):
            self.paper_calls.append(arxiv_id)
            return {
                "title": "Paper 2603.30000",
                "abstract": "Abstract for 2603.30000",
                "versionId": "version-2603.30000",
            }, None

        async def get_overview_payload_by_version_id(self, version_id: str, *, language: str = "en"):
            self.overview_calls.append((version_id, language))
            return {"overview": "## Overview\n\nOverview for 2603.30000"}, None

    client = FakeAlphaXivContentClient()
    content_cache = PaperContentCache(cache_root=cache_root, content_client=client)

    overview_path = await content_cache.ensure_overview_path(
        "https://arxiv.org/pdf/2603.30000v1.pdf",
        relative_to=csv_dir,
    )
    abs_path = await content_cache.ensure_abs_path(
        "https://arxiv.org/abs/2603.30000v2",
        relative_to=csv_dir,
    )
    overview_path_repeat = await content_cache.ensure_overview_path(
        "https://arxiv.org/abs/2603.30000",
        relative_to=csv_dir,
    )
    abs_path_repeat = await content_cache.ensure_abs_path(
        "https://arxiv.org/abs/2603.30000",
        relative_to=csv_dir,
    )

    assert overview_path == "../cache/overview/2603.30000.md"
    assert abs_path == "../cache/abs/2603.30000.md"
    assert overview_path_repeat == overview_path
    assert abs_path_repeat == abs_path
    assert client.paper_calls == ["2603.30000"]
    assert client.overview_calls == [("version-2603.30000", "en")]

    overview_file = cache_root / "overview" / "2603.30000.md"
    abs_file = cache_root / "abs" / "2603.30000.md"
    assert overview_file.read_text(encoding="utf-8").find("Overview for 2603.30000") != -1
    assert abs_file.read_text(encoding="utf-8").find("Abstract for 2603.30000") != -1


@pytest.mark.anyio
async def test_run_csv_mode_prints_progress_updates_file_and_writes_cached_markdown(
    tmp_path: Path, capsys, monkeypatch
):
    monkeypatch.setenv("ALPHAXIV_TOKEN", "ax_token")
    received = {}
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Github,Stars",
                "Paper A,https://arxiv.org/abs/2603.20000v2,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

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

    class FakeAlphaXivContentClient:
        def __init__(self, session, *, alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.alphaxiv_token = alphaxiv_token
            received["content_client"] = self

        async def get_paper_payload_by_arxiv_id(self, arxiv_id: str):
            assert arxiv_id == "2603.20000"
            return {
                "title": "Paper A",
                "abstract": "Abstract body",
                "versionId": "version-2603.20000",
            }, None

        async def get_overview_payload_by_version_id(self, version_id: str, *, language: str = "en"):
            assert (version_id, language) == ("version-2603.20000", "en")
            return {"overview": "## Overview\n\nOverview body"}, None

    exit_code = await run_csv_mode(
        csv_path,
        session_factory=lambda **kwargs: FakeSession(),
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        content_client_cls=FakeAlphaXivContentClient,
        content_cache_root=tmp_path / "cache",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Found 1 rows" in captured.out
    assert "[1/1] Paper A" in captured.out
    assert "foo/bar" in captured.out
    assert "Updated: N/A → 11" in captured.out
    assert "Updated: 1" in captured.out

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2603.20000v2",
            "Github": "https://github.com/foo/bar",
            "Stars": "11",
        }
    ]
    assert received["content_client"].alphaxiv_token == "ax_token"
    assert (tmp_path / "cache" / "overview" / "2603.20000.md").read_text(encoding="utf-8").find("Overview body") != -1
    assert (tmp_path / "cache" / "abs" / "2603.20000.md").read_text(encoding="utf-8").find("Abstract body") != -1
