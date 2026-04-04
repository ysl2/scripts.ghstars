# Single-Paper Target Cache Warmup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make single-paper arXiv relation mode run the input target paper through the existing shared single-paper sync path as a best-effort concurrent cache warmup, without creating extra CSV output and without failing the main relation export when warmup misses or errors.

**Architecture:** Keep the change localized to relation mode. `src/arxiv_relations/pipeline.py` will construct one authoritative target-paper `PaperSeed`, start a relation-local best-effort warmup task that reuses `sync_paper_seed(...)`, then continue the existing references/citations flow unchanged. No cache schema changes and no new export abstraction are introduced.

**Tech Stack:** Python, asyncio, pytest, existing `RecordSyncService` / `sync_paper_seed(...)` shared pipeline

---

### Task 1: Add Relation-Mode Regression Tests For Target Warmup

**Files:**
- Modify: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write a failing success-path test for target-paper warmup**

Add a new test near the existing relation export integration tests to verify that the target paper itself is warmed in addition to related papers, while still producing only the two normal CSVs.

Use this shape:

```python
@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_warms_target_paper_cache_without_creating_extra_rows(
    tmp_path: Path,
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class RecordingContentCache:
        def __init__(self):
            self.calls: list[str] = []

        async def ensure_local_content_cache(self, canonical_arxiv_url: str) -> None:
            self.calls.append(canonical_arxiv_url)

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            if arxiv_identifier == "https://arxiv.org/abs/2603.23502":
                return "Target Paper", None
            raise AssertionError(f"Unexpected arXiv title lookup: {arxiv_identifier}")

        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_match_by_title_from_api(self, title: str):
            return None, None, None, "No arXiv ID found from title search"

    class FakeSemanticScholarGraphClient:
        async def fetch_paper_by_identifier(self, identifier: str):
            if identifier == "DOI:10.48550/arXiv.2603.23502":
                return {"paperId": "ss-target", "title": "Target Paper"}
            return None

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            raise AssertionError("Title fallback should not run")

        async def fetch_references(self, paper: dict):
            return [
                {
                    "paperId": "R1",
                    "title": "Direct Reference",
                    "externalIds": {"ArXiv": "2501.00001"},
                }
            ]

        async def fetch_citations(self, paper: dict):
            return []

        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Direct Reference",
                direct_arxiv_url="https://arxiv.org/abs/2501.00001",
                doi_url=None,
                landing_page_url="https://arxiv.org/abs/2501.00001",
                source_url="https://www.semanticscholar.org/paper/R1",
            )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            mapping = {
                "https://arxiv.org/abs/2603.23502": "https://github.com/foo/target",
                "https://arxiv.org/abs/2501.00001": "https://github.com/foo/reference",
            }
            return mapping.get(seed.url)

    class FakeGitHubClient:
        async def get_repo_metadata(self, owner, repo):
            mapping = {
                ("foo", "target"): (
                    SimpleNamespace(
                        stars=100,
                        created="2024-01-01T00:00:00Z",
                        about="target repo",
                    ),
                    None,
                ),
                ("foo", "reference"): (
                    SimpleNamespace(
                        stars=12,
                        created="2024-03-03T00:00:00Z",
                        about="reference repo",
                    ),
                    None,
                ),
            }
            return mapping[(owner, repo)]

    content_cache = RecordingContentCache()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=FakeArxivClient(),
        semanticscholar_graph_client=FakeSemanticScholarGraphClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=content_cache,
        output_dir=tmp_path,
    )

    with result.references.csv_path.open(newline="", encoding="utf-8") as handle:
        reference_rows = list(csv.DictReader(handle))
    with result.citations.csv_path.open(newline="", encoding="utf-8") as handle:
        citation_rows = list(csv.DictReader(handle))

    assert reference_rows == [
        {
            "Name": "Direct Reference",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/reference",
            "Stars": "12",
            "Created": "2024-03-03T00:00:00Z",
            "About": "reference repo",
        }
    ]
    assert citation_rows == []
    assert sorted(content_cache.calls) == [
        "https://arxiv.org/abs/2501.00001",
        "https://arxiv.org/abs/2603.23502",
    ]
```

- [ ] **Step 2: Write a failing best-effort failure test**

Add a second test that proves target-paper warmup failure does not fail the main relation export. Keep the related-paper export successful so the test isolates best-effort behavior.

Use this shape:

```python
@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_ignores_target_paper_warmup_failure(
    tmp_path: Path,
):
    from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv

    class RecordingContentCache:
        def __init__(self):
            self.calls: list[str] = []

        async def ensure_local_content_cache(self, canonical_arxiv_url: str) -> None:
            self.calls.append(canonical_arxiv_url)

    class FakeArxivClient:
        async def get_title(self, arxiv_identifier: str):
            return "Target Paper", None

        async def get_arxiv_id_by_title(self, title: str):
            return None, None, "No arXiv ID found from title search"

        async def get_arxiv_match_by_title_from_api(self, title: str):
            return None, None, None, "No arXiv ID found from title search"

    class FakeSemanticScholarGraphClient:
        async def fetch_paper_by_identifier(self, identifier: str):
            return {"paperId": "ss-target", "title": "Target Paper"}

        async def search_papers_by_title(self, title: str, *, limit: int = 5):
            return []

        async def fetch_references(self, paper: dict):
            return [
                {
                    "paperId": "R1",
                    "title": "Direct Reference",
                    "externalIds": {"ArXiv": "2501.00001"},
                }
            ]

        async def fetch_citations(self, paper: dict):
            return []

        def build_related_work_candidate(self, work: dict):
            return RelatedWorkCandidate(
                title="Direct Reference",
                direct_arxiv_url="https://arxiv.org/abs/2501.00001",
                doi_url=None,
                landing_page_url="https://arxiv.org/abs/2501.00001",
                source_url="https://www.semanticscholar.org/paper/R1",
            )

    class FakeDiscoveryClient:
        async def resolve_github_url(self, seed):
            mapping = {
                "https://arxiv.org/abs/2603.23502": "https://github.com/foo/target",
                "https://arxiv.org/abs/2501.00001": "https://github.com/foo/reference",
            }
            return mapping.get(seed.url)

    class FakeGitHubClient:
        async def get_repo_metadata(self, owner, repo):
            if (owner, repo) == ("foo", "target"):
                raise RuntimeError("target warmup metadata failed")
            return (
                SimpleNamespace(
                    stars=12,
                    created="2024-03-03T00:00:00Z",
                    about="reference repo",
                ),
                None,
            )

    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=FakeArxivClient(),
        semanticscholar_graph_client=FakeSemanticScholarGraphClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=RecordingContentCache(),
        output_dir=tmp_path,
    )

    with result.references.csv_path.open(newline="", encoding="utf-8") as handle:
        reference_rows = list(csv.DictReader(handle))

    assert reference_rows == [
        {
            "Name": "Direct Reference",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/reference",
            "Stars": "12",
            "Created": "2024-03-03T00:00:00Z",
            "About": "reference repo",
        }
    ]
```

- [ ] **Step 3: Run the two new tests and verify they fail**

Run:

```bash
uv run pytest tests/test_arxiv_relations.py -k "target_paper_warmup or warms_target_paper_cache" -vv
```

Expected:

- FAIL because the current pipeline never warms the target paper itself
- FAIL because there is no best-effort target warmup path to suppress target-only failures

- [ ] **Step 4: Commit the failing test stage**

Run:

```bash
git add tests/test_arxiv_relations.py
git commit -m "test: cover relation target cache warmup"
```

### Task 2: Implement Best-Effort Target-Paper Warmup In Relation Mode

**Files:**
- Modify: `src/arxiv_relations/pipeline.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Add the target-paper warmup helper**

In `src/arxiv_relations/pipeline.py`, add a small relation-local helper above
`export_arxiv_relations_to_csv(...)` with this shape:

```python
async def _warm_target_paper_cache_best_effort(
    seed: PaperSeed,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
    status_callback=None,
) -> None:
    try:
        sync_result = await sync_paper_seed(
            seed,
            discovery_client=discovery_client,
            github_client=github_client,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            content_cache=content_cache,
            relation_resolution_cache=relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        )
    except Exception as exc:
        if callable(status_callback):
            status_callback(f"ℹ️ Target paper cache warmup skipped: {exc}")
        return

    if callable(status_callback):
        github_url = sync_result.record.github.value
        if github_url:
            status_callback(f"🧠 Warmed target paper cache: {github_url}")
        elif sync_result.reason:
            status_callback(f"ℹ️ Target paper cache warmup skipped: {sync_result.reason}")
```

Also add the import:

```python
from src.core.paper_export_sync import sync_paper_seed
```

- [ ] **Step 2: Start the warmup task as soon as target title resolution succeeds**

Inside `export_arxiv_relations_to_csv(...)`, immediately after:

```python
if callable(status_callback):
    status_callback(f"📄 Resolved title: {title}")
```

construct the authoritative target seed and start a concurrent task:

```python
    target_seed = PaperSeed(
        name=title,
        url=arxiv_url,
        canonical_arxiv_url=arxiv_url,
        url_resolution_authoritative=True,
    )
    target_warmup_task = asyncio.create_task(
        _warm_target_paper_cache_best_effort(
            target_seed,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            discovery_client=discovery_client,
            github_client=github_client,
            content_cache=content_cache,
            relation_resolution_cache=relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
            status_callback=status_callback,
        )
    )
```

Do not gate the task on target-paper GitHub certainty. The whole point is to
let the shared sync path attempt discovery and cache warming for the target.

- [ ] **Step 3: Await the warmup task before returning, while suppressing its failure**

Before the final `return ArxivRelationsExportResult(...)`, add:

```python
    try:
        await target_warmup_task
    except Exception:
        pass
```

If you want to avoid an unbound local when a failure happens before the task is
created, initialize it earlier:

```python
    target_warmup_task: asyncio.Task[None] | None = None
```

and then use:

```python
    if target_warmup_task is not None:
        try:
            await target_warmup_task
        except Exception:
            pass
```

The important contract is:

- warmup runs concurrently with the existing relation flow
- process exit still waits for the task to finish
- warmup exceptions never replace the real relation-export result

- [ ] **Step 4: Run the focused relation tests and verify they pass**

Run:

```bash
uv run pytest tests/test_arxiv_relations.py -k "target_paper_warmup or warms_target_paper_cache or ignores_target_paper_warmup_failure" -vv
```

Expected:

- PASS
- target-paper content warmup is now visible in the test cache recorder
- relation export still writes only references/citations CSVs

- [ ] **Step 5: Run the broader relation and shared single-paper sync coverage**

Run:

```bash
uv run pytest tests/test_arxiv_relations.py tests/test_paper_export_sync.py -vv
```

Expected:

- PASS
- no regressions in the shared `sync_paper_seed(...)` behavior

- [ ] **Step 6: Commit the implementation stage**

Run:

```bash
git add src/arxiv_relations/pipeline.py tests/test_arxiv_relations.py
git commit -m "feat: warm target paper cache in relation mode"
```

### Task 3: Document The New Single-Paper Warmup Behavior

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update single-paper mode description**

Adjust the top-level mode list and the shared enrichment section so they mention
that single-paper relation mode also warms cache for the input target paper
itself, without exporting that target paper as a CSV row.

Use wording like:

```markdown
- One supported single-paper arXiv URL: export related references and citations into two CSV files under `./output` in the current working directory, while also best-effort warming cache for the input target paper itself
```

and in `### Shared enrichment behavior` add a sentence like:

```markdown
- in single-paper relation mode, the input target paper itself also goes through the same shared single-paper sync path as a best-effort cache warmup, but it is not added to the references/citations CSVs
```

- [ ] **Step 2: Run the README-sensitive dispatch and relation tests**

Run:

```bash
uv run pytest tests/test_dispatch.py tests/test_arxiv_relations.py -vv
```

Expected:

- PASS
- docs edits do not require behavior changes outside the tested relation path

- [ ] **Step 3: Commit the docs stage**

Run:

```bash
git add README.md
git commit -m "docs: describe relation target cache warmup"
```

### Task 4: Final Verification

**Files:**
- No additional file changes required

- [ ] **Step 1: Run the full targeted regression set**

Run:

```bash
uv run pytest tests/test_arxiv_relations.py tests/test_paper_export_sync.py tests/test_dispatch.py -vv
```

Expected:

- PASS
- target warmup behavior covered
- no regression in relation export entrypoint or shared paper sync support

- [ ] **Step 2: Inspect git status**

Run:

```bash
git status --short
```

Expected:

- only the intended files are modified
- no accidental cache/output artifacts are staged

- [ ] **Step 3: Prepare handoff summary**

Summarize:

- target paper now warms existing cache surfaces through the shared sync path
- warmup is best-effort and concurrent
- references/citations CSV behavior is unchanged
