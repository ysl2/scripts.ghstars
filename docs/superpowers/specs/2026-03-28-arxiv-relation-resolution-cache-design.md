# ArXiv Relation Resolution Cache Design

**Goal**

Improve practical recall and batch efficiency for single-paper arXiv citation/reference export by caching the "related work -> arXiv" resolution step globally. The cache should reduce repeated title-search cost across runs while preserving the current low-blast-radius relation-export design.

**Scope**

In scope:

- cache arXiv-resolution results for non-direct related works in the single-paper arXiv relation export flow
- use canonical arXiv `abs` URLs as cached resolved values
- cache both successful resolutions and confirmed misses
- make the miss recheck window configurable
- keep the new cache design close to the existing `repo_cache` style
- keep the existing `repo_cache` behavior unchanged
- limit changes to the single-paper arXiv relation-resolution path

Out of scope:

- redesigning the existing `repo_cache` schema
- using title text as a persistent cache key in v1
- supporting non-arXiv input papers
- adding new CLI flags for cache control
- introducing new external services for relation resolution

**Problem**

The current relation pipeline can retain unresolved non-arXiv related works, but repeated runs still pay for resolution attempts again and again. In real citation/reference sets, the same OpenAlex works and DOIs recur frequently. Without a global cache, the batch script wastes time repeating identical arXiv lookups.

This cache is meant to solve the repeated-lookup problem, not to change the exported CSV schema or broaden the supported input types.

**Design Summary**

Add one new global cache table in `cache.db` dedicated to relation-resolution results.

- the table stores a resolution key and its latest resolved canonical `arxiv_url`
- unresolved rows are also cached as misses with `arxiv_url = NULL`
- miss rows expire after a configurable number of days
- successful rows do not expire by TTL in this design
- the resolution flow first checks this cache before calling the arXiv API

The resolved value should be stored as canonical `arxiv_url`, not bare `arxiv_id`, so it aligns with the rest of the codebase and can interact cleanly with the existing `arxiv_url -> github_url` cache.

**Cache Model**

Use one simple table rather than a more normalized alias graph.

Recommended logical shape:

- `key_type`
- `key_value`
- `arxiv_url`
- `checked_at`

Primary key:

- `(key_type, key_value)`

Initial key types:

1. `openalex_work`
2. `doi`

Field semantics:

- `key_type`: identifies which external identity namespace produced the lookup key
- `key_value`: the concrete OpenAlex work URL or DOI URL used for lookup
- `arxiv_url`: canonical versionless `https://arxiv.org/abs/...` URL when resolution succeeds; `NULL` when the latest checked result is a confirmed miss
- `checked_at`: timestamp of the latest successful or unsuccessful resolution check

This intentionally allows multiple keys to point to the same `arxiv_url`. That duplication is acceptable in v1 because the table is a lookup accelerator, not a canonical paper-identity store.

**Negative Cache**

The table should cache misses as well as hits.

A negative-cache row means:

- the pipeline already attempted to resolve this key to arXiv
- no arXiv match was accepted at that time
- the pipeline should skip another immediate arXiv API lookup until the miss entry expires

This avoids repeatedly re-running the same expensive miss path across batches.

**Resolution Flow**

For each related work that does not already have a direct arXiv identity from OpenAlex:

1. collect all available cache keys in this order:
   - `openalex_work`
   - `doi`
2. look up all available keys in the relation-resolution cache
3. if any key has a positive cached `arxiv_url`, use that cached `arxiv_url` immediately
4. otherwise, if no key has a positive hit but at least one key has a non-expired negative-cache row, skip arXiv API lookup and keep the relation unresolved for this run
5. otherwise, call the arXiv API title-search flow for the related work title
6. if arXiv resolves successfully:
   - normalize to canonical versionless `abs` URL
   - write that same `arxiv_url` back to all available keys for the current related work
7. if arXiv does not resolve successfully:
   - write negative-cache rows for all available keys for the current related work

Important precedence rule:

- a positive cache hit on any available key wins over negative-cache rows on other available keys

This prevents one stale miss key from blocking a valid hit on another key for the same related work.

**Search Strategy**

Use a single search layer in v1:

- arXiv API title search only

Do not add a second fallback search provider in this design. The goal is to improve recall and batch efficiency with minimal new moving parts.

**Configuration**

Keep the new TTL configuration close to the existing GitHub-cache miss TTL settings.

In `src/shared/settings.py`, define the related constants near each other:

- `HF_EXACT_NO_REPO_RECHECK_DAYS = 7`
- `ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS = 30`

Environment/runtime configuration:

- environment variable: `ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS`
- runtime config key: `arxiv_relation_no_arxiv_recheck_days`

The two TTLs should not share the same setting because they control different negative-cache domains:

- one for "known arXiv paper but no GitHub repo found"
- one for "related work could not be resolved to arXiv"

They may coincidentally have similar values, but their semantics should remain separate.

**Boundaries**

This design should not change:

- URL mode behavior
- CSV mode behavior
- Notion mode behavior
- current `repo_cache` schema or semantics
- relation-export CSV schema

The new cache should only participate in the single-paper arXiv relation-resolution path for citations and references.

**Testing**

Add coverage for:

- cache schema initialization
- positive cache lookup by `openalex_work`
- positive cache lookup by `doi`
- positive cache hit winning over a negative-cache row on another key
- non-expired negative-cache rows skipping arXiv API lookup
- expired negative-cache rows allowing a fresh arXiv API lookup
- successful arXiv resolution writing the same canonical `arxiv_url` back to all available keys
- unresolved lookups writing negative-cache rows for all available keys
- no regression in the existing GitHub cache behavior
- full `uv run pytest`

**Rationale**

This design deliberately chooses a simple lookup table over a more normalized identity model.

That trade-off is correct for this project stage because:

- it matches the existing cache style
- it keeps blast radius low
- it avoids title-key instability
- it gives real batch-speed benefits quickly
- it preserves room for future redesign if the relation source later expands beyond OpenAlex
