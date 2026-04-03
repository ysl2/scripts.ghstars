# Closeout Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed metadata-refresh bugs, then align Notion parsing, URL support checks, concurrency bounds, and docs/logging with the current architecture.

**Architecture:** Keep the record-centric core intact and make the smallest possible behavior fixes around it. Lock each bug with tests first, then update the shared services and mode pipelines so all entrypoints agree on failure propagation, empty-About semantics, and supported-input checks.

**Tech Stack:** Python 3.12, pytest, asyncio, aiohttp, SQLite-backed caches

---

### Task 1: Lock metadata failure propagation bugs

**Files:**
- Modify: `tests/test_csv_update.py`
- Modify: `tests/test_notion_mode.py`

- [x] Add a CSV test proving repo metadata refresh failure is surfaced when the row already has `Github`, `Stars`, `Created`, and `About`.
- [x] Add a Notion test proving repo metadata refresh failure is treated as skipped, not updated, when the page already has managed values.
- [x] Run the targeted tests and confirm they fail for the expected reason before implementation changes.

### Task 2: Lock empty remote About semantics

**Files:**
- Modify: `tests/test_shared_services.py`
- Modify: `tests/test_csv_update.py`
- Modify: `tests/test_notion_mode.py`

- [x] Add a GitHub client test for `description: null`.
- [x] Add CSV and Notion tests proving remote `description: null` clears local `About`.
- [x] Run the targeted tests and confirm they fail before implementation changes.

### Task 3: Fix metadata sync behavior

**Files:**
- Modify: `src/shared/github.py`
- Modify: `src/core/record_sync.py`
- Modify: `src/csv_update/pipeline.py`
- Modify: `src/notion_sync/pipeline.py`

- [x] Implement the smallest shared fix so remote `description: null` becomes an explicit empty About value.
- [x] Make CSV and Notion pipelines treat `repo_metadata_error` the same way paper export already does.
- [x] Re-run the targeted tests until they pass.

### Task 4: Lock and fix Notion title parsing

**Files:**
- Modify: `tests/test_input_adapters.py`
- Modify: `tests/test_notion_mode.py`
- Modify: `src/core/input_adapters.py`
- Modify: `src/notion_sync/pipeline.py`

- [x] Add tests for multi-fragment Notion titles.
- [x] Update Notion title extraction to join all title fragments consistently.
- [x] Re-run the targeted tests until they pass.

### Task 5: Lock and fix supported URL checks

**Files:**
- Modify: `tests/test_url_sources.py`
- Modify: `src/url_to_csv/arxivxplorer.py`
- Modify: `src/url_to_csv/semanticscholar.py`

- [x] Add negative tests for missing or blank `q` parameters.
- [x] Tighten `is_supported_*` to match parser and README rules.
- [x] Re-run the targeted tests until they pass.

### Task 6: Bound remaining task fan-out

**Files:**
- Modify: `tests/test_arxiv_relations.py`
- Modify: `src/arxiv_relations/pipeline.py`
- Modify: `src/url_to_csv/arxiv_org.py`

- [x] Add tests that assert bounded worker scheduling instead of full eager fan-out.
- [x] Replace eager task creation with `iter_bounded_as_completed`.
- [x] Re-run the targeted tests until they pass.

### Task 7: Align docs and logs

**Files:**
- Modify: `README.md`
- Modify: `src/notion_sync/runner.py`

- [x] Fix the documented pytest command to match the project environment.
- [x] Make the Notion runner summary line describe what the query actually fetched.

### Task 8: Verify end to end

**Files:**
- No file changes required

- [x] Run the focused test files touched by this work.
- [x] Run the full test suite with `uv run python -m pytest -q`.
- [x] Review the diff for scope creep before reporting completion.
