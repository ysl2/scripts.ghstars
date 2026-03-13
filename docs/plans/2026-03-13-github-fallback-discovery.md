# GitHub Fallback Discovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor the updater so each page first resolves a final GitHub repository candidate, then reuses the existing star update path; pages with empty/`WIP` `Github` values should try abstract lookup first and AlphaXiv fallback second.

**Architecture:** Keep the project in a single `main.py`, but extract pure helper functions for GitHub field classification, text/property extraction, arXiv parsing, GitHub URL discovery, and generic Notion property updates. `process_page()` becomes a single top-level control flow: classify → resolve final repo URL → fetch stars → update Notion.

**Tech Stack:** Python 3.12, aiohttp, notion-client, unittest

---

### Task 1: Add failing tests for new pure helper behavior

**Files:**
- Modify: `tests/test_config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing tests**

Add tests for:
- `classify_github_value(None) == "empty"`
- `classify_github_value("   ") == "empty"`
- `classify_github_value("WIP") == "wip"`
- `classify_github_value(" https://github.com/owner/repo ") == "valid_github"`
- `classify_github_value("https://example.com") == "other"`
- `find_github_url_in_text("code: https://github.com/a/b")` returns canonical GitHub URL string
- `extract_arxiv_id_from_url("https://arxiv.org/abs/2601.22135") == "2601.22135"`
- `extract_arxiv_id_from_url("https://arxiv.org/pdf/2601.22135") == "2601.22135"`
- tolerant property text extraction from mocked Notion property payloads

**Step 2: Run test to verify it fails**

Run: `cd /Users/songliyu/Documents/scripts.zotero-notion-ghstars && python -m unittest tests/test_config.py -v`
Expected: FAIL with missing helper functions

**Step 3: Commit**

```bash
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars add tests/test_config.py
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars commit -m "test: cover github fallback helper behavior"
```

### Task 2: Implement reusable helper functions in `main.py`

**Files:**
- Modify: `main.py`
- Test: `tests/test_config.py`

**Step 1: Write minimal implementation**

Add pure helpers for:
- GitHub field classification
- GitHub URL discovery from arbitrary text
- property text extraction from Notion property payloads
- page-level abstract extraction by trying a small ordered set of property names
- arXiv URL/id extraction and AlphaXiv resource URL construction

Reuse existing GitHub URL validation / owner-repo extraction where possible.

**Step 2: Run tests to verify they pass**

Run: `cd /Users/songliyu/Documents/scripts.zotero-notion-ghstars && python -m unittest tests/test_config.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars add main.py tests/test_config.py
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars commit -m "refactor: add reusable github discovery helpers"
```

### Task 3: Add fallback discovery methods and generic Notion page update

**Files:**
- Modify: `main.py`

**Step 1: Implement network-backed discovery helpers**

Add async helpers that:
- search abstract text for a GitHub repo URL
- fetch AlphaXiv resources page from an arXiv ID and search the response body for a GitHub repo URL

Use the existing aiohttp session from `GitHubClient` when practical, or a shared session helper, to avoid unnecessary duplication.

**Step 2: Generalize Notion updates**

Replace the narrow `update_github_stars()` call path with a generic page property update helper that can update:
- only `Github stars`
- or both `Github` and `Github stars`

**Step 3: Sanity-run tests**

Run: `cd /Users/songliyu/Documents/scripts.zotero-notion-ghstars && python -m unittest tests/test_config.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars add main.py
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars commit -m "feat: add github fallback discovery sources"
```

### Task 4: Refactor `process_page()` into one unified resolution pipeline

**Files:**
- Modify: `main.py`

**Step 1: Update control flow**

Refactor `process_page()` so it:
- classifies the current `Github` value once
- skips unchanged `other` values immediately
- resolves a final repo URL through one path
- fetches stars once
- updates Notion once
- records whether the source was existing / abstract / AlphaXiv

**Step 2: Improve result reporting**

Ensure console output distinguishes:
- updated stars from existing GitHub field
- discovered GitHub via abstract
- discovered GitHub via AlphaXiv
- skipped because unsupported content / discovery failure

**Step 3: Run tests**

Run: `cd /Users/songliyu/Documents/scripts.zotero-notion-ghstars && python -m unittest tests/test_config.py -v`
Expected: PASS

**Step 4: Manual syntax check**

Run: `cd /Users/songliyu/Documents/scripts.zotero-notion-ghstars && python -m py_compile main.py`
Expected: no output

**Step 5: Commit**

```bash
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars add main.py
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars commit -m "feat: unify github resolution and star update flow"
```

### Task 5: Update docs for the new fallback behavior

**Files:**
- Modify: `README.md`

**Step 1: Update README feature and behavior docs**

Document:
- only empty/`WIP` GitHub fields trigger discovery
- fallback order: abstract → AlphaXiv
- successful discovery updates both GitHub URL and stars
- non-empty non-`WIP` values remain untouched

**Step 2: Run lightweight verification**

Run: `cd /Users/songliyu/Documents/scripts.zotero-notion-ghstars && python -m unittest tests/test_config.py -v && python -m py_compile main.py`
Expected: PASS and no output

**Step 3: Commit**

```bash
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars add README.md
git -C /Users/songliyu/Documents/scripts.zotero-notion-ghstars commit -m "docs: describe github fallback discovery behavior"
```
