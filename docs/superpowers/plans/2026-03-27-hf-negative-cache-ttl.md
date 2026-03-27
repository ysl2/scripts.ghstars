# HF Negative Cache TTL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace threshold-based negative repo caching with time-based rechecks controlled by a configurable number of days.

**Architecture:** Keep `cache.db` as the shared project-level repo cache, but remove the no-repo count field and rely on `last_hf_exact_checked_at` to decide whether a negative cache row is still fresh. Positive repo mappings remain long-lived. Runners load one env-configured recheck interval and pass it into `DiscoveryClient`.

**Tech Stack:** Python, sqlite3, aiohttp, pytest

---

### Task 1: Lock TTL Behavior With Tests

**Files:**
- Modify: `tests/test_main.py`
- Modify: `tests/test_shared_services.py`
- Modify: `tests/test_repo_cache.py`

- [ ] Add failing tests for `HF_EXACT_NO_REPO_RECHECK_DAYS`.
- [ ] Replace threshold-based discovery tests with TTL-based skip/recheck tests.
- [ ] Add a failing test for migrating an old cache schema with `hf_exact_no_repo_count`.
- [ ] Run focused tests and confirm they fail first.

### Task 2: Implement TTL-Based Negative Cache

**Files:**
- Modify: `src/shared/settings.py`
- Modify: `src/shared/runtime.py`
- Modify: `src/shared/repo_cache.py`
- Modify: `src/shared/discovery.py`

- [ ] Replace threshold setting with recheck-days setting.
- [ ] Update repo-cache schema and migration logic.
- [ ] Remove no-repo count usage from discovery.
- [ ] Use `last_hf_exact_checked_at` freshness to decide whether to skip or retry HF exact.

### Task 3: Update Wiring And Docs

**Files:**
- Modify: `src/url_to_csv/runner.py`
- Modify: `src/csv_update/runner.py`
- Modify: `src/notion_sync/runner.py`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] Pass `HF_EXACT_NO_REPO_RECHECK_DAYS` from runtime config into `DiscoveryClient`.
- [ ] Remove threshold wording from docs.
- [ ] Document the default 7-day recheck interval.

### Task 4: Verify End-To-End

**Files:**
- No new files expected

- [ ] Run focused tests for runtime/discovery/cache.
- [ ] Run full `uv run pytest`.
- [ ] Inspect final diff for only intended TTL-based behavior changes.
