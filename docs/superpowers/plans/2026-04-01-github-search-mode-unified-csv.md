# GitHub Search Mode And Unified CSV Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge `scripts.ghcsv` into `scripts.ghstars` behind the same `uv run main.py <input>` entrypoint, with a fixed shared CSV schema and a `csv_update` path that refreshes `Stars` from whatever row inputs are already available.

**Architecture:** Keep one user-facing CLI and one shared CSV/export contract, but preserve two honest ingestion families: paper-collection ingestion and direct GitHub-search ingestion. Introduce a shared CSV row model/writer for all fresh exports, route GitHub search URLs to a dedicated collector/runner, and relax `csv_update` so it trusts existing `Github` values and only falls back to `Url`-based discovery when `Github` is missing.

**Tech Stack:** Python, asyncio, csv module, httpx/aiohttp, pytest, uv

---

### Task 1: Introduce A Shared CSV Row Schema And Writer

**Files:**
- Create: `src/shared/csv_rows.py`
- Modify: `src/shared/csv_io.py`
- Modify: `src/shared/paper_export.py`
- Modify: `src/shared/papers.py`
- Create: `tests/test_csv_io.py`
- Modify: `tests/test_paper_export.py`

- [ ] **Step 1: Write the failing schema/writer tests**

Add a focused writer test file that fixes the new header contract and verifies paper-family exports leave repo-search-only fields empty.

```python
from pathlib import Path

from src.shared.csv_io import CSV_HEADERS, write_rows_to_csv_path
from src.shared.csv_rows import CsvRow


def test_write_rows_to_csv_path_uses_unified_header_order(tmp_path: Path):
    csv_path = tmp_path / "rows.csv"
    write_rows_to_csv_path(
        [
            CsvRow(
                name="Paper A",
                url="https://arxiv.org/abs/2501.00001",
                github="https://github.com/foo/bar",
                stars=7,
                created="",
                about="",
                sort_index=1,
            )
        ],
        csv_path,
    )

    assert CSV_HEADERS == ["Name", "Url", "Github", "Stars", "Created", "About"]
    assert csv_path.read_text(encoding="utf-8").splitlines() == [
        "Name,Url,Github,Stars,Created,About",
        "Paper A,https://arxiv.org/abs/2501.00001,https://github.com/foo/bar,7,,",
    ]
```

- [ ] **Step 2: Run the focused writer tests to verify they fail**

Run: `uv run python -m pytest tests/test_csv_io.py tests/test_paper_export.py -q`
Expected: FAIL because the current writer only knows `Name,Url,Github,Stars` and `paper_export` still emits `PaperRecord`.

- [ ] **Step 3: Add a CSV-facing shared row model and upgrade the writer**

Create `src/shared/csv_rows.py` and switch the writer to operate on it instead of paper-only records.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CsvRow:
    name: str
    url: str
    github: str
    stars: int | str | None
    created: str
    about: str
    sort_index: int = 0


def sort_csv_rows(rows: list[CsvRow]) -> list[CsvRow]:
    if any(row.sort_index for row in rows):
        return sorted(rows, key=lambda row: row.sort_index)
    return rows
```

```python
CSV_HEADERS = ["Name", "Url", "Github", "Stars", "Created", "About"]


def write_rows_to_csv_path(rows: list[CsvRow], csv_path: Path) -> Path:
    sorted_rows = sort_csv_rows(rows)
    ...
    for row in sorted_rows:
        writer.writerow(
            {
                "Name": row.name,
                "Url": row.url,
                "Github": row.github,
                "Stars": "" if row.stars in (None, "") else str(row.stars),
                "Created": row.created,
                "About": row.about,
            }
        )
```

- [ ] **Step 4: Adapt paper export to emit the unified CSV row shape**

Keep `PaperSeed` paper-specific, but change fresh paper exports to map enrichment results into `CsvRow`.

```python
from src.shared.csv_rows import CsvRow

...
    return PaperOutcome(
        index=index,
        record=CsvRow(
            name=enrichment.title,
            url=enrichment.normalized_url or enrichment.raw_url or "",
            github=enrichment.github_url or "",
            stars=enrichment.stars if enrichment.reason is None else "",
            created="",
            about="",
            sort_index=index,
        ),
        reason=enrichment.reason,
    )
```

- [ ] **Step 5: Re-run the focused tests and commit**

Run: `uv run python -m pytest tests/test_csv_io.py tests/test_paper_export.py -q`
Expected: PASS

```bash
git add src/shared/csv_rows.py src/shared/csv_io.py src/shared/paper_export.py src/shared/papers.py tests/test_csv_io.py tests/test_paper_export.py
git commit -m "Add unified CSV row schema"
```

### Task 2: Add Dedicated GitHub Search Export Support Behind The Main CLI

**Files:**
- Create: `src/github_search_to_csv/models.py`
- Create: `src/github_search_to_csv/search.py`
- Create: `src/github_search_to_csv/pipeline.py`
- Create: `src/github_search_to_csv/runner.py`
- Modify: `src/app.py`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Create: `tests/test_github_search_to_csv.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write the failing CLI and ingestion tests**

Cover dispatch, URL support, row mapping, filename generation, and `Created`-descending ordering.

```python
import csv
from pathlib import Path

import pytest

from src.github_search_to_csv.pipeline import export_github_search_to_csv


@pytest.mark.anyio
async def test_export_github_search_to_csv_writes_unified_rows_sorted_by_created_desc(tmp_path: Path):
    class FakeSearchClient:
        async def collect_repositories(self, request):
            return [
                {"github": "https://github.com/foo/older", "stars": 2, "about": "older", "created": "2023-01-01T00:00:00Z"},
                {"github": "https://github.com/foo/newer", "stars": 5, "about": "newer", "created": "2024-01-01T00:00:00Z"},
            ]

    result = await export_github_search_to_csv(
        "https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc",
        search_client=FakeSearchClient(),
        output_dir=tmp_path,
    )

    with result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {"Name": "", "Url": "", "Github": "https://github.com/foo/newer", "Stars": "5", "Created": "2024-01-01T00:00:00Z", "About": "newer"},
        {"Name": "", "Url": "", "Github": "https://github.com/foo/older", "Stars": "2", "Created": "2023-01-01T00:00:00Z", "About": "older"},
    ]
```

```python
async def test_async_main_routes_github_search_url_to_github_search_runner(monkeypatch):
    called = []

    async def fake_run_github_search_mode(raw_url: str):
        called.append(raw_url)
        return 0

    monkeypatch.setattr("src.app.run_github_search_mode", fake_run_github_search_mode)

    assert await async_main(["https://github.com/search?q=cvpr+2026&type=repositories"]) == 0
    assert called == ["https://github.com/search?q=cvpr+2026&type=repositories"]
```

- [ ] **Step 2: Run the new GitHub-search tests to verify they fail**

Run: `uv run python -m pytest tests/test_github_search_to_csv.py tests/test_main.py -q`
Expected: FAIL because the repo-search collector/runner and dispatch branch do not exist yet.

- [ ] **Step 3: Port `ghcsv`’s collector into a dedicated repo-search package**

Move the GitHub Search API partitioning logic into a dedicated internal family and map collected rows into `CsvRow`.

```python
@dataclass(frozen=True)
class RepositorySearchRow:
    github: str
    stars: int
    about: str
    created: str
```

```python
async def export_github_search_to_csv(...):
    repositories = await collect_repositories(...)
    rows = [
        CsvRow(
            name="",
            url="",
            github=row.github,
            stars=row.stars,
            created=row.created,
            about=row.about,
            sort_index=0,
        )
        for row in sorted(repositories, key=lambda row: row.created, reverse=True)
    ]
    return ConversionResult(
        csv_path=write_rows_to_csv_path(rows, csv_path),
        resolved=len(rows),
        skipped=[],
    )
```

- [ ] **Step 4: Add top-level dispatch for GitHub repository-search URLs**

Detect supported GitHub search URLs in `src/app.py` before the generic unsupported-URL branch and route them to the new runner while keeping the user-facing command shape unchanged.

```python
from src.github_search_to_csv.runner import run_github_search_mode
from src.github_search_to_csv.search import is_supported_github_search_url

...
    if _is_url(raw_input):
        if is_supported_github_search_url(raw_input):
            return await run_github_search_mode(raw_input)
        if not is_supported_url_source(raw_input):
            print(f"Input file or URL not supported: {raw_input}", file=sys.stderr)
            return 1
        return await run_url_mode(raw_input)
```

- [ ] **Step 5: Re-run the GitHub-search/dispatch tests and commit**

Run: `uv run python -m pytest tests/test_github_search_to_csv.py tests/test_main.py -q`
Expected: PASS

```bash
git add src/github_search_to_csv src/app.py README.md ARCHITECTURE.md tests/test_github_search_to_csv.py tests/test_main.py
git commit -m "Add GitHub search export mode"
```

### Task 3: Redesign `csv_update` Around Available Row Inputs

**Files:**
- Modify: `src/csv_update/pipeline.py`
- Modify: `src/shared/paper_enrichment.py`
- Modify: `tests/test_csv_update.py`

- [ ] **Step 1: Write the failing row-update tests**

Add tests that lock in the new per-row contract.

```python
@pytest.mark.anyio
async def test_update_csv_file_refreshes_stars_without_url_when_github_exists(tmp_path: Path):
    csv_path = tmp_path / "repos.csv"
    csv_path.write_text(
        "Name,Url,Github,Stars,Created,About\n"
        ",,https://github.com/foo/bar,1,2024-01-01T00:00:00Z,repo\n",
        encoding="utf-8",
    )

    class FakeGitHubClient:
        async def get_star_count(self, owner, repo):
            assert (owner, repo) == ("foo", "bar")
            return 9, None

    result = await update_csv_file(
        csv_path,
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=FakeGitHubClient(),
        content_cache=None,
    )

    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    assert result.updated == 1
    assert rows[0]["Stars"] == "9"
    assert rows[0]["Created"] == "2024-01-01T00:00:00Z"
    assert rows[0]["About"] == "repo"
```

```python
@pytest.mark.anyio
async def test_update_csv_file_skips_row_without_github_or_url(tmp_path: Path):
    csv_path = tmp_path / "rows.csv"
    csv_path.write_text("Name,Github,Stars,Created,About\nNo Input,,,2024-01-01T00:00:00Z,\n", encoding="utf-8")

    result = await update_csv_file(
        csv_path,
        discovery_client=SimpleNamespace(resolve_github_url=AsyncMock()),
        github_client=SimpleNamespace(get_star_count=AsyncMock()),
        content_cache=None,
    )

    assert result.updated == 0
    assert result.skipped[0]["reason"] == "Row has neither Github nor Url"
```

- [ ] **Step 2: Run the focused CSV update tests to verify they fail**

Run: `uv run python -m pytest tests/test_csv_update.py -q`
Expected: FAIL because `csv_update` still requires a file-level `Url` header and still routes every row through the paper-first assumptions.

- [ ] **Step 3: Remove the file-wide `Url` requirement and update rows per available inputs**

Reshape `csv_update` so `Github` is enough for a successful `Stars` refresh, and only fall back to `Url` when discovery is needed.

```python
def _read_csv_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV file must include a header row")
        fieldnames = _normalize_fieldnames(list(reader.fieldnames))
        rows = [{field: raw_row.get(field, "") or "" for field in fieldnames} for raw_row in reader]
        return rows, fieldnames
```

```python
    url = (updated_row.get(URL_COLUMN, "") or "").strip()
    existing_github = (updated_row.get(GITHUB_COLUMN, "") or "").strip()
    if not existing_github and not url:
        outcome = CsvRowOutcome(
            index=index,
            record=CsvRow(name=name, url="", github="", stars=updated_row.get(STARS_COLUMN, ""), created=updated_row.get("Created", ""), about=updated_row.get("About", "")),
            current_stars=current_stars,
            reason="Row has neither Github nor Url",
            source_label=None,
            github_url_set=None,
        )
        return index - 1, updated_row, outcome
```

- [ ] **Step 4: Keep `Github` trust semantics intact and preserve repo-search metadata columns**

Do not overwrite a non-empty `Github`, and do not touch `Created` / `About`.

```python
    enrichment = await process_single_paper(
        PaperEnrichmentRequest(
            title=name,
            raw_url=url,
            existing_github_url=existing_github,
            allow_title_search=True,
            allow_github_discovery=True,
        ),
        ...
    )

    if existing_github:
        assert updated_row["Github"] == existing_github
    if enrichment.reason is None and enrichment.stars is not None:
        updated_row[STARS_COLUMN] = str(enrichment.stars)
```

- [ ] **Step 5: Re-run the focused CSV update tests and commit**

Run: `uv run python -m pytest tests/test_csv_update.py -q`
Expected: PASS

```bash
git add src/csv_update/pipeline.py src/shared/paper_enrichment.py tests/test_csv_update.py
git commit -m "Relax csv update input requirements"
```

### Task 4: Finish Docs, Full Regression, And Push-Ready Verification

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: any files from Tasks 1-3 only

- [ ] **Step 1: Update documentation to reflect the merged product**

Document:
- `uv run main.py <github search url>` support
- fixed shared CSV schema
- repo-search exports leaving `Name` / `Url` empty
- `csv_update` refreshing `Stars` from existing `Github` without requiring `Url`

```markdown
One CLI, five input shapes:

- no positional argument -> Notion sync
- one existing `.csv` file path -> update that CSV in place
- one supported paper collection URL -> export papers CSV
- one supported GitHub repository-search URL -> export repositories CSV
- one supported single-paper arXiv URL -> export references/citations CSVs
```

- [ ] **Step 2: Run the required focused suite for the new work**

Run:
`uv run python -m pytest tests/test_csv_io.py tests/test_github_search_to_csv.py tests/test_main.py tests/test_csv_update.py tests/test_paper_export.py tests/test_url_to_csv.py -q`

Expected: PASS

- [ ] **Step 3: Run the broader regression suite that covers shared export/update behavior**

Run:
`uv run python -m pytest tests/test_main.py tests/test_csv_update.py tests/test_url_to_csv.py tests/test_paper_export.py tests/test_notion_mode.py tests/test_arxiv_relations.py tests/test_semanticscholar.py -q`

Expected: PASS

- [ ] **Step 4: Sanity-check the new CLI flows manually**

Run:
`uv run main.py 'https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc'`

Expected:
- writes a CSV under `./output`
- headers are `Name,Url,Github,Stars,Created,About`
- rows are sorted by `Created` descending

Run:
`uv run main.py /path/to/repo-search-export.csv`

Expected:
- `Stars` refreshes from existing `Github`
- `Created` / `About` remain unchanged

- [ ] **Step 5: Commit the final implementation state**

```bash
git add src/github_search_to_csv src/shared src/csv_update src/app.py README.md ARCHITECTURE.md tests docs/superpowers/plans/2026-04-01-github-search-mode-unified-csv.md
git commit -m "Merge GitHub search export into unified CSV flow"
```
