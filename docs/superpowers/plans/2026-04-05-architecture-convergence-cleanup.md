# Architecture Convergence Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the runtime and maintenance surface of `scripts.ghstars` onto one canonical `src/core/*` architecture by moving shared paper-seed normalization and shared record-sync orchestration into `src/core/*`, migrating `url_to_csv` / `csv_update` / `notion_sync` onto those workflows, and deleting the remaining compatibility facades in `src/shared/*`.

**Architecture:** Add two focused core modules: one for single-seed arXiv normalization and one for shared record-sync orchestration. Keep `url_to_csv` responsible only for mode-specific retain/drop and dedupe policy, keep `notion_sync` responsible only for mode-specific update/patch policy, and move all shared property/domain semantics to `src/core/*`. Remove `src/shared/property_model.py`, `src/shared/property_resolvers.py`, and `src/shared/paper_enrichment.py` after runtime callers and tests are migrated.

**Tech Stack:** Python, asyncio, pytest, existing `Record`, `RecordSyncService`, `PaperSeed`, `NotionPageInputAdapter`, `CsvRowInputAdapter`

---

## File Structure

### Create

- `src/core/paper_seed_normalization.py`
  Owns per-seed normalization from arbitrary paper URLs to authoritative arXiv-backed `PaperSeed` values.

- `src/core/record_sync_workflow.py`
  Owns shared sync-call orchestration over `RecordSyncService`, including content-cache warming, optional normalized-URL writeback, and actionable-reason selection.

- `tests/test_paper_seed_normalization.py`
  Protects the new core normalization workflow directly.

- `tests/test_record_sync_workflow.py`
  Protects the new shared sync-call workflow directly.

### Modify

- `src/url_to_csv/pipeline.py`
  Stop owning normalization semantics; call the core normalization workflow and keep only mode-specific filtering and dedupe.

- `src/core/paper_export_sync.py`
  Become thin wrappers over the shared record-sync workflow for fresh paper export.

- `src/csv_update/pipeline.py`
  Reuse the shared record-sync workflow instead of building `RecordSyncService` locally.

- `src/notion_sync/pipeline.py`
  Remove duplicated page parsing helpers and `PaperEnrichmentRequest` dependency; use `NotionPageInputAdapter` plus a local mode-specific sync-decision object and the shared record-sync workflow.

- `src/core/input_adapters.py`
  Preserve and test the Notion parsing boundary now that `notion_sync/pipeline.py` no longer re-parses page fields.

- `tests/test_url_to_csv.py`
  Keep mode behavior coverage while shifting normalization ownership to the new core workflow.

- `tests/test_csv_update.py`
  Keep CSV behavior coverage while shifting orchestration ownership to the new core workflow.

- `tests/test_notion_mode.py`
  Keep Notion mode behavior coverage while moving page parsing ownership to `NotionPageInputAdapter`.

- `tests/test_input_adapters.py`
  Absorb page-parsing assertions that currently live in `tests/test_notion_mode.py`.

- `tests/test_paper_export_sync.py`
  Keep wrapper coverage after `src/core/paper_export_sync.py` becomes thin.

- `tests/test_repo_hygiene.py`
  Add explicit assertions that the deleted compatibility facades stay deleted.

- `ARCHITECTURE.md`
  Update the maintainer doc so `src/core/*` is the only property/domain API and `src/shared/*` is described as lower-level support only.

### Delete

- `src/shared/property_model.py`
- `src/shared/property_resolvers.py`
- `src/shared/paper_enrichment.py`
- `tests/test_property_model.py`
- `tests/test_property_resolvers.py`
- `tests/test_paper_enrichment.py`

---

### Task 1: Introduce The Core Paper-Seed Normalization Workflow

**Files:**
- Create: `src/core/paper_seed_normalization.py`
- Create: `tests/test_paper_seed_normalization.py`
- Modify: `src/url_to_csv/pipeline.py`
- Modify: `tests/test_url_to_csv.py`

- [ ] **Step 1: Write the failing normalization-workflow tests**

Create `tests/test_paper_seed_normalization.py` with direct tests for the new canonical workflow.

Add this test file content:

```python
from types import SimpleNamespace

import pytest

from src.shared.papers import PaperSeed


@pytest.mark.anyio
async def test_normalize_paper_seed_to_arxiv_returns_authoritative_seed(monkeypatch):
    import src.core.paper_seed_normalization as paper_seed_normalization

    async def fake_resolve_arxiv_url(*args, **kwargs):
        return SimpleNamespace(
            resolved_url="https://arxiv.org/abs/2501.12345v2",
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
        )

    monkeypatch.setattr(
        paper_seed_normalization,
        "resolve_arxiv_url",
        fake_resolve_arxiv_url,
    )

    result = await paper_seed_normalization.normalize_paper_seed_to_arxiv(
        PaperSeed(name="Paper A", url="https://doi.org/10.1145/example"),
        discovery_client=object(),
        arxiv_client=object(),
    )

    assert result.normalized_seed == PaperSeed(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345v2",
        canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
        url_resolution_authoritative=True,
    )
    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"


@pytest.mark.anyio
async def test_normalize_paper_seed_to_arxiv_returns_unresolved_when_resolution_misses(monkeypatch):
    import src.core.paper_seed_normalization as paper_seed_normalization

    async def fake_resolve_arxiv_url(*args, **kwargs):
        return SimpleNamespace(
            resolved_url=None,
            canonical_arxiv_url=None,
        )

    monkeypatch.setattr(
        paper_seed_normalization,
        "resolve_arxiv_url",
        fake_resolve_arxiv_url,
    )

    result = await paper_seed_normalization.normalize_paper_seed_to_arxiv(
        PaperSeed(name="Paper A", url="https://doi.org/10.1145/example"),
        discovery_client=object(),
        arxiv_client=object(),
    )

    assert result.normalized_seed is None
    assert result.canonical_arxiv_url is None
```

Extend `tests/test_url_to_csv.py` by strengthening an existing normalization regression test so the returned seed must carry authoritative facts:

```python
assert resolved == [
    PaperSeed(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345v2",
        canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
        url_resolution_authoritative=True,
    )
]
```

- [ ] **Step 2: Run the new tests and verify they fail for the right reason**

Run:

```bash
uv run python -m pytest -q tests/test_paper_seed_normalization.py tests/test_url_to_csv.py -k "paper_seed_normalization or normalize_paper_seeds_to_arxiv"
```

Expected:

- `tests/test_paper_seed_normalization.py` fails with an import error because `src.core.paper_seed_normalization` does not exist yet
- the strengthened `tests/test_url_to_csv.py` assertion fails because the current returned `PaperSeed` does not carry authoritative facts

- [ ] **Step 3: Implement the core normalization module and migrate `url_to_csv` to consume it**

Create `src/core/paper_seed_normalization.py` with this implementation:

```python
from __future__ import annotations

from dataclasses import dataclass

from src.shared.arxiv_url_resolution import resolve_arxiv_url
from src.shared.papers import PaperSeed


@dataclass(frozen=True)
class PaperSeedNormalizationResult:
    normalized_seed: PaperSeed | None
    canonical_arxiv_url: str | None


async def normalize_paper_seed_to_arxiv(
    seed: PaperSeed,
    *,
    discovery_client=None,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> PaperSeedNormalizationResult:
    resolution = await resolve_arxiv_url(
        seed.name,
        seed.url,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    if not resolution.canonical_arxiv_url:
        return PaperSeedNormalizationResult(
            normalized_seed=None,
            canonical_arxiv_url=None,
        )

    normalized_url = resolution.resolved_url or resolution.canonical_arxiv_url
    return PaperSeedNormalizationResult(
        normalized_seed=PaperSeed(
            name=seed.name,
            url=normalized_url,
            canonical_arxiv_url=resolution.canonical_arxiv_url,
            url_resolution_authoritative=True,
        ),
        canonical_arxiv_url=resolution.canonical_arxiv_url,
    )
```

Then update `src/url_to_csv/pipeline.py` so it imports and uses the new helper instead of owning `_normalize_seed_to_arxiv(...)` locally:

```python
from src.core.paper_seed_normalization import normalize_paper_seed_to_arxiv
```

and inside `normalize_seed(...)`:

```python
        normalization = await normalize_paper_seed_to_arxiv(
            seed,
            discovery_client=discovery_client,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            relation_resolution_cache=relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        )
        return index, normalization.normalized_seed, normalization.canonical_arxiv_url
```

Delete `_normalize_seed_to_arxiv(...)` from `src/url_to_csv/pipeline.py`.

- [ ] **Step 4: Run the normalization tests and make sure they pass**

Run:

```bash
uv run python -m pytest -q tests/test_paper_seed_normalization.py tests/test_url_to_csv.py -k "paper_seed_normalization or normalize_paper_seeds_to_arxiv"
```

Expected:

- all selected tests pass
- `tests/test_url_to_csv.py` now shows normalized seeds carrying authoritative facts

- [ ] **Step 5: Commit the normalization-workflow migration**

Run:

```bash
git add src/core/paper_seed_normalization.py src/url_to_csv/pipeline.py tests/test_paper_seed_normalization.py tests/test_url_to_csv.py
git commit -m "refactor: move paper seed normalization into core"
```

---

### Task 2: Introduce The Shared Core Record-Sync Workflow

**Files:**
- Create: `src/core/record_sync_workflow.py`
- Create: `tests/test_record_sync_workflow.py`
- Modify: `src/core/paper_export_sync.py`
- Modify: `tests/test_paper_export_sync.py`

- [ ] **Step 1: Write failing tests for the shared sync workflow**

Create `tests/test_record_sync_workflow.py` with focused coverage for the shared orchestration:

```python
from unittest.mock import AsyncMock

import pytest

from src.core.record_model import PropertyState, Record, RecordFacts


@pytest.mark.anyio
async def test_sync_record_with_policy_warms_content_and_applies_normalized_url(monkeypatch):
    import src.core.record_sync_workflow as record_sync_workflow

    events: list[str] = []
    content_cache = type(
        "RecordingContentCache",
        (),
        {"ensure_local_content_cache": AsyncMock(side_effect=lambda url: events.append(f"warm:{url}"))},
    )()

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            pass

        async def sync(self, record, **kwargs):
            synced = (
                record.with_property(
                    "github",
                    PropertyState.resolved("https://github.com/foo/bar", source="discovered"),
                )
                .with_supporting_state(
                    facts=RecordFacts(
                        normalized_url="https://arxiv.org/pdf/2501.12345v2.pdf",
                        canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
                        github_source="discovered",
                        url_resolution_authoritative=True,
                    )
                )
            )
            await kwargs["before_repo_metadata"](synced)
            events.append("after-warm")
            return synced

    monkeypatch.setattr(
        record_sync_workflow,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await record_sync_workflow.sync_record_with_policy(
        Record.from_source(name="Paper A", url="https://doi.org/10.1145/example", source="csv"),
        policy=record_sync_workflow.RecordSyncPolicy(
            allow_title_search=True,
            allow_github_discovery=True,
            apply_normalized_url=True,
        ),
        discovery_client=object(),
        github_client=object(),
        content_cache=content_cache,
    )

    assert result.record.url.value == "https://arxiv.org/pdf/2501.12345v2.pdf"
    assert result.record.url.source == "url_resolution"
    assert events == [
        "warm:https://arxiv.org/abs/2501.12345",
        "after-warm",
    ]


@pytest.mark.anyio
async def test_sync_record_with_policy_uses_repo_metadata_error_when_no_state_reason_exists(monkeypatch):
    import src.core.record_sync_workflow as record_sync_workflow

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            pass

        async def sync(self, record, **kwargs):
            return record.with_supporting_state(
                facts=RecordFacts(repo_metadata_error="repo metadata cache miss")
            )

    monkeypatch.setattr(
        record_sync_workflow,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await record_sync_workflow.sync_record_with_policy(
        Record.from_source(
            name="Paper A",
            url="https://arxiv.org/abs/2501.12345",
            github="https://github.com/foo/bar",
            stars=10,
            created="2024-01-01T00:00:00Z",
            about="repo",
            source="csv",
        ),
        policy=record_sync_workflow.RecordSyncPolicy(
            allow_title_search=False,
            allow_github_discovery=False,
        ),
        discovery_client=object(),
        github_client=object(),
    )

    assert result.reason == "repo metadata cache miss"
```

Then adapt `tests/test_paper_export_sync.py` so it asserts `sync_paper_record(...)` and `sync_paper_seed(...)` are thin wrappers around the new workflow module rather than direct `RecordSyncService` owners.

- [ ] **Step 2: Run the workflow tests and verify they fail**

Run:

```bash
uv run python -m pytest -q tests/test_record_sync_workflow.py tests/test_paper_export_sync.py
```

Expected:

- the new file fails to import because `src.core.record_sync_workflow` does not exist yet
- wrapper tests fail because `paper_export_sync.py` still owns the orchestration directly

- [ ] **Step 3: Implement the shared sync workflow and thin the paper-export wrappers**

Create `src/core/record_sync_workflow.py`:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.core.record_model import PropertyState, Record
from src.core.record_sync import RecordSyncService
from src.shared.paper_identity import build_arxiv_abs_url, extract_arxiv_id_from_single_paper_url


@dataclass(frozen=True)
class RecordSyncPolicy:
    allow_title_search: bool
    allow_github_discovery: bool
    trust_existing_github: bool = False
    apply_normalized_url: bool = False


@dataclass(frozen=True)
class RecordSyncWorkflowResult:
    record: Record
    reason: str | None


def first_actionable_reason(original: Record, synced: Record) -> str | None:
    for field_name in ("github", "stars", "created", "about"):
        original_state = getattr(original, field_name)
        synced_state = getattr(synced, field_name)
        if synced_state.reason is None:
            continue
        if synced_state == original_state:
            continue
        return synced_state.reason
    return synced.facts.repo_metadata_error


def build_content_warming_callback(content_cache) -> Callable[[Record], Awaitable[None]]:
    async def warm_content_cache(record: Record) -> None:
        arxiv_id = extract_arxiv_id_from_single_paper_url(record.facts.canonical_arxiv_url or "")
        if not arxiv_id or content_cache is None:
            return

        warmer = getattr(content_cache, "ensure_local_content_cache", None)
        if not callable(warmer):
            return

        try:
            await warmer(build_arxiv_abs_url(arxiv_id))
        except Exception:
            return

    return warm_content_cache


async def sync_record_with_policy(
    record: Record,
    *,
    policy: RecordSyncPolicy,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> RecordSyncWorkflowResult:
    service = RecordSyncService(
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    synced = await service.sync(
        record,
        allow_title_search=policy.allow_title_search,
        allow_github_discovery=policy.allow_github_discovery,
        trust_existing_github=policy.trust_existing_github,
        precomputed_normalized_url=record.facts.normalized_url if record.facts.url_resolution_authoritative else None,
        precomputed_canonical_arxiv_url=record.facts.canonical_arxiv_url,
        url_resolution_authoritative=record.facts.url_resolution_authoritative,
        before_repo_metadata=build_content_warming_callback(content_cache),
    )
    if policy.apply_normalized_url and synced.facts.normalized_url is not None and synced.facts.url_resolution_authoritative:
        synced = synced.with_property(
            "url",
            PropertyState.resolved(
                synced.facts.normalized_url,
                source="url_resolution",
            ),
        )
    return RecordSyncWorkflowResult(
        record=synced,
        reason=first_actionable_reason(record, synced),
    )
```

Then thin `src/core/paper_export_sync.py` so it imports `RecordSyncPolicy` and `sync_record_with_policy(...)`, and `sync_paper_record(...)` becomes:

```python
    result = await sync_record_with_policy(
        record,
        policy=RecordSyncPolicy(
            allow_title_search=allow_title_search,
            allow_github_discovery=allow_github_discovery,
            trust_existing_github=trust_existing_github,
            apply_normalized_url=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    return PaperSyncResult(record=result.record, reason=result.reason)
```

- [ ] **Step 4: Run the workflow tests and verify they pass**

Run:

```bash
uv run python -m pytest -q tests/test_record_sync_workflow.py tests/test_paper_export_sync.py
```

Expected:

- all selected tests pass
- `tests/test_paper_export_sync.py` still proves the public wrappers preserve existing behavior

- [ ] **Step 5: Commit the shared sync-workflow migration**

Run:

```bash
git add src/core/record_sync_workflow.py src/core/paper_export_sync.py tests/test_record_sync_workflow.py tests/test_paper_export_sync.py
git commit -m "refactor: centralize shared record sync workflow"
```

---

### Task 3: Migrate CSV Update To The Shared Core Sync Workflow

**Files:**
- Modify: `src/csv_update/pipeline.py`
- Modify: `tests/test_csv_update.py`

- [ ] **Step 1: Write failing CSV-update tests for the new workflow boundary**

Add this targeted regression test to `tests/test_csv_update.py` near the existing `build_csv_row_outcome(...)` tests:

```python
@pytest.mark.anyio
async def test_build_csv_row_outcome_routes_through_shared_record_sync_workflow(monkeypatch, tmp_path: Path):
    import src.csv_update.pipeline as csv_update_pipeline
    from src.core.record_model import PropertyState, RecordFacts
    from src.core.record_sync_workflow import RecordSyncWorkflowResult

    seen: dict[str, object] = {}

    async def fake_sync_record_with_policy(record, **kwargs):
        seen["record"] = record
        seen["policy"] = kwargs["policy"]
        synced = (
            record.with_property(
                "github",
                PropertyState.resolved("https://github.com/foo/bar", source="discovered"),
            )
            .with_property("stars", PropertyState.resolved(42, source="github_api"))
            .with_supporting_state(
                facts=RecordFacts(
                    normalized_url="https://arxiv.org/abs/2501.12345v2",
                    canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
                    github_source="discovered",
                    url_resolution_authoritative=True,
                )
            )
        )
        return RecordSyncWorkflowResult(record=synced, reason=None)

    monkeypatch.setattr(csv_update_pipeline, "sync_record_with_policy", fake_sync_record_with_policy)

    _, updated_row, outcome = await csv_update_pipeline.build_csv_row_outcome(
        1,
        {
            "Name": "Paper A",
            "Url": "https://doi.org/10.1145/example",
            "Github": "",
            "Stars": "",
            "Created": "",
            "About": "",
        },
        discovery_client=object(),
        github_client=object(),
        content_cache=None,
        csv_dir=tmp_path,
    )

    assert seen["policy"].allow_title_search is True
    assert seen["policy"].allow_github_discovery is True
    assert seen["policy"].apply_normalized_url is True
    assert updated_row["Url"] == "https://arxiv.org/abs/2501.12345v2"
    assert updated_row["Github"] == "https://github.com/foo/bar"
    assert updated_row["Stars"] == "42"
    assert outcome.reason is None
```

- [ ] **Step 2: Run the CSV-update tests and verify they fail**

Run:

```bash
uv run python -m pytest -q tests/test_csv_update.py -k "build_csv_row_outcome"
```

Expected:

- the new test fails because `csv_update.pipeline` does not import or call `sync_record_with_policy(...)`

- [ ] **Step 3: Replace local orchestration in `csv_update` with the shared workflow**

Modify `src/csv_update/pipeline.py`:

```python
from src.core.record_sync_workflow import RecordSyncPolicy, sync_record_with_policy
```

Replace the local `RecordSyncService(...)` block in `build_csv_row_outcome(...)` with:

```python
    workflow_result = await sync_record_with_policy(
        record,
        policy=RecordSyncPolicy(
            allow_title_search=bool(url),
            allow_github_discovery=not bool(existing_github),
            trust_existing_github=bool(existing_github),
            apply_normalized_url=not bool(existing_github),
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    synced_record = workflow_result.record
    reason = workflow_result.reason
```

Delete the local `_warm_content_cache(...)` helper and the local `_first_reason(...)` helper from `src/csv_update/pipeline.py`.

- [ ] **Step 4: Run the CSV-update tests and verify they pass**

Run:

```bash
uv run python -m pytest -q tests/test_csv_update.py -k "build_csv_row_outcome or update_csv_file"
```

Expected:

- all selected CSV-update tests pass
- URL writeback, stars refresh, created backfill, and skip behavior remain unchanged

- [ ] **Step 5: Commit the CSV-update migration**

Run:

```bash
git add src/csv_update/pipeline.py tests/test_csv_update.py
git commit -m "refactor: reuse shared sync workflow in csv update"
```

---

### Task 4: Migrate Notion To Adapter-Only Parsing And Delete Compatibility Facades

**Files:**
- Modify: `src/notion_sync/pipeline.py`
- Modify: `src/core/input_adapters.py`
- Modify: `tests/test_input_adapters.py`
- Modify: `tests/test_notion_mode.py`
- Delete: `src/shared/property_model.py`
- Delete: `src/shared/property_resolvers.py`
- Delete: `src/shared/paper_enrichment.py`
- Delete: `tests/test_property_model.py`
- Delete: `tests/test_property_resolvers.py`
- Delete: `tests/test_paper_enrichment.py`

- [ ] **Step 1: Write failing Notion-boundary tests and facade-removal expectations**

First, move page parsing expectations to `tests/test_input_adapters.py` by adding one richer Notion parsing test there:

```python
def test_notion_page_input_adapter_reads_title_url_github_stars_created_and_about():
    record = NotionPageInputAdapter().to_record(
        {
            "id": "page-1",
            "properties": {
                "Title": {"type": "title", "title": [{"plain_text": "Paper A"}]},
                "Paper URL": {"type": "url", "url": "https://doi.org/10.1145/example"},
                "Github": {"type": "url", "url": "https://github.com/foo/bar"},
                "Stars": {"type": "number", "number": 42},
                "Created": {"type": "date", "date": {"start": "2024-01-01"}},
                "About": {"type": "rich_text", "rich_text": [{"plain_text": "repo"}]},
            },
        }
    )

    assert record.context.notion_page_id == "page-1"
    assert record.name.value == "Paper A"
    assert record.url.value == "https://doi.org/10.1145/example"
    assert record.github.value == "https://github.com/foo/bar"
    assert record.stars.value == 42
    assert record.created.value == "2024-01-01"
    assert record.about.value == "repo"
```

Then replace the current `build_page_enrichment_request(...)`-style unit with a local-decision test in `tests/test_notion_mode.py`:

```python
def test_build_page_sync_decision_trusts_existing_github_record():
    from src.notion_sync.pipeline import PageSyncDecision, build_page_sync_decision

    record = Record.from_source(
        name="Paper A",
        url="https://doi.org/10.1145/example",
        github="https://github.com/foo/bar",
        source="notion",
        trusted_fields={"github"},
    )

    decision = build_page_sync_decision(record)

    assert decision == PageSyncDecision(
        allow_title_search=False,
        allow_github_discovery=False,
        trust_existing_github=True,
        update_github=False,
    )
```

Finally, add a hygiene test to `tests/test_repo_hygiene.py`:

```python
def test_property_compatibility_facades_are_removed():
    assert not Path("src/shared/property_model.py").exists()
    assert not Path("src/shared/property_resolvers.py").exists()
    assert not Path("src/shared/paper_enrichment.py").exists()
```

- [ ] **Step 2: Run the Notion and hygiene tests and verify they fail**

Run:

```bash
uv run python -m pytest -q tests/test_input_adapters.py tests/test_notion_mode.py tests/test_repo_hygiene.py
```

Expected:

- the new `build_page_sync_decision(...)` test fails because the type/function do not exist yet
- the hygiene test fails because the compatibility facades are still present

- [ ] **Step 3: Migrate `notion_sync` to adapter-only parsing and shared sync workflow**

Modify `src/notion_sync/pipeline.py`:

```python
from dataclasses import dataclass

from src.core.record_sync_workflow import RecordSyncPolicy, sync_record_with_policy
```

Define a local mode-specific decision object:

```python
@dataclass(frozen=True)
class PageSyncDecision:
    allow_title_search: bool
    allow_github_discovery: bool
    trust_existing_github: bool
    update_github: bool


def build_page_sync_decision(record: Record) -> PageSyncDecision:
    github_value = _string_value(record.github.value)
    if github_value and is_valid_github_repo_url(github_value):
        return PageSyncDecision(
            allow_title_search=False,
            allow_github_discovery=False,
            trust_existing_github=True,
            update_github=False,
        )
    return PageSyncDecision(
        allow_title_search=True,
        allow_github_discovery=True,
        trust_existing_github=False,
        update_github=True,
    )
```

Refactor `process_page(...)` so it:

- gets `title`, `current_stars`, and existing values from the adapter-backed `record`
- uses `build_page_sync_decision(record)` instead of `PaperEnrichmentRequest`
- calls `sync_record_with_policy(...)` with `apply_normalized_url=False`
- keeps only mode-specific patch generation and skip/logging behavior

Delete the duplicated helper functions that re-parse raw page fields:

- `get_github_url_from_page`
- `get_current_stars_from_page`
- `get_current_created_from_page`
- `get_github_property_type`
- `get_text_from_property`
- `get_page_title`
- `get_page_url`
- `get_paper_url_from_page`
- `extract_arxiv_id_from_url`
- `get_arxiv_id_from_page`
- `build_page_enrichment_request`

Also delete:

```bash
rm src/shared/property_model.py src/shared/property_resolvers.py src/shared/paper_enrichment.py
rm tests/test_property_model.py tests/test_property_resolvers.py tests/test_paper_enrichment.py
```

- [ ] **Step 4: Run the Notion-focused tests and verify they pass after the migration**

Run:

```bash
uv run python -m pytest -q tests/test_input_adapters.py tests/test_notion_mode.py tests/test_repo_hygiene.py
```

Expected:

- all selected tests pass
- the new hygiene assertion confirms the facades are gone
- `process_page(...)` behavior remains unchanged from the user's perspective

- [ ] **Step 5: Commit the Notion migration and facade deletion**

Run:

```bash
git add src/notion_sync/pipeline.py src/core/input_adapters.py tests/test_input_adapters.py tests/test_notion_mode.py tests/test_repo_hygiene.py
git add -u src/shared tests
git commit -m "refactor: remove property compatibility facades"
```

---

### Task 5: Update Maintainer Docs And Run Full Verification

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `tests/test_src_layout.py`

- [ ] **Step 1: Write a failing doc/hygiene test for the converged architecture**

Add to `tests/test_src_layout.py`:

```python
def test_property_domain_surface_lives_under_core_only():
    import pathlib

    assert pathlib.Path("src/core/record_model.py").exists()
    assert pathlib.Path("src/core/record_sync.py").exists()
    assert not pathlib.Path("src/shared/property_model.py").exists()
    assert not pathlib.Path("src/shared/property_resolvers.py").exists()
```

- [ ] **Step 2: Run the doc/hygiene tests and verify the new one fails until the doc cleanup is complete**

Run:

```bash
uv run python -m pytest -q tests/test_src_layout.py tests/test_repo_hygiene.py
```

Expected:

- the new test passes only after Task 4's file deletion is in place
- this step acts as a final gate before the full verification run

- [ ] **Step 3: Update `ARCHITECTURE.md` so it matches the converged boundaries**

Update the "Runtime Shape", "Record-Centric Core", and "Shared Subsystems" sections so they explicitly say:

```markdown
- `src/core/*` is the only property/domain API.
- `src/shared/*` holds lower-level mechanics such as caches, HTTP, provider clients, and normalization primitives.
- `url_to_csv` may call the core normalization workflow for pre-filtering, but does not own a separate normalization semantics layer.
- compatibility wrappers formerly under `src/shared/property_*` and `src/shared/paper_enrichment.py` are removed.
```

- [ ] **Step 4: Run the full test suite**

Run:

```bash
uv run python -m pytest -q
```

Expected:

- the entire suite passes
- no test imports deleted compatibility facades
- mode behavior and documentation constraints remain consistent with the converged architecture

- [ ] **Step 5: Commit the documentation update and verified final state**

Run:

```bash
git add ARCHITECTURE.md tests/test_src_layout.py
git commit -m "docs: describe converged core architecture"
```

---

## Self-Review

### Spec coverage

- Core-owned paper-seed normalization workflow: covered by Task 1.
- Shared core sync-call workflow: covered by Task 2.
- `csv_update` migration: covered by Task 3.
- `notion_sync` adapter-only parsing and shared sync workflow: covered by Task 4.
- Compatibility facade deletion: covered by Task 4.
- Maintainer docs and full verification: covered by Task 5.

### Placeholder scan

- No `TBD`, `TODO`, or "similar to previous task" placeholders remain.
- Every task has explicit files, tests, commands, and concrete code blocks.

### Type consistency

- New core normalization module: `src/core/paper_seed_normalization.py`
- New shared workflow module: `src/core/record_sync_workflow.py`
- Shared policy type: `RecordSyncPolicy`
- Shared workflow result type: `RecordSyncWorkflowResult`
- Notion-local decision type: `PageSyncDecision`

These names are used consistently across all tasks.
