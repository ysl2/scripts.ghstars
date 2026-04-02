# Property-Centric Metadata Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `scripts.ghstars` around a shared property-centric core so `Github`, `Stars`, `Created`, and `About` follow one acquisition/write policy across fresh export, CSV update, and Notion sync.

**Architecture:** Keep source-specific ingestion families honest, but move shared business logic into a small property model plus explicit property resolvers. Treat URL normalization, `overview/abs`, and cache/db layout as supporting contracts, then let CSV and Notion become target adapters that apply per-property write policies instead of owning mode-local business rules.

**Tech Stack:** Python 3, `aiohttp`, SQLite, `pytest`, Notion API client, existing shared arXiv/GitHub/discovery helpers

---

## File Structure

**Create**

- `src/shared/property_model.py`
  - Shared `PropertyState`, `RecordState`, and status enums/constants for the six core properties.
- `src/shared/property_resolvers.py`
  - Shared `Github` acquisition and repo metadata resolution helpers with explicit partial-success contracts.
- `src/shared/repo_metadata_cache.py`
  - SQLite-backed durable cache for repo-level durable facts keyed by normalized GitHub URL.
- `src/shared/csv_schema.py`
  - Canonical column order constants and append-without-reorder helpers for `csv -> csv` updates.
- `tests/test_property_model.py`
  - Unit coverage for the new property model and status transitions.
- `tests/test_property_resolvers.py`
  - Unit coverage for `Github` acquisition and repo metadata partial-success behavior.

**Modify**

- `src/shared/github.py`
  - Add full repo metadata fetch support while keeping `get_star_count()` as a compatibility wrapper.
- `src/shared/runtime.py`
  - Open/close the new repo metadata cache alongside existing shared caches.
- `src/shared/paper_enrichment.py`
  - Turn the current monolith into a compatibility wrapper around explicit acquisition + metadata resolvers.
- `src/shared/paper_export.py`
  - Fill `Created`/`About` from the shared metadata path for paper-family fresh exports.
- `src/url_to_csv/pipeline.py`
  - Keep source behavior, but rely on updated shared export semantics.
- `src/arxiv_relations/pipeline.py`
  - Keep relation normalization, but rely on updated shared export semantics.
- `src/csv_update/pipeline.py`
  - Replace the current `Github/Stars` patcher with property-aware update/write policy logic.
- `src/notion_sync/notion_client.py`
  - Extend schema creation and page updates to `Created` and `About`.
- `src/notion_sync/pipeline.py`
  - Apply property-centric resolver results and per-property write policies.
- `src/notion_sync/runner.py`
  - Keep the same CLI behavior while calling the new schema validation/creation flow.
- `src/shared/csv_io.py`
  - Keep canonical fresh-export headers and share canonical column order constants with the new CSV schema helper.
- `README.md`
  - Update product behavior for `Created`/`About` fresh export and update semantics.
- `ARCHITECTURE.md`
  - Document the property-centric core, supporting contracts, and adapter boundaries.

**Test**

- `tests/test_shared_services.py`
- `tests/test_main.py`
- `tests/test_paper_enrichment.py`
- `tests/test_paper_export.py`
- `tests/test_url_to_csv.py`
- `tests/test_arxiv_relations.py`
- `tests/test_csv_update.py`
- `tests/test_notion_mode.py`

### Task 1: Introduce The Property Model

**Files:**
- Create: `src/shared/property_model.py`
- Test: `tests/test_property_model.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.shared.property_model import PropertyState, PropertyStatus, RecordState


def test_record_state_seeds_peer_level_properties_from_source_values():
    state = RecordState.from_source(
        name="Paper A",
        url="https://doi.org/10.1145/example",
        github="https://github.com/foo/bar",
        stars="7",
    )

    assert state.name.value == "Paper A"
    assert state.name.status is PropertyStatus.PRESENT
    assert state.url.status is PropertyStatus.PRESENT
    assert state.github.status is PropertyStatus.PRESENT
    assert state.created.status is PropertyStatus.BLOCKED
    assert state.about.status is PropertyStatus.BLOCKED


def test_property_state_helpers_support_resolved_failed_and_skipped_states():
    assert PropertyState.resolved("https://github.com/foo/bar", source="url").status is PropertyStatus.RESOLVED
    assert PropertyState.failed("metadata failed").reason == "metadata failed"
    assert PropertyState.skipped("preserve existing value").status is PropertyStatus.SKIPPED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_property_model.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.shared.property_model'`

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass
from enum import Enum


class PropertyStatus(str, Enum):
    PRESENT = "present"
    RESOLVED = "resolved"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class PropertyState:
    value: str | int | None
    status: PropertyStatus
    source: str | None = None
    reason: str | None = None

    @classmethod
    def present(cls, value, *, source: str):
        return cls(value=value, status=PropertyStatus.PRESENT, source=source)

    @classmethod
    def resolved(cls, value, *, source: str):
        return cls(value=value, status=PropertyStatus.RESOLVED, source=source)

    @classmethod
    def skipped(cls, reason: str):
        return cls(value=None, status=PropertyStatus.SKIPPED, reason=reason)

    @classmethod
    def blocked(cls, reason: str):
        return cls(value=None, status=PropertyStatus.BLOCKED, reason=reason)

    @classmethod
    def failed(cls, reason: str):
        return cls(value=None, status=PropertyStatus.FAILED, reason=reason)


@dataclass(frozen=True)
class RecordState:
    name: PropertyState
    url: PropertyState
    github: PropertyState
    stars: PropertyState
    created: PropertyState
    about: PropertyState

    @classmethod
    def from_source(cls, *, name="", url="", github="", stars="", created="", about=""):
        return cls(
            name=PropertyState.present(name, source="source") if name else PropertyState.blocked("Name missing"),
            url=PropertyState.present(url, source="source") if url else PropertyState.blocked("Url missing"),
            github=PropertyState.present(github, source="source") if github else PropertyState.blocked("Github missing"),
            stars=PropertyState.present(stars, source="source") if stars not in ("", None) else PropertyState.blocked("Stars missing"),
            created=PropertyState.present(created, source="source") if created else PropertyState.blocked("Created missing"),
            about=PropertyState.present(about, source="source") if about else PropertyState.blocked("About missing"),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_property_model.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_property_model.py src/shared/property_model.py
git commit -m "feat: add property state model"
```

### Task 2: Add Shared Repo Metadata Fetch And Durable Created Cache

**Files:**
- Create: `src/shared/repo_metadata_cache.py`
- Modify: `src/shared/github.py`
- Modify: `src/shared/runtime.py`
- Test: `tests/test_shared_services.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.shared.github import GitHubClient
from src.shared.repo_metadata_cache import RepoMetadataCacheStore


@pytest.mark.anyio
async def test_github_client_fetches_repo_metadata_with_created_and_about():
    session = FakeSession(
        payload={"stargazers_count": 123, "created_at": "2024-01-01T00:00:00Z", "description": "repo"}
    )
    client = GitHubClient(session=session)

    metadata, error = await client.get_repo_metadata("foo", "bar")

    assert error is None
    assert metadata.stars == 123
    assert metadata.created == "2024-01-01T00:00:00Z"
    assert metadata.about == "repo"


def test_repo_metadata_cache_store_round_trips_created_value(tmp_path):
    store = RepoMetadataCacheStore(tmp_path / "cache.db")
    store.record_created("https://github.com/foo/bar", "2024-01-01T00:00:00Z")

    entry = store.get("https://github.com/foo/bar")

    assert entry is not None
    assert entry.github_url == "https://github.com/foo/bar"
    assert entry.created == "2024-01-01T00:00:00Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_shared_services.py::test_github_client_fetches_repo_metadata_with_created_and_about tests/test_main.py -q`
Expected: FAIL because `get_repo_metadata()` and `RepoMetadataCacheStore` do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RepoMetadata:
    github_url: str
    stars: int | None
    created: str
    about: str


async def get_repo_metadata(self, owner: str, repo: str) -> tuple[RepoMetadata | None, str | None]:
    payload, error = await self._fetch_repo_payload(owner, repo)
    if error:
        return None, error

    return (
        RepoMetadata(
            github_url=f"https://github.com/{owner}/{repo}",
            stars=payload.get("stargazers_count"),
            created=str(payload.get("created_at") or "").strip(),
            about=str(payload.get("description") or ""),
        ),
        None,
    )


async def get_star_count(self, owner: str, repo: str) -> tuple[int | None, str | None]:
    metadata, error = await self.get_repo_metadata(owner, repo)
    return (None if metadata is None else metadata.stars, error)
```

```python
class RepoMetadataCacheStore:
    def get(self, github_url: str) -> RepoMetadataCacheEntry | None:
        row = self._fetch_row(github_url)
        if row is None:
            return None
        return RepoMetadataCacheEntry(github_url=row["github_url"], created=row["created"])

    def record_created(self, github_url: str, created: str) -> None:
        self._upsert_created(github_url=github_url, created=created)
```

```python
@dataclass(frozen=True)
class RuntimeClients:
    session: object
    repo_cache: RepoCacheStore
    relation_resolution_cache: RelationResolutionCacheStore | None
    repo_metadata_cache: RepoMetadataCacheStore | None
    discovery_client: object
    github_client: object
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_shared_services.py tests/test_main.py -q`
Expected: PASS, including runtime tests that the new cache opens/closes with shared runtime clients

- [ ] **Step 5: Commit**

```bash
git add tests/test_shared_services.py tests/test_main.py src/shared/repo_metadata_cache.py src/shared/github.py src/shared/runtime.py
git commit -m "feat: add shared repo metadata cache"
```

### Task 3: Split Shared Acquisition From Repo Metadata Resolution

**Files:**
- Create: `src/shared/property_resolvers.py`
- Modify: `src/shared/paper_enrichment.py`
- Test: `tests/test_property_resolvers.py`
- Test: `tests/test_paper_enrichment.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.shared.property_resolvers import acquire_github_property, resolve_repo_metadata_properties


@pytest.mark.anyio
async def test_acquire_github_property_uses_existing_then_url_then_name():
    result = await acquire_github_property(
        existing_github_url="https://github.com/foo/bar",
        raw_url="https://doi.org/10.1145/example",
        name="Paper A",
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        arxiv_client=None,
        semanticscholar_graph_client=None,
        crossref_client=None,
        datacite_client=None,
        relation_resolution_cache=None,
        allow_title_search=True,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.github_source == "existing"


@pytest.mark.anyio
async def test_resolve_repo_metadata_properties_returns_partial_failure_without_erasing_github():
    github_client = SimpleNamespace(get_repo_metadata=AsyncMock(return_value=(None, "GitHub API error (500)")))

    result = await resolve_repo_metadata_properties(
        github_url="https://github.com/foo/bar",
        github_client=github_client,
        repo_metadata_cache=None,
    )

    assert result.github_url == "https://github.com/foo/bar"
    assert result.reason == "GitHub API error (500)"
    assert result.stars is None
    assert result.created is None
    assert result.about is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_property_resolvers.py tests/test_paper_enrichment.py -q`
Expected: FAIL because `property_resolvers.py` does not exist and `paper_enrichment` still exposes only the monolithic contract

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class GithubAcquisitionResult:
    github_url: str | None
    github_source: str | None
    normalized_url: str | None
    canonical_arxiv_url: str | None
    reason: str | None


@dataclass(frozen=True)
class RepoMetadataResolutionResult:
    github_url: str
    stars: int | None
    created: str | None
    about: str | None
    reason: str | None
```

```python
async def process_single_paper(
    request: PaperEnrichmentRequest,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
):
    acquisition = await acquire_github_property(
        existing_github_url=request.existing_github_url,
        raw_url=request.raw_url,
        name=request.title,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        allow_title_search=request.allow_title_search,
        allow_github_discovery=request.allow_github_discovery,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    if acquisition.reason is not None:
        return PaperEnrichmentResult(
            title=request.title,
            raw_url=request.raw_url,
            normalized_url=acquisition.normalized_url,
            canonical_arxiv_url=acquisition.canonical_arxiv_url,
            github_url=acquisition.github_url,
            github_source=acquisition.github_source,
            stars=None,
            reason=acquisition.reason,
        )

    metadata = await resolve_repo_metadata_properties(
        github_url=acquisition.github_url,
        github_client=github_client,
        repo_metadata_cache=None,
    )
    return PaperEnrichmentResult(
        title=request.title,
        raw_url=request.raw_url,
        normalized_url=acquisition.normalized_url,
        canonical_arxiv_url=acquisition.canonical_arxiv_url,
        github_url=metadata.github_url,
        github_source=acquisition.github_source,
        stars=metadata.stars,
        created=metadata.created,
        about=metadata.about,
        reason=metadata.reason,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_property_resolvers.py tests/test_paper_enrichment.py -q`
Expected: PASS, including existing `paper_enrichment` compatibility tests updated to assert the new `created/about` fields

- [ ] **Step 5: Commit**

```bash
git add tests/test_property_resolvers.py tests/test_paper_enrichment.py src/shared/property_resolvers.py src/shared/paper_enrichment.py
git commit -m "refactor: split github acquisition from metadata"
```

### Task 4: Migrate Shared Fresh Export Paths

**Files:**
- Modify: `src/shared/paper_export.py`
- Modify: `src/url_to_csv/pipeline.py`
- Modify: `src/arxiv_relations/pipeline.py`
- Test: `tests/test_paper_export.py`
- Test: `tests/test_url_to_csv.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.anyio
async def test_build_paper_outcome_maps_created_and_about_from_shared_metadata(monkeypatch):
    async def fake_process_single_paper(request, **kwargs):
        return SimpleNamespace(
            title=request.title,
            raw_url=request.raw_url,
            normalized_url="https://arxiv.org/abs/2501.00001",
            canonical_arxiv_url="https://arxiv.org/abs/2501.00001",
            github_url="https://github.com/foo/bar",
            github_source="discovered",
            stars=12,
            created="2024-01-01T00:00:00Z",
            about="repo",
            reason=None,
        )

    monkeypatch.setattr(paper_export, "process_single_paper", fake_process_single_paper)

    outcome = await paper_export.build_paper_outcome(
        1,
        PaperSeed(name="Paper A", url="https://arxiv.org/abs/2501.00001"),
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
    )

    assert outcome.record.created == "2024-01-01T00:00:00Z"
    assert outcome.record.about == "repo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_paper_export.py tests/test_url_to_csv.py tests/test_arxiv_relations.py -q`
Expected: FAIL because paper-family fresh export still hardcodes empty `Created/About`

- [ ] **Step 3: Write minimal implementation**

```python
return PaperOutcome(
    index=index,
    record=CsvRow(
        name=enrichment.title,
        url=enrichment.normalized_url or enrichment.raw_url or "",
        github=enrichment.github_url or "",
        stars=enrichment.stars if enrichment.reason is None else "",
        created=enrichment.created or "",
        about=enrichment.about or "",
        sort_index=index,
    ),
    reason=enrichment.reason,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_paper_export.py tests/test_url_to_csv.py tests/test_arxiv_relations.py -q`
Expected: PASS, with paper-family fresh exports now carrying `Created/About`

- [ ] **Step 5: Commit**

```bash
git add tests/test_paper_export.py tests/test_url_to_csv.py tests/test_arxiv_relations.py src/shared/paper_export.py src/url_to_csv/pipeline.py src/arxiv_relations/pipeline.py
git commit -m "feat: populate repo metadata in fresh exports"
```

### Task 5: Refactor CSV Update Around Property Policies

**Files:**
- Create: `src/shared/csv_schema.py`
- Modify: `src/csv_update/pipeline.py`
- Modify: `src/shared/csv_io.py`
- Test: `tests/test_csv_update.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.anyio
async def test_update_csv_file_appends_missing_repo_metadata_columns_without_reordering_existing_columns(tmp_path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text("Url,Name\\nhttps://arxiv.org/abs/2501.12345,Paper A\\n", encoding="utf-8")

    result = await update_csv_file(
        csv_path,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(metadata=("https://github.com/foo/bar", 7, "2024-01-01T00:00:00Z", "repo")),
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert reader.fieldnames == ["Url", "Name", "Github", "Stars", "Created", "About"]
    assert rows[0]["Stars"] == "7"
    assert rows[0]["Created"] == "2024-01-01T00:00:00Z"
    assert rows[0]["About"] == "repo"


@pytest.mark.anyio
async def test_update_csv_file_overwrites_about_even_when_remote_description_is_empty(tmp_path):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "Name,Github,Stars,Created,About\\nPaper A,https://github.com/foo/bar,1,2024-01-01T00:00:00Z,old about\\n",
        encoding="utf-8",
    )

    await update_csv_file(
        csv_path,
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=FakeGitHubClient(metadata=("https://github.com/foo/bar", 7, "2024-01-01T00:00:00Z", "")),
        content_cache=FakeContentCache(),
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))

    assert row["Stars"] == "7"
    assert row["Created"] == "2024-01-01T00:00:00Z"
    assert row["About"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_csv_update.py -q`
Expected: FAIL because CSV update only manages `Github/Stars` and does not append `Created/About`

- [ ] **Step 3: Write minimal implementation**

```python
CANONICAL_PROPERTY_COLUMNS = ["Name", "Url", "Github", "Stars", "Created", "About"]


def append_missing_property_columns(existing_columns: list[str], required_columns: list[str]) -> list[str]:
    output = list(existing_columns)
    for column in CANONICAL_PROPERTY_COLUMNS:
        if column in required_columns and column not in output:
            output.append(column)
    return output
```

```python
if metadata.reason is None and metadata.stars is not None:
    updated_row["Stars"] = str(metadata.stars)

if metadata.reason is None:
    updated_row["About"] = metadata.about or ""
    if not (updated_row.get("Created") or "").strip() and metadata.created:
        updated_row["Created"] = metadata.created
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_csv_update.py -q`
Expected: PASS, including append-order, overwrite-to-empty, and backfill-only semantics

- [ ] **Step 5: Commit**

```bash
git add tests/test_csv_update.py src/shared/csv_schema.py src/shared/csv_io.py src/csv_update/pipeline.py
git commit -m "feat: apply property-aware csv update policies"
```

### Task 6: Migrate Notion Schema And Property Writes

**Files:**
- Modify: `src/notion_sync/notion_client.py`
- Modify: `src/notion_sync/pipeline.py`
- Modify: `src/notion_sync/runner.py`
- Test: `tests/test_notion_mode.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.anyio
async def test_ensure_sync_properties_adds_created_and_about_with_expected_types():
    client = NotionClient("token", max_concurrent=1)
    client.client = types.SimpleNamespace(
        data_sources=types.SimpleNamespace(
            retrieve=AsyncMock(return_value={"properties": {"Name": {"type": "title", "title": {}}}}),
            update=AsyncMock(return_value={"ok": True}),
        )
    )

    await client.ensure_sync_properties("data-source-1")

    client.client.data_sources.update.assert_awaited_once_with(
        data_source_id="data-source-1",
        properties={
            "Github": {"type": "url", "url": {}},
            "Stars": {"type": "number", "number": {"format": "number"}},
            "Created": {"type": "date", "date": {}},
            "About": {"type": "rich_text", "rich_text": {}},
        },
    )


@pytest.mark.anyio
async def test_process_page_hard_fails_when_existing_created_property_has_wrong_type():
    page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Paper A"}]},
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Created": {"type": "rich_text", "rich_text": []},
        },
    }

    results = {"updated": 0, "skipped": []}
    await process_page(
        page,
        1,
        1,
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=SimpleNamespace(get_star_count=AsyncMock(return_value=(7, None))),
        notion_client=SimpleNamespace(update_page_properties=AsyncMock()),
        results=results,
        lock=asyncio.Lock(),
    )

    assert results["updated"] == 0
    assert results["skipped"][0]["reason"] == "Notion property Created must have type date"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_notion_mode.py -q`
Expected: FAIL because Notion only provisions/writes `Github` and `Stars`

- [ ] **Step 3: Write minimal implementation**

```python
MANAGED_NOTION_PROPERTIES = {
    "Github": ("url", {"url": {}}),
    "Stars": ("number", {"number": {"format": "number"}}),
    "Created": ("date", {"date": {}}),
    "About": ("rich_text", {"rich_text": {}}),
}
```

```python
async def update_page_properties(
    self,
    page_id: str,
    *,
    github_url: str | None = None,
    stars_count: int | None = None,
    created_value: str | None = None,
    about_text: str | None = None,
    github_property_type: str = "url",
):
    properties = {}
    if stars_count is not None:
        properties["Stars"] = {"number": stars_count}
    if created_value is not None:
        properties["Created"] = {"date": {"start": created_value}}
    if about_text is not None:
        properties["About"] = {"rich_text": [{"type": "text", "text": {"content": about_text}}]} if about_text else {"rich_text": []}
```

```python
if property_exists_with_wrong_type(properties, "Created", expected_type="date"):
    return None, False, "Notion property Created must have type date"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_notion_mode.py -q`
Expected: PASS, including new schema creation and hard-error coverage

- [ ] **Step 5: Commit**

```bash
git add tests/test_notion_mode.py src/notion_sync/notion_client.py src/notion_sync/pipeline.py src/notion_sync/runner.py
git commit -m "feat: add notion property metadata sync"
```

### Task 7: Clean Up Legacy Helpers, Update Docs, And Run Broad Regression

**Files:**
- Modify: `src/shared/csv_io.py`
- Modify: `src/shared/papers.py`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Test: `tests/test_csv_io.py`
- Test: `tests/test_shared_papers.py`
- Test: `tests/test_main.py`
- Test: `tests/test_github_search_to_csv.py`
- Test: `tests/test_url_to_csv.py`
- Test: `tests/test_arxiv_relations.py`
- Test: `tests/test_csv_update.py`
- Test: `tests/test_notion_mode.py`
- Test: `tests/test_paper_enrichment.py`
- Test: `tests/test_paper_export.py`
- Test: `tests/test_property_model.py`
- Test: `tests/test_property_resolvers.py`

- [ ] **Step 1: Write the failing cleanup/regression tests**

```python
def test_write_records_to_csv_path_is_no_longer_the_preferred_shared_export_shape(tmp_path):
    csv_path = tmp_path / "papers.csv"
    rows = [CsvRow(name="Paper A", url="https://arxiv.org/abs/2501.12345", github="https://github.com/foo/bar", stars=7, created="2024-01-01T00:00:00Z", about="repo")]

    write_rows_to_csv_path(rows, csv_path)

    assert csv_path.read_text(encoding="utf-8").splitlines()[0] == "Name,Url,Github,Stars,Created,About"
```

- [ ] **Step 2: Run targeted regression to verify current gaps**

Run: `uv run python -m pytest tests/test_csv_io.py tests/test_shared_papers.py tests/test_paper_enrichment.py tests/test_paper_export.py tests/test_url_to_csv.py tests/test_arxiv_relations.py tests/test_csv_update.py tests/test_notion_mode.py tests/test_main.py tests/test_github_search_to_csv.py tests/test_property_model.py tests/test_property_resolvers.py -q`
Expected: Any remaining failures should now point to stale four-column assumptions, outdated docs assertions, or compatibility mismatches

- [ ] **Step 3: Write minimal cleanup and docs implementation**

```python
CSV_HEADERS = ["Name", "Url", "Github", "Stars", "Created", "About"]


def write_records_to_csv_path(records: list[PaperRecord], csv_path: Path) -> Path:
    rows = [
        CsvRow(
            name=record.name,
            url=record.url,
            github=record.github,
            stars=record.stars,
            created="",
            about="",
            sort_index=record.sort_index,
        )
        for record in records
    ]
    return write_rows_to_csv_path(rows, csv_path)
```

```markdown
- `url_to_csv` / `arxiv_relations` now populate `Created` / `About` through shared repo metadata resolution
- `csv_update` always refreshes `Stars` and `About`, and only backfills `Created`
- Notion auto-provisions `Github`, `Stars`, `Created`, and `About` when missing, but hard-fails on wrong property types
```

- [ ] **Step 4: Run broad regression**

Run: `uv run python -m pytest -q`
Expected: PASS across the full suite

- [ ] **Step 5: Commit**

```bash
git add src/shared/csv_io.py src/shared/papers.py README.md ARCHITECTURE.md tests/test_csv_io.py tests/test_shared_papers.py tests/test_main.py tests/test_github_search_to_csv.py tests/test_url_to_csv.py tests/test_arxiv_relations.py tests/test_csv_update.py tests/test_notion_mode.py tests/test_paper_enrichment.py tests/test_paper_export.py tests/test_property_model.py tests/test_property_resolvers.py
git commit -m "refactor: finish property-centric metadata sync"
```

## Self-Review

**Spec coverage**

- Shared property-centric core: covered by Tasks 1 and 3.
- Shared `Github URL -> repo metadata`: covered by Task 2.
- Fresh export adoption for `Created/About`: covered by Task 4.
- CSV append-without-reorder and per-property write policies: covered by Task 5.
- Notion schema creation and hard type errors: covered by Task 6.
- Supporting contracts, cleanup, and full regression: covered by Task 7.

**Placeholder scan**

- No `TODO` / `TBD` placeholders remain.
- Every task contains explicit files, test code, commands, and commit messages.

**Type consistency**

- Core model names stay consistent across tasks:
  - `PropertyState`
  - `PropertyStatus`
  - `RecordState`
  - `GithubAcquisitionResult`
  - `RepoMetadataResolutionResult`
  - `RepoMetadataCacheStore`
