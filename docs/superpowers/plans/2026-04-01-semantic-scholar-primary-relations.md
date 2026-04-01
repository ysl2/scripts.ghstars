# Semantic Scholar Primary Relations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Semantic Scholar Graph API` the primary source for single-paper arXiv references/citations export, with side-specific `OpenAlex` fallback and strict title-fallback acceptance.

**Architecture:** Keep the current relation normalization, DOI/arXiv resolution, GitHub discovery, stars enrichment, and CSV export chain unchanged after relation rows are fetched. Introduce a dedicated `Semantic Scholar Graph` client plus a provider-neutral relation-candidate boundary, then switch the relation fetch stage to `Semantic Scholar` first and `OpenAlex` second per side.

**Tech Stack:** Python 3.12, `aiohttp`, `pytest`, existing shared runtime/config helpers, existing `OpenAlex` and arXiv relation pipeline.

---

### Task 1: Introduce a provider-neutral relation candidate boundary

**Files:**
- Create: `src/shared/relation_candidates.py`
- Modify: `src/shared/openalex.py`
- Modify: `src/arxiv_relations/pipeline.py`
- Test: `tests/test_openalex.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write the failing tests for the neutral candidate type**

```python
# tests/test_openalex.py
from src.shared.relation_candidates import RelatedWorkCandidate


def test_related_work_candidate_exposes_provider_neutral_source_url():
    candidate = RelatedWorkCandidate(
        title="Paper",
        direct_arxiv_url=None,
        doi_url="https://doi.org/10.1000/example",
        landing_page_url="https://example.org/paper",
        source_url="https://openalex.org/W123",
    )

    assert candidate.source_url == "https://openalex.org/W123"
    assert candidate.openalex_url == "https://openalex.org/W123"


@pytest.mark.anyio
async def test_build_related_work_candidate_uses_source_url_field():
    session = FakeSession([])
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    candidate = client.build_related_work_candidate(
        {
            "id": "https://openalex.org/W123",
            "display_name": "Paper",
            "doi": "https://doi.org/10.1000/example",
            "locations": [{"landing_page_url": "https://example.org/paper"}],
        }
    )

    assert candidate == RelatedWorkCandidate(
        title="Paper",
        direct_arxiv_url=None,
        doi_url="https://doi.org/10.1000/example",
        landing_page_url="https://example.org/paper",
        source_url="https://openalex.org/W123",
    )
```

- [ ] **Step 2: Run the targeted tests to confirm the red state**

Run: `uv run python -m pytest tests/test_openalex.py -k "provider_neutral_source_url or uses_source_url_field" -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.shared.relation_candidates'` or an assertion failure mentioning missing `source_url`.

- [ ] **Step 3: Add the shared relation candidate module and rewire OpenAlex to use it**

```python
# src/shared/relation_candidates.py
from dataclasses import dataclass


@dataclass(frozen=True)
class RelatedWorkCandidate:
    title: str
    direct_arxiv_url: str | None
    doi_url: str | None
    landing_page_url: str | None
    source_url: str

    @property
    def openalex_url(self) -> str:
        # Temporary compatibility alias for existing tests/helpers.
        return self.source_url
```

```python
# src/shared/openalex.py
from src.shared.relation_candidates import RelatedWorkCandidate

    def build_related_work_candidate(self, work: dict[str, Any]) -> RelatedWorkCandidate:
        return RelatedWorkCandidate(
            title=work.get("display_name") or work.get("title") or "",
            direct_arxiv_url=self._canonical_arxiv_url(work),
            doi_url=_normalize_doi_url(work.get("doi")),
            landing_page_url=self._extract_landing_page_url(work),
            source_url=work.get("id") or "",
        )
```

```python
# src/arxiv_relations/pipeline.py
from src.shared.relation_candidates import RelatedWorkCandidate


async def normalize_related_work_candidates_to_seeds(
    candidates: list[RelatedWorkCandidate],
    *,
    openalex_client,
    arxiv_client,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    progress_callback=None,
) -> list[PaperSeed]:
    normalized_rows = await _resolve_related_work_rows(
        candidates,
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        progress_callback=progress_callback,
    )
    return [
        PaperSeed(
            name=row.title,
            url=row.url,
            canonical_arxiv_url=row.url if extract_arxiv_id(row.url) else None,
            url_resolution_authoritative=True,
        )
        for row in _dedupe_normalized_rows(normalized_rows)
    ]
```

- [ ] **Step 4: Make the existing OpenAlex normalization wrapper delegate to the neutral helper**

```python
# src/arxiv_relations/pipeline.py
async def normalize_related_works_to_seeds(
    related_works: list[dict],
    *,
    openalex_client,
    arxiv_client,
    crossref_client=None,
    datacite_client=None,
    discovery_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    progress_callback=None,
) -> list[PaperSeed]:
    candidates = [openalex_client.build_related_work_candidate(work) for work in related_works]
    return await normalize_related_work_candidates_to_seeds(
        candidates,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        progress_callback=progress_callback,
    )
```

- [ ] **Step 5: Run the focused tests to verify the boundary stays green**

Run: `uv run python -m pytest tests/test_openalex.py tests/test_arxiv_relations.py -k "RelatedWorkCandidate or normalize_related_work_candidates_to_seeds or uses_source_url_field" -v`

Expected: PASS

- [ ] **Step 6: Commit the boundary change**

```bash
git add src/shared/relation_candidates.py src/shared/openalex.py src/arxiv_relations/pipeline.py tests/test_openalex.py tests/test_arxiv_relations.py
git commit -m "refactor: add shared relation candidate model"
```

### Task 2: Add a Semantic Scholar Graph client for target lookup and relation fetches

**Files:**
- Create: `src/shared/semantic_scholar_graph.py`
- Test: `tests/test_semantic_scholar_graph.py`

- [ ] **Step 1: Write the failing client tests first**

```python
# tests/test_semantic_scholar_graph.py
import pytest

from src.shared.relation_candidates import RelatedWorkCandidate
from src.shared.semantic_scholar_graph import SemanticScholarGraphClient


@pytest.mark.anyio
async def test_fetch_paper_by_identifier_uses_graph_identifier_endpoint():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "paperId": "paper-1",
                    "title": "Target Paper",
                    "externalIds": {"DOI": "10.48550/arXiv.2510.22706", "ArXiv": "2510.22706"},
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    paper = await client.fetch_paper_by_identifier("DOI:10.48550/arXiv.2510.22706")

    assert paper["paperId"] == "paper-1"
    assert session.calls[0]["url"].endswith("/paper/DOI:10.48550/arXiv.2510.22706")


@pytest.mark.anyio
async def test_fetch_references_unwraps_cited_paper_rows():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [
                        {
                            "citedPaper": {
                                "paperId": "ref-1",
                                "title": "Reference Paper",
                                "externalIds": {"ArXiv": "2501.00001"},
                            }
                        }
                    ],
                    "next": None,
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    rows = await client.fetch_references({"paperId": "target-1"})

    assert rows == [
        {
            "paperId": "ref-1",
            "title": "Reference Paper",
            "externalIds": {"ArXiv": "2501.00001"},
        }
    ]


@pytest.mark.anyio
async def test_build_related_work_candidate_prefers_arxiv_then_doi_then_paper_url():
    session = FakeSession([])
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    candidate = client.build_related_work_candidate(
        {
            "paperId": "paper-1",
            "title": "Reference Paper",
            "externalIds": {"DOI": "10.1000/example"},
        }
    )

    assert candidate == RelatedWorkCandidate(
        title="Reference Paper",
        direct_arxiv_url=None,
        doi_url="https://doi.org/10.1000/example",
        landing_page_url="https://www.semanticscholar.org/paper/paper-1",
        source_url="https://www.semanticscholar.org/paper/paper-1",
    )
```

- [ ] **Step 2: Run the new test file and verify the red state**

Run: `uv run python -m pytest tests/test_semantic_scholar_graph.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'src.shared.semantic_scholar_graph'`.

- [ ] **Step 3: Implement the Graph API client with shared retry/rate-limit behavior**

```python
# src/shared/semantic_scholar_graph.py
import asyncio
from typing import Any

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import build_arxiv_abs_url, normalize_doi_url
from src.shared.relation_candidates import RelatedWorkCandidate


SEMANTIC_SCHOLAR_GRAPH_URL = "https://api.semanticscholar.org/graph/v1"
SEMANTIC_SCHOLAR_SEARCH_LIMIT = 5


class SemanticScholarGraphClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        semantic_scholar_api_key: str = "",
        max_concurrent: int = 4,
        min_interval: float = 0.2,
    ):
        self.session = session
        self.semantic_scholar_api_key = semantic_scholar_api_key.strip()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def fetch_paper_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        payload = await self._get_json(
            f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/{identifier}",
            params={"fields": "paperId,title,externalIds"},
        )
        return payload if isinstance(payload, dict) and payload.get("paperId") else None

    async def search_papers_by_title(self, title: str, *, limit: int = SEMANTIC_SCHOLAR_SEARCH_LIMIT) -> list[dict[str, Any]]:
        payload = await self._get_json(
            f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/search",
            params={"query": title, "limit": limit, "fields": "paperId,title,externalIds"},
        )
        results = payload.get("data") or []
        return [row for row in results if isinstance(row, dict)]

    async def fetch_references(self, paper: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._fetch_relation_rows(
            paper,
            relation_path="references",
            row_key="citedPaper",
        )

    async def fetch_citations(self, paper: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._fetch_relation_rows(
            paper,
            relation_path="citations",
            row_key="citingPaper",
        )
```

- [ ] **Step 4: Finish the client adapter and header logic**

```python
# src/shared/semantic_scholar_graph.py
    def build_related_work_candidate(self, paper: dict[str, Any]) -> RelatedWorkCandidate:
        external_ids = paper.get("externalIds") or {}
        arxiv_id = str(external_ids.get("ArXiv") or "").strip()
        doi = normalize_doi_url(str(external_ids.get("DOI") or ""))
        paper_url = self._build_paper_url(paper)
        direct_arxiv_url = build_arxiv_abs_url(arxiv_id) if arxiv_id else None

        return RelatedWorkCandidate(
            title=" ".join(str(paper.get("title") or "").split()).strip(),
            direct_arxiv_url=direct_arxiv_url,
            doi_url=doi,
            landing_page_url=paper_url,
            source_url=paper_url,
        )

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": "scripts.ghstars"}
        if self.semantic_scholar_api_key:
            headers["x-api-key"] = self.semantic_scholar_api_key
        return headers

    @staticmethod
    def _build_paper_url(paper: dict[str, Any]) -> str:
        paper_id = str(paper.get("paperId") or "").strip()
        return f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""
```

- [ ] **Step 5: Run the client tests to verify the green state**

Run: `uv run python -m pytest tests/test_semantic_scholar_graph.py -v`

Expected: PASS

- [ ] **Step 6: Commit the client**

```bash
git add src/shared/semantic_scholar_graph.py tests/test_semantic_scholar_graph.py
git commit -m "feat: add semantic scholar graph client"
```

### Task 3: Switch relation export to Semantic Scholar primary with side-specific OpenAlex fallback

**Files:**
- Modify: `src/arxiv_relations/pipeline.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Add the failing relation-source priority tests**

```python
# tests/test_arxiv_relations.py
@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_uses_semantic_scholar_before_openalex(monkeypatch, tmp_path: Path):
    class FakeSemanticScholarGraphClient:
        async def fetch_paper_by_identifier(self, identifier: str):
            return {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            raise AssertionError("Should not need title fallback when DOI lookup succeeds")

        async def fetch_references(self, paper: dict):
            return [{"paperId": "ss-ref", "title": "Reference A", "externalIds": {"ArXiv": "2501.00001"}}]

        async def fetch_citations(self, paper: dict):
            return [{"paperId": "ss-cite", "title": "Citation A", "externalIds": {"DOI": "10.1000/example"}}]

        def build_related_work_candidate(self, paper: dict):
            return RelatedWorkCandidate(
                title=paper["title"],
                direct_arxiv_url="https://arxiv.org/abs/2501.00001" if paper["paperId"] == "ss-ref" else None,
                doi_url="https://doi.org/10.1000/example" if paper["paperId"] == "ss-cite" else None,
                landing_page_url=f"https://www.semanticscholar.org/paper/{paper['paperId']}",
                source_url=f"https://www.semanticscholar.org/paper/{paper['paperId']}",
            )

    class FakeOpenAlexClient:
        async def fetch_work_by_identifier(self, identifier: str):
            raise AssertionError("OpenAlex target lookup should not run when Semantic Scholar succeeds")
```

```python
@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_falls_back_to_openalex_when_semantic_scholar_title_match_fails(
    monkeypatch, tmp_path: Path
):
    class FakeSemanticScholarGraphClient:
        async def fetch_paper_by_identifier(self, identifier: str):
            return None

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            return [{"paperId": "wrong-paper", "title": "Different Paper", "externalIds": {}}]

    class FakeOpenAlexClient:
        async def fetch_work_by_identifier(self, identifier: str):
            return {"id": "https://openalex.org/W1", "display_name": "Target Paper", "doi": "https://doi.org/10.48550/arXiv.2510.22706", "referenced_works": []}
```

```python
@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_falls_back_per_side_when_semantic_scholar_returns_empty_side(
    monkeypatch, tmp_path: Path
):
    class FakeSemanticScholarGraphClient:
        async def fetch_paper_by_identifier(self, identifier: str):
            return {"paperId": "ss-target", "title": "Target Paper", "externalIds": {"ArXiv": "2510.22706"}}

        async def fetch_references(self, paper: dict):
            return []

        async def fetch_citations(self, paper: dict):
            return [{"paperId": "ss-cite", "title": "Citation A", "externalIds": {"ArXiv": "2502.00001"}}]
```

- [ ] **Step 2: Run the targeted relation tests to confirm the red state**

Run: `uv run python -m pytest tests/test_arxiv_relations.py -k "semantic_scholar_before_openalex or title_match_fails or empty_side" -v`

Expected: FAIL because `export_arxiv_relations_to_csv()` does not yet accept or use `semanticscholar_graph_client`.

- [ ] **Step 3: Add Semantic Scholar target-resolution helpers and side fetch selection**

```python
# src/arxiv_relations/pipeline.py
async def _resolve_target_semantic_scholar_paper(arxiv_url: str, title: str, semanticscholar_graph_client) -> dict | None:
    arxiv_id = extract_arxiv_id(arxiv_url)
    doi_identifier = f"DOI:10.48550/arXiv.{arxiv_id}" if arxiv_id else None
    arxiv_identifier = f"ARXIV:{arxiv_id}" if arxiv_id else None

    for identifier in [doi_identifier, arxiv_identifier]:
        if not identifier:
            continue
        paper = await semanticscholar_graph_client.fetch_paper_by_identifier(identifier)
        if isinstance(paper, dict):
            return paper

    matches = await semanticscholar_graph_client.search_papers_by_title(title)
    normalized_title = normalize_title_for_matching(title)
    for paper in matches:
        candidate_title = " ".join(str(paper.get("title") or "").split()).strip()
        if normalize_title_for_matching(candidate_title) == normalized_title:
            return paper

    return None
```

```python
# src/arxiv_relations/pipeline.py
async def _fetch_primary_relation_candidates(
    *,
    relation_label: str,
    semanticscholar_graph_client,
    semantic_scholar_target_paper: dict | None,
    openalex_client,
    openalex_target_work: dict | None,
    status_callback=None,
) -> list[RelatedWorkCandidate]:
    if semantic_scholar_target_paper is not None:
        if callable(status_callback):
            status_callback(f"🔎 Fetching Semantic Scholar {relation_label}")
        fetcher = (
            semanticscholar_graph_client.fetch_references
            if relation_label == "references"
            else semanticscholar_graph_client.fetch_citations
        )
        rows = await fetcher(semantic_scholar_target_paper)
        if rows:
            if callable(status_callback):
                status_callback(f"📚 Semantic Scholar returned {len(rows)} {relation_label}")
            return [semanticscholar_graph_client.build_related_work_candidate(row) for row in rows]
        if callable(status_callback):
            status_callback(f"📚 Semantic Scholar {relation_label} empty; falling back to OpenAlex")

    if openalex_target_work is None:
        return []

    if callable(status_callback):
        status_callback(f"🔎 Fetching OpenAlex {relation_label}")
    fetcher = openalex_client.fetch_referenced_works if relation_label == "references" else openalex_client.fetch_citations
    rows = await fetcher(openalex_target_work)
    if callable(status_callback):
        status_callback(f"📚 OpenAlex returned {len(rows)} {relation_label}")
    return [openalex_client.build_related_work_candidate(row) for row in rows]
```

- [ ] **Step 4: Thread the new primary/fallback flow through `export_arxiv_relations_to_csv()`**

```python
# src/arxiv_relations/pipeline.py
async def export_arxiv_relations_to_csv(
    arxiv_input: str,
    *,
    arxiv_client,
    openalex_client,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    discovery_client,
    github_client,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    output_dir: Path | None = None,
    status_callback=None,
    normalization_progress_callback=None,
    progress_callback=None,
) -> ArxivRelationsExportResult:
    arxiv_url = normalize_single_arxiv_input(arxiv_input)
    title, error = await arxiv_client.get_title(arxiv_url)
    if error or not title:
        raise ValueError(f"Failed to resolve arXiv title: {error or 'No title found'}")

    semantic_scholar_target_paper = None
    if semanticscholar_graph_client is not None:
        semantic_scholar_target_paper = await _resolve_target_semantic_scholar_paper(
            arxiv_url,
            title,
            semanticscholar_graph_client,
        )

    openalex_target_work = None
    if semantic_scholar_target_paper is None:
        openalex_target_work = await _resolve_target_openalex_work(arxiv_url, title, openalex_client)
    else:
        openalex_target_work = await _resolve_target_openalex_work(arxiv_url, title, openalex_client)

    if openalex_target_work is None and semantic_scholar_target_paper is None:
        raise ValueError(f"No relation source work found for title: {title}")

    reference_candidates = await _fetch_primary_relation_candidates(
        relation_label="references",
        semanticscholar_graph_client=semanticscholar_graph_client,
        semantic_scholar_target_paper=semantic_scholar_target_paper,
        openalex_client=openalex_client,
        openalex_target_work=openalex_target_work,
        status_callback=status_callback,
    )
    citation_candidates = await _fetch_primary_relation_candidates(
        relation_label="citations",
        semanticscholar_graph_client=semanticscholar_graph_client,
        semantic_scholar_target_paper=semantic_scholar_target_paper,
        openalex_client=openalex_client,
        openalex_target_work=openalex_target_work,
        status_callback=status_callback,
    )

    reference_seeds = await normalize_related_work_candidates_to_seeds(
        reference_candidates,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        progress_callback=normalization_progress_callback,
    )
    citation_seeds = await normalize_related_work_candidates_to_seeds(
        citation_candidates,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        progress_callback=normalization_progress_callback,
    )
```

- [ ] **Step 5: Run the relation tests and confirm the green state**

Run: `uv run python -m pytest tests/test_arxiv_relations.py -k "semantic_scholar or openalex_retry_after or prefers_exact_openalex_target_selection" -v`

Expected: PASS

- [ ] **Step 6: Commit the pipeline switch**

```bash
git add src/arxiv_relations/pipeline.py tests/test_arxiv_relations.py
git commit -m "feat: use semantic scholar as primary relation source"
```

### Task 4: Wire runtime config and runner injection for the Graph client

**Files:**
- Modify: `src/shared/runtime.py`
- Modify: `src/arxiv_relations/runner.py`
- Test: `tests/test_main.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Add the failing config/runner tests**

```python
# tests/test_main.py
def test_load_runtime_config_reads_optional_semantic_scholar_api_key():
    config = load_runtime_config({"SEMANTIC_SCHOLAR_API_KEY": "ss_key"})
    assert config["semantic_scholar_api_key"] == "ss_key"
```

```python
# tests/test_arxiv_relations.py
class FakeSemanticScholarGraphClient:
    def __init__(self, session, *, semantic_scholar_api_key="", max_concurrent=0, min_interval=0):
        self.session = session
        self.semantic_scholar_api_key = semantic_scholar_api_key
        constructed["semantic_scholar_graph_client"] = self


assert export_calls[0]["semanticscholar_graph_client"] is constructed["semantic_scholar_graph_client"]
assert constructed["semantic_scholar_graph_client"].semantic_scholar_api_key == "ss_key"
```

- [ ] **Step 2: Run the focused runtime/runner tests to confirm the red state**

Run: `uv run python -m pytest tests/test_main.py tests/test_arxiv_relations.py -k "semantic_scholar_api_key or semantic_scholar_graph_client" -v`

Expected: FAIL because runtime config does not yet expose `semantic_scholar_api_key` and the runner does not yet build/pass the client.

- [ ] **Step 3: Extend runtime config and runner wiring**

```python
# src/shared/runtime.py
def load_runtime_config(env: dict[str, str]) -> dict[str, str | int]:
    return {
        "github_token": (env.get("GITHUB_TOKEN") or "").strip(),
        "huggingface_token": (env.get("HUGGINGFACE_TOKEN") or "").strip(),
        "alphaxiv_token": (env.get("ALPHAXIV_TOKEN") or "").strip(),
        "openalex_api_key": (env.get("OPENALEX_API_KEY") or "").strip(),
        "semantic_scholar_api_key": (env.get("SEMANTIC_SCHOLAR_API_KEY") or "").strip(),
        "arxiv_relation_no_arxiv_recheck_days": _parse_positive_int(
            env.get("ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS"),
            default=ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS,
        ),
        "repo_discovery_no_repo_recheck_days": _parse_positive_int(
            repo_discovery_recheck_days_raw,
            default=REPO_DISCOVERY_NO_REPO_RECHECK_DAYS,
        ),
    }
```

```python
# src/arxiv_relations/runner.py
from src.shared.semantic_scholar_graph import SemanticScholarGraphClient

async def run_arxiv_relations_mode(
    arxiv_input: str,
    *,
    output_dir: Path | None = None,
    session_factory=aiohttp.ClientSession,
    arxiv_client_cls=ArxivClient,
    openalex_client_cls=OpenAlexClient,
    semanticscholar_graph_client_cls=SemanticScholarGraphClient,
    crossref_client_cls=CrossrefClient,
    datacite_client_cls=DataCiteClient,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
    content_client_cls=AlphaXivContentClient,
) -> int:
    semanticscholar_graph_client = build_client(
        semanticscholar_graph_client_cls,
        runtime.session,
        semantic_scholar_api_key=config["semantic_scholar_api_key"],
        max_concurrent=CONCURRENT_LIMIT,
        min_interval=REQUEST_DELAY,
    )
    result = await export_arxiv_relations_to_csv(
        arxiv_input,
        output_dir=output_dir,
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=runtime.discovery_client,
        github_client=runtime.github_client,
        content_cache=content_cache,
        relation_resolution_cache=runtime.relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=config["arxiv_relation_no_arxiv_recheck_days"],
        status_callback=lambda message: print(message, flush=True),
        normalization_progress_callback=lambda outcome, total: print_relation_progress(outcome, total),
        progress_callback=lambda outcome, total: print_paper_progress(
            outcome,
            total,
            is_minor_reason=is_minor_skip_reason,
        ),
    )
```

- [ ] **Step 4: Run the runtime and runner tests to verify the green state**

Run: `uv run python -m pytest tests/test_main.py tests/test_arxiv_relations.py -k "semantic_scholar_api_key or semantic_scholar_graph_client or successfully_wires_clients_callbacks" -v`

Expected: PASS

- [ ] **Step 5: Commit the runtime wiring**

```bash
git add src/shared/runtime.py src/arxiv_relations/runner.py tests/test_main.py tests/test_arxiv_relations.py
git commit -m "feat: wire semantic scholar graph client into relations mode"
```

### Task 5: Run the full focused verification suite and a real export

**Files:**
- Modify: `src/shared/relation_candidates.py`
- Modify: `src/shared/openalex.py`
- Modify: `src/shared/semantic_scholar_graph.py`
- Modify: `src/shared/runtime.py`
- Modify: `src/arxiv_relations/pipeline.py`
- Modify: `src/arxiv_relations/runner.py`
- Test: `tests/test_openalex.py`
- Test: `tests/test_semantic_scholar_graph.py`
- Test: `tests/test_arxiv_relations.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Run the focused unit/integration suite**

Run: `uv run python -m pytest tests/test_openalex.py tests/test_semantic_scholar_graph.py tests/test_arxiv_relations.py tests/test_main.py`

Expected: PASS

- [ ] **Step 2: Run the real single-paper command against the motivating paper**

Run: `uv run main.py 'https://arxiv.org/abs/2510.22706'`

Expected: exit code `0`, logs mention `Semantic Scholar` as the primary source for at least the initial fetch attempt, and the command writes both relation CSV paths under `output/`.

- [ ] **Step 3: Inspect the newest output files and confirm they are not header-only**

Run: `ls -t output/arxiv-2510.22706-references-*.csv output/arxiv-2510.22706-citations-*.csv | head -n 2 | xargs -I{} sh -c 'echo \"=== {} ===\"; sed -n \"1,5p\" \"{}\"'`

Expected: both files exist and include at least one data row below `Name,Url,Github,Stars`.

- [ ] **Step 4: Commit the verified integration**

```bash
git add src/shared/relation_candidates.py src/shared/openalex.py src/shared/semantic_scholar_graph.py src/shared/runtime.py src/arxiv_relations/pipeline.py src/arxiv_relations/runner.py tests/test_openalex.py tests/test_semantic_scholar_graph.py tests/test_arxiv_relations.py tests/test_main.py
git commit -m "feat: add semantic scholar primary relation export"
```
