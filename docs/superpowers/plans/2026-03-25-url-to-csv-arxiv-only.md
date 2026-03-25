# URL To CSV ArXiv-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all `url -> csv` modes export only arXiv-backed rows, keyed and sorted by canonical arXiv URL, while leaving Notion mode unchanged.

**Architecture:** Keep each source crawler isolated, then add one shared normalization stage in the URL-to-CSV pipeline that resolves or filters rows before Github/Stars enrichment. Reuse existing CSV sorting through shared paper record helpers, but tighten the invariant so URL-mode rows are always arXiv-backed before export.

**Tech Stack:** Python, asyncio, aiohttp, pytest

---

### Task 1: Add failing tests for shared arXiv-only normalization

**Files:**
- Modify: `tests/test_url_to_csv.py`
- Test: `tests/test_url_to_csv.py`

- [ ] Step 1: Write failing tests for URL-mode filtering of non-arXiv rows
- [ ] Step 2: Run `uv run pytest -q tests/test_url_to_csv.py`
- [ ] Step 3: Implement minimal shared normalization support
- [ ] Step 4: Re-run `uv run pytest -q tests/test_url_to_csv.py`

### Task 2: Move URL-mode filtering into shared pipeline

**Files:**
- Modify: `src/url_to_csv/pipeline.py`
- Modify: `src/shared/paper_export.py`
- Modify: `src/shared/papers.py`
- Test: `tests/test_url_to_csv.py`
- Test: `tests/test_shared_papers.py`

- [ ] Step 1: Add one shared normalization stage before enrichment
- [ ] Step 2: Ensure unresolved rows are dropped before Github/Stars work starts
- [ ] Step 3: Tighten shared sorting assumptions for arXiv-backed URL exports
- [ ] Step 4: Run `uv run pytest -q tests/test_url_to_csv.py tests/test_shared_papers.py`

### Task 3: Align Semantic Scholar raw collection with shared normalization

**Files:**
- Modify: `src/url_to_csv/semanticscholar.py`
- Test: `tests/test_semanticscholar.py`

- [ ] Step 1: Keep Semantic Scholar collection focused on raw candidates plus arXiv resolution hooks
- [ ] Step 2: Remove behavior that writes unresolved title-only rows into final export inputs
- [ ] Step 3: Run `uv run pytest -q tests/test_semanticscholar.py`

### Task 4: Verify full repository behavior

**Files:**
- Modify: `tests/test_csv_update.py` if needed
- Test: full suite

- [ ] Step 1: Run `uv run pytest -q`
- [ ] Step 2: Smoke test one real Semantic Scholar URL export
- [ ] Step 3: Confirm final CSV contains only arXiv URLs and remains sorted descending by URL
