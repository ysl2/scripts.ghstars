# Shared Single-Paper Processing Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor current `master` so one shared single-paper engine owns generic per-paper business rules, with `csv_update` and `notion_sync` migrated in Phase A and `url -> csv` plus relation export migrated in Phase B.

**Architecture:** Evolve `src/shared/paper_enrichment.py` into the shared engine boundary and keep `src/shared/paper_export.py` as the batch export orchestrator. Keep parsing and writeback local to each mode, make `overview` / `abs` a global final-valid-repo rule, and keep relation normalization plus retained-row behavior inside `src/arxiv_relations/*`.

**Tech Stack:** Python 3.12, asyncio, aiohttp, pathlib, pytest, Notion API, GitHub API, AlphaXiv content cache

---

## Current-Master Grounding

This plan is grounded on `master` at `4f95fd156190429e40aa67c7f62bf1555fe17960`.

Current code layout on that commit:

- `src/shared/paper_enrichment.py`
  - Only exposes `EnrichedPaper` and `enrich_paper(...)`.
  - Owns canonical arXiv or Semantic Scholar normalization, GitHub discovery, repo validation, and star lookup.
- `src/csv_update/pipeline.py`
  - Reuses `enrich_paper(...)`.
  - Still launches `PaperContentCache.ensure_overview_path(...)` and `ensure_abs_path(...)` in parallel outside the shared boundary.
- `src/notion_sync/pipeline.py`
  - Still owns its own arXiv resolution, GitHub validation, discovery, and star lookup flow.
- `src/shared/paper_export.py`
  - Already serves as the shared batch export path for `url_to_csv` and `arxiv_relations`.
  - Still calls `enrich_paper(...)` directly.
- `src/arxiv_relations/pipeline.py`
  - Already owns relation-specific normalization, resolution cache usage, fallback retention, deduplication, and ordering.

That split is the basis for the phase cut in this plan:

- Phase A fixes the worst business-rule drift first: `csv_update` and `notion_sync`.
- Phase B then migrates the export path onto the same boundary without moving relation-local logic into generic code.

## File Map

### Phase A

- Modify: `src/shared/paper_enrichment.py`
  - Add the shared single-paper request/result contract.
  - Add the new engine entrypoint.
  - Keep `enrich_paper(...)` as a thin compatibility wrapper until Phase B finishes.
- Modify: `src/shared/paper_content.py`
  - Add an engine-friendly cache-warming operation for canonical arXiv papers.
  - Keep the current per-kind path helpers only as compatibility wrappers if they are still needed during migration.
- Modify: `src/csv_update/pipeline.py`
  - Replace the row-level `enrich_paper(...)` + direct content fan-out with a local request builder and row sink around the engine call.
- Modify: `src/csv_update/runner.py`
  - Keep current runtime setup and continue passing `PaperContentCache`.
- Modify: `src/notion_sync/pipeline.py`
  - Keep Notion property parsing and unsupported-value classification local.
  - Replace duplicated generic resolution logic with a local request builder plus local update sink around the engine call.
- Modify: `src/notion_sync/runner.py`
  - Build `AlphaXivContentClient` and `PaperContentCache` and pass them into the page-processing path.
- Create: `tests/test_paper_enrichment.py`
  - Focused engine tests.
- Modify: `tests/test_csv_update.py`
  - Update expectations from the old CSV-local content rule to the new global final-valid-repo rule.
- Modify: `tests/test_notion_mode.py`
  - Add coverage for engine-backed processing, content warming, and preserved Notion property semantics.

### Phase B

- Modify: `src/shared/paper_export.py`
  - Build engine requests from `PaperSeed`.
  - Keep batch concurrency, progress, and CSV writing here.
- Modify: `src/url_to_csv/pipeline.py`
  - Thread `content_cache` through the export call into `paper_export`.
- Modify: `src/url_to_csv/runner.py`
  - Build `AlphaXivContentClient` and `PaperContentCache`.
- Modify: `src/arxiv_relations/pipeline.py`
  - Thread `content_cache` into the existing export handoff only.
  - Do not move relation normalization or retained-row logic out of this module.
- Modify: `src/arxiv_relations/runner.py`
  - Build `AlphaXivContentClient` and `PaperContentCache`.
- Modify: `tests/test_url_to_csv.py`
  - Cover the global content rule and cache reuse once export goes through the new engine boundary.
- Modify: `tests/test_arxiv_relations.py`
  - Cover content warming for arXiv-backed rows while preserving retained non-arXiv rows and current normalization behavior.

## Guardrails

- Do not revive `unify-csv-content-fetch` abstractions such as a generic `paper_engine`, `paper_task_builders`, or `paper_sinks` framework.
- Do not add shared adapter or sink modules unless a tiny helper is clearly forced by repeated duplication after the mode-local refactors.
- Keep parsing and writeback close to the owning modes:
  - CSV row parsing and mutation stay in `src/csv_update/pipeline.py`.
  - Notion property parsing and update payload construction stay in `src/notion_sync/pipeline.py`.
  - URL source crawling stays in `src/url_to_csv/*`.
  - Relation normalization and retained-row policy stay in `src/arxiv_relations/*`.
- Keep `PaperSeed` and the shared request/result contract business-focused. Do not thread CSV rows, Notion pages, output hints, or relation payloads through the engine.
- Preserve current-master behavior unless the spec explicitly changes it:
  - CLI dispatch stays unchanged.
  - CSV schema stays `Name, Url, Github, Stars`.
  - Notion property names and property-type handling stay unchanged.
  - Runtime cache locations stay unchanged.
  - Relation resolution cache semantics and retained non-arXiv relation rows stay unchanged.
- Keep reason strings stable where current tests already assert them, especially:
  - `No valid arXiv URL found`
  - `No Github URL found from discovery`
  - `Existing Github URL is not a valid GitHub repository`
  - `Discovered URL is not a valid GitHub repository`
  - `Unsupported Github field content`

## Global `overview` / `abs` Rule

This rule is part of the implementation plan and must be enforced explicitly in code and tests:

1. Determine the final GitHub repo URL first.
2. Validate that the final repo URL is a real GitHub repository URL.
3. If there is no final valid repo URL, do not warm `overview` or `abs`.
4. If there is a final valid repo URL and a canonical arXiv URL is available, warm only the missing content files.
5. Run cache warming before star lookup so a GitHub API failure does not suppress `overview` / `abs`.
6. If a valid repo exists but no canonical arXiv identity exists, keep the GitHub/stars outcome and skip content warming.

This is the intentional behavior change from current `master`: content warming is no longer a CSV-local side effect.

## Phase A

### Task 1: Lock the shared engine boundary with tests before moving callers

**Files:**
- Create: `tests/test_paper_enrichment.py`
- Modify: `tests/test_csv_update.py`
- Modify: `tests/test_notion_mode.py`

- [ ] **Step 1: Add focused engine tests for the shared boundary**

Cover these cases in `tests/test_paper_enrichment.py`:

- existing valid GitHub repo
- discovered GitHub repo
- invalid existing GitHub repo
- invalid discovered GitHub repo
- discovery miss
- title search allowed vs disabled
- valid repo plus canonical arXiv URL warms content
- valid repo plus GitHub API failure still warms content
- no valid repo skips content warming
- valid repo plus no canonical arXiv URL skips content warming but still returns the repo or star outcome

- [ ] **Step 2: Rewrite CSV tests that currently encode the old content rule**

Update `tests/test_csv_update.py` so it no longer expects content warming on rows that fail to produce a final valid repo. In particular:

- replace the current discovery-miss expectation with a skip that leaves content untouched
- replace the current row-level parallelism expectation with an engine-ordering expectation:
  - repo resolution first
  - content warming second
  - star lookup third
- keep row preservation, field preservation, and normalized URL behavior unchanged

- [ ] **Step 3: Expand Notion tests around local classification plus engine reuse**

Add or update `tests/test_notion_mode.py` so it proves:

- unsupported `Github` property content is still rejected locally before the engine runs
- valid existing GitHub values still skip discovery
- empty GitHub values still allow title-backed discovery through the shared engine
- `url` and `rich_text` GitHub property types are still updated correctly
- the runner now wires in content caching and the page flow uses the global `overview` / `abs` rule

- [ ] **Step 4: Run the focused test slice and confirm the intended failures**

Run:

```bash
uv run pytest tests/test_paper_enrichment.py tests/test_csv_update.py tests/test_notion_mode.py -q
```

Expected before implementation:

- the new engine tests fail because the shared request/result boundary and content rule do not exist yet
- CSV tests fail where they still observe the old parallel content behavior
- Notion tests fail where the shared engine and content cache have not been wired in yet

### Task 2: Evolve `paper_enrichment` and `paper_content` into the Phase A engine

**Files:**
- Modify: `src/shared/paper_enrichment.py`
- Modify: `src/shared/paper_content.py`
- Test: `tests/test_paper_enrichment.py`

- [ ] **Step 1: Introduce the shared request/result contract in `paper_enrichment.py`**

Add business-focused dataclasses in `src/shared/paper_enrichment.py`. The exact names can be chosen during implementation, but the fields should match the approved design:

- request fields:
  - `title`
  - `raw_url`
  - `existing_github_url`
  - `allow_title_search`
  - `allow_github_discovery`
- result fields:
  - `title`
  - `raw_url`
  - `normalized_url`
  - `github_url`
  - `github_source`
  - `stars`
  - `reason`

Do not add mode-local context to these dataclasses.

- [ ] **Step 2: Add a new shared engine entrypoint in `paper_enrichment.py`**

Implement one engine entrypoint that accepts the request plus:

- `discovery_client`
- `github_client`
- optional `arxiv_client`
- optional `content_cache`

The engine should perform these stages in order:

1. derive canonical identity from `raw_url`
2. if identity is still missing and `allow_title_search` is true, try title-backed arXiv resolution using current shared helpers
3. determine the final GitHub repo using either `existing_github_url` or discovery when allowed
4. validate the final GitHub repo URL
5. warm shared content cache only when the global rule says to
6. fetch stars
7. return the shared result

- [ ] **Step 3: Keep `enrich_paper(...)` as a compatibility wrapper**

Do not migrate `paper_export` in Phase A. Instead, make `enrich_paper(...)` call the new engine with a request built from the old parameters and translate the result back into `EnrichedPaper`.

That keeps current URL export and relation export behavior stable while Phase A moves only `csv_update` and `notion_sync`.

- [ ] **Step 4: Add an engine-friendly content cache operation**

In `src/shared/paper_content.py`, add one method conceptually equivalent to:

- ensure local content cache for this canonical arXiv paper

That method should:

- check for both `cache/overview/<arxiv_id>.md` and `cache/abs/<arxiv_id>.md`
- fetch only the missing artifact(s)
- stay mode-agnostic

Keep `ensure_overview_path(...)` and `ensure_abs_path(...)` only as compatibility wrappers if they are still needed during the migration window.

- [ ] **Step 5: Re-run the focused engine tests**

Run:

```bash
uv run pytest tests/test_paper_enrichment.py -q
```

Expected:

- PASS for the new engine boundary and global content rule cases

### Task 3: Migrate `csv_update` to a local request builder and row sink

**Files:**
- Modify: `src/csv_update/pipeline.py`
- Modify: `tests/test_csv_update.py`

- [ ] **Step 1: Replace row-local orchestration with one engine call**

In `build_csv_row_outcome(...)`, remove the direct fan-out to:

- `enrich_paper(...)`
- `_ensure_content_path(..., kind="overview")`
- `_ensure_content_path(..., kind="abs")`

Build one shared-engine request from the row instead:

- `title`: the current row title fallback logic
- `raw_url`: current `Url`
- `existing_github_url`: current `Github`
- `allow_title_search=False`
- `allow_github_discovery=True`

- [ ] **Step 2: Keep all CSV parsing and row mutation local**

After the engine call, keep row writeback decisions in `src/csv_update/pipeline.py`:

- update `Url` when the engine produced a normalized URL
- update `Github` when the engine produced one
- update `Stars` only when stars were resolved successfully
- preserve unrelated columns, field order, row order, and atomic rewrite behavior

Do not move row mutation rules into `paper_enrichment.py`.

- [ ] **Step 3: Remove the CSV-local content helper path**

Delete `_ensure_content_path(...)` and any row-level content tasks from `src/csv_update/pipeline.py`. After this task, CSV content warming should happen only via the shared engine.

- [ ] **Step 4: Re-run the CSV regression tests**

Run:

```bash
uv run pytest tests/test_csv_update.py -q
```

Expected:

- PASS with row preservation unchanged
- PASS with the intentional content-rule change reflected in the updated tests

### Task 4: Migrate `notion_sync` to a local request builder and update sink

**Files:**
- Modify: `src/notion_sync/pipeline.py`
- Modify: `src/notion_sync/runner.py`
- Modify: `tests/test_notion_mode.py`

- [ ] **Step 1: Keep Notion-only GitHub classification local**

Retain these behaviors in `src/notion_sync/pipeline.py`:

- property-type reading
- `classify_github_value(...)`
- unsupported-value handling such as `WIP`
- page title and property extraction
- Notion update payload construction

These rules stay local and do not move into the shared engine.

- [ ] **Step 2: Replace duplicated generic repo flow with the shared engine**

Shrink `resolve_repo_for_page(...)` into a local request-building helper or replace it entirely with one. The end state should be:

- Notion parsing stays local
- generic arXiv identity, repo validation, discovery, content warming, and stars move to the shared engine

For the request builder:

- pass `existing_github_url` only when the current Notion value is a valid GitHub repo
- keep `allow_title_search=True` only for the empty-GitHub cases where current Notion behavior relies on title-backed discovery
- keep unsupported GitHub values outside the engine and skip them locally

- [ ] **Step 3: Wire content caching into `run_notion_mode(...)`**

Match the current CSV runner pattern:

- build `AlphaXivContentClient`
- build `PaperContentCache`
- pass `content_cache` into the page-processing flow

Use the same shared cache directory and keep current concurrency and runtime-client setup unchanged.

- [ ] **Step 4: Keep the update sink local**

After the engine call:

- update the Notion `Github` property only when local classification says the field was empty and the engine source is discovered GitHub
- always preserve `url` vs `rich_text` property-type handling
- keep current success and skip reporting shape

- [ ] **Step 5: Re-run the Notion regression tests**

Run:

```bash
uv run pytest tests/test_notion_mode.py -q
```

Expected:

- PASS for local property behavior
- PASS for the engine-backed generic flow
- PASS for content-cache wiring

### Task 5: Verify Phase A and freeze the boundary before Phase B

**Files:**
- Modify: previous Phase A files only

- [ ] **Step 1: Run the Phase A focused suite**

Run:

```bash
uv run pytest tests/test_paper_enrichment.py tests/test_csv_update.py tests/test_notion_mode.py -q
```

Expected:

- PASS

- [ ] **Step 2: Run export regressions to confirm the compatibility wrapper preserved current behavior**

Run:

```bash
uv run pytest tests/test_url_to_csv.py tests/test_arxiv_relations.py -q
```

Expected:

- PASS with `paper_export` still on the compatibility path

- [ ] **Step 3: Run the full suite**

Run:

```bash
uv run pytest -q
```

Expected:

- PASS

- [ ] **Step 4: Confirm the intended Phase A endpoint**

Before starting Phase B, verify these statements are true:

- `paper_enrichment.py` now owns the shared single-paper business rules
- `csv_update` no longer orchestrates `overview` / `abs` itself
- `notion_sync` no longer duplicates generic repo resolution and stars logic
- `paper_export.py` is still unchanged except for relying on the compatibility wrapper path

## Phase B

Phase B should be implemented only after Phase A is stable. The work here is intentionally less detailed because the main boundary will already exist.

### Task 6: Move `paper_export` onto the direct engine contract

**Files:**
- Modify: `src/shared/paper_export.py`
- Modify: `src/shared/paper_enrichment.py`
- Modify: `tests/test_url_to_csv.py`
- Modify: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Build engine requests directly from `PaperSeed`**

Update `export_paper_seeds_to_csv(...)` so each seed becomes one shared-engine request. Keep `paper_export.py` responsible for:

- batch concurrency
- progress callbacks
- `PaperOutcome` assembly
- CSV writing

Do not move these responsibilities into a new generic framework.

- [ ] **Step 2: Preserve raw export URLs when normalization is unavailable**

When writing the final `PaperRecord`, prefer:

- `result.normalized_url` when present
- otherwise `result.raw_url`

This is required so retained non-arXiv relation rows still write their original DOI, landing-page, or OpenAlex URLs.

- [ ] **Step 3: Remove Phase A compatibility code only when no caller needs it**

If `paper_export.py`, `csv_update`, and `notion_sync` all use the direct engine contract, either:

- delete `enrich_paper(...)`, or
- keep it as a trivial shim if tests or external callers still depend on it

Do not remove it before all call sites are migrated.

### Task 7: Bring `url_to_csv` onto the final architecture without moving source adapters

**Files:**
- Modify: `src/url_to_csv/pipeline.py`
- Modify: `src/url_to_csv/runner.py`
- Modify: `src/shared/paper_export.py`
- Modify: `tests/test_url_to_csv.py`

- [ ] **Step 1: Keep source crawling and seed normalization local to `url_to_csv/*`**

Do not move:

- collection crawling
- source detection
- `normalize_paper_seeds_to_arxiv(...)`

The only migration here is the handoff from normalized seeds into the shared engine via `paper_export.py`.

- [ ] **Step 2: Thread content caching into URL export**

Match the CSV and Notion runners:

- build `AlphaXivContentClient`
- build `PaperContentCache`
- pass `content_cache` through `export_url_to_csv(...)` into `paper_export.py`

- [ ] **Step 3: Add URL export regressions for the global content rule**

Cover:

- arXiv-backed rows with a valid repo warm content
- repeated runs reuse local content files instead of refetching
- CSV shape and ordering stay unchanged

### Task 8: Bring relation export onto the final architecture without moving relation logic

**Files:**
- Modify: `src/arxiv_relations/pipeline.py`
- Modify: `src/arxiv_relations/runner.py`
- Modify: `src/shared/paper_export.py`
- Modify: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Keep relation normalization fully inside `src/arxiv_relations/*`**

Do not move any of these into generic shared code:

- OpenAlex fetch flow
- relation resolution cache usage
- title-resolution fallback ladder
- retained non-arXiv row decisions
- deduplication and ordering

- [ ] **Step 2: Thread `content_cache` only through the export handoff**

After relation normalization has already produced final related-paper rows, pass them into the shared export path with `content_cache` wired in.

- [ ] **Step 3: Preserve retained non-arXiv relation rows**

Retained DOI or landing-page rows must continue to:

- write their original URL
- skip content warming when no canonical arXiv identity exists
- keep relation-local normalization behavior unchanged

- [ ] **Step 4: Add relation export regressions for the final architecture**

Cover:

- valid repo plus canonical arXiv URL warms content
- valid repo plus missing stars still warms content
- retained non-arXiv rows skip warming but remain in the CSV
- relation resolution and deduplication behavior stays unchanged

### Task 9: Verify Phase B and remove dead migration code

**Files:**
- Modify: previous Phase B files only

- [ ] **Step 1: Run export-focused regressions**

Run:

```bash
uv run pytest tests/test_url_to_csv.py tests/test_arxiv_relations.py -q
```

Expected:

- PASS

- [ ] **Step 2: Re-run the full suite**

Run:

```bash
uv run pytest -q
```

Expected:

- PASS

- [ ] **Step 3: Remove dead compatibility helpers only after the suite is green**

Possible cleanup targets:

- wrapper-only `enrich_paper(...)` glue
- any old `paper_content.py` compatibility entrypoints no longer used after all callers share the engine boundary

Remove only what is proven unused by the final suite.

## Sequencing Notes

- Do not start Phase B until Phase A passes with `paper_export` still using the compatibility wrapper.
- The highest-risk Phase A regression is brittle test coupling to current reason strings and progress behavior; update tests first so the intended behavior change is explicit.
- The second highest-risk area is Notion runner wiring because `run_notion_mode(...)` currently does not construct `AlphaXivContentClient` or `PaperContentCache`.
- The highest-risk Phase B regression is accidentally moving relation-local normalization into generic shared code; keep that boundary explicit in code review.

## Self-Review

Coverage check against the approved spec:

- shared single-paper engine boundary: covered by Phase A Task 2
- Phase A migration of `csv_update` and `notion_sync`: covered by Phase A Tasks 3 and 4
- explicit global `overview` / `abs` rule: called out in its own section and enforced by Phase A Task 1 plus Task 2
- Phase B migration of `url -> csv` and relation export: covered by Phase B Tasks 6 through 8
- preserving relation-specific normalization inside `src/arxiv_relations/*`: enforced by guardrails and Phase B Task 8
- avoiding over-generic adapter or sink abstractions: enforced by guardrails and the file map
- evolving `paper_enrichment.py` and `paper_export.py` instead of replacing them: enforced by the architecture section and the phase cut

Contradiction scan:

- No Phase A task assumes `paper_export.py` already moved to the direct engine contract.
- No Phase B task pulls relation normalization into shared code.
- The global content rule is consistent across all phases: final valid repo first, content warming second, stars third.
- Raw URL preservation is explicitly called out for export mode so retained non-arXiv relation rows keep working after Phase B.

Residual implementation risks:

- exact reason strings are test-coupled and should be preserved intentionally
- the Notion tests will need to cover new runner wiring because this path does not currently build a content client
- if `paper_content.py` keeps both old path helpers and the new engine-friendly entrypoint during Phase A, cleanup discipline in Phase B matters so two content APIs do not linger indefinitely
