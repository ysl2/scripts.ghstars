# arXiv.org Generic Collections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `arxiv.org -> csv` so structurally valid arXiv collection pages such as `/catchup/...` and archive-style `/list/.../YYYY-MM` are supported, while failing whenever full traversal cannot be guaranteed.

**Architecture:** Keep a single arXiv adapter in `src/url_to_csv/arxiv_org.py`, but formalize three routed families: `list-like`, `catchup-like`, and `search-like`. Reuse the existing `dt/dd` list parser and `li.arxiv-result` search parser, expand URL detection intentionally, and add explicit completeness checks so ambiguous multi-page collections fail instead of exporting partial CSVs.

**Tech Stack:** Python 3.12, asyncio, aiohttp, pytest, regex-based HTML parsing, uv

## Support Boundary

- Supported arXiv.org collection shapes are `/list/<category>/recent`, `/list/<category>/new`, archive-style `/list/<category>/YYYY-MM`, exact-shape `/catchup/<category>/YYYY-MM-DD`, and `/search/?...`.
- `list-like` pages, including archive pages, reuse the shared `dt/dd` extraction and `skip/show` pagination path.
- `catchup` pages reuse list extraction but must fail when the reported total exceeds the rows visible on the page, because partial export is not acceptable.

---

### Task 1: Expand URL detection and filename coverage for new arXiv collection shapes

**Files:**
- Modify: `src/url_to_csv/arxiv_org.py`
- Modify: `tests/test_arxiv_org.py`
- Test: `tests/test_url_sources.py`

- [ ] **Step 1: Write the failing detection tests**

Add tests proving:
- `/catchup/cs.CV/2026-03-26` is accepted as an arXiv collection URL
- `/list/cs.CV/2026-03` is accepted as an intentional archive-style arXiv collection URL
- `/catchup/...` and `/list/.../YYYY-MM` generate readable timestamped CSV paths

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `uv run pytest tests/test_arxiv_org.py tests/test_url_sources.py -q`
Expected: FAIL because `/catchup/...` is not currently recognized and naming for catchup pages is not implemented.

- [ ] **Step 3: Write the minimal implementation**

Update `src/url_to_csv/arxiv_org.py` to:
- accept `/catchup/...` URLs in `is_supported_arxiv_org_url()`
- keep `/list/...` URLs accepted, including archive-style `/list/.../YYYY-MM`
- derive readable timestamped CSV paths for catchup pages using category + date

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `uv run pytest tests/test_arxiv_org.py tests/test_url_sources.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/url_to_csv/arxiv_org.py tests/test_arxiv_org.py tests/test_url_sources.py
git commit -m "feat: detect generic arxiv collection urls"
```

### Task 2: Generalize list-like page extraction while preserving existing list/search behavior

**Files:**
- Modify: `src/url_to_csv/arxiv_org.py`
- Modify: `tests/test_arxiv_org.py`

- [ ] **Step 1: Write the failing extraction tests**

Add tests proving:
- archive-style `/list/cs.CV/2026-03` pages are parsed through the existing `dt/dd` list-entry flow
- catchup-style HTML with `dt/dd` and `.list-title` extracts canonical `(title, abs-url)` seeds correctly
- existing `/list/.../recent`, `/list/.../new`, and `/search/...` extraction tests still express the expected parser families

- [ ] **Step 2: Run the focused extraction tests to verify they fail**

Run: `uv run pytest tests/test_arxiv_org.py -q`
Expected: FAIL because catchup-specific extraction/routing is not yet modeled intentionally.

- [ ] **Step 3: Write the minimal implementation**

Refactor `src/url_to_csv/arxiv_org.py` so:
- `list-like` pages share one extraction path
- `search-like` pages keep the current search extraction path
- catchup pages reuse the list-entry parser instead of introducing a separate duplicate parser

- [ ] **Step 4: Run the focused extraction tests to verify they pass**

Run: `uv run pytest tests/test_arxiv_org.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/url_to_csv/arxiv_org.py tests/test_arxiv_org.py
git commit -m "refactor: route arxiv collection pages by structure"
```

### Task 3: Enforce “full export or fail” for catchup and other ambiguous collection pages

**Files:**
- Modify: `src/url_to_csv/arxiv_org.py`
- Modify: `tests/test_arxiv_org.py`
- Modify: `tests/test_url_to_csv.py`

- [ ] **Step 1: Write the failing completeness tests**

Add tests proving:
- catchup succeeds when parsed row count already equals the reported total
- catchup fails with an explicit error when `total_entries > current_page_rows` and no reliable next-page construction rule is known
- standard `/list/...` pagination still crawls all pages with `skip/show`
- standard `/search/...` pagination still crawls all pages with `start`

- [ ] **Step 2: Run the focused completeness tests to verify they fail**

Run: `uv run pytest tests/test_arxiv_org.py tests/test_url_to_csv.py -q`
Expected: FAIL because catchup completeness semantics do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

Implement explicit routing rules in `src/url_to_csv/arxiv_org.py`:
- `/search` keeps `start`-based traversal
- `/list/...` keeps `skip/show` traversal
- `/catchup/...` reuses list extraction but:
  - returns immediately when current-page rows cover the reported total
  - raises a completeness error when total count proves the page is incomplete and no reliable traversal rule is available

- [ ] **Step 4: Run the focused completeness tests to verify they pass**

Run: `uv run pytest tests/test_arxiv_org.py tests/test_url_to_csv.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/url_to_csv/arxiv_org.py tests/test_arxiv_org.py tests/test_url_to_csv.py
git commit -m "feat: fail incomplete generic arxiv collection exports"
```

### Task 4: Re-verify end-to-end URL mode behavior and document support boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-03-26-arxiv-org-generic-collections.md`
- Test: `tests/test_dispatch.py`

- [ ] **Step 1: Update docs**

Document:
- `/catchup/...` support
- archive-style `/list/.../YYYY-MM` support
- the rule that ambiguous collection pages fail rather than exporting partial CSVs

- [ ] **Step 2: Run the targeted URL-mode regression suite**

Run: `uv run pytest tests/test_arxiv_org.py tests/test_url_sources.py tests/test_dispatch.py tests/test_url_to_csv.py -q`
Expected: PASS

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 4: Run real verification commands**

Run:

```bash
uv run main.py 'https://arxiv.org/catchup/cs.CV/2026-03-26'
uv run main.py 'https://arxiv.org/list/cs.CV/2026-03'
```

Verify:
- both URLs are accepted by URL mode
- CSV naming is readable and timestamped
- if a page cannot be proven complete, the command fails explicitly instead of silently writing a partial CSV

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/plans/2026-03-26-arxiv-org-generic-collections.md
git commit -m "docs: describe generic arxiv collection support"
```
