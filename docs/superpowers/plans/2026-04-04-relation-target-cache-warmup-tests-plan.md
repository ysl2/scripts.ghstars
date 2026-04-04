# Relation Target Cache Warmup Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add regression coverage proving the target paper is warmed alongside related papers and that a best-effort target warmup failure does not stop the relation export.

**Architecture:** Build two targeted `pytest` cases exercising `export_arxiv_relations_to_csv` with the existing fake Semantic Scholar/Ai servers plus a recording content cache hook. Assert the CSV output stays unchanged and the content cache sees the target warmup on the success branch, then reuse the harness with a failing helper to prove resilience.

**Tech Stack:** `pytest` (anyio), `tmp_path` fixtures, `csv.DictReader`, `Pathlib`, `uv`, simple async helper classes.

---

### Task 1: Extend `tests/test_arxiv_relations.py` with target-warmup regressions

**Files:**
- Modify: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Add a reusable recording content-cache helper**

```python
class RecordingContentCache:
    def __init__(self):
        self.calls: list[str] = []

    async def ensure_local_content_cache(self, canonical_arxiv_url: str) -> None:
        self.calls.append(canonical_arxiv_url)


class FailingTargetContentCache(RecordingContentCache):
    async def ensure_local_content_cache(self, canonical_arxiv_url: str) -> None:
        if canonical_arxiv_url == "https://arxiv.org/abs/2603.23502":
            raise RuntimeError("target warmup simulation")
        await super().ensure_local_content_cache(canonical_arxiv_url)
```

 - [ ] **Step 2: Add the target warmup success-path regression**

```python
@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_warms_target_paper(tmp_path: Path, monkeypatch):
    recording_cache = RecordingContentCache()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=FakeArxivClient(),
        semanticscholar_graph_client=FakeSemanticScholarGraphClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=recording_cache,
        output_dir=tmp_path,
    )

    reference_rows = list(
        csv.DictReader(result.references.csv_path.open(newline="", encoding="utf-8"))
    )
    citation_rows = list(
        csv.DictReader(result.citations.csv_path.open(newline="", encoding="utf-8"))
    )
    assert reference_rows == [
        {
            "Name": "Direct Reference",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/reference",
            "Stars": "12",
            "Created": "2024-03-03T00:00:00Z",
            "About": "reference repo",
        },
        {
            "Name": "Retained DOI Reference",
            "Url": "https://doi.org/10.1145/example",
            "Github": "",
            "Stars": "",
            "Created": "",
            "About": "",
        },
    ]
    assert citation_rows == [
        {
            "Name": "Citation With Missing Stars",
            "Url": "https://arxiv.org/abs/2502.00002",
            "Github": "https://github.com/foo/citation",
            "Stars": "",
            "Created": "",
            "About": "",
        }
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "arxiv-2603.23502-citations-20260326113045.csv",
        "arxiv-2603.23502-references-20260326113045.csv",
    ]
    assert set(recording_cache.calls) == {
        "https://arxiv.org/abs/2603.23502",
        "https://arxiv.org/abs/2501.00001",
        "https://arxiv.org/abs/2502.00002",
    }
```

 - [ ] **Step 3: Add the best-effort target-warmup failure test**

```python
@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_tolerates_target_warmup_failure(tmp_path: Path):
    failing_cache = FailingTargetContentCache()
    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2603.23502",
        arxiv_client=FakeArxivClient(),
        semanticscholar_graph_client=FakeSemanticScholarGraphClient(),
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        content_cache=failing_cache,
        output_dir=tmp_path,
    )

    reference_rows = list(
        csv.DictReader(result.references.csv_path.open(newline="", encoding="utf-8"))
    )
    assert reference_rows == [
        {
            "Name": "Direct Reference",
            "Url": "https://arxiv.org/abs/2501.00001",
            "Github": "https://github.com/foo/reference",
            "Stars": "12",
            "Created": "2024-03-03T00:00:00Z",
            "About": "reference repo",
        },
        {
            "Name": "Retained DOI Reference",
            "Url": "https://doi.org/10.1145/example",
            "Github": "",
            "Stars": "",
            "Created": "",
            "About": "",
        },
    ]
    assert result.references.csv_path.exists()
    assert result.citations.csv_path.exists()
    assert set(failing_cache.calls) == {
        "https://arxiv.org/abs/2501.00001",
        "https://arxiv.org/abs/2502.00002",
    }
```


### Task 2: Confirm the new tests currently fail

**Files:**
- None (runtime verification)

- [ ] **Step 4: Run the focused pytest command**

```
uv run pytest tests/test_arxiv_relations.py -k "target_paper_warmup or warms_target_paper_cache" -vv
```

Expected: FAIL because `export_arxiv_relations_to_csv` does not yet warm `https://arxiv.org/abs/2603.23502` and the best-effort failure path never runs.


### Task 3: Commit the regression spec and failing tests

**Files:**
- Modify: `tests/test_arxiv_relations.py`
- Already added: `docs/superpowers/specs/2026-04-04-relation-target-cache-warmup-tests-design.md`

- [ ] **Step 5: Stage and commit**

```
git add tests/test_arxiv_relations.py docs/superpowers/specs/2026-04-04-relation-target-cache-warmup-tests-design.md
git commit -m "test: cover relation target cache warmup"
```
