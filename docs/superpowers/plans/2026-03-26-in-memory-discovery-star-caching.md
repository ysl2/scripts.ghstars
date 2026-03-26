# In-Memory Discovery And Star Caching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce duplicate network requests within one CLI run by caching repeated GitHub discovery and GitHub star lookups in memory.

**Architecture:** Keep caching inside the existing network clients. Add a per-run discovery cache and in-flight request map to `DiscoveryClient`, and add a per-run star cache plus in-flight request map to `GitHubClient`. Leave pipeline code unchanged so CSV, URL, and Notion modes all benefit automatically.

**Tech Stack:** Python 3.12, asyncio, aiohttp, pytest, uv

---

### Task 1: Lock in cache behavior with failing tests

**Files:**
- Modify: `tests/test_shared_services.py`

- [ ] **Step 1: Write the failing discovery-cache test**

Add a test proving two concurrent `DiscoveryClient.resolve_github_url(...)` calls for the same paper trigger only one upstream discovery request and both get the same repo URL.

- [ ] **Step 2: Write the failing star-cache test**

Add a test proving two concurrent `GitHubClient.get_star_count(...)` calls for the same repo trigger only one HTTP request and both get the same star count.

- [ ] **Step 3: Run the focused tests to verify they fail**

Run: `uv run pytest tests/test_shared_services.py -q`
Expected: FAIL because duplicate requests are still executed independently.

- [ ] **Step 4: Commit**

```bash
git add tests/test_shared_services.py
git commit -m "test: cover in-memory discovery and star caching"
```

### Task 2: Implement minimal client-side caches

**Files:**
- Modify: `src/shared/discovery.py`
- Modify: `src/shared/github.py`

- [ ] **Step 1: Add DiscoveryClient cache and in-flight dedupe**

Store successful discovery results by normalized paper key and collapse concurrent lookups for the same key onto one task.

- [ ] **Step 2: Add GitHubClient cache and in-flight dedupe**

Store successful star lookups, cache stable `Repository not found` misses, and collapse concurrent lookups for the same repo key onto one task.

- [ ] **Step 3: Run the focused tests to verify they pass**

Run: `uv run pytest tests/test_shared_services.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/shared/discovery.py src/shared/github.py
git commit -m "perf: cache duplicate discovery and star lookups"
```

### Task 3: Verify shared behavior

**Files:**
- Modify: `docs/superpowers/specs/2026-03-26-in-memory-discovery-star-caching-design.md`
- Modify: `docs/superpowers/plans/2026-03-26-in-memory-discovery-star-caching.md`

- [ ] **Step 1: Run a focused shared regression suite**

Run: `uv run pytest tests/test_shared_services.py tests/test_csv_update.py tests/test_url_to_csv.py -q`
Expected: PASS

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 3: Commit docs if needed**

```bash
git add docs/superpowers/specs/2026-03-26-in-memory-discovery-star-caching-design.md docs/superpowers/plans/2026-03-26-in-memory-discovery-star-caching.md
git commit -m "docs: record in-memory caching behavior"
```
