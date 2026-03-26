# CSV Output Directory Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make newly generated CSV exports default to `./output` while preserving in-place behavior for `csv -> csv` updates and leaving Notion mode unchanged.

**Architecture:** Keep the behavior split at the existing mode boundary. Change the shared URL-export filename helper so default path assembly targets `output/`, and make the shared CSV writer create parent directories before replacing the destination file. Leave CSV update flow untouched so the input file path remains the write target.

**Tech Stack:** Python 3.12, pathlib, tempfile, pytest, uv

---

### Task 1: Lock in default URL export path behavior with failing tests

**Files:**
- Modify: `tests/test_url_export_filenames.py`
- Modify: `tests/test_url_to_csv.py`

- [ ] **Step 1: Write the failing helper test**

Add a test in `tests/test_url_export_filenames.py` proving `build_url_export_csv_path(...)` defaults to `Path("output")` when `output_dir` is omitted.

- [ ] **Step 2: Write the failing end-to-end export test**

Add a focused regression test in `tests/test_url_to_csv.py` that runs a URL export without `output_dir` inside an isolated temporary working directory and asserts:
- the returned path is under `output/`
- the CSV file exists after export completes

- [ ] **Step 3: Run the focused tests to verify they fail**

Run: `uv run pytest tests/test_url_export_filenames.py tests/test_url_to_csv.py -q`
Expected: FAIL because the helper still defaults to the current working directory and the export path will not be under `output/`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_url_export_filenames.py tests/test_url_to_csv.py
git commit -m "test: cover default csv output directory"
```

### Task 2: Implement the new default path with minimal code

**Files:**
- Modify: `src/url_to_csv/filenames.py`
- Modify: `src/shared/csv_io.py`

- [ ] **Step 1: Update the URL export filename helper**

Change the default directory in `src/url_to_csv/filenames.py` from `Path.cwd()` to `Path("output")` when `output_dir` is `None`.

- [ ] **Step 2: Update CSV writing to create parent directories**

Before opening the temporary file in `src/shared/csv_io.py`, create `csv_path.parent` with `mkdir(parents=True, exist_ok=True)` so first-run exports succeed even when `./output` does not exist.

- [ ] **Step 3: Run the focused tests to verify they pass**

Run: `uv run pytest tests/test_url_export_filenames.py tests/test_url_to_csv.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/url_to_csv/filenames.py src/shared/csv_io.py
git commit -m "feat: default new csv exports to output directory"
```

### Task 3: Verify unchanged in-place update behavior

**Files:**
- Test: `tests/test_csv_update.py`

- [ ] **Step 1: Run the CSV update regression tests**

Run: `uv run pytest tests/test_csv_update.py -q`
Expected: PASS, confirming `csv -> csv` still rewrites the original file in place.

- [ ] **Step 2: Run the combined regression suite**

Run: `uv run pytest tests/test_url_export_filenames.py tests/test_url_to_csv.py tests/test_csv_update.py -q`
Expected: PASS

- [ ] **Step 3: Commit documentation updates if needed**

```bash
git add docs/superpowers/specs/2026-03-26-csv-output-directory-defaults-design.md docs/superpowers/plans/2026-03-26-csv-output-directory-defaults.md
git commit -m "docs: record csv output directory defaults"
```
