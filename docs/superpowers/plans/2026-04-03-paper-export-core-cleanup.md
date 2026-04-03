# Paper Export Core Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the remaining fresh paper-export compatibility bridge so `url_to_csv` and `arxiv_relations` run directly through the record-centric core while preserving all current export semantics.

**Architecture:** Add one small core helper module for paper-export sync, teach `PaperSeedInputAdapter` to preserve seed supporting facts, route `src/shared/paper_export.py` directly through `RecordSyncService` + `FreshCsvExportAdapter`, and keep `src/shared/paper_enrichment.py` only as a compatibility facade. Remove the duplicated `_adapt_paper_seeds_for_export()` helpers from the two paper-family pipelines.

**Tech Stack:** Python 3.12, `pytest`, existing `src/core/*` record model/sync services, existing `src/shared/*` discovery and CSV helpers

---

## Scope Check

This plan stays within one bounded cleanup. It does not change resolver order,
repo-discovery order, cache schema, or CSV/Notion write policy. The work is one
coherent refactor because all touched files belong to the same compatibility
bridge:

- seed adaptation
- fresh paper sync orchestration
- paper export row building
- compatibility facade retention
- removal of the duplicated seed-export adapter helpers

## File Structure

**Create**

- `src/core/paper_export_sync.py`
  - Shared paper-export sync entrypoints and support helpers.
- `tests/test_paper_export_sync.py`
  - Focused coverage for seed fact preservation, content warming, normalized URL
    writeback, and skip-reason selection.

**Modify**

- `src/core/record_model.py`
  - Extend `RecordFacts` with `url_resolution_authoritative`.
- `src/core/input_adapters.py`
  - Preserve `PaperSeed` supporting facts on adapted `Record`s.
- `src/shared/paper_export.py`
  - Replace compatibility DTO round-trip with direct core sync flow.
- `src/shared/paper_enrichment.py`
  - Thin compatibility facade over the new core helper.
- `src/url_to_csv/pipeline.py`
  - Remove `_adapt_paper_seeds_for_export()` and pass seeds directly.
- `src/arxiv_relations/pipeline.py`
  - Remove `_adapt_paper_seeds_for_export()` and pass seeds directly.
- `tests/test_record_model.py`
  - Cover the new supporting fact field.
- `tests/test_input_adapters.py`
  - Cover `PaperSeedInputAdapter` fact preservation.
- `tests/test_paper_export.py`
  - Assert the direct core helper flow and preserved row semantics.
- `tests/test_paper_enrichment.py`
  - Assert the compatibility facade routes through the new core helper.
- `tests/test_url_to_csv.py`
  - Assert fetched seeds are passed directly to export.
- `tests/test_arxiv_relations.py`
  - Assert normalized relation seeds are passed directly to export.

### Task 1: Preserve Seed Facts And Add Core Paper Sync Helper

**Files:**
- Create: `src/core/paper_export_sync.py`
- Create: `tests/test_paper_export_sync.py`
- Modify: `src/core/record_model.py`
- Modify: `src/core/input_adapters.py`
- Modify: `tests/test_record_model.py`
- Modify: `tests/test_input_adapters.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_record_model.py
def test_record_facts_can_store_url_resolution_authoritativeness():
    facts = RecordFacts(
        canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
        normalized_url="https://arxiv.org/abs/2501.12345v2",
        url_resolution_authoritative=True,
    )

    assert facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert facts.normalized_url == "https://arxiv.org/abs/2501.12345v2"
    assert facts.url_resolution_authoritative is True


# tests/test_input_adapters.py
def test_paper_seed_input_adapter_preserves_seed_supporting_facts():
    record = PaperSeedInputAdapter().to_record(
        PaperSeed(
            name="Paper A",
            url="https://arxiv.org/abs/2501.12345v2",
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            url_resolution_authoritative=True,
        )
    )

    assert record.name.value == "Paper A"
    assert record.url.value == "https://arxiv.org/abs/2501.12345v2"
    assert record.facts.normalized_url == "https://arxiv.org/abs/2501.12345v2"
    assert record.facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert record.facts.url_resolution_authoritative is True


# tests/test_paper_export_sync.py
import types
from unittest.mock import AsyncMock

import pytest

from src.core.paper_export_sync import PaperSyncResult, sync_paper_seed
from src.core.record_model import Record, RecordFacts
from src.shared.papers import PaperSeed


class RecordingContentCache:
    def __init__(self):
        self.calls = []

    async def ensure_local_content_cache(self, canonical_arxiv_url: str) -> None:
        self.calls.append(canonical_arxiv_url)


@pytest.mark.anyio
async def test_sync_paper_seed_uses_precomputed_seed_facts_and_ignores_paper_seed_blocked_states(monkeypatch):
    import src.core.paper_export_sync as paper_export_sync

    updated = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345v2",
        github="https://github.com/foo/bar",
        stars=42,
        created="2024-01-01T00:00:00Z",
        about="repo",
        source="github_api",
    ).with_supporting_state(
        facts=RecordFacts(
            normalized_url="https://arxiv.org/abs/2501.12345v2",
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            github_source="discovered",
            url_resolution_authoritative=True,
        )
    )

    async def fake_sync(self, record, **kwargs):
        assert record.facts.normalized_url == "https://arxiv.org/abs/2501.12345v2"
        assert record.facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
        assert record.facts.url_resolution_authoritative is True
        assert kwargs["precomputed_normalized_url"] == "https://arxiv.org/abs/2501.12345v2"
        assert kwargs["precomputed_canonical_arxiv_url"] == "https://arxiv.org/abs/2501.12345"
        assert kwargs["url_resolution_authoritative"] is True
        await kwargs["before_repo_metadata"](updated)
        return updated

    monkeypatch.setattr(paper_export_sync.RecordSyncService, "sync", fake_sync)

    content_cache = RecordingContentCache()
    result = await sync_paper_seed(
        PaperSeed(
            name="Paper A",
            url="https://arxiv.org/abs/2501.12345v2",
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            url_resolution_authoritative=True,
        ),
        discovery_client=types.SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=types.SimpleNamespace(get_repo_metadata=AsyncMock()),
        content_cache=content_cache,
    )

    assert isinstance(result, PaperSyncResult)
    assert result.record.url.value == "https://arxiv.org/abs/2501.12345v2"
    assert result.record.facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.reason is None
    assert content_cache.calls == ["https://arxiv.org/abs/2501.12345"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_record_model.py::test_record_facts_can_store_url_resolution_authoritativeness tests/test_input_adapters.py::test_paper_seed_input_adapter_preserves_seed_supporting_facts tests/test_paper_export_sync.py::test_sync_paper_seed_uses_precomputed_seed_facts_and_ignores_paper_seed_blocked_states -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.paper_export_sync'` and `TypeError`/`AttributeError` around missing `url_resolution_authoritative`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/core/record_model.py
@dataclass(frozen=True)
class RecordFacts:
    canonical_arxiv_url: str | None = None
    normalized_url: str | None = None
    github_source: str | None = None
    repo_metadata_error: str | None = None
    url_resolution_authoritative: bool = False


# src/core/input_adapters.py
from src.core.record_model import PropertyState, Record, RecordContext, RecordFacts


class PaperSeedInputAdapter:
    def to_record(self, seed) -> Record:
        record = Record.from_source(
            name=seed.name,
            url=seed.url,
            source="paper_seed",
        )
        return record.with_supporting_state(
            facts=RecordFacts(
                normalized_url=seed.url if getattr(seed, "url_resolution_authoritative", False) else None,
                canonical_arxiv_url=getattr(seed, "canonical_arxiv_url", None),
                url_resolution_authoritative=bool(getattr(seed, "url_resolution_authoritative", False)),
            )
        )


# src/core/paper_export_sync.py
from __future__ import annotations

from dataclasses import dataclass

from src.core.input_adapters import PaperSeedInputAdapter
from src.core.record_model import PropertyState, Record
from src.core.record_sync import RecordSyncService


@dataclass(frozen=True)
class PaperSyncResult:
    record: Record
    reason: str | None


def _first_actionable_reason(record: Record, *, ignored_sources: set[str]) -> str | None:
    for state in (record.github, record.stars, record.created, record.about):
        if state.reason is None:
            continue
        if state.source in ignored_sources:
            continue
        return state.reason
    return None


def _build_content_warmer(content_cache):
    if content_cache is None:
        return None

    async def before_repo_metadata(record: Record) -> None:
        canonical_arxiv_url = record.facts.canonical_arxiv_url
        if not canonical_arxiv_url:
            return

        warmer = getattr(content_cache, "ensure_local_content_cache", None)
        if not callable(warmer):
            return

        try:
            await warmer(canonical_arxiv_url)
        except Exception:
            return

    return before_repo_metadata


async def sync_paper_record(
    record: Record,
    *,
    allow_title_search: bool,
    allow_github_discovery: bool,
    trust_existing_github: bool = False,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> PaperSyncResult:
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
        allow_title_search=allow_title_search,
        allow_github_discovery=allow_github_discovery,
        trust_existing_github=trust_existing_github,
        precomputed_normalized_url=record.facts.normalized_url if record.facts.url_resolution_authoritative else None,
        precomputed_canonical_arxiv_url=record.facts.canonical_arxiv_url,
        url_resolution_authoritative=record.facts.url_resolution_authoritative,
        before_repo_metadata=_build_content_warmer(content_cache),
    )
    if synced.facts.normalized_url is not None:
        synced = synced.with_property(
            "url",
            PropertyState.resolved(synced.facts.normalized_url, source="url_resolution"),
        )
    ignored_sources = {state.source for state in (record.name, record.url, record.github) if state.source}
    return PaperSyncResult(
        record=synced,
        reason=_first_actionable_reason(synced, ignored_sources=ignored_sources),
    )


async def sync_paper_seed(seed, **kwargs) -> PaperSyncResult:
    record = PaperSeedInputAdapter().to_record(seed)
    return await sync_paper_record(
        record,
        allow_title_search=True,
        allow_github_discovery=True,
        **kwargs,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_record_model.py::test_record_facts_can_store_url_resolution_authoritativeness tests/test_input_adapters.py::test_paper_seed_input_adapter_preserves_seed_supporting_facts tests/test_paper_export_sync.py::test_sync_paper_seed_uses_precomputed_seed_facts_and_ignores_paper_seed_blocked_states -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/record_model.py src/core/input_adapters.py src/core/paper_export_sync.py tests/test_record_model.py tests/test_input_adapters.py tests/test_paper_export_sync.py
git commit -m "refactor: add core paper export sync helper"
```

### Task 2: Route Shared Paper Export Directly Through The Core Helper

**Files:**
- Modify: `src/shared/paper_export.py`
- Modify: `tests/test_paper_export.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paper_export.py
from src.core.paper_export_sync import PaperSyncResult
from src.core.record_model import Record, RecordFacts


@pytest.mark.anyio
async def test_build_paper_outcome_routes_seed_through_core_sync_helper(monkeypatch):
    received = {}

    async def fake_sync_paper_seed(seed, **kwargs):
        received["seed"] = seed
        received["arxiv_client"] = kwargs.get("arxiv_client")
        received["semanticscholar_graph_client"] = kwargs.get("semanticscholar_graph_client")
        received["crossref_client"] = kwargs.get("crossref_client")
        received["datacite_client"] = kwargs.get("datacite_client")
        received["relation_resolution_cache"] = kwargs.get("relation_resolution_cache")
        return PaperSyncResult(
            record=Record.from_source(
                name="Paper A",
                url="https://arxiv.org/abs/2501.00001",
                github="https://github.com/foo/bar",
                stars=12,
                created="2024-01-01T00:00:00Z",
                about="repo",
                source="paper_export_sync",
            ).with_supporting_state(
                facts=RecordFacts(
                    normalized_url="https://arxiv.org/abs/2501.00001",
                    canonical_arxiv_url="https://arxiv.org/abs/2501.00001",
                    github_source="discovered",
                )
            ),
            reason=None,
        )

    monkeypatch.setattr(paper_export, "sync_paper_seed", fake_sync_paper_seed)

    outcome = await paper_export.build_paper_outcome(
        1,
        PaperSeed(name="Paper A", url="https://doi.org/10.1145/example"),
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
        arxiv_client=SimpleNamespace(),
        semanticscholar_graph_client=SimpleNamespace(),
        crossref_client=SimpleNamespace(),
        datacite_client=SimpleNamespace(),
        relation_resolution_cache=SimpleNamespace(name="relation-cache"),
    )

    assert received["seed"] == PaperSeed(name="Paper A", url="https://doi.org/10.1145/example")
    assert outcome.record.url == "https://arxiv.org/abs/2501.00001"
    assert outcome.record.created == "2024-01-01T00:00:00Z"
    assert outcome.record.about == "repo"


@pytest.mark.anyio
async def test_build_paper_outcome_suppresses_stars_but_keeps_created_and_about_when_reason_present(monkeypatch):
    async def fake_sync_paper_seed(seed, **kwargs):
        return PaperSyncResult(
            record=Record.from_source(
                name=seed.name,
                url="https://arxiv.org/abs/2501.00001",
                github="https://github.com/foo/bar",
                stars=12,
                created="2024-01-01T00:00:00Z",
                about="repo",
                source="paper_export_sync",
            ),
            reason="No Github URL found from discovery",
        )

    monkeypatch.setattr(paper_export, "sync_paper_seed", fake_sync_paper_seed)

    outcome = await paper_export.build_paper_outcome(
        3,
        PaperSeed(name="Paper A", url="https://arxiv.org/abs/2501.00001"),
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
    )

    assert outcome.reason == "No Github URL found from discovery"
    assert outcome.record.github == "https://github.com/foo/bar"
    assert outcome.record.stars == ""
    assert outcome.record.created == "2024-01-01T00:00:00Z"
    assert outcome.record.about == "repo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_paper_export.py::test_build_paper_outcome_routes_seed_through_core_sync_helper tests/test_paper_export.py::test_build_paper_outcome_suppresses_stars_but_keeps_created_and_about_when_reason_present -q`
Expected: FAIL because `src/shared/paper_export.py` still calls `process_single_paper`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/shared/paper_export.py
from dataclasses import replace
from pathlib import Path

from src.core.output_adapters import FreshCsvExportAdapter
from src.core.paper_export_sync import sync_paper_seed
from src.shared.async_batch import iter_bounded_as_completed, resolve_worker_count
from src.shared.csv_io import write_rows_to_csv_path
from src.shared.papers import ConversionResult, PaperOutcome, PaperSeed, sort_paper_export_rows


async def build_paper_outcome(
    index: int,
    seed: PaperSeed,
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
) -> PaperOutcome:
    result = await sync_paper_seed(
        seed,
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

    row = FreshCsvExportAdapter().to_csv_row(result.record, sort_index=index)
    if result.reason is not None:
        row = replace(row, stars="")

    return PaperOutcome(
        index=index,
        record=row,
        reason=result.reason,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_paper_export.py::test_build_paper_outcome_routes_seed_through_core_sync_helper tests/test_paper_export.py::test_build_paper_outcome_suppresses_stars_but_keeps_created_and_about_when_reason_present -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shared/paper_export.py tests/test_paper_export.py
git commit -m "refactor: route paper export through core sync helper"
```

### Task 3: Thin `paper_enrichment` And Remove Pipeline-Local Seed Export Adapters

**Files:**
- Modify: `src/shared/paper_enrichment.py`
- Modify: `src/url_to_csv/pipeline.py`
- Modify: `src/arxiv_relations/pipeline.py`
- Modify: `tests/test_paper_enrichment.py`
- Modify: `tests/test_url_to_csv.py`
- Modify: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paper_enrichment.py
@pytest.mark.anyio
async def test_process_single_paper_routes_through_sync_paper_record_facade(monkeypatch):
    import src.shared.paper_enrichment as paper_enrichment

    calls = {}

    async def fake_sync_paper_record(record, **kwargs):
        calls["record"] = record
        calls["allow_title_search"] = kwargs["allow_title_search"]
        calls["allow_github_discovery"] = kwargs["allow_github_discovery"]
        calls["trust_existing_github"] = kwargs["trust_existing_github"]
        return PaperSyncResult(
            record=Record.from_source(
                name="Paper A",
                url="https://arxiv.org/abs/2603.20000v2",
                github="https://github.com/foo/bar",
                stars=17,
                created="2024-01-01T00:00:00Z",
                about="repo",
                source="paper_export_sync",
                trusted_fields={"github"},
            ).with_supporting_state(
                facts=RecordFacts(
                    normalized_url="https://arxiv.org/abs/2603.20000v2",
                    canonical_arxiv_url="https://arxiv.org/abs/2603.20000",
                    github_source="existing",
                )
            ),
            reason=None,
        )

    monkeypatch.setattr(paper_enrichment, "sync_paper_record", fake_sync_paper_record)

    result = await paper_enrichment.process_single_paper(
        PaperEnrichmentRequest(
            title="Paper A",
            raw_url="https://arxiv.org/pdf/2603.20000v2.pdf",
            existing_github_url="https://github.com/foo/bar",
            allow_title_search=False,
            allow_github_discovery=True,
            trust_existing_github=True,
        ),
        discovery_client=types.SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=types.SimpleNamespace(get_repo_metadata=AsyncMock()),
    )

    assert calls["record"].github.value == "https://github.com/foo/bar"
    assert calls["allow_title_search"] is False
    assert calls["allow_github_discovery"] is True
    assert calls["trust_existing_github"] is True
    assert result.github_source == "existing"
    assert result.created == "2024-01-01T00:00:00Z"
    assert result.about == "repo"


# tests/test_url_to_csv.py
@pytest.mark.anyio
async def test_export_url_to_csv_passes_fetched_seeds_directly_to_export(monkeypatch, tmp_path: Path):
    exported = {}

    async def fake_fetch_paper_seeds_from_url(*args, **kwargs):
        return FetchedSeedsResult(
            seeds=[PaperSeed(name="Paper A", url="https://arxiv.org/abs/2501.00001")],
            csv_path=tmp_path / "papers.csv",
        )

    async def fake_export_paper_seeds_to_csv(seeds, csv_path, **kwargs):
        exported["seeds"] = seeds
        return ConversionResult(csv_path=csv_path, resolved=1, skipped=[])

    monkeypatch.setattr(url_pipeline, "fetch_paper_seeds_from_url", fake_fetch_paper_seeds_from_url)
    monkeypatch.setattr(url_pipeline, "export_paper_seeds_to_csv", fake_export_paper_seeds_to_csv)

    await export_url_to_csv(
        "https://arxivxplorer.com/?q=test&cats=cs.CV&year=2026",
        search_client=SimpleNamespace(),
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
    )

    assert exported["seeds"] == [
        PaperSeed(name="Paper A", url="https://arxiv.org/abs/2501.00001")
    ]


# tests/test_arxiv_relations.py
@pytest.mark.anyio
async def test_export_arxiv_relations_passes_normalized_seeds_directly_to_export(monkeypatch, tmp_path: Path):
    export_calls = []

    async def fake_normalize_related_work_candidates_to_seeds(*args, **kwargs):
        return [PaperSeed(name="Mapped Related", url="https://doi.org/10.1145/example")]

    async def fake_export_paper_seeds_to_csv(seeds, csv_path, **kwargs):
        export_calls.append(seeds)
        return ConversionResult(csv_path=csv_path, resolved=0, skipped=[])

    monkeypatch.setattr(
        "src.arxiv_relations.pipeline.normalize_related_work_candidates_to_seeds",
        fake_normalize_related_work_candidates_to_seeds,
    )
    monkeypatch.setattr(
        "src.arxiv_relations.pipeline.export_paper_seeds_to_csv",
        fake_export_paper_seeds_to_csv,
    )

    await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=FakeArxivClient(),
        semanticscholar_graph_client=FakeSemanticScholarGraphClient(),
        discovery_client=SimpleNamespace(),
        github_client=SimpleNamespace(),
        output_dir=tmp_path,
    )

    assert export_calls == [
        [PaperSeed(name="Mapped Related", url="https://doi.org/10.1145/example")],
        [PaperSeed(name="Mapped Related", url="https://doi.org/10.1145/example")],
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_paper_enrichment.py::test_process_single_paper_routes_through_sync_paper_record_facade tests/test_url_to_csv.py::test_export_url_to_csv_passes_fetched_seeds_directly_to_export tests/test_arxiv_relations.py::test_export_arxiv_relations_passes_normalized_seeds_directly_to_export -q`
Expected: FAIL because `paper_enrichment` still owns the sync logic and both pipelines still rewrite seeds through `_adapt_paper_seeds_for_export()`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/shared/paper_enrichment.py
from dataclasses import dataclass

from src.core.paper_export_sync import sync_paper_record
from src.core.record_model import Record, RecordFacts


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
) -> PaperEnrichmentResult:
    title = (request.title or "").strip()
    raw_url = (request.raw_url or "").strip()
    record = Record.from_source(
        name=title,
        url=raw_url,
        github=request.existing_github_url,
        source="paper_enrichment",
        trusted_fields={"github"} if request.trust_existing_github else set(),
    ).with_supporting_state(
        facts=RecordFacts(
            normalized_url=request.precomputed_normalized_url,
            canonical_arxiv_url=request.precomputed_canonical_arxiv_url,
            url_resolution_authoritative=request.url_resolution_authoritative,
        )
    )

    result = await sync_paper_record(
        record,
        allow_title_search=request.allow_title_search,
        allow_github_discovery=request.allow_github_discovery,
        trust_existing_github=request.trust_existing_github,
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

    updated = result.record
    return PaperEnrichmentResult(
        title=title,
        raw_url=raw_url,
        normalized_url=updated.facts.normalized_url,
        canonical_arxiv_url=updated.facts.canonical_arxiv_url,
        github_url=updated.github.value if isinstance(updated.github.value, str) else None,
        github_source=updated.facts.github_source,
        stars=updated.stars.value if isinstance(updated.stars.value, int) else None,
        created=updated.created.value if isinstance(updated.created.value, str) else None,
        about=updated.about.value if isinstance(updated.about.value, str) else None,
        reason=result.reason,
    )


# src/url_to_csv/pipeline.py
export_seeds = fetched.seeds
return await export_paper_seeds_to_csv(export_seeds, fetched.csv_path, ...)


# src/arxiv_relations/pipeline.py
references_result = await export_paper_seeds_to_csv(reference_seeds, references_csv_path, ...)
citations_result = await export_paper_seeds_to_csv(citation_seeds, citations_csv_path, ...)

# delete the local _adapt_paper_seeds_for_export() and _string_value() helpers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_paper_enrichment.py::test_process_single_paper_routes_through_sync_paper_record_facade tests/test_url_to_csv.py::test_export_url_to_csv_passes_fetched_seeds_directly_to_export tests/test_arxiv_relations.py::test_export_arxiv_relations_passes_normalized_seeds_directly_to_export -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shared/paper_enrichment.py src/url_to_csv/pipeline.py src/arxiv_relations/pipeline.py tests/test_paper_enrichment.py tests/test_url_to_csv.py tests/test_arxiv_relations.py
git commit -m "refactor: thin paper enrichment and drop seed export adapters"
```

### Task 4: Verify The Whole Cleanup And Check For Leftover Bridge Code

**Files:**
- Modify: none expected
- Test: `tests/test_paper_export_sync.py`
- Test: `tests/test_paper_enrichment.py`
- Test: `tests/test_paper_export.py`
- Test: `tests/test_url_to_csv.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Run the focused cleanup suite**

Run: `uv run python -m pytest tests/test_paper_export_sync.py tests/test_paper_enrichment.py tests/test_paper_export.py tests/test_url_to_csv.py tests/test_arxiv_relations.py -q`
Expected: PASS

- [ ] **Step 2: Run the full test suite**

Run: `uv run python -m pytest -q`
Expected: PASS

- [ ] **Step 3: Check that the duplicated helper bridge is actually gone**

Run: `rg -n "_adapt_paper_seeds_for_export|process_single_paper" src/url_to_csv/pipeline.py src/arxiv_relations/pipeline.py src/shared/paper_export.py`
Expected: no matches

- [ ] **Step 4: Inspect the final diff before handing back**

Run: `git diff --stat master...HEAD`
Expected: only the planned core/helper, export, pipeline, and test files changed
