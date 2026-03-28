import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from src.arxiv_relations.pipeline import ArxivRelationsExportResult
from src.shared.openalex import RelatedWorkCandidate
from src.shared.papers import ConversionResult, PaperSeed
from src.shared.papers import PaperRecord
from src.arxiv_relations.runner import run_arxiv_relations_mode


def test_dedup_prefers_direct_arxiv_over_title_mapped_row():
    from src.arxiv_relations.pipeline import (
        NormalizationStrength,
        NormalizedRelatedRow,
        _dedupe_normalized_rows,
    )

    rows = [
        NormalizedRelatedRow(
            title="Mapped Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.TITLE_SEARCH,
        ),
        NormalizedRelatedRow(
            title="Direct Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.DIRECT_ARXIV,
        ),
    ]

    winner = _dedupe_normalized_rows(rows)

    assert winner == [
        NormalizedRelatedRow(
            title="Direct Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.DIRECT_ARXIV,
        )
    ]


def test_dedup_breaks_same_strength_ties_by_normalized_then_original_title():
    from src.arxiv_relations.pipeline import (
        NormalizationStrength,
        NormalizedRelatedRow,
        _dedupe_normalized_rows,
    )

    rows = [
        NormalizedRelatedRow(
            title="Zoo",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        ),
        NormalizedRelatedRow(
            title="alpha",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        ),
    ]

    winner = _dedupe_normalized_rows(rows)

    assert winner == [
        NormalizedRelatedRow(
            title="alpha",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        )
    ]


@pytest.mark.anyio
async def test_normalize_related_works_maps_non_arxiv_title_hits_to_canonical_arxiv():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R1": RelatedWorkCandidate(
                    title="Direct Paper",
                    direct_arxiv_url="https://arxiv.org/abs/2403.00001",
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/W1",
                ),
                "R2": RelatedWorkCandidate(
                    title="Original OpenAlex Title",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/mapped",
                    openalex_url="https://openalex.org/W2",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            if title == "Original OpenAlex Title":
                return "2501.12345", "title_search_exact", None
            raise AssertionError(f"Unexpected title search: {title}")

        async def get_title(self, arxiv_identifier: str):
            if arxiv_identifier == "2501.12345":
                return "Mapped Arxiv Title", None
            raise AssertionError(f"Unexpected arXiv title lookup: {arxiv_identifier}")

    related_works = [{"id": "R1"}, {"id": "R2"}]
    seeds = await normalize_related_works_to_seeds(
        related_works,
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
    )

    assert seeds == [
        PaperSeed(name="Direct Paper", url="https://arxiv.org/abs/2403.00001"),
        PaperSeed(name="Mapped Arxiv Title", url="https://arxiv.org/abs/2501.12345"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_retains_unresolved_non_arxiv_rows_with_url_priority():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R3": RelatedWorkCandidate(
                    title="With DOI",
                    direct_arxiv_url=None,
                    doi_url="https://doi.org/10.1145/example",
                    landing_page_url="https://publisher.example/doi",
                    openalex_url="https://openalex.org/W3",
                ),
                "R4": RelatedWorkCandidate(
                    title="With Landing",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/paper",
                    openalex_url="https://openalex.org/W4",
                ),
                "R5": RelatedWorkCandidate(
                    title="OpenAlex Only",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/W5",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

    related_works = [{"id": "R3"}, {"id": "R4"}, {"id": "R5"}]
    seeds = await normalize_related_works_to_seeds(
        related_works,
        openalex_client=FakeOpenAlexClient(),
        arxiv_client=FakeArxivClient(),
    )

    assert seeds == [
        PaperSeed(name="With DOI", url="https://doi.org/10.1145/example"),
        PaperSeed(name="With Landing", url="https://publisher.example/paper"),
        PaperSeed(name="OpenAlex Only", url="https://openalex.org/W5"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_resolves_non_direct_rows_concurrently():
    from src.arxiv_relations.pipeline import normalize_related_works_to_seeds

    class FakeOpenAlexClient:
        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R6": RelatedWorkCandidate(
                    title="Concurrent Paper A",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/a",
                    openalex_url="https://openalex.org/W6",
                ),
                "R7": RelatedWorkCandidate(
                    title="Concurrent Paper B",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url="https://publisher.example/b",
                    openalex_url="https://openalex.org/W7",
                ),
            }
            return mapping[work["id"]]

    class FakeArxivClient:
        def __init__(self):
            self.search_started: list[str] = []
            self.release_searches = asyncio.Event()

        async def get_arxiv_id_by_title(self, title: str):
            self.search_started.append(title)
            if len(self.search_started) == 2:
                self.release_searches.set()
            await self.release_searches.wait()

            mapping = {
                "Concurrent Paper A": ("2601.00001", "title_search_exact", None),
                "Concurrent Paper B": ("2601.00002", "title_search_exact", None),
            }
            return mapping[title]

        async def get_title(self, arxiv_identifier: str):
            mapping = {
                "2601.00001": ("Concurrent Match A", None),
                "2601.00002": ("Concurrent Match B", None),
            }
            return mapping[arxiv_identifier]

    seeds = await asyncio.wait_for(
        normalize_related_works_to_seeds(
            [{"id": "R6"}, {"id": "R7"}],
            openalex_client=FakeOpenAlexClient(),
            arxiv_client=FakeArxivClient(),
        ),
        timeout=0.2,
    )

    assert seeds == [
        PaperSeed(name="Concurrent Match A", url="https://arxiv.org/abs/2601.00001"),
        PaperSeed(name="Concurrent Match B", url="https://arxiv.org/abs/2601.00002"),
    ]


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_resolves_title_filters_and_exports_both_sets(
    tmp_path: Path, monkeypatch
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        def __init__(self):
            self.calls: list[str] = []
            self.title_searches: list[str] = []

        async def get_title(self, arxiv_identifier: str):
            self.calls.append(arxiv_identifier)
            title_mapping = {
                "https://arxiv.org/abs/2603.23502": "Target Paper",
                "2501.00001": "Reference A",
                "2502.00002": "Citation A",
            }
            return title_mapping[arxiv_identifier], None

        async def get_arxiv_id_by_title(self, title: str):
            self.title_searches.append(title)
            return None, None, "No arXiv ID found from title search"

    class FakeOpenAlexClient:
        def __init__(self):
            self.title_queries: list[str] = []
            self.reference_work_queries: list[dict] = []
            self.citation_work_queries: list[dict] = []

        async def search_first_work(self, title: str):
            self.title_queries.append(title)
            return {"id": "https://openalex.org/W0"}

        async def fetch_referenced_works(self, work: dict):
            self.reference_work_queries.append(work)
            return [
                {"id": "R1"},
                {"id": "R2"},
                {"id": "R3"},
            ]

        async def fetch_citations(self, work: dict):
            self.citation_work_queries.append(work)
            return [
                {"id": "C1"},
                {"id": "C2"},
            ]

        def build_related_work_candidate(self, work: dict):
            mapping = {
                "R1": RelatedWorkCandidate(
                    title="Reference A",
                    direct_arxiv_url="https://arxiv.org/abs/2501.00001",
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/WR1",
                ),
                "R2": RelatedWorkCandidate(
                    title="Reference Missing",
                    direct_arxiv_url=None,
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/WR2",
                ),
                "R3": RelatedWorkCandidate(
                    title="Reference A Duplicate",
                    direct_arxiv_url="https://arxiv.org/abs/2501.00001",
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/WR3",
                ),
                "C1": RelatedWorkCandidate(
                    title="Citation A",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00002",
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/WC1",
                ),
                "C2": RelatedWorkCandidate(
                    title="Citation A Duplicate",
                    direct_arxiv_url="https://arxiv.org/abs/2502.00002",
                    doi_url=None,
                    landing_page_url=None,
                    openalex_url="https://openalex.org/WC2",
                ),
            }
            return mapping[work["id"]]

    arxiv_client = FakeArxivClient()
    openalex_client = FakeOpenAlexClient()
    discovery_client = object()
    github_client = object()
    export_calls = []
    statuses = []

    async def fake_export(
        seeds: list[PaperSeed],
        csv_path: Path,
        *,
        discovery_client,
        github_client,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append(
            {
                "seeds": seeds,
                "csv_path": csv_path,
                "discovery_client": discovery_client,
                "github_client": github_client,
            }
        )
        return ConversionResult(csv_path=csv_path, resolved=len(seeds), skipped=[])

    monkeypatch.setattr("src.arxiv_relations.pipeline.export_paper_seeds_to_csv", fake_export)

    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/pdf/2603.23502v4.pdf?download=1",
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        discovery_client=discovery_client,
        github_client=github_client,
        output_dir=tmp_path,
        status_callback=statuses.append,
    )

    assert arxiv_client.calls == ["https://arxiv.org/abs/2603.23502"]
    assert arxiv_client.title_searches == ["Reference Missing"]
    assert openalex_client.title_queries == ["Target Paper"]
    assert openalex_client.reference_work_queries == [{"id": "https://openalex.org/W0"}]
    assert openalex_client.citation_work_queries == [{"id": "https://openalex.org/W0"}]

    assert len(export_calls) == 2
    assert [call["csv_path"].name for call in export_calls] == [
        "arxiv-2603.23502-references-20260326113045.csv",
        "arxiv-2603.23502-citations-20260326113045.csv",
    ]

    reference_seeds = export_calls[0]["seeds"]
    citation_seeds = export_calls[1]["seeds"]
    assert reference_seeds == [
        PaperSeed(name="Reference A", url="https://arxiv.org/abs/2501.00001"),
        PaperSeed(name="Reference Missing", url="https://openalex.org/WR2"),
    ]
    assert citation_seeds == [PaperSeed(name="Citation A", url="https://arxiv.org/abs/2502.00002")]
    assert all(isinstance(seed, PaperSeed) for seed in reference_seeds + citation_seeds)

    assert export_calls[0]["discovery_client"] is discovery_client
    assert export_calls[0]["github_client"] is github_client
    assert export_calls[1]["discovery_client"] is discovery_client
    assert export_calls[1]["github_client"] is github_client

    assert result.references.csv_path.name == "arxiv-2603.23502-references-20260326113045.csv"
    assert result.citations.csv_path.name == "arxiv-2603.23502-citations-20260326113045.csv"
    assert any("Fetching OpenAlex referenced works" in message for message in statuses)
    assert any("Fetching OpenAlex citations" in message for message in statuses)


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_rejects_invalid_single_paper_input():
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            raise AssertionError("Should not request arXiv title for invalid input")

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            raise AssertionError("Should not query OpenAlex for invalid input")

    with pytest.raises(ValueError, match="Invalid single-paper arXiv URL"):
        await export_arxiv_relations_to_csv(
            "https://arxiv.org/list/cs.CV/recent",
            arxiv_client=FakeArxivClient(),
            openalex_client=FakeOpenAlexClient(),
            discovery_client=object(),
            github_client=object(),
        )


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_fails_when_arxiv_title_lookup_fails():
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            return None, "metadata lookup timeout"

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            raise AssertionError("OpenAlex search should not run when title lookup fails")

    with pytest.raises(ValueError, match="Failed to resolve arXiv title: metadata lookup timeout"):
        await export_arxiv_relations_to_csv(
            "https://arxiv.org/abs/2603.23502",
            arxiv_client=FakeArxivClient(),
            openalex_client=FakeOpenAlexClient(),
            discovery_client=object(),
            github_client=object(),
        )


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_fails_when_no_openalex_work_found():
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            return "Target Paper", None

    class FakeOpenAlexClient:
        async def search_first_work(self, title: str):
            return None

    with pytest.raises(ValueError, match="No OpenAlex work found for title: Target Paper"):
        await export_arxiv_relations_to_csv(
            "https://arxiv.org/abs/2603.23502",
            arxiv_client=FakeArxivClient(),
            openalex_client=FakeOpenAlexClient(),
            discovery_client=object(),
            github_client=object(),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exc_type", "message"),
    [
        (ValueError, "Invalid single-paper arXiv URL: bad-input"),
        (RuntimeError, "OpenAlex API error (503)"),
        (aiohttp.ClientError, "connection reset by peer"),
    ],
)
async def test_run_arxiv_relations_mode_prints_concise_stderr_and_returns_nonzero_on_expected_errors(
    monkeypatch, capsys, exc_type, message
):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeOpenAlexClient:
        def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(
            self,
            session,
            *,
            huggingface_token="",
            repo_cache=None,
            hf_exact_no_repo_recheck_days=0,
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    async def fake_export(*args, **kwargs):
        raise exc_type(message)

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    exit_code = await run_arxiv_relations_mode(
        "https://arxiv.org/abs/2603.23502",
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        openalex_client_cls=FakeOpenAlexClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == f"ArXiv relation export failed: {message}"


@pytest.mark.anyio
async def test_run_arxiv_relations_mode_returns_nonzero_on_unexpected_hard_failure(monkeypatch, capsys):
    class HardFailure(Exception):
        pass

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session

    class FakeOpenAlexClient:
        def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
            self.session = session

    class FakeDiscoveryClient:
        def __init__(
            self,
            session,
            *,
            huggingface_token="",
            repo_cache=None,
            hf_exact_no_repo_recheck_days=0,
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session

    async def fake_export(*args, **kwargs):
        raise HardFailure("unhandled export branch")

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    exit_code = await run_arxiv_relations_mode(
        "https://arxiv.org/abs/2603.23502",
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        openalex_client_cls=FakeOpenAlexClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == "ArXiv relation export failed: unhandled export branch"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure_stage", "message"),
    [
        ("runtime", "runtime setup exploded"),
        ("build", "arxiv client construction exploded"),
    ],
)
async def test_run_arxiv_relations_mode_returns_nonzero_on_pre_export_setup_failures(
    monkeypatch, capsys, failure_stage, message
):
    async def fake_export(*args, **kwargs):
        raise AssertionError("export should not run when setup fails")

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    if failure_stage == "runtime":

        @asynccontextmanager
        async def fake_open_runtime_clients(*args, **kwargs):
            raise RuntimeError(message)
            yield

        monkeypatch.setattr("src.arxiv_relations.runner.open_runtime_clients", fake_open_runtime_clients)

        exit_code = await run_arxiv_relations_mode("https://arxiv.org/abs/2603.23502")
    else:

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FailingArxivClient:
            def __init__(self, session, *, max_concurrent=0, min_interval=0):
                raise RuntimeError(message)

        class FakeOpenAlexClient:
            def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
                self.session = session

        class FakeDiscoveryClient:
            def __init__(
                self,
                session,
                *,
                huggingface_token="",
                repo_cache=None,
                hf_exact_no_repo_recheck_days=0,
                max_concurrent=0,
                min_interval=0,
            ):
                self.session = session

        class FakeGitHubClient:
            def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
                self.session = session

        exit_code = await run_arxiv_relations_mode(
            "https://arxiv.org/abs/2603.23502",
            session_factory=lambda **kwargs: FakeSession(),
            arxiv_client_cls=FailingArxivClient,
            openalex_client_cls=FakeOpenAlexClient,
            discovery_client_cls=FakeDiscoveryClient,
            github_client_cls=FakeGitHubClient,
        )

    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == f"ArXiv relation export failed: {message}"


@pytest.mark.anyio
async def test_run_arxiv_relations_mode_successfully_wires_clients_callbacks_and_summary_output(
    tmp_path: Path, monkeypatch, capsys
):
    references_csv_path = tmp_path / "arxiv-2603.23502-references-20260326113045.csv"
    citations_csv_path = tmp_path / "arxiv-2603.23502-citations-20260326113045.csv"
    constructed = {}
    export_calls = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeArxivClient:
        def __init__(self, session, *, max_concurrent=0, min_interval=0):
            self.session = session
            self.max_concurrent = max_concurrent
            self.min_interval = min_interval
            constructed["arxiv_client"] = self

    class FakeOpenAlexClient:
        def __init__(self, session, *, openalex_api_key="", max_concurrent=0, min_interval=0):
            self.session = session
            self.openalex_api_key = openalex_api_key
            self.max_concurrent = max_concurrent
            self.min_interval = min_interval
            constructed["openalex_client"] = self

    class FakeDiscoveryClient:
        def __init__(
            self,
            session,
            *,
            huggingface_token="",
            repo_cache=None,
            hf_exact_no_repo_recheck_days=0,
            max_concurrent=0,
            min_interval=0,
        ):
            self.session = session
            constructed["discovery_client"] = self

    class FakeGitHubClient:
        def __init__(self, session, *, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            constructed["github_client"] = self

    async def fake_export(
        arxiv_input: str,
        *,
        output_dir: Path | None = None,
        arxiv_client,
        openalex_client,
        discovery_client,
        github_client,
        status_callback=None,
        progress_callback=None,
    ):
        export_calls.append(
            {
                "arxiv_input": arxiv_input,
                "output_dir": output_dir,
                "arxiv_client": arxiv_client,
                "openalex_client": openalex_client,
                "discovery_client": discovery_client,
                "github_client": github_client,
                "status_callback": status_callback,
                "progress_callback": progress_callback,
            }
        )
        assert callable(status_callback)
        assert callable(progress_callback)

        status_callback("Starting relation export")
        progress_callback(
            SimpleNamespace(
                index=1,
                record=PaperRecord(
                    name="Reference Paper",
                    url="https://arxiv.org/abs/2501.00001",
                    github="https://github.com/foo/bar",
                    stars=12,
                ),
                reason=None,
                current_stars=10,
            ),
            1,
        )

        return ArxivRelationsExportResult(
            arxiv_url="https://arxiv.org/abs/2603.23502",
            title="Target Paper",
            references=ConversionResult(csv_path=references_csv_path, resolved=1, skipped=[]),
            citations=ConversionResult(csv_path=citations_csv_path, resolved=2, skipped=[]),
        )

    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    exit_code = await run_arxiv_relations_mode(
        "https://arxiv.org/abs/2603.23502",
        output_dir=tmp_path,
        session_factory=lambda **kwargs: FakeSession(),
        arxiv_client_cls=FakeArxivClient,
        openalex_client_cls=FakeOpenAlexClient,
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert len(export_calls) == 1
    assert export_calls[0]["arxiv_input"] == "https://arxiv.org/abs/2603.23502"
    assert export_calls[0]["output_dir"] == tmp_path
    assert export_calls[0]["arxiv_client"] is constructed["arxiv_client"]
    assert export_calls[0]["openalex_client"] is constructed["openalex_client"]
    assert export_calls[0]["discovery_client"] is constructed["discovery_client"]
    assert export_calls[0]["github_client"] is constructed["github_client"]
    assert "Starting relation export" in captured.out
    assert "[1/1] Reference Paper" in captured.out
    assert "foo/bar" in captured.out
    assert "Updated: 10 → 12" in captured.out
    assert "References resolved: 1" in captured.out
    assert "Citations resolved: 2" in captured.out
    assert f"Wrote references CSV: {references_csv_path}" in captured.out
    assert f"Wrote citations CSV: {citations_csv_path}" in captured.out
