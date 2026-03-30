# Shared arXiv URL Normalization Design

**Goal**

Add one shared arXiv URL normalization and resolution layer so every paper-processing path follows the same rule:

- if the incoming `Url` is already an arXiv URL, preserve it exactly
- otherwise, try to resolve an arXiv-backed identity through explicit metadata/crosswalk first
- only then fall back to title-based resolution
- write back a script-derived arXiv URL only when the script actually resolved it

This shared layer must be reused across:

- `csv_update`
- `notion_sync`
- `url -> csv`
- `arxiv_relations`

It must also align cache behavior with the user-approved rule that caches only store script-derived mappings, never user-provided URLs.

**Scope**

In scope:

- define a shared arXiv URL resolver under `src/shared/`
- preserve existing arXiv URLs exactly, including versioned Zotero-style URLs
- preserve any existing non-empty `Github` value exactly and skip GitHub discovery
- extend CSV and Notion flows so non-arXiv URLs such as DOI URLs can normalize to arXiv through the shared layer
- reuse `relation_resolution_cache` as the persistent cache for script-derived URL normalization where the raw identifier is stable enough to key
- extend `cache.py` so one run clears negative cache entries from both:
  - `repo_cache`
  - `relation_resolution_cache`

Out of scope:

- changing CLI commands or mode names
- changing CSV or Notion property names
- validating or rewriting user-provided non-empty `Github` values
- introducing title as a persistent cache key
- broad unrelated refactoring outside the normalization boundary

**Current Problem**

Current `master` has multiple normalization paths with different semantics:

- `src/shared/paper_enrichment.py`
  - canonicalizes arXiv URLs immediately
  - only knows generic URL normalization plus title search
- `src/url_to_csv/pipeline.py`
  - has a second `_normalize_seed_to_arxiv(...)` path
- `src/arxiv_relations/title_resolution.py`
  - has a separate OpenAlex/arXiv/HF resolution ladder
- `src/notion_sync/pipeline.py`
  - drops non-arXiv raw URLs entirely by converting only extracted arXiv IDs into `raw_url`

That creates three concrete failures:

1. DOI and OpenAlex-backed inputs do not get the same best-effort normalization outside relation mode.
2. Existing arXiv URLs are canonicalized when some flows should preserve the original URL string exactly.
3. Cache behavior is inconsistent because only relation mode has a persistent URL-resolution cache today.

**Design Judgment**

The right fix is one shared resolver, not separate patches inside each mode.

The resolver should be narrow:

- accept a paper title and raw URL
- return both the URL to preserve/write back and the canonical arXiv identity used internally
- optionally use OpenAlex, arXiv, Hugging Face, and the existing resolution cache

The resolver should not own:

- GitHub discovery
- stars lookup
- CSV mutation
- Notion property classification
- relation-row retention policy

Those remain in their current layers.

**Shared Resolver Contract**

Create a shared module, for example `src/shared/arxiv_url_resolution.py`, with one business-level entrypoint that returns:

- `resolved_url`
  - the URL the caller should preserve or write back
- `canonical_arxiv_url`
  - canonical `https://arxiv.org/abs/<id>` for internal use
- `resolved_title`
  - the resolved arXiv title when available
- `source`
  - how resolution happened
- `script_derived`
  - whether the arXiv URL was produced by the script rather than supplied directly
- `negative_cacheable`
  - whether an unresolved result is safe to persist as a negative cache entry

The critical design choice is splitting `resolved_url` from `canonical_arxiv_url`.

That is necessary because:

- user-provided arXiv URLs must remain byte-for-byte preserved
- repo discovery, content warming, and internal arXiv identity logic still need a canonical arXiv URL

Example:

- input `https://arxiv.org/abs/2312.03203v3`
  - `resolved_url` = exact same string
  - `canonical_arxiv_url` = `https://arxiv.org/abs/2312.03203`
  - `script_derived` = `False`

- input `https://doi.org/10.48550/arXiv.2312.03203`
  - `resolved_url` = `https://arxiv.org/abs/2312.03203`
  - `canonical_arxiv_url` = same canonical URL
  - `script_derived` = `True`

**Resolution Ladder**

The shared resolver should use this order:

1. **Existing arXiv URL passthrough**
   - If `raw_url` is already an arXiv URL, preserve it exactly.
   - Derive `canonical_arxiv_url` internally.
   - Do not write any cache entry.

2. **Persistent cache lookup**
   - If `raw_url` can be converted into stable cache keys such as:
     - OpenAlex work URL
     - DOI URL
   - consult `relation_resolution_cache`.
   - Positive cached mapping returns canonical arXiv immediately.
   - Fresh negative cache skips the expensive resolver ladder.

3. **Explicit metadata / crosswalk**
   - If `openalex_client` is available and `raw_url` is keyable:
     - fetch the matching OpenAlex work by DOI or OpenAlex ID
     - first try direct arXiv identity on that work
     - if absent, try sibling/preprint crosswalk using the work metadata plus title

4. **arXiv title search**
   - If still unresolved and title search is allowed, try the current arXiv API/title-search path.

5. **Hugging Face title fallback**
   - If still unresolved and the current flow already supports HF title fallback, keep that behavior inside the shared resolver rather than duplicating it across callers.

6. **Unresolved**
   - Return no arXiv identity.
   - Let each caller decide whether to skip the row, preserve the original URL, or retain a non-arXiv relation row.

This keeps explicit metadata/crosswalk ahead of title search, which is the main quality improvement requested in this change.

**Cache Semantics**

Keep one strict rule:

- cache only script-derived mappings

That means:

- existing arXiv URLs do not get written into `relation_resolution_cache`
- existing non-empty `Github` values do not trigger repo discovery and therefore do not touch `repo_cache`
- title-only fallbacks without a stable identifier do not get persisted under a title key

For `relation_resolution_cache`, persist only when both are true:

- the resolver actually performed script-driven resolution
- there is a stable key derived from `raw_url`

Allowed key types remain:

- `openalex_work`
- `doi`

Negative cache entries should likewise only be recorded for those stable key types.

**Global Caller Rules**

All caller paths should consume the shared resolver with the same preservation semantics:

1. Existing arXiv URL:
   - preserve exact original string
   - still use canonical arXiv internally

2. Existing non-empty `Github` value:
   - preserve exact original string
   - skip GitHub discovery entirely
   - still allow stars lookup if the value is a valid GitHub repo URL in flows that already support that

3. Empty `Github` value:
   - allow GitHub discovery only after a canonical arXiv identity exists

This means `paper_enrichment` needs a small but important semantic change:

- the engine can no longer treat `normalized_url` as both internal identity and output URL
- it needs both preserved/write-back URL and canonical arXiv identity

**Per-Mode Integration**

`csv_update`

- pass raw `Url` through unchanged into the shared resolver
- if the resolver returns a script-derived arXiv URL, overwrite `Url`
- if the input was already arXiv, keep the original exact string
- if `Github` is already non-empty, do not discover or rewrite it

`notion_sync`

- stop collapsing non-arXiv property values to empty `raw_url`
- preserve the raw property value when present
- if the shared resolver script-derives an arXiv URL, use that internally for enrichment, but keep the current rule that this mode does not rewrite the Notion URL property unless the mode already owns that behavior

`url -> csv`

- replace `_normalize_seed_to_arxiv(...)` with the shared resolver
- dedupe on canonical arXiv identity when resolution succeeds
- unresolved seeds continue following current mode policy

`arxiv_relations`

- replace the local title-only normalization entry with the shared resolver
- keep relation-specific retained-non-arXiv fallback, ordering, and dedupe in `src/arxiv_relations/`

**Cache Cleanup Script**

`cache.py` should become one unified maintenance script for both negative caches.

Dry-run output should show at least:

- repo negative entries
- relation-resolution negative entries
- total deletions that would happen

Apply mode should delete:

- negative `repo_cache` entries
- negative `relation_resolution_cache` entries

There is no need for separate switches.

**Risk Assessment**

The main risk is changing URL semantics in paths that currently assume canonicalized arXiv output.

This risk is acceptable if implementation keeps these boundaries explicit:

- preserved/write-back URL
- canonical internal arXiv identity
- script-derived cache writes only

That split should make the architecture clearer than today, not less clear, because it removes the current ambiguity where one field tries to mean both “what to write back” and “what internal identity to use.”
