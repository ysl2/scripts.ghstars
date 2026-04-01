# OpenAlex Removal And Semantic Scholar Core Migration Design

## Goal

Replace OpenAlex completely across `scripts.ghstars` so that the project uses `Semantic Scholar Graph API` as its sole paper-metadata core, with `ai4scholar` as a transport-compatible relay. After this migration, the codebase, tests, config, docs, and database cache contract must contain no OpenAlex-specific behavior or terminology.

## Hard Requirements

- Remove OpenAlex completely. Do not keep code-level compatibility shims.
- Remove all OpenAlex references from implementation, tests, docs, env examples, and runtime config.
- Remove OpenAlex traces from the database/cache layer as well, not only from code.
- Keep transport priority:
  1. official `Semantic Scholar API`
  2. `ai4scholar` relay
  3. anonymous official `Semantic Scholar API`
- Do not use `Semantic Scholar` HTML search anywhere after the migration.
- Keep existing `Semantic Scholar search URL` input support, but reimplement it with Graph API only.
- Treat the migration as a careful hard cut of core functionality, not a partial swap.

## Current State

OpenAlex currently plays two independent roles in the project:

1. It is part of the shared arXiv URL normalization chain used by CSV update, collection URL export, Notion sync, and relation export.
2. It is still retained as the fallback relation source in single-paper arXiv relation export.

The current runtime and test surface still contains OpenAlex-specific concepts:

- `OpenAlexClient`
- `OPENALEX_API_KEY`
- `openalex_work` cache keys
- `normalize_openalex_work_url()`
- relation fallback logs and tests tied to OpenAlex
- README and architecture references to OpenAlex stages

The current Semantic Scholar support is split:

- Graph API support exists for target-paper lookup and relation export
- `Semantic Scholar search URL` input still uses HTML search scraping and a headless browser path

## Final Target State

After the migration:

- `Semantic Scholar Graph API` is the only paper-metadata core.
- `ai4scholar` is only a transport alternative for the same Graph API contract.
- The shared arXiv normalization chain no longer contains any OpenAlex stage.
- Single-paper relation export uses only Semantic Scholar Graph API.
- Semantic Scholar search URL ingestion also uses only Graph API.
- The database cache contract is generic and contains no OpenAlex-specific key types.

## Architecture

### 1. One Shared Metadata Core

The project will standardize on one shared metadata resolver built on top of `Semantic Scholar Graph API`.

This resolver serves both:

- shared arXiv URL normalization
- single-paper relation target resolution

There must not be separate OpenAlex-era and Semantic-Scholar-era ladders. The replacement is complete only when the entire project uses the same Semantic Scholar-centered contract.

### 2. One Logical Resolver Stage, Two Internal Steps

The replacement resolver remains one logical stage in the pipeline:

- `Semantic Scholar resolver`

Internally, that stage runs in this fixed order:

1. `identifier exact`
2. `title exact fallback`

This remains the correct design because the two calls solve different input classes:

- `identifier exact` is the clean path for DOI / arXiv / directly usable source URLs
- `title exact fallback` is the recovery path when identifier lookup does not produce an arXiv mapping

This stage is not split into multiple public stages in user-facing docs or logs, but the implementation keeps the internal order explicit.

### 3. Shared arXiv URL Normalization Chain

The new shared chain becomes:

`cache -> Semantic Scholar resolver -> arXiv title search (HTML -> API fallback) -> Crossref -> DataCite -> Hugging Face`

The `Semantic Scholar resolver` stage behaves as follows:

- If the input or extra identifiers contain a DOI, try `DOI:<doi>` exact lookup first.
- If the caller already has an arXiv identifier, use `ARXIV:<id>` before title fallback.
- If the source URL is a Semantic Scholar paper URL or another directly usable source URL, use `URL:<url>` exact lookup when available.
- If exact lookup does not yield an arXiv mapping, run Graph API title search and accept only normalized-title exact matches.
- If no exact-title Semantic Scholar result maps to arXiv, continue to the downstream non-Semantic-Scholar stages.

The title fallback remains conservative:

- no fuzzy acceptance
- no heuristic author/year guessing
- normalized-title exact match only

### 4. Single-Paper Relation Export

Single-paper arXiv relation export becomes fully Semantic-Scholar-based:

- target paper resolution uses the shared `identifier exact -> title exact fallback` policy
- references and citations come only from Semantic Scholar Graph API
- there is no OpenAlex relation fallback

Behavioral consequences:

- if Semantic Scholar returns empty references or citations, the export keeps that side empty
- if target-paper resolution fails after exact lookup and exact-title fallback, the run fails with a Semantic Scholar-centered error
- retained unresolved rows still remain supported

Retained non-arXiv row URL priority becomes:

`DOI > landing_page_url > source_url`

No OpenAlex URL fallback remains.

### 5. Semantic Scholar Search URL Input

Support for Semantic Scholar search URLs is retained, but the implementation becomes Graph API-only.

The HTML/browser crawler path is removed entirely.

The search URL ingester must:

- parse the existing search URL query string
- map supported filters to Graph API parameters
- fetch results using `/paper/search/bulk`
- paginate using the API token-based pagination model
- request sufficient fields to construct seeds without HTML scraping

Supported filters already present in current URLs map naturally to Graph API parameters:

- `q`
- `year`
- `fieldsOfStudy`
- `venue`
- `sort`

Search results should request Graph API fields sufficient for downstream normalization:

- `paperId`
- `title`
- `externalIds`
- `url`

Preferred seed construction:

- if `externalIds.ArXiv` is present, emit an arXiv-backed seed directly
- otherwise emit a seed carrying the Semantic Scholar paper URL as its source URL for downstream shared normalization

This reduces unnecessary second-hop lookups while still keeping one shared normalization contract.

## Runtime Transport Policy

Transport selection remains shared and centralized:

1. if `SEMANTIC_SCHOLAR_API_KEY` is configured, use official Graph API with `x-api-key`
2. else if `AIFORSCHOLAR_TOKEN` is configured, use `ai4scholar` relay with bearer auth
3. else use anonymous official Graph API

There must not be duplicated clients for official vs relay access. The relay stays a transport choice only.

## Data Contract And Database Migration

### 1. Runtime Cache Contract

The relation/arXiv-resolution cache contract becomes generic and OpenAlex-free.

Allowed runtime key types after migration:

- `doi`
- `source_url`

Removed key types:

- `openalex_work`

`source_url` is the generic non-arXiv source identity. It may represent:

- Semantic Scholar paper URLs
- other source URLs that the resolver can use for `URL:<url>` exact lookup

### 2. Database Cleanup

The `relation_resolution_cache` table schema can remain structurally generic, but OpenAlex-era data must be removed.

Required database cleanup:

- delete all rows where `key_type = 'openalex_work'`
- ensure no new code writes `openalex_work`
- ensure no runtime path reads `openalex_work`

This migration does not attempt to translate old OpenAlex-keyed rows into new Semantic Scholar keys. Those rows are deleted because there is no clean deterministic rewrite, and the new code must behave as if OpenAlex had never been part of the runtime contract.

If any other OpenAlex-specific cache structures or seeded fixtures exist, they must also be removed or rewritten to the new generic contract.

## Cleanup Scope

The migration is complete only if all of the following are removed or rewritten:

- `src/shared/openalex.py`
- `OPENALEX_API_KEY` runtime loading and documentation
- `normalize_openalex_work_url()`
- `RelatedWorkCandidate.openalex_url`
- OpenAlex-specific progress labels
- OpenAlex-specific tests and fixtures
- OpenAlex references in README / ARCHITECTURE / comments
- Semantic Scholar HTML search code, browser usage, and related tests

## Rollout Plan

### Phase 1: Shared Resolver Replacement

- Replace OpenAlex stages in `resolve_arxiv_url()` with Semantic Scholar Graph API resolver logic.
- Change cache key generation from OpenAlex-specific identity to generic `source_url` / `doi`.
- Remove OpenAlex normalization helpers and related tests.
- Clean old `openalex_work` rows from the cache database path during migration.

### Phase 2: Relation Pipeline Hard Cut

- Remove OpenAlex target lookup and relation fallback from single-paper relation export.
- Keep only Semantic Scholar target resolution and relation fetching.
- Rewrite retained-row fallback to `DOI > landing_page_url > source_url`.
- Remove all OpenAlex relation tests and logs.

### Phase 3: Final Sweep

- Replace Semantic Scholar search HTML scraping with Graph API search.
- Remove remaining OpenAlex config, docs, tests, and implementation files.
- Remove any OpenAlex mentions from README and architecture docs.
- Confirm that the codebase contains no OpenAlex references.

## Error Handling

- Resolver behavior must preserve current negative-cache policy semantics.
- Semantic Scholar transient/network failures must not poison the negative cache.
- Empty Semantic Scholar relation sides are valid outputs and should not be masked as fallback success.
- Search URL API failures should fail explicitly as API failures, not as HTML parsing failures.

## Verification Requirements

The migration is only acceptable if verification covers:

- shared arXiv normalization in CSV mode
- shared arXiv normalization in URL mode
- shared arXiv normalization in Notion mode
- single-paper arXiv relation export
- Semantic Scholar search URL ingestion through API-only mode
- cache DB behavior with legacy `openalex_work` rows present before migration
- absence of blank relation rows
- absence of OpenAlex references in runtime config, docs, and tests

At the end of implementation, the repository should be in a state where:

- `rg -n "OpenAlex|openalex|OPENALEX"` over source, tests, README, architecture docs, and env examples returns no leftover runtime/product OpenAlex behavior references
- `rg -n "Semantic Scholar search" ...` shows only Graph API-based behavior, not HTML scraping

## Rationale For Not Collapsing To One Remote Call

The migration should not attempt to force the resolver into a single HTTP call. `identifier exact` and `title exact fallback` are both required, but they belong inside one logical `Semantic Scholar resolver` stage.

This keeps the external architecture simple while preserving the right precision/recall tradeoff:

- exact identifiers remain first-class and cheap
- title fallback remains available when metadata records are incomplete
- no fuzzy guessing is introduced

## Non-Goals

- preserving OpenAlex cache compatibility
- keeping OpenAlex as an optional secondary provider
- keeping any Semantic Scholar HTML or browser fallback
- introducing a general provider-abstraction layer just for the sake of this migration
