# Shared arXiv URL Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one shared arXiv URL normalization/resolution layer across all paper-processing paths, preserve existing arXiv and GitHub values exactly, and extend `cache.py` to clear both negative caches.

**Architecture:** Introduce a shared resolver that separates preserved/write-back URL from canonical internal arXiv identity, wire it into `paper_enrichment`, `url_to_csv`, `notion_sync`, and `arxiv_relations`, and persist only script-derived DOI/OpenAlex mappings in `relation_resolution_cache`.

**Tech Stack:** Python 3.12, asyncio, aiohttp, sqlite3, pytest, OpenAlex API, arXiv API

---

## File Map

- Create: `src/shared/arxiv_url_resolution.py`
  - shared arXiv URL resolver contract and ladder
- Modify: `src/shared/paper_identity.py`
  - add small URL helpers needed by the shared resolver
- Modify: `src/shared/openalex.py`
  - add direct work lookup by DOI/OpenAlex ID plus shared crosswalk helpers
- Modify: `src/shared/paper_enrichment.py`
  - consume the shared resolver and separate write-back URL from canonical arXiv identity
- Modify: `src/url_to_csv/pipeline.py`
  - replace local `_normalize_seed_to_arxiv(...)` path with the shared resolver
- Modify: `src/notion_sync/pipeline.py`
  - stop dropping non-arXiv raw URLs before normalization
- Modify: `src/notion_sync/runner.py`
  - build and pass `OpenAlexClient`
- Modify: `src/csv_update/pipeline.py`
  - preserve existing values and only rewrite URL when the resolver script-derives arXiv
- Modify: `src/csv_update/runner.py`
  - build and pass `OpenAlexClient`
- Modify: `src/shared/paper_export.py`
  - thread shared resolver dependencies into export-side enrichment
- Modify: `src/url_to_csv/runner.py`
  - build and pass `OpenAlexClient`
- Modify: `src/arxiv_relations/title_resolution.py`
  - delegate shared arXiv normalization to the new shared resolver
- Modify: `src/arxiv_relations/pipeline.py`
  - adapt relation retention/caching around the shared resolver result
- Modify: `src/arxiv_relations/runner.py`
  - pass `OpenAlexClient` into shared export/enrichment layers consistently
- Modify: `src/shared/relation_resolution_cache.py`
  - add negative-entry count/delete helpers
- Modify: `cache.py`
  - dry-run/apply both cache tables together
- Modify: `README.md`
  - document unified cache cleanup semantics if output changes
- Modify tests:
  - `tests/test_paper_identity.py`
  - `tests/test_openalex.py`
  - `tests/test_paper_enrichment.py`
  - `tests/test_csv_update.py`
  - `tests/test_notion_mode.py`
  - `tests/test_url_to_csv.py`
  - `tests/test_arxiv_relations.py`
  - `tests/test_relation_resolution_cache.py`
  - `tests/test_cache_cli.py`

## Guardrails

- Preserve exact incoming arXiv URL strings; do not canonicalize them for output.
- Preserve exact incoming non-empty `Github` strings; do not rewrite them.
- Skip GitHub discovery entirely when `Github` is already non-empty.
- Do not use title as a persistent cache key.
- Only write positive or negative normalization cache entries when the raw URL yields a stable DOI/OpenAlex cache key and the mapping was script-derived.
- Keep relation-specific retained-row behavior inside `src/arxiv_relations/`.

## Task 1: Lock shared normalization semantics with tests

**Files:**
- Modify: `tests/test_paper_enrichment.py`
- Modify: `tests/test_csv_update.py`
- Modify: `tests/test_notion_mode.py`
- Modify: `tests/test_url_to_csv.py`
- Modify: `tests/test_arxiv_relations.py`
- Modify: `tests/test_relation_resolution_cache.py`
- Modify: `tests/test_cache_cli.py`

- [ ] **Step 1: Add failing tests for preserved-vs-canonical URL semantics**

Cover these cases:

- existing arXiv URL with version suffix is preserved exactly for output
- the same row still uses canonical arXiv internally for GitHub discovery/content warming
- existing non-empty `Github` value skips discovery and is preserved exactly
- CSV DOI rows rewrite `Url` to canonical arXiv only when the resolver script-derives it
- Notion non-arXiv URL properties are no longer dropped before normalization

- [ ] **Step 2: Add failing tests for shared cache semantics**

Cover these cases:

- script-derived DOI/OpenAlex resolutions are persisted in `relation_resolution_cache`
- existing arXiv URLs do not write cache entries
- title-only resolutions without stable keys do not write cache entries
- fresh negative relation-resolution cache suppresses repeated expensive resolution

- [ ] **Step 3: Add failing tests for cache maintenance**

Cover these cases:

- `cache.py` dry run reports both repo negative entries and relation negative entries
- `cache.py --apply` deletes both negative entry types while preserving positives

- [ ] **Step 4: Run the focused slice and confirm failures**

Run:

```bash
uv run pytest tests/test_paper_enrichment.py tests/test_csv_update.py tests/test_notion_mode.py tests/test_url_to_csv.py tests/test_arxiv_relations.py tests/test_relation_resolution_cache.py tests/test_cache_cli.py -q
```

Expected before implementation:

- failures around preserved arXiv output semantics
- failures around DOI/OpenAlex normalization outside relation mode
- failures around cache.py still only touching repo cache

## Task 2: Implement the shared arXiv URL resolver and OpenAlex crosswalk

**Files:**
- Create: `src/shared/arxiv_url_resolution.py`
- Modify: `src/shared/paper_identity.py`
- Modify: `src/shared/openalex.py`
- Test: `tests/test_paper_identity.py`
- Test: `tests/test_openalex.py`

- [ ] **Step 1: Add URL helper coverage first**

Add tests for:

- detecting existing arXiv URLs without canonicalizing them
- normalizing DOI URLs
- normalizing OpenAlex work URLs if helper functions are added for them

- [ ] **Step 2: Add failing OpenAlex tests for direct identifier lookup**

Add tests that prove:

- a DOI URL can fetch a single OpenAlex work
- an OpenAlex work URL can fetch the same work
- direct work metadata can yield canonical arXiv on the work itself
- otherwise sibling/preprint crosswalk can yield canonical arXiv

- [ ] **Step 3: Implement the resolver minimally**

Implement this order:

1. existing arXiv passthrough
2. positive/fresh-negative cache lookup for DOI/OpenAlex keys
3. OpenAlex direct metadata/crosswalk
4. arXiv title search
5. optional HF fallback already supported by the current environment

- [ ] **Step 4: Re-run focused resolver tests**

Run:

```bash
uv run pytest tests/test_paper_identity.py tests/test_openalex.py -q
```

Expected:

- PASS for the new helper and direct-lookup behavior

## Task 3: Wire the shared resolver into single-paper enrichment and update flows

**Files:**
- Modify: `src/shared/paper_enrichment.py`
- Modify: `src/csv_update/pipeline.py`
- Modify: `src/csv_update/runner.py`
- Modify: `src/notion_sync/pipeline.py`
- Modify: `src/notion_sync/runner.py`
- Test: `tests/test_paper_enrichment.py`
- Test: `tests/test_csv_update.py`
- Test: `tests/test_notion_mode.py`

- [ ] **Step 1: Update `paper_enrichment` to carry both output URL and canonical arXiv identity**

Minimal contract changes:

- preserve original arXiv `raw_url` for output
- use canonical arXiv only for internal discovery/content steps
- skip GitHub discovery whenever `existing_github_url` is non-empty

- [ ] **Step 2: Thread `openalex_client` into CSV and Notion runners**

Build `OpenAlexClient` from runtime config in both runners and pass it into the pipeline/enrichment calls.

- [ ] **Step 3: Stop Notion from discarding non-arXiv raw URLs**

Make `build_page_enrichment_request(...)` preserve the raw Notion URL property value when present.

- [ ] **Step 4: Re-run focused flow tests**

Run:

```bash
uv run pytest tests/test_paper_enrichment.py tests/test_csv_update.py tests/test_notion_mode.py -q
```

Expected:

- PASS for preserved arXiv output semantics
- PASS for exact existing GitHub preservation
- PASS for DOI/OpenAlex normalization in CSV/Notion paths

## Task 4: Migrate export and relation paths onto the shared resolver

**Files:**
- Modify: `src/shared/paper_export.py`
- Modify: `src/url_to_csv/pipeline.py`
- Modify: `src/url_to_csv/runner.py`
- Modify: `src/arxiv_relations/title_resolution.py`
- Modify: `src/arxiv_relations/pipeline.py`
- Modify: `src/arxiv_relations/runner.py`
- Test: `tests/test_url_to_csv.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Replace `_normalize_seed_to_arxiv(...)` with the shared resolver**

Preserve current mode-level dedupe and skip behavior, but source the arXiv identity from the shared resolver.

- [ ] **Step 2: Reuse the shared resolver in relation normalization**

Keep relation-local retained-row fallback and ordering unchanged; only replace the arXiv-identification ladder and cache write/read semantics.

- [ ] **Step 3: Re-run focused export/relation tests**

Run:

```bash
uv run pytest tests/test_url_to_csv.py tests/test_arxiv_relations.py -q
```

Expected:

- PASS for shared normalization across export and relation paths
- PASS for retained non-arXiv relation behavior staying intact

## Task 5: Extend cache maintenance and finish verification

**Files:**
- Modify: `src/shared/relation_resolution_cache.py`
- Modify: `cache.py`
- Modify: `README.md`
- Test: `tests/test_relation_resolution_cache.py`
- Test: `tests/test_cache_cli.py`

- [ ] **Step 1: Add negative-entry helpers to `relation_resolution_cache`**

Implement:

- count negative entries
- delete negative entries

- [ ] **Step 2: Update `cache.py` output and apply behavior**

Make one run always report or delete:

- repo negative cache entries
- relation-resolution negative cache entries

- [ ] **Step 3: Run focused cache tests**

Run:

```bash
uv run pytest tests/test_relation_resolution_cache.py tests/test_cache_cli.py -q
```

Expected:

- PASS with both negative cache families covered

- [ ] **Step 4: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected:

- full suite green
