import asyncio
import csv
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.csv_update.pipeline as csv_update_pipeline
from src.core.record_model import PropertyState, Record, RecordContext
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
                "Name,Url,Notes,Github,Stars,Created,About,Tag",
                "Keep Github,https://arxiv.org/abs/2603.20000v2,note-1,https://github.com/foo/existing,1,2024-03-01T00:00:00Z,keep repo,A",
                "Discover Github,https://arxiv.org/pdf/2603.10000v1.pdf,note-2,,,2024-02-01T00:00:00Z,discover repo,B",
                "Invalid Url,https://example.com/not-arxiv,note-3,,9,2024-01-01T00:00:00Z,bad row,C",
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
        async def get_repo_metadata(self, owner, repo):
            mapping = {
                ("foo", "existing"): (
                    SimpleNamespace(stars=99, created="2020-01-01T00:00:00Z", about="remote existing"),
                    None,
                ),
                ("foo", "discovered"): (
                    SimpleNamespace(stars=42, created="2019-01-01T00:00:00Z", about="remote discovered"),
                    None,
                ),
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
    assert reader.fieldnames == ["Name", "Url", "Notes", "Github", "Stars", "Created", "About", "Tag"]
    assert discovery_client.urls == ["https://arxiv.org/abs/2603.10000"]
    assert rows == [
        {
            "Name": "Keep Github",
            "Url": "https://arxiv.org/abs/2603.20000v2",
            "Notes": "note-1",
            "Github": "https://github.com/foo/existing",
            "Stars": "99",
            "Created": "2024-03-01T00:00:00Z",
            "About": "remote existing",
            "Tag": "A",
        },
        {
            "Name": "Discover Github",
            "Url": "https://arxiv.org/pdf/2603.10000v1.pdf",
            "Notes": "note-2",
            "Github": "https://github.com/foo/discovered",
            "Stars": "42",
            "Created": "2024-02-01T00:00:00Z",
            "About": "remote discovered",
            "Tag": "B",
        },
        {
            "Name": "Invalid Url",
            "Url": "https://example.com/not-arxiv",
            "Notes": "note-3",
            "Github": "",
            "Stars": "9",
            "Created": "2024-01-01T00:00:00Z",
            "About": "bad row",
            "Tag": "C",
        },
    ]
    assert content_cache.calls == ["https://arxiv.org/abs/2603.10000"]


@pytest.mark.anyio
async def test_update_csv_file_rewrites_doi_to_arxiv_when_semantic_scholar_exact_lookup_resolves_it(tmp_path: Path):
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

    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title", "semantic_scholar_exact_doi")
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
        semanticscholar_graph_client=semanticscholar_graph_client,
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
            "Created": "",
            "About": "",
        }
    ]
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1007/978-3-031-72933-1_9",
        title="DOI Paper",
        allow_title_fallback=False,
    )


@pytest.mark.anyio
async def test_update_csv_file_uses_arxiv_html_title_search_after_semantic_scholar_misses(tmp_path: Path):
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

    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(return_value=(None, None, None)),
        find_arxiv_match_by_title=AsyncMock(return_value=(None, None, None)),
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
        semanticscholar_graph_client=semanticscholar_graph_client,
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
            "Created": "",
            "About": "",
        }
    ]
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_awaited_once_with(
        "https://doi.org/10.1145/example",
        title="DOI Paper",
        allow_title_fallback=False,
    )
    semanticscholar_graph_client.find_arxiv_match_by_title.assert_awaited_once_with("DOI Paper")
    arxiv_client.get_arxiv_id_by_title.assert_awaited_once_with("DOI Paper")
    arxiv_client.get_arxiv_match_by_title_from_api.assert_not_awaited()
    crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()


@pytest.mark.anyio
async def test_run_csv_mode_builds_and_passes_metadata_clients(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPHAXIV_TOKEN", "ax_token")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "ss_key")
    monkeypatch.setenv("AIFORSCHOLAR_TOKEN", "relay_token")
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
        semanticscholar_graph_client=None,
        crossref_client=None,
        datacite_client=None,
        content_cache=None,
        relation_resolution_cache=None,
        arxiv_relation_no_arxiv_recheck_days=30,
        status_callback=None,
        progress_callback=None,
    ):
        received["arxiv_arg"] = arxiv_client
        received["semanticscholar_arg"] = semanticscholar_graph_client
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
        semanticscholar_graph_client_cls=FakeSemanticScholarGraphClient,
        crossref_client_cls=FakeCrossrefClient,
        datacite_client_cls=FakeDataCiteClient,
        content_client_cls=FakeContentClient,
        content_cache_root=tmp_path / "cache",
    )

    assert exit_code == 0
    assert received["arxiv_arg"] is received["arxiv_client"]
    assert received["semanticscholar_arg"] is received["semanticscholar_graph_client"]
    assert received["semanticscholar_graph_client"].semantic_scholar_api_key == "ss_key"
    assert received["semanticscholar_graph_client"].aiforscholar_token == "relay_token"
    assert received["crossref_arg"] is received["crossref_client"]
    assert received["datacite_arg"] is received["datacite_client"]


@pytest.mark.anyio
async def test_run_csv_mode_reports_rows_without_inputs_as_preserved_skips(tmp_path: Path, monkeypatch, capsys):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text("Name,Github,Stars\nPaper A,,\n", encoding="utf-8")

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

    class FakeCrossrefClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDataCiteClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeContentClient:
        def __init__(self, session, *, alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session

    async def fake_update_csv_file(*args, **kwargs):
        return SimpleNamespace(
            csv_path=csv_path,
            updated=0,
            skipped=[
                {
                    "title": "Paper A",
                    "github_url": None,
                    "detail_url": "",
                    "reason": "Row has neither Github nor Url",
                }
            ],
        )

    monkeypatch.setattr("src.csv_update.runner.update_csv_file", fake_update_csv_file)

    exit_code = await run_csv_mode(
        csv_path,
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
        semanticscholar_graph_client_cls=FakeSemanticScholarGraphClient,
        crossref_client_cls=FakeCrossrefClient,
        datacite_client_cls=FakeDataCiteClient,
        content_client_cls=FakeContentClient,
        content_cache_root=tmp_path / "cache",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Skipped rows (CSV rows preserved):" in captured.out
    assert "Failed rows (need attention):" not in captured.out
    assert "Row has neither Github nor Url" in captured.out


@pytest.mark.anyio
async def test_update_csv_file_appends_missing_repo_metadata_columns_without_reordering_existing_columns(
    tmp_path: Path,
):
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
        async def get_repo_metadata(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return SimpleNamespace(stars=7, created="2024-01-01T00:00:00Z", about="repo"), None

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
    assert result.skipped[0]["reason"] == "Row has neither Github nor Url"
    assert reader.fieldnames == ["Name", "Url", "Notes", "Github", "Stars", "Created", "About"]
    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2603.30000v1",
            "Notes": "note-a",
            "Github": "https://github.com/foo/bar",
            "Stars": "7",
            "Created": "2024-01-01T00:00:00Z",
            "About": "repo",
        },
        {
            "Name": "Paper B",
            "Url": "",
            "Notes": "note-b",
            "Github": "",
            "Stars": "",
            "Created": "",
            "About": "",
        },
    ]


@pytest.mark.anyio
async def test_update_csv_file_skips_row_without_github_or_url(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Github,Stars,Created,About",
                "Paper A,,,2024-01-01T00:00:00Z,row about",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    discovery_client = SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = SimpleNamespace(get_star_count=AsyncMock())

    result = await update_csv_file(
        csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert result.updated == 0
    assert result.skipped == [
        {
            "title": "Paper A",
            "github_url": None,
            "detail_url": "",
            "reason": "Row has neither Github nor Url",
        }
    ]
    assert reader.fieldnames == ["Name", "Github", "Stars", "Created", "About"]
    assert rows == [
        {
            "Name": "Paper A",
            "Github": "",
            "Stars": "",
            "Created": "2024-01-01T00:00:00Z",
            "About": "row about",
        }
    ]
    discovery_client.resolve_github_url.assert_not_awaited()
    github_client.get_star_count.assert_not_awaited()


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
        async def get_repo_metadata(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return SimpleNamespace(stars=7, created="2024-01-01T00:00:00Z", about="repo"), None

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
            "Created": "2024-01-01T00:00:00Z",
            "About": "repo",
        }
    ]
    assert reader.fieldnames == ["Url", "Notes", "Github", "Stars", "Created", "About"]


@pytest.mark.anyio
async def test_update_csv_file_refreshes_repo_metadata_without_url_when_github_exists(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Github,Stars,Created,About",
                "Paper A,https://github.com/foo/bar,,2024-01-01T00:00:00Z,old about",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            raise AssertionError("discovery should not run when Github already exists")

    class FakeGitHubClient:
        async def get_repo_metadata(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return SimpleNamespace(stars=11, created="2020-12-31T00:00:00Z", about=""), None

    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            side_effect=AssertionError("identifier lookup should not run when Github already exists")
        ),
        find_arxiv_match_by_title=AsyncMock(
            side_effect=AssertionError("title search should not run when Github already exists")
        ),
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(
            side_effect=AssertionError("arXiv title search should not run when Github already exists")
        ),
        get_arxiv_match_by_title_from_api=AsyncMock(
            side_effect=AssertionError("arXiv API title search should not run when Github already exists")
        ),
    )

    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert result.updated == 1
    assert result.skipped == []
    assert rows == [
        {
            "Name": "Paper A",
            "Github": "https://github.com/foo/bar",
            "Stars": "11",
            "Created": "2024-01-01T00:00:00Z",
            "About": "",
        }
    ]
    assert reader.fieldnames == ["Name", "Github", "Stars", "Created", "About"]


@pytest.mark.anyio
async def test_update_csv_file_marks_repo_metadata_failure_as_skipped_when_row_is_fully_populated(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Github,Stars,Created,About",
                "Paper A,https://github.com/foo/bar,11,2024-01-01T00:00:00Z,old about",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    discovery_client = SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = SimpleNamespace(
        get_repo_metadata=AsyncMock(return_value=(None, "GitHub API error (500)"))
    )

    result = await update_csv_file(
        csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.updated == 0
    assert result.skipped == [
        {
            "title": "Paper A",
            "github_url": "https://github.com/foo/bar",
            "detail_url": "",
            "reason": "GitHub API error (500)",
        }
    ]
    assert rows == [
        {
            "Name": "Paper A",
            "Github": "https://github.com/foo/bar",
            "Stars": "11",
            "Created": "2024-01-01T00:00:00Z",
            "About": "old about",
        }
    ]
    discovery_client.resolve_github_url.assert_not_awaited()
    github_client.get_repo_metadata.assert_awaited_once_with("foo", "bar")


@pytest.mark.anyio
async def test_update_csv_file_clears_about_when_remote_description_is_null(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Github,Stars,Created,About",
                "Paper A,https://github.com/foo/bar,11,2024-01-01T00:00:00Z,old about",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = await update_csv_file(
        csv_path,
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=SimpleNamespace(
            get_repo_metadata=AsyncMock(
                return_value=(
                    SimpleNamespace(
                        stars=12,
                        created="2020-12-31T00:00:00Z",
                        about=None,
                    ),
                    None,
                )
            )
        ),
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.updated == 1
    assert result.skipped == []
    assert rows == [
        {
            "Name": "Paper A",
            "Github": "https://github.com/foo/bar",
            "Stars": "12",
            "Created": "2024-01-01T00:00:00Z",
            "About": "",
        }
    ]


@pytest.mark.anyio
async def test_update_csv_file_does_not_use_or_rewrite_url_when_github_exists(tmp_path: Path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Name,Url,Github,Stars,Created,About",
                "Paper A,https://doi.org/10.1000/example,https://github.com/foo/bar,2,,repo",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    discovery_client = SimpleNamespace(resolve_github_url=AsyncMock())
    github_client = SimpleNamespace(
        get_repo_metadata=AsyncMock(
            return_value=(SimpleNamespace(stars=13, created="2024-02-02T00:00:00Z", about="remote repo"), None)
        )
    )
    semanticscholar_graph_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title", "semantic_scholar_exact_doi")
        ),
        find_arxiv_match_by_title=AsyncMock(return_value=(None, None, None)),
    )
    arxiv_client = SimpleNamespace(
        get_arxiv_id_by_title=AsyncMock(return_value=("2501.54321", "title_search_exact", None)),
        get_arxiv_match_by_title_from_api=AsyncMock(return_value=("2999.99999", "Wrong API Match", "title_search_exact", None)),
    )

    result = await update_csv_file(
        csv_path,
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert result.updated == 1
    assert result.skipped == []
    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://doi.org/10.1000/example",
            "Github": "https://github.com/foo/bar",
            "Stars": "13",
            "Created": "2024-02-02T00:00:00Z",
            "About": "remote repo",
        }
    ]
    discovery_client.resolve_github_url.assert_not_awaited()
    semanticscholar_graph_client.find_arxiv_match_by_identifier.assert_not_awaited()
    semanticscholar_graph_client.find_arxiv_match_by_title.assert_not_awaited()
    arxiv_client.get_arxiv_id_by_title.assert_not_awaited()
    arxiv_client.get_arxiv_match_by_title_from_api.assert_not_awaited()
    github_client.get_repo_metadata.assert_awaited_once_with("foo", "bar")


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
            "Created": "",
            "About": "",
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
async def test_build_csv_row_outcome_uses_csv_row_input_adapter(monkeypatch, tmp_path: Path):
    adapter_calls = []
    sync_calls = []
    update_adapter_calls = []
    init_kwargs = {}

    class FakeAdapter:
        def to_record(self, index, row):
            adapter_calls.append((index, dict(row)))
            return Record.from_source(
                name="Adapted Title",
                url="https://arxiv.org/abs/2603.12345",
                source="csv",
            ).with_supporting_state(context=RecordContext(csv_row_index=index))

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            init_kwargs.update(kwargs)

        async def sync(self, record, **kwargs):
            before_repo_metadata = kwargs.get("before_repo_metadata")
            sync_calls.append((record.name.value, record.url.value, kwargs))
            synced_record = (
                record.with_property(
                    "github",
                    PropertyState.resolved("https://github.com/foo/bar", source="github_api"),
                )
                .with_property(
                    "stars",
                    PropertyState.resolved(7, source="github_api"),
                )
                .with_property(
                    "created",
                    PropertyState.resolved("2024-01-01T00:00:00Z", source="github_api"),
                )
                .with_property(
                    "about",
                    PropertyState.resolved("repo", source="github_api"),
                )
                .with_supporting_state(
                    facts=replace(
                        record.facts,
                        normalized_url="https://arxiv.org/abs/2603.12345",
                        canonical_arxiv_url="https://arxiv.org/abs/2603.12345",
                        github_source="discovered",
                    )
                )
            )
            if callable(before_repo_metadata):
                await before_repo_metadata(synced_record)
            return synced_record

    class FakeCsvUpdateAdapter:
        def apply(self, row, record):
            update_adapter_calls.append((dict(row), record.github.value, record.stars.value))
            updated = dict(row)
            updated["Url"] = "https://arxiv.org/abs/2603.12345"
            updated["Github"] = "https://github.com/foo/bar"
            updated["Stars"] = "7"
            updated["Created"] = "2024-01-01T00:00:00Z"
            updated["About"] = "repo"
            return updated

    monkeypatch.setattr(csv_update_pipeline, "CsvRowInputAdapter", FakeAdapter)
    monkeypatch.setattr(csv_update_pipeline, "RecordSyncService", FakeRecordSyncService, raising=False)
    monkeypatch.setattr(csv_update_pipeline, "CsvUpdateAdapter", FakeCsvUpdateAdapter, raising=False)

    content_cache = FakeContentCache()

    _, updated_row, outcome = await build_csv_row_outcome(
        3,
        {
            "Name": "Original Title",
            "Url": "https://example.com/ignored",
            "Github": "",
            "Stars": "",
        },
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
        content_cache=content_cache,
        csv_dir=tmp_path,
    )

    assert adapter_calls == [
        (
            3,
            {
                "Name": "Original Title",
                "Url": "https://example.com/ignored",
                "Github": "",
                "Stars": "",
            },
        )
    ]
    assert len(sync_calls) == 1
    assert init_kwargs["discovery_client"] is not None
    assert init_kwargs["github_client"] is not None
    assert sync_calls[0][0] == "Adapted Title"
    assert sync_calls[0][1] == "https://arxiv.org/abs/2603.12345"
    assert sync_calls[0][2]["allow_title_search"] is True
    assert sync_calls[0][2]["allow_github_discovery"] is True
    assert sync_calls[0][2]["trust_existing_github"] is False
    assert callable(sync_calls[0][2]["before_repo_metadata"])
    assert update_adapter_calls == [
        (
            {
                "Name": "Original Title",
                "Url": "https://example.com/ignored",
                "Github": "",
                "Stars": "",
            },
            "https://github.com/foo/bar",
            7,
        )
    ]
    assert updated_row["Url"] == "https://arxiv.org/abs/2603.12345"
    assert updated_row["Github"] == "https://github.com/foo/bar"
    assert outcome.record.name == "Adapted Title"
    assert outcome.source_label == "Discovered Github"
    assert outcome.github_url_set == "https://github.com/foo/bar"
    assert content_cache.calls == ["https://arxiv.org/abs/2603.12345"]


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
        async def get_repo_metadata(self, owner, repo):
            return SimpleNamespace(stars=7, created="2024-01-01T00:00:00Z", about="repo"), None

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
    assert reader.fieldnames == ["Name", "Url", "Overview", "Abs", "Github", "Stars", "Created", "About"]
    assert rows == [
        {
            "Name": "Paper A",
            "Url": "https://arxiv.org/abs/2603.30000v1",
            "Overview": "old-overview.md",
            "Abs": "old-abs.md",
            "Github": "https://github.com/foo/bar",
            "Stars": "7",
            "Created": "2024-01-01T00:00:00Z",
            "About": "repo",
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

        async def get_repo_metadata(self, owner, repo):
            return SimpleNamespace(stars=11, created="2024-01-01T00:00:00Z", about="repo"), None

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
            "Created": "2024-01-01T00:00:00Z",
            "About": "repo",
        }
    ]
    assert received["content_client"].alphaxiv_token == "ax_token"
    assert (tmp_path / "cache" / "overview" / "2603.20000.md").read_text(encoding="utf-8").find("Overview body") != -1
    assert (tmp_path / "cache" / "abs" / "2603.20000.md").read_text(encoding="utf-8").find("Abstract body") != -1
