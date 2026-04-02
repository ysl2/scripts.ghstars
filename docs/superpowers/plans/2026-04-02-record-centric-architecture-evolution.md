# Record-Centric Architecture Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve `scripts.ghstars` from thin mode-oriented pipelines plus shared helpers into a record-centric architecture with a real domain object, explicit sync services, adapter boundaries, and repository wrappers, while preserving the single-CLI product contract.

**Architecture:** Introduce a new `src/core/` package as the architectural center. Keep existing `runner/pipeline` modules as thin shells and keep `src/shared/*` compatibility wrappers during the migration. Move the fresh-export families onto the new `Record` flow first, then migrate CSV/Notion updates, and only after that reshape cache/runtime boundaries into explicit repositories.

**Tech Stack:** Python 3.12, `aiohttp`, SQLite, `pytest`, existing shared arXiv/GitHub/discovery helpers, Notion API client

---

## Scope Check

This spec is broad, but the work is still one coherent refactor rather than several unrelated projects. The phases are tightly coupled:

- the `Record` object has to exist before adapters can converge on it
- shared sync services have to exist before CSV and Notion can stop owning business rules
- repository wrappers are safest after the domain/service boundaries stabilize

So this should stay one plan with ordered phases, not be split into unrelated plan files.

## File Structure

**Create**

- `src/core/__init__.py`
  - Stable import surface for the new architecture center.
- `src/core/record_model.py`
  - `PropertyStatus`, `PropertyState`, `Record`, `RecordFacts`, `RecordArtifacts`, and `RecordContext`.
- `src/core/record_sync.py`
  - `GithubAcquisitionService`, `RepoMetadataSyncService`, `PropertyPolicyService`, and `RecordSyncService`.
- `src/core/input_adapters.py`
  - `PaperSeedInputAdapter`, `GithubSearchInputAdapter`, `CsvRowInputAdapter`, and `NotionPageInputAdapter`.
- `src/core/output_adapters.py`
  - `FreshCsvExportAdapter`, `CsvUpdateAdapter`, and `NotionUpdateAdapter`.
- `src/core/repositories.py`
  - Repository wrappers over durable cache/store access.
- `tests/test_record_model.py`
  - Focused unit coverage for the new immutable record model.
- `tests/test_record_sync.py`
  - Focused coverage for shared sync services and trusted-input behavior.
- `tests/test_input_adapters.py`
  - Coverage for converting current source objects into `Record`.
- `tests/test_output_adapters.py`
  - Coverage for converting `Record` back into CSV/Notion writes.
- `tests/test_repositories.py`
  - Coverage for the repository wrappers and durable-fact semantics.

**Modify**

- `src/shared/property_model.py`
  - Turn into a compatibility shim over `src/core/record_model.py`.
- `src/shared/property_resolvers.py`
  - Turn into a compatibility shim over `src/core/record_sync.py`.
- `src/shared/paper_enrichment.py`
  - Keep the old request/result API, but route it through `RecordSyncService`.
- `src/shared/paper_export.py`
  - Build `Record` objects from paper seeds and export them through `FreshCsvExportAdapter`.
- `src/shared/csv_io.py`
  - Share the final CSV row serialization path with `FreshCsvExportAdapter`.
- `src/shared/csv_schema.py`
  - Keep canonical header constants and shared append-order helpers for adapters.
- `src/shared/runtime.py`
  - Build and expose repository wrappers alongside existing runtime clients.
- `src/shared/github.py`
  - Stay as the low-level GitHub client used by the new repositories/services.
- `src/shared/repo_metadata_cache.py`
  - Stay as the low-level SQLite store used by `RepoMetadataRepository`.
- `src/url_to_csv/pipeline.py`
  - Replace direct paper-export coupling with input-adapter + sync + output-adapter orchestration.
- `src/arxiv_relations/pipeline.py`
  - Same migration pattern as `url_to_csv`.
- `src/github_search_to_csv/pipeline.py`
  - Convert collected repo rows into trusted `Record` objects and write through `FreshCsvExportAdapter`.
- `src/csv_update/pipeline.py`
  - Replace row-local mutation rules with `CsvRowInputAdapter` + `RecordSyncService` + `CsvUpdateAdapter`.
- `src/notion_sync/pipeline.py`
  - Replace page-local write logic with `NotionPageInputAdapter` + `RecordSyncService` + `NotionUpdateAdapter`.
- `src/notion_sync/notion_client.py`
  - Accept adapter-built property patches instead of mode-local field branching.
- `src/notion_sync/runner.py`
  - Keep schema validation entrypoints, but wire them through the new output adapter.
- `src/app.py`
  - Keep the same CLI contract, but express routing as input-shape detection instead of five mode branches.
- `README.md`
  - Document the record-centric core and unchanged user-facing CLI.
- `ARCHITECTURE.md`
  - Reframe maintainers docs around `src/core/*`, thin shells, adapters, and repositories.

**Test**

- `tests/test_property_model.py`
- `tests/test_property_resolvers.py`
- `tests/test_paper_enrichment.py`
- `tests/test_paper_export.py`
- `tests/test_url_to_csv.py`
- `tests/test_arxiv_relations.py`
- `tests/test_github_search_to_csv.py`
- `tests/test_csv_update.py`
- `tests/test_notion_mode.py`
- `tests/test_csv_io.py`
- `tests/test_main.py`

### Task 1: Introduce The Core Record Model

**Files:**
- Create: `src/core/__init__.py`
- Create: `src/core/record_model.py`
- Create: `tests/test_record_model.py`
- Modify: `src/shared/property_model.py`
- Test: `tests/test_property_model.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.core.record_model import (
    PropertyState,
    PropertyStatus,
    Record,
    RecordArtifacts,
    RecordContext,
    RecordFacts,
)


def test_record_with_property_returns_new_record_without_mutating_original():
    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345",
        github="https://github.com/foo/bar",
        source="csv",
    )

    updated = record.with_property(
        "stars",
        PropertyState.resolved(42, source="github_api", trusted=True),
    )

    assert record.stars.value is None
    assert updated.stars.value == 42
    assert updated.github.value == "https://github.com/foo/bar"


def test_record_can_attach_facts_artifacts_and_context_without_promoting_them_to_core_properties():
    record = Record.from_source(name="Paper A", source="url_to_csv").with_supporting_state(
        facts=RecordFacts(canonical_arxiv_url="https://arxiv.org/abs/2501.12345"),
        artifacts=RecordArtifacts(overview_path="cache/overview/2501.12345.md"),
        context=RecordContext(csv_row_index=7),
    )

    assert record.facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert record.artifacts.overview_path.endswith("2501.12345.md")
    assert record.context.csv_row_index == 7


def test_property_state_supports_explicit_empty_string_values_for_about_sync():
    state = PropertyState.resolved("", source="github_api", trusted=True)

    assert state.status is PropertyStatus.RESOLVED
    assert state.value == ""
    assert state.trusted is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_record_model.py tests/test_property_model.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.record_model'`

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass, replace
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
    trusted: bool = False
    reason: str | None = None

    @classmethod
    def present(cls, value, *, source: str, trusted: bool = False):
        if value is None:
            raise ValueError("present state requires a value")
        return cls(value=value, status=PropertyStatus.PRESENT, source=source, trusted=trusted)

    @classmethod
    def resolved(cls, value, *, source: str, trusted: bool = False):
        if value is None:
            raise ValueError("resolved state requires a value")
        return cls(value=value, status=PropertyStatus.RESOLVED, source=source, trusted=trusted)

    @classmethod
    def blocked(cls, reason: str, *, source: str | None = None):
        return cls(value=None, status=PropertyStatus.BLOCKED, source=source, reason=reason)


@dataclass(frozen=True)
class RecordFacts:
    canonical_arxiv_url: str | None = None
    normalized_url: str | None = None
    github_source: str | None = None


@dataclass(frozen=True)
class RecordArtifacts:
    overview_path: str | None = None
    abs_path: str | None = None


@dataclass(frozen=True)
class RecordContext:
    csv_row_index: int | None = None
    notion_page_id: str | None = None


@dataclass(frozen=True)
class Record:
    name: PropertyState
    url: PropertyState
    github: PropertyState
    stars: PropertyState
    created: PropertyState
    about: PropertyState
    facts: RecordFacts = RecordFacts()
    artifacts: RecordArtifacts = RecordArtifacts()
    context: RecordContext = RecordContext()

    @classmethod
    def from_source(cls, *, source: str, trusted_fields: set[str] | None = None, **values):
        trusted_fields = trusted_fields or set()

        def seed(field_name: str):
            value = values.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                return PropertyState.blocked(f"{field_name} missing from source", source=source)
            return PropertyState.present(value, source=source, trusted=field_name in trusted_fields)

        return cls(
            name=seed("name"),
            url=seed("url"),
            github=seed("github"),
            stars=seed("stars"),
            created=seed("created"),
            about=seed("about"),
        )

    def with_property(self, property_name: str, state: PropertyState) -> "Record":
        return replace(self, **{property_name: state})

    def with_supporting_state(
        self,
        *,
        facts: RecordFacts | None = None,
        artifacts: RecordArtifacts | None = None,
        context: RecordContext | None = None,
    ) -> "Record":
        return replace(
            self,
            facts=self.facts if facts is None else facts,
            artifacts=self.artifacts if artifacts is None else artifacts,
            context=self.context if context is None else context,
        )
```

```python
from src.core.record_model import (
    PropertyState,
    PropertyStatus,
    Record,
    RecordArtifacts,
    RecordContext,
    RecordFacts,
)

RecordState = Record

__all__ = [
    "PropertyState",
    "PropertyStatus",
    "Record",
    "RecordState",
    "RecordFacts",
    "RecordArtifacts",
    "RecordContext",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_record_model.py tests/test_property_model.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_record_model.py tests/test_property_model.py src/core/__init__.py src/core/record_model.py src/shared/property_model.py
git commit -m "refactor: add record domain model"
```

### Task 2: Add Shared Record Sync Services And Compatibility Wrappers

**Files:**
- Create: `src/core/record_sync.py`
- Create: `tests/test_record_sync.py`
- Modify: `src/shared/property_resolvers.py`
- Modify: `src/shared/paper_enrichment.py`
- Modify: `tests/test_property_resolvers.py`
- Modify: `tests/test_paper_enrichment.py`

- [ ] **Step 1: Write the failing tests**

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.record_model import PropertyStatus, Record
from src.core.record_sync import RecordSyncService


@pytest.mark.anyio
async def test_record_sync_discovers_github_and_repo_metadata_for_paper_like_record():
    service = RecordSyncService(
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock(return_value="https://github.com/foo/bar")),
        github_client=SimpleNamespace(
            get_repo_metadata=AsyncMock(
                return_value=(SimpleNamespace(stars=12, created="2020-01-01T00:00:00Z", about="repo"), None)
            )
        ),
    )
    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345",
        source="url_to_csv",
    )

    updated = await service.sync(record, allow_title_search=True, allow_github_discovery=True)

    assert updated.github.value == "https://github.com/foo/bar"
    assert updated.stars.value == 12
    assert updated.created.value == "2020-01-01T00:00:00Z"
    assert updated.about.value == "repo"


@pytest.mark.anyio
async def test_record_sync_skips_repo_metadata_fetch_for_trusted_github_search_record():
    github_client = SimpleNamespace(get_repo_metadata=AsyncMock())
    service = RecordSyncService(
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=github_client,
    )
    record = Record.from_source(
        github="https://github.com/foo/bar",
        stars=99,
        created="2020-01-01T00:00:00Z",
        about="repo",
        source="github_search",
        trusted_fields={"github", "stars", "created", "about"},
    )

    updated = await service.sync(record, allow_title_search=False, allow_github_discovery=False)

    assert updated.github.trusted is True
    assert updated.stars.status is PropertyStatus.PRESENT
    github_client.get_repo_metadata.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_record_sync.py tests/test_property_resolvers.py tests/test_paper_enrichment.py -q`
Expected: FAIL because `src.core.record_sync` does not exist and current wrappers do not delegate through a record service

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import replace
from types import SimpleNamespace

from src.core.record_model import PropertyState, Record, RecordFacts
from src.shared.arxiv_url_resolution import resolve_arxiv_url
from src.shared.discovery import resolve_github_url
from src.shared.github import extract_owner_repo, normalize_github_url


class PropertyPolicyService:
    def should_refresh_repo_metadata(self, record: Record) -> bool:
        return not (
            record.github.trusted
            and record.stars.trusted
            and record.created.trusted
            and record.about.trusted
        )


class RecordSyncService:
    def __init__(self, *, discovery_client, github_client, policy: PropertyPolicyService | None = None):
        self.discovery_client = discovery_client
        self.github_client = github_client
        self.policy = policy or PropertyPolicyService()

    async def sync(self, record: Record, *, allow_title_search: bool, allow_github_discovery: bool) -> Record:
        acquisition = await self.acquire_github(
            record,
            allow_title_search=allow_title_search,
            allow_github_discovery=allow_github_discovery,
        )
        record = record.with_supporting_state(
            facts=RecordFacts(
                canonical_arxiv_url=acquisition.canonical_arxiv_url,
                normalized_url=acquisition.normalized_url,
                github_source=acquisition.github_source,
            )
        )
        if acquisition.github_url:
            record = record.with_property(
                "github",
                PropertyState.resolved(
                    acquisition.github_url,
                    source=acquisition.github_source or "github_acquisition",
                    trusted=record.github.trusted,
                ),
            )

        if not acquisition.github_url or not self.policy.should_refresh_repo_metadata(record):
            return record

        metadata = await self.resolve_repo_metadata(acquisition.github_url)
        if metadata.reason is None and metadata.stars is not None:
            record = record.with_property("stars", PropertyState.resolved(metadata.stars, source="github_api"))
        if metadata.reason is None and metadata.created is not None:
            record = record.with_property("created", PropertyState.resolved(metadata.created, source="github_api"))
        if metadata.reason is None and metadata.about is not None:
            record = record.with_property("about", PropertyState.resolved(metadata.about, source="github_api"))
        return record

    async def acquire_github(self, record: Record, *, allow_title_search: bool, allow_github_discovery: bool):
        existing_github = record.github.value if isinstance(record.github.value, str) else None
        if existing_github:
            return SimpleNamespace(
                github_url=existing_github,
                github_source="existing",
                normalized_url=None,
                canonical_arxiv_url=None,
            )

        raw_url = record.url.value if isinstance(record.url.value, str) else ""
        normalized = await resolve_arxiv_url(
            record.name.value if isinstance(record.name.value, str) else "",
            raw_url,
            discovery_client=self.discovery_client,
            allow_title_search=allow_title_search,
        )
        github_url = None
        if normalized.canonical_arxiv_url and allow_github_discovery:
            github_url = await resolve_github_url(
                SimpleNamespace(
                    name=record.name.value if isinstance(record.name.value, str) else "",
                    url=normalized.canonical_arxiv_url,
                ),
                self.discovery_client,
            )
        return SimpleNamespace(
            github_url=normalize_github_url(github_url) if github_url else None,
            github_source="discovered" if github_url else None,
            normalized_url=normalized.resolved_url,
            canonical_arxiv_url=normalized.canonical_arxiv_url,
        )

    async def resolve_repo_metadata(self, github_url: str):
        owner_repo = extract_owner_repo(github_url)
        if owner_repo is None:
            return SimpleNamespace(stars=None, created=None, about=None, reason="GitHub URL is not a valid GitHub repository")
        metadata, error = await self.github_client.get_repo_metadata(*owner_repo)
        if error is not None or metadata is None:
            return SimpleNamespace(stars=None, created=None, about=None, reason=error or "GitHub client returned no repo metadata")
        return SimpleNamespace(stars=metadata.stars, created=metadata.created, about=metadata.about, reason=None)
```

```python
from src.core.record_model import Record
from src.core.record_sync import RecordSyncService


async def process_single_paper(
    request,
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
    record = Record.from_source(
        name=request.title,
        url=request.raw_url,
        github=request.existing_github_url,
        source="paper_enrichment",
        trusted_fields={"github"} if request.trust_existing_github else set(),
    )
    service = RecordSyncService(discovery_client=discovery_client, github_client=github_client)
    updated = await service.sync(
        record,
        allow_title_search=request.allow_title_search,
        allow_github_discovery=request.allow_github_discovery,
    )
    return PaperEnrichmentResult(
        title=request.title,
        raw_url=request.raw_url,
        normalized_url=updated.url.value if isinstance(updated.url.value, str) else None,
        canonical_arxiv_url=updated.facts.canonical_arxiv_url,
        github_url=updated.github.value if isinstance(updated.github.value, str) else None,
        github_source=updated.facts.github_source,
        stars=updated.stars.value if isinstance(updated.stars.value, int) else None,
        created=updated.created.value if isinstance(updated.created.value, str) else None,
        about=updated.about.value if isinstance(updated.about.value, str) else None,
        reason=updated.github.reason or updated.stars.reason,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_record_sync.py tests/test_property_resolvers.py tests/test_paper_enrichment.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_record_sync.py tests/test_property_resolvers.py tests/test_paper_enrichment.py src/core/record_sync.py src/shared/property_resolvers.py src/shared/paper_enrichment.py
git commit -m "refactor: add record sync services"
```

### Task 3: Add Input Adapters For Paper Seeds, GitHub Search, CSV Rows, And Notion Pages

**Files:**
- Create: `src/core/input_adapters.py`
- Create: `tests/test_input_adapters.py`
- Modify: `src/github_search_to_csv/pipeline.py`
- Modify: `src/url_to_csv/pipeline.py`
- Modify: `src/arxiv_relations/pipeline.py`
- Modify: `src/csv_update/pipeline.py`
- Modify: `src/notion_sync/pipeline.py`
- Modify: `tests/test_github_search_to_csv.py`
- Modify: `tests/test_url_to_csv.py`
- Modify: `tests/test_arxiv_relations.py`
- Modify: `tests/test_csv_update.py`
- Modify: `tests/test_notion_mode.py`

- [ ] **Step 1: Write the failing tests**

```python
from types import SimpleNamespace

from src.core.input_adapters import GithubSearchInputAdapter, PaperSeedInputAdapter
from src.shared.papers import PaperSeed


def test_github_search_input_adapter_marks_repo_side_values_as_trusted():
    record = GithubSearchInputAdapter().to_record(
        SimpleNamespace(
            github="https://github.com/foo/bar",
            stars=99,
            created="2020-01-01T00:00:00Z",
            about="repo",
        )
    )

    assert record.github.trusted is True
    assert record.stars.trusted is True
    assert record.created.trusted is True
    assert record.about.trusted is True


def test_paper_seed_input_adapter_keeps_name_and_url_as_source_values():
    record = PaperSeedInputAdapter().to_record(PaperSeed(name="Paper A", url="https://arxiv.org/abs/2501.12345"))

    assert record.name.value == "Paper A"
    assert record.url.value == "https://arxiv.org/abs/2501.12345"
    assert record.github.value is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_input_adapters.py tests/test_github_search_to_csv.py tests/test_url_to_csv.py tests/test_arxiv_relations.py tests/test_csv_update.py tests/test_notion_mode.py -q`
Expected: FAIL because `src.core.input_adapters` does not exist and current pipelines still build rows/pages directly

- [ ] **Step 3: Write minimal implementation**

```python
from src.core.record_model import Record, RecordContext


class PaperSeedInputAdapter:
    def to_record(self, seed) -> Record:
        return Record.from_source(name=seed.name, url=seed.url, source="paper_seed")


class GithubSearchInputAdapter:
    def to_record(self, row) -> Record:
        return Record.from_source(
            github=row.github,
            stars=row.stars,
            created=row.created,
            about=row.about,
            source="github_search",
            trusted_fields={"github", "stars", "created", "about"},
        )


class CsvRowInputAdapter:
    def to_record(self, index: int, row: dict[str, str]) -> Record:
        return Record.from_source(
            name=row.get("Name"),
            url=row.get("Url"),
            github=row.get("Github"),
            stars=row.get("Stars"),
            created=row.get("Created"),
            about=row.get("About"),
            source="csv",
        ).with_supporting_state(context=RecordContext(csv_row_index=index))


class NotionPageInputAdapter:
    def _get_current_about_text(self, page: dict) -> str | None:
        about_property = page.get("properties", {}).get("About", {})
        return get_text_from_property(about_property)

    def to_record(self, page: dict) -> Record:
        return Record.from_source(
            name=get_page_title(page),
            url=get_paper_url_from_page(page),
            github=get_github_url_from_page(page),
            stars=get_current_stars_from_page(page),
            created=get_current_created_from_page(page),
            about=self._get_current_about_text(page),
            source="notion",
            trusted_fields={"github"} if get_github_url_from_page(page) else set(),
        ).with_supporting_state(context=RecordContext(notion_page_id=page["id"]))
```

```python
records = [GithubSearchInputAdapter().to_record(row) for row in repositories]
records = [PaperSeedInputAdapter().to_record(seed) for seed in fetched.seeds]
record = CsvRowInputAdapter().to_record(index, row)
record = NotionPageInputAdapter().to_record(page)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_input_adapters.py tests/test_github_search_to_csv.py tests/test_url_to_csv.py tests/test_arxiv_relations.py tests/test_csv_update.py tests/test_notion_mode.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_input_adapters.py tests/test_github_search_to_csv.py tests/test_url_to_csv.py tests/test_arxiv_relations.py tests/test_csv_update.py tests/test_notion_mode.py src/core/input_adapters.py src/github_search_to_csv/pipeline.py src/url_to_csv/pipeline.py src/arxiv_relations/pipeline.py src/csv_update/pipeline.py src/notion_sync/pipeline.py
git commit -m "refactor: add record input adapters"
```

### Task 4: Add The Shared Fresh CSV Output Adapter And Migrate Fresh Export Families

**Files:**
- Create: `src/core/output_adapters.py`
- Create: `tests/test_output_adapters.py`
- Modify: `src/shared/paper_export.py`
- Modify: `src/shared/csv_io.py`
- Modify: `src/github_search_to_csv/pipeline.py`
- Modify: `tests/test_paper_export.py`
- Modify: `tests/test_csv_io.py`
- Modify: `tests/test_github_search_to_csv.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.core.output_adapters import FreshCsvExportAdapter
from src.core.record_model import PropertyState, Record


def test_fresh_csv_export_adapter_serializes_record_into_shared_six_column_row():
    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345",
        github="https://github.com/foo/bar",
        stars=42,
        created="2020-01-01T00:00:00Z",
        about="repo",
        source="paper_export",
    )

    row = FreshCsvExportAdapter().to_csv_row(record, sort_index=3)

    assert row.name == "Paper A"
    assert row.url == "https://arxiv.org/abs/2501.12345"
    assert row.github == "https://github.com/foo/bar"
    assert row.stars == 42
    assert row.created == "2020-01-01T00:00:00Z"
    assert row.about == "repo"
    assert row.sort_index == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_output_adapters.py tests/test_paper_export.py tests/test_csv_io.py tests/test_github_search_to_csv.py -q`
Expected: FAIL because `FreshCsvExportAdapter` does not exist and fresh export code still serializes rows directly

- [ ] **Step 3: Write minimal implementation**

```python
from src.shared.csv_rows import CsvRow


class FreshCsvExportAdapter:
    def to_csv_row(self, record: Record, *, sort_index: int = 0) -> CsvRow:
        return CsvRow(
            name="" if record.name.value is None else str(record.name.value),
            url="" if record.url.value is None else str(record.url.value),
            github="" if record.github.value is None else str(record.github.value),
            stars="" if record.stars.value is None else record.stars.value,
            created="" if record.created.value is None else str(record.created.value),
            about="" if record.about.value is None else str(record.about.value),
            sort_index=sort_index,
        )
```

```python
adapter = FreshCsvExportAdapter()
rows = [adapter.to_csv_row(record, sort_index=index) for index, record in enumerate(records, 1)]
return ConversionResult(
    csv_path=write_rows_to_csv_path(rows, csv_path),
    resolved=sum(1 for record in records if record.github.value),
    skipped=skipped,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_output_adapters.py tests/test_paper_export.py tests/test_csv_io.py tests/test_github_search_to_csv.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_output_adapters.py tests/test_paper_export.py tests/test_csv_io.py tests/test_github_search_to_csv.py src/core/output_adapters.py src/shared/paper_export.py src/shared/csv_io.py src/github_search_to_csv/pipeline.py
git commit -m "refactor: add fresh csv output adapter"
```

### Task 5: Migrate CSV Update To `Record` + `CsvUpdateAdapter`

**Files:**
- Modify: `src/core/output_adapters.py`
- Modify: `src/csv_update/pipeline.py`
- Modify: `src/shared/csv_schema.py`
- Modify: `tests/test_output_adapters.py`
- Modify: `tests/test_csv_update.py`
- Modify: `tests/test_csv_io.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.core.output_adapters import CsvUpdateAdapter
from src.core.record_model import Record


def test_csv_update_adapter_overwrites_stars_and_about_but_only_backfills_created():
    adapter = CsvUpdateAdapter()
    original = {
        "Name": "Paper A",
        "Url": "https://arxiv.org/abs/2501.12345",
        "Github": "https://github.com/foo/bar",
        "Stars": "5",
        "Created": "2019-01-01T00:00:00Z",
        "About": "old",
    }
    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345",
        github="https://github.com/foo/bar",
        stars=42,
        created="2020-01-01T00:00:00Z",
        about="",
        source="csv_update",
    )

    updated = adapter.apply(original, record)

    assert updated["Stars"] == "42"
    assert updated["About"] == ""
    assert updated["Created"] == "2019-01-01T00:00:00Z"


def test_csv_update_adapter_appends_missing_managed_columns_without_reordering_existing_columns():
    adapter = CsvUpdateAdapter()

    assert adapter.normalize_fieldnames(["Url", "Name"]) == [
        "Url",
        "Name",
        "Github",
        "Stars",
        "Created",
        "About",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_output_adapters.py tests/test_csv_update.py tests/test_csv_io.py -q`
Expected: FAIL because CSV update logic still lives inside `src/csv_update/pipeline.py`

- [ ] **Step 3: Write minimal implementation**

```python
class CsvUpdateAdapter:
    def normalize_fieldnames(self, fieldnames: list[str]) -> list[str]:
        return append_missing_property_columns(fieldnames, ["Github", "Stars", "Created", "About"])

    def apply(self, row: dict[str, str], record: Record) -> dict[str, str]:
        updated = dict(row)
        if not (updated.get("Github", "").strip()) and record.github.value:
            updated["Github"] = str(record.github.value)
        if record.url.value and not (row.get("Github", "").strip()):
            updated["Url"] = str(record.url.value)
        if record.stars.value is not None:
            updated["Stars"] = str(record.stars.value)
        if record.about.value is not None:
            updated["About"] = str(record.about.value)
        if not (updated.get("Created", "").strip()) and record.created.value is not None:
            updated["Created"] = str(record.created.value)
        return updated
```

```python
row_record = CsvRowInputAdapter().to_record(index, row)
synced_record = await record_sync_service.sync(
    row_record,
    allow_title_search=bool(row.get("Url")),
    allow_github_discovery=not bool(row.get("Github")),
)
updated_row = csv_update_adapter.apply(row, synced_record)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_output_adapters.py tests/test_csv_update.py tests/test_csv_io.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_output_adapters.py tests/test_csv_update.py tests/test_csv_io.py src/core/output_adapters.py src/csv_update/pipeline.py src/shared/csv_schema.py
git commit -m "refactor: migrate csv update to record adapters"
```

### Task 6: Migrate Notion Sync To `Record` + `NotionUpdateAdapter`

**Files:**
- Modify: `src/core/output_adapters.py`
- Modify: `src/notion_sync/pipeline.py`
- Modify: `src/notion_sync/notion_client.py`
- Modify: `src/notion_sync/runner.py`
- Modify: `tests/test_output_adapters.py`
- Modify: `tests/test_notion_mode.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.core.output_adapters import NotionUpdateAdapter
from src.core.record_model import Record


def test_notion_update_adapter_builds_patch_with_about_overwrite_and_created_backfill_only():
    adapter = NotionUpdateAdapter()
    page = {
        "id": "page-1",
        "properties": {
            "Github": {"type": "url", "url": "https://github.com/foo/bar"},
            "Stars": {"type": "number", "number": 5},
            "Created": {"type": "date", "date": {"start": "2019-01-01"}},
            "About": {"type": "rich_text", "rich_text": [{"plain_text": "old"}]},
        },
    }
    record = Record.from_source(
        github="https://github.com/foo/bar",
        stars=42,
        created="2020-01-01",
        about="",
        source="notion_sync",
    )

    patch = adapter.build_patch(page, record, update_github=False)

    assert patch["Stars"]["number"] == 42
    assert patch["About"]["rich_text"] == []
    assert "Created" not in patch
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_output_adapters.py tests/test_notion_mode.py -q`
Expected: FAIL because Notion update policy still lives in `src/notion_sync/pipeline.py`

- [ ] **Step 3: Write minimal implementation**

```python
class NotionUpdateAdapter:
    MANAGED_PROPERTY_TYPES = {
        "Github": "url",
        "Stars": "number",
        "Created": "date",
        "About": "rich_text",
    }

    def validate_schema(self, properties: dict) -> None:
        for name, expected_type in self.MANAGED_PROPERTY_TYPES.items():
            if name in properties and properties[name].get("type") != expected_type:
                raise ValueError(f"Notion property {name} must have type {expected_type}")

    def build_patch(self, page: dict, record: Record, *, update_github: bool) -> dict:
        patch = {
            "Stars": {"number": int(record.stars.value)} if record.stars.value is not None else {"number": None},
            "About": {"rich_text": [] if record.about.value == "" else [{"type": "text", "text": {"content": str(record.about.value)}}]},
        }
        created_prop = page.get("properties", {}).get("Created", {})
        has_created = bool((created_prop.get("date") or {}).get("start"))
        if not has_created and record.created.value is not None:
            patch["Created"] = {"date": {"start": str(record.created.value)}}
        if update_github and record.github.value is not None:
            patch["Github"] = {"url": str(record.github.value)}
        return patch
```

```python
adapter = NotionUpdateAdapter()
adapter.validate_schema(page["properties"])
record = NotionPageInputAdapter().to_record(page)
synced = await record_sync_service.sync(
    record,
    allow_title_search=bool(get_paper_url_from_page(page)),
    allow_github_discovery=not bool(get_github_url_from_page(page)),
)
patch = adapter.build_patch(page, synced, update_github=needs_github_update)
await notion_client.update_page_properties(page_id, properties=patch)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_output_adapters.py tests/test_notion_mode.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_output_adapters.py tests/test_notion_mode.py src/core/output_adapters.py src/notion_sync/pipeline.py src/notion_sync/notion_client.py src/notion_sync/runner.py
git commit -m "refactor: migrate notion sync to record adapters"
```

### Task 7: Add Repository Wrappers, Rewire Runtime, And Finish Routing/Docs

**Files:**
- Create: `src/core/repositories.py`
- Create: `tests/test_repositories.py`
- Modify: `src/shared/runtime.py`
- Modify: `src/shared/repo_metadata_cache.py`
- Modify: `src/shared/github.py`
- Modify: `src/app.py`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.app import InputShape, detect_input_shape
from src.core.repositories import RepoMetadataRepository
from src.shared.repo_metadata_cache import RepoMetadataCacheStore


def test_repo_metadata_repository_reads_and_writes_durable_created_values(tmp_path):
    store = RepoMetadataCacheStore(tmp_path / "cache.db")
    repository = RepoMetadataRepository(store=store)

    repository.record_created("https://github.com/foo/bar", "2020-01-01T00:00:00Z")
    entry = repository.get("https://github.com/foo/bar")

    assert entry is not None
    assert entry.created == "2020-01-01T00:00:00Z"


def test_app_detects_input_shapes_without_exposing_mode_as_top_level_concept():
    assert detect_input_shape([]) == InputShape.NOTION
    assert detect_input_shape(["/tmp/input.csv"]) == InputShape.CSV_FILE
    assert detect_input_shape(["https://github.com/search?q=cvpr%202026&type=repositories"]) == InputShape.GITHUB_SEARCH_URL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_repositories.py tests/test_main.py -q`
Expected: FAIL because `src.core.repositories` and input-shape routing helpers do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
class RepoMetadataRepository:
    def __init__(self, *, store):
        self.store = store

    def get(self, github_url: str):
        return self.store.get(github_url)

    def record_created(self, github_url: str, created: str) -> None:
        self.store.record_created(github_url, created)
```

```python
from dataclasses import dataclass
from enum import Enum

from src.shared.relation_resolution_cache import RelationResolutionCacheStore
from src.shared.repo_cache import RepoCacheStore
from src.shared.repo_metadata_cache import RepoMetadataCacheStore


class InputShape(str, Enum):
    NOTION = "notion"
    CSV_FILE = "csv_file"
    PAPER_COLLECTION_URL = "paper_collection_url"
    GITHUB_SEARCH_URL = "github_search_url"
    ARXIV_RELATIONS_URL = "arxiv_relations_url"


def detect_input_shape(argv: list[str]) -> InputShape:
    if not argv:
        return InputShape.NOTION
    raw_input = argv[0]
    if _is_arxiv_single_paper_url(raw_input):
        return InputShape.ARXIV_RELATIONS_URL
    if _is_url(raw_input) and is_supported_github_search_url(raw_input):
        return InputShape.GITHUB_SEARCH_URL
    if _is_url(raw_input):
        return InputShape.PAPER_COLLECTION_URL
    return InputShape.CSV_FILE
```

```python
@dataclass(frozen=True)
class RuntimeClients:
    session: object
    repo_cache: RepoCacheStore
    repo_metadata_cache: RepoMetadataCacheStore | None
    relation_resolution_cache: RelationResolutionCacheStore | None
    discovery_client: object
    github_client: object
    repo_metadata_repository: RepoMetadataRepository | None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_repositories.py tests/test_main.py -q`
Expected: PASS

- [ ] **Step 5: Run broad regression**

Run: `uv run python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_repositories.py tests/test_main.py src/core/repositories.py src/shared/runtime.py src/shared/repo_metadata_cache.py src/shared/github.py src/app.py README.md ARCHITECTURE.md
git commit -m "refactor: finish record-centric architecture evolution"
```

## Notes For Execution

- Keep `src/shared/paper_enrichment.py` and `src/shared/property_resolvers.py` as compatibility wrappers until the end of the plan. They should shrink, not disappear immediately.
- Do not rewrite low-level collectors just to make them look uniform. The new merge point is the `Record` layer, not the upstream fetcher layer.
- Keep `Overview` / `Abs` outside the six core properties. If a task needs them, store them under `RecordArtifacts`.
- Preserve all current product semantics:
  - existing non-empty `Github` stays source-of-truth
  - `Stars` always overwrite
  - `About` always overwrite, including overwrite-to-empty
  - `Created` only backfills when empty
  - GitHub search repo-side values stay trusted and should not be re-fetched by default
  - CSV/Notion do not require all properties up front
  - Notion wrong-type same-name properties stay hard failures

## Final Verification Commands

Run after Task 7, before any merge or push:

```bash
uv run python -m pytest tests/test_record_model.py tests/test_record_sync.py tests/test_input_adapters.py tests/test_output_adapters.py tests/test_repositories.py -q
```

```bash
uv run python -m pytest tests/test_property_model.py tests/test_property_resolvers.py tests/test_paper_enrichment.py tests/test_paper_export.py tests/test_url_to_csv.py tests/test_arxiv_relations.py tests/test_github_search_to_csv.py tests/test_csv_update.py tests/test_notion_mode.py tests/test_csv_io.py tests/test_main.py -q
```

```bash
uv run python -m pytest -q
```
