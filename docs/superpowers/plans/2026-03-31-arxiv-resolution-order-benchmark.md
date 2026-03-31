# ArXiv Resolution Order Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorder the shared DOI-to-arXiv resolver for hit-rate-first efficiency and add an opt-in benchmark that records which stage resolves each `2312.03203` citation/reference.

**Architecture:** Keep one shared `resolve_arxiv_url()` chain and change only its stage order. Extend relation normalization to carry stage/source metadata from the shared resolver, then add a dedicated benchmark entrypoint that reuses the same relation pipeline logic and emits per-row plus aggregated stage stats without changing normal CLI behavior.

**Tech Stack:** Python 3.12, `aiohttp`, `pytest`, existing `src/shared/*` runtime clients and relation pipeline.

---

### Task 1: Lock the New Shared Resolver Order with Tests

**Files:**
- Modify: `tests/test_arxiv_url_resolution.py`
- Modify: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write failing resolver-order tests**

Add tests that assert:
- `title_search` is attempted before `openalex_preprint`
- `openalex_preprint` is attempted before `crossref`
- `huggingface` remains last and is only reached after all earlier stages miss

- [ ] **Step 2: Run targeted tests to verify failure**

Run: `uv run pytest tests/test_arxiv_url_resolution.py tests/test_arxiv_relations.py -q`
Expected: FAIL because current resolver still runs `openalex_preprint` before `title_search`, and relation rows do not yet expose stage metadata for benchmarking.

- [ ] **Step 3: Implement the minimal order change**

Modify `src/shared/arxiv_url_resolution.py` so the shared order becomes:
`cache -> OpenAlex exact -> arXiv title (HTML -> API) -> OpenAlex preprint -> Crossref -> DataCite -> Hugging Face`

- [ ] **Step 4: Re-run targeted tests**

Run: `uv run pytest tests/test_arxiv_url_resolution.py tests/test_arxiv_relations.py -q`
Expected: PASS for the new order assertions.


### Task 2: Expose Resolver Stage Metadata Without Creating a Second Chain

**Files:**
- Modify: `src/arxiv_relations/pipeline.py`
- Modify: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write failing benchmark-shape tests**

Add tests that assert normalized relation rows can retain:
- direct-arXiv source markers
- shared resolver `source`
- unresolved markers

- [ ] **Step 2: Run the specific failing tests**

Run: `uv run pytest tests/test_arxiv_relations.py -q`
Expected: FAIL because `NormalizedRelatedRow` currently does not preserve stage/source metadata.

- [ ] **Step 3: Implement minimal metadata plumbing**

Update relation normalization so:
- direct OpenAlex arXiv rows record a direct-source label
- shared resolver results carry through `resolution.source`
- unresolved retained rows carry a stable unresolved label

Do not add a second resolver or benchmark-only resolution path.

- [ ] **Step 4: Re-run relation tests**

Run: `uv run pytest tests/test_arxiv_relations.py -q`
Expected: PASS with stage/source metadata preserved.


### Task 3: Add an Opt-In Benchmark Entry Point

**Files:**
- Create: `src/arxiv_relations/benchmark.py`
- Create: `benchmark.py`
- Create: `tests/test_arxiv_relations_benchmark.py`

- [ ] **Step 1: Write failing benchmark tests**

Add tests for a benchmark helper that:
- aggregates per-stage hit counts
- separates `citations`, `references`, and `overall`
- emits per-row detail records with title/url/source/category

- [ ] **Step 2: Run benchmark tests to verify failure**

Run: `uv run pytest tests/test_arxiv_relations_benchmark.py -q`
Expected: FAIL because benchmark helpers and wrapper script do not exist yet.

- [ ] **Step 3: Implement the benchmark module and wrapper**

Add a dedicated benchmark runner that:
- resolves the target paper’s citations/references using the same shared pipeline logic
- collects per-row stage labels
- writes a detail CSV plus a summary JSON/TXT to `output/`
- stays opt-in and does not affect `main.py`

- [ ] **Step 4: Re-run benchmark tests**

Run: `uv run pytest tests/test_arxiv_relations_benchmark.py -q`
Expected: PASS.


### Task 4: Full Verification and Real Benchmark Run

**Files:**
- Verify only

- [ ] **Step 1: Run focused test suite**

Run: `uv run pytest tests/test_arxiv_url_resolution.py tests/test_arxiv_relations.py tests/test_arxiv_relations_benchmark.py -q`
Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 3: Run the real benchmark**

Run: `uv run python benchmark.py 'https://arxiv.org/abs/2312.03203'`
Expected: stage-stat output files appear under `output/`, and summary shows which stages resolved citations/references.

- [ ] **Step 4: Review benchmark output**

Confirm:
- every resolved row has a stage label
- unresolved rows are counted explicitly
- per-stage counts make it obvious which stages have high or low yield

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-03-31-arxiv-resolution-order-benchmark.md src/shared/arxiv_url_resolution.py src/arxiv_relations/pipeline.py src/arxiv_relations/benchmark.py benchmark.py tests/test_arxiv_url_resolution.py tests/test_arxiv_relations.py tests/test_arxiv_relations_benchmark.py
git commit -m "Add arXiv resolution benchmark and reorder shared stages"
```
