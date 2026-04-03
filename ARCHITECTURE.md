# Architecture

This document is for maintainers.

- Use [README.md](/Users/songliyu/Documents/scripts.ghstars/README.md) for user-facing setup and CLI usage.
- Use this file for the current runtime structure, shared rules, and extension points.
- When historical docs disagree with the running code, trust the code first, then align the docs.

## Runtime Shape

The runtime is intentionally shallow at the top and centered around a record-oriented core:

`main.py -> src/app.py -> <input-shape>/runner.py -> <input-shape>/pipeline.py -> src/core/* -> src/shared/*`

Responsibilities by layer:

- `main.py`
  Re-exports the real entrypoint from `src.app`.
- `src/app.py`
  Detects the single positional argument shape and dispatches to one of five input families.
- `src/<mode>/runner.py`
  Builds config, HTTP clients, caches, and progress callbacks.
- `src/<mode>/pipeline.py`
  Implements input-family orchestration around adapters and sync services.
- `src/core/*`
  Holds the `Record` domain model, sync services, input adapters, output adapters, and repository wrappers.
- `src/shared/*`
  Holds reusable normalization, discovery, caching, HTTP, export, and enrichment support code.

This split is the core architectural rule of the repository: keep input-shape dispatch and runner wiring thin; keep property semantics in `src/core/*`; keep lower-level transport, normalization, and cache mechanics in `src/shared/*`.

## Record-Centric Core

The `Record` model and its adapters are now the architectural center.

Key files:

- `src/core/record_model.py`
- `src/core/record_sync.py`
- `src/core/input_adapters.py`
- `src/core/output_adapters.py`
- `src/core/repositories.py`
- compatibility shims in `src/shared/property_model.py`, `src/shared/property_resolvers.py`, and `src/shared/paper_enrichment.py`

Responsibilities:

- Treat `Name`, `Url`, `Github`, `Stars`, `Created`, and `About` as explicit `Record` properties.
- Reuse one `Github URL -> repo metadata` path across fresh export, CSV update, and Notion sync.
- Keep input adapters and output adapters thin, with shared acquisition and write-policy semantics owned by sync services.
- Expose durable-cache access through repository wrappers instead of scattering low-level store calls through pipelines and runtime.

Important semantics:

- Existing non-empty `Github` values are source-of-truth input.
- Fresh paper-family exports use the shared six-column CSV shape `Name`, `Url`, `Github`, `Stars`, `Created`, `About`.
- When shared enrichment reaches a valid GitHub repo, fresh exports populate `Stars` and, when available, `Created` / `About`.
- CSV update always refreshes `Stars` and `About`, and only backfills `Created`.
- Notion auto-provisions missing `Github`, `Stars`, `Created`, and `About` properties, but same-name wrong-type properties are hard failures.

## The Five Modes

### 1. Notion sync

Path:

- `src/notion_sync/runner.py`
- `src/notion_sync/pipeline.py`

Purpose:

- Read papers from Notion.
- Resolve GitHub repositories and shared repo metadata properties.
- Update Notion properties in place.

Special rule:

- Notion may use non-arXiv -> arXiv normalization internally, but it must not rewrite the stored literature URL field.
- Missing `Github`, `Stars`, `Created`, and `About` properties are auto-created with the expected Notion types.
- Existing same-name properties with the wrong type are fatal schema errors.

### 2. CSV update

Path:

- `src/csv_update/runner.py`
- `src/csv_update/pipeline.py`

Purpose:

- Read one existing CSV.
- Keep unrelated columns untouched.
- Apply the shared property write policy to `Github`, `Stars`, `Created`, and `About`.

Special rule:

- Existing valid `Github` values are source-of-truth and are preserved exactly.
- CSV update can refresh `Stars` from an existing `Github` without requiring `Url`.
- CSV update always refreshes `Stars` and `About`, and only backfills `Created`.

### 3. Collection URL -> CSV

Path:

- `src/url_to_csv/runner.py`
- `src/url_to_csv/pipeline.py`
- `src/url_to_csv/sources.py`
- source adapters under `src/url_to_csv/`

Purpose:

- Fetch supported paper collections.
- Normalize rows to arXiv-backed papers when possible.
- Export a new CSV under `./output`.

Special rule:

- Existing arXiv URLs are preserved as-is in the output row.
- Canonical arXiv URLs are still used internally for identity, dedupe, and downstream cache lookups.
- Once shared enrichment has a valid GitHub repo, fresh exports populate `Stars` and, when available, `Created` / `About`.

### 4. GitHub repository search -> CSV

Path:

- `src/github_search_to_csv/runner.py`
- `src/github_search_to_csv/pipeline.py`
- `src/github_search_to_csv/search.py`
- `src/github_search_to_csv/models.py`

Purpose:

- Accept GitHub repository-search URLs on the main CLI.
- Harvest repositories through the GitHub Search API.
- Split oversized searches recursively by `stars` and `created`.
- Export fresh rows into the shared six-column CSV schema under `./output`.

Special rule:

- This family is intentionally separate from paper normalization and paper enrichment.
- `Name` and `Url` stay empty by design for GitHub-search exports.
- Fresh GitHub-search exports sort rows by `Created` descending before write.

### 5. Single-paper arXiv relation export

Path:

- `src/arxiv_relations/runner.py`
- `src/arxiv_relations/pipeline.py`
- shared normalization via `src/shared/arxiv_url_resolution.py`

Purpose:

- Start from one arXiv paper.
- Fetch references and citations from Semantic Scholar Graph API.
- Resolve related works to arXiv when possible.
- Export two CSVs under `./output`.

Special rule:

- Relation mode retains unresolved non-arXiv works instead of dropping them outright.
- Resolved rows reuse the same shared repo-metadata path as other fresh export families.

## Shared Subsystems

### Identity and normalization

Key files:

- `src/shared/paper_identity.py`
- `src/shared/arxiv_url_resolution.py`
- `src/shared/arxiv.py`
- `src/shared/semantic_scholar_graph.py`
- `src/shared/crossref.py`
- `src/shared/datacite.py`

Responsibilities:

- Normalize paper URLs and identifiers.
- Run the shared Semantic Scholar-backed arXiv resolution ladder used by CSV, Notion, URL, and relation flows.
- Detect whether an input is already arXiv-hosted.
- Resolve DOI / source URLs to arXiv when possible.

Current shared non-arXiv -> arXiv resolution order:

`cache -> Semantic Scholar exact -> Semantic Scholar title exact -> arXiv title search -> Crossref -> DataCite -> Hugging Face`

Important semantics:

- Cache is always checked first.
- The ladder short-circuits strictly.
- Existing arXiv URLs are treated as source-of-truth input and are not canonical-rewritten in user-facing outputs.
- Negative cache entries are written only after the active ladder fully fails without transient metadata errors.

### Repository discovery and repo metadata

Key files:

- `src/shared/discovery.py`
- `src/shared/github.py`

Responsibilities:

- Resolve GitHub repositories for arXiv-backed papers.
- Fetch shared repo metadata (`Stars`, `Created`, `About`).

Current repo-discovery order:

`repo_cache -> Hugging Face exact paper API -> AlphaXiv paper-page HTML`

Important semantics:

- Repository discovery uses canonical, versionless arXiv URLs as the cache key.
- Existing non-empty `Github` values are source-of-truth and skip discovery.
- Negative repo-discovery results are cached with a recheck TTL.

### Shared enrichment and export

Key files:

- `src/shared/paper_enrichment.py`
- `src/shared/paper_export.py`
- `src/shared/csv_rows.py`
- `src/shared/papers.py`
- `src/shared/csv_io.py`
- `src/shared/paper_content.py`
- `src/shared/alphaxiv_content.py`

Responsibilities:

- Apply shared paper processing once a row is in the common enrichment path.
- Normalize URLs, acquire `Github`, resolve shared repo metadata, warm local content cache, and write CSV records.
- Define the shared six-column CSV row contract reused by paper exports and GitHub-search exports.

CSV contract:

- Fresh CSV exports use the fixed header order `Name`, `Url`, `Github`, `Stars`, `Created`, `About`.
- Paper-family exports populate `Created` and `About` when shared repo metadata resolution has those values.
- GitHub-search exports leave `Name` and `Url` empty.

Design intent:

- `process_single_paper()` is the compatibility wrapper over the shared property-centric acquisition and metadata flow.
- Mode pipelines should prefer threading data into shared enrichment instead of re-implementing URL normalization or GitHub discovery locally.

### Runtime and infrastructure

Key files:

- `src/shared/runtime.py`
- `src/shared/http.py`
- `src/shared/async_batch.py`
- `src/shared/settings.py`
- `src/shared/progress.py`
- `src/shared/skip_reasons.py`

Responsibilities:

- Build shared runtime clients and settings.
- Apply request throttling and retry behavior.
- Provide bounded concurrency helpers and common progress formatting.

## Cache Layout

There are three distinct cache layers:

### 1. SQLite repo cache

Path:

- `src/shared/repo_cache.py`
- stored in `./cache.db`

Meaning:

- Key: canonical arXiv URL
- Value: discovered GitHub repo URL, or a negative repo-discovery timestamp

### 2. SQLite relation-resolution cache

Path:

- `src/shared/relation_resolution_cache.py`
- stored in the same `./cache.db`

Meaning:

- Key: DOI URL or source URL
- Value: resolved arXiv URL plus optional resolved title, or a negative normalization result

### 3. Filesystem content cache

Path:

- `./cache/overview/<arxiv_id>.md`
- `./cache/abs/<arxiv_id>.md`

Meaning:

- Locally cached AlphaXiv-derived overview and abstract markdown

Maintenance:

- `cache.py` clears negative SQLite cache entries.
- It does not remove positive mappings or filesystem content cache files.

## High-Value Global Rules

These rules are cross-cutting. Preserve them when modifying any mode.

- Existing arXiv URLs are source-of-truth input.
- Existing non-empty `Github` values are source-of-truth input.
- Caches store only script-derived positive or negative results.
- Non-Notion flows may rewrite a non-arXiv literature URL to a resolved arXiv URL.
- Notion must not rewrite the stored literature URL field.
- CSV update always refreshes `Stars` and `About`, and only backfills `Created`.
- Notion auto-provisions missing repo-metadata properties, but wrong-type collisions are hard failures.
- If a shared rule changes, thread it through all modes, not only the mode you are editing.

## Document Map

### User-facing docs

- `README.md`
  Setup, env vars, supported inputs, mode behavior, and CLI examples.

### Maintainer-facing current overview

- `ARCHITECTURE.md`
  Current runtime structure, shared rules, and extension guidance.
- `docs/README.md`
  Boundary map for current docs versus historical plans/specs.

### Historical docs and design records

- `docs/plans/`
- `docs/superpowers/plans/`
- `docs/superpowers/specs/`
- `docs/huggingface.md`
- `docs/find_alphaxiv_github.sh`

Treat these as design history, research notes, or migration-era materials unless they are explicitly brought back into active maintenance. They are useful context, but not the primary source of truth for current runtime behavior.

## Safe Extension Points

### Add a new collection URL source

Preferred path:

1. Add the adapter under `src/url_to_csv/`.
2. Register it in `src/url_to_csv/sources.py`.
3. Keep downstream enrichment in shared code.

Do not duplicate GitHub discovery or normalization logic inside the adapter.

### Add a new DOI / metadata source

Preferred path:

1. Add the client under `src/shared/`.
2. Integrate it into `src/shared/arxiv_url_resolution.py`.
3. Thread the client through all affected runners and shared enrichment/export paths.

If the source affects global normalization behavior, verify:

- CSV update
- URL export
- Notion sync
- relation export tails that reuse shared export

### Add a new repo-discovery source

Preferred path:

1. Integrate it into `src/shared/discovery.py`.
2. Keep cache-first behavior.
3. Make its position in the short-circuit order explicit and intentional.

Do not add mode-specific GitHub discovery ladders unless the mode truly has different semantics.

## When In Doubt

If a change touches paper identity, URL normalization, repository discovery, or cache semantics, assume it is global until proven otherwise.

The most common regression pattern in this repository is not broken local code. It is changing one path and forgetting that the same rule is reused by another mode through `src/shared/`.
