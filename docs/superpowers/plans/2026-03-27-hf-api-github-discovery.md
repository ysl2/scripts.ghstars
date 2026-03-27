# HF API GitHub Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fragile Hugging Face HTML-based GitHub repo discovery for arXiv papers with Hugging Face official JSON API calls, while keeping GitHub stars lookup unchanged.

**Architecture:** arXiv paper discovery will first call `GET /api/papers/{arxiv_id}` and read `githubRepo` directly from JSON. Only exact misses will trigger `GET /api/papers/search?q=<title>&limit=1`, sharing the same Hugging Face rate limiter but using a stricter search concurrency budget. AlphaXiv will no longer participate in arXiv repo discovery.

**Tech Stack:** Python, aiohttp, pytest, existing async rate limiting utilities

---

### Task 1: Lock Down Discovery Behavior With Tests

**Files:**
- Modify: `tests/test_shared_services.py`

- [ ] Add failing tests for exact HF paper payload discovery.
- [ ] Add failing tests for exact miss -> HF search fallback with `limit=1`.
- [ ] Add failing tests proving exact request errors do not trigger search fallback.
- [ ] Run the focused tests and confirm they fail for the expected reason.

### Task 2: Implement HF API Discovery

**Files:**
- Modify: `src/shared/discovery.py`

- [ ] Add JSON helpers for reading `githubRepo` from HF paper payloads.
- [ ] Add official HF API request methods for exact paper lookup and search lookup.
- [ ] Add HF minimum interval clamping to `0.7s` and a tighter search concurrency budget.
- [ ] Update arXiv GitHub resolution to use HF exact first, then search fallback, without AlphaXiv.
- [ ] Keep non-arXiv Semantic Scholar page scraping behavior unchanged.

### Task 3: Verify Integration

**Files:**
- Modify if needed: `src/url_to_csv/runner.py`, `src/csv_update/runner.py`, `src/notion_sync/runner.py`

- [ ] Confirm discovery client construction still flows through existing runners.
- [ ] Only touch runner/config code if needed for visibility or plumbing.
- [ ] Run focused tests, then full `uv run pytest`.
- [ ] Run a live spot-check against a few known arXiv IDs on HF API.
