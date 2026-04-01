# Semantic Scholar Primary ArXiv Relations Design

**Goal**

Improve practical recall for single-paper arXiv citation/reference export by making `Semantic Scholar Graph API` the primary relation source while keeping `OpenAlex` as a fallback provider for this rollout. The change should preserve the existing relation normalization, GitHub discovery, stars enrichment, CSV shape, and cache semantics outside the source-selection step.

**Scope**

In scope:

- keep the existing single-paper arXiv relation export entrypoint and CSV shape unchanged
- make `Semantic Scholar` the default source for target-paper lookup plus references/citations retrieval in relation mode
- keep `OpenAlex` available as a fallback source when `Semantic Scholar` cannot identify the target paper or returns an empty relation side
- add a `Semantic Scholar` title-fallback path for target-paper lookup in v1
- keep the existing downstream normalization ladder for related works unchanged after relation rows are fetched
- reuse the current retained non-arXiv row behavior and GitHub/stars enrichment flow
- add tests that lock source priority, side-specific fallback behavior, and title-fallback acceptance rules

Out of scope:

- changing URL mode, CSV mode, or Notion mode behavior
- redesigning shared DOI-to-arXiv resolution behavior outside relation mode
- introducing cross-source union/merge behavior between `Semantic Scholar` and `OpenAlex`
- removing `OpenAlex` from the project in this iteration
- changing the relation CSV schema or adding provenance columns
- redesigning `relation_resolution_cache` schema or key strategy

**Problem**

The current relation export was designed around `OpenAlex`, but the real failing case `https://arxiv.org/abs/2510.22706` shows that the correct `OpenAlex` work can still expose zero references and zero citations. External validation indicates that `Semantic Scholar` currently returns real reference and citation counts plus linked rows for the same paper.

So the main bottleneck is no longer only "choose the right `OpenAlex` target work". It is "the primary relation source itself may have materially worse coverage for recent arXiv/preprint-heavy papers".

Because the downstream pipeline already handles mixed arXiv/non-arXiv relation rows well, the smallest high-value change is to replace only the relation-row fetch source, not to rework the normalization/export chain.

**Design Summary**

The single-paper relation export flow becomes:

1. resolve the input arXiv URL to canonical versionless `abs` form
2. resolve the target paper through `Semantic Scholar` using:
   - `DOI:10.48550/arXiv.<id>`
   - `ARXIV:<id>`
   - title fallback with strict normalized-title equality
3. fetch `Semantic Scholar` references
4. fetch `Semantic Scholar` citations
5. for each side independently:
   - if `Semantic Scholar` returned one or more rows, use only those rows for that side
   - if `Semantic Scholar` returned no rows or could not identify the target paper, fall back to `OpenAlex` for that side
6. convert fetched rows into the existing shared relation-candidate structure
7. run the existing related-work normalization pipeline unchanged
8. run the existing GitHub/stars enrichment and CSV write path unchanged

This rollout explicitly does **not** merge `Semantic Scholar` and `OpenAlex` rows for the same side. Source selection is winner-takes-side, not union.

**Target-Paper Resolution Flow**

For the input arXiv paper:

1. derive canonical arXiv URL `https://arxiv.org/abs/<id>`
2. derive canonical DOI URL `https://doi.org/10.48550/arXiv.<id>`
3. attempt `Semantic Scholar` target lookup by DOI identifier
4. if that fails, attempt `Semantic Scholar` target lookup by arXiv identifier
5. if that fails, attempt `Semantic Scholar` title search
6. accept a title-search result only when the normalized returned title exactly matches the normalized input title
7. if no acceptable `Semantic Scholar` target paper exists, fall back to the current `OpenAlex` target-resolution logic

This keeps the title-fallback path available in v1 without reintroducing the "trust the first search result" bug.

**Title-Fallback Acceptance Rule**

The `Semantic Scholar` title fallback must stay strict.

Accept the fallback candidate only if:

- the returned candidate title exists, and
- `normalize_title_for_matching(candidate_title) == normalize_title_for_matching(input_title)`

Reject the candidate if:

- the search endpoint returns no rows
- the first result exists but normalized title differs
- multiple rows exist but none satisfies normalized-title equality

This v1 design intentionally avoids looser heuristics such as top-k scoring, substring matching, author checks, or year checks.

**Relation-Side Source Selection**

`references` and `citations` are chosen independently.

For each side:

1. if `Semantic Scholar` target-paper lookup failed:
   - use `OpenAlex` for that side
2. if `Semantic Scholar` target-paper lookup succeeded:
   - fetch that side from `Semantic Scholar`
3. if `Semantic Scholar` returns one or more relation rows:
   - use those rows
   - do not call `OpenAlex` for that side
4. if `Semantic Scholar` returns an empty list:
   - fall back to `OpenAlex` for that side
5. if `Semantic Scholar` fails transiently:
   - fall back to `OpenAlex` for that side

This matches the chosen rollout policy: `Semantic Scholar` first, `OpenAlex` only as a backup.

**Shared Candidate Boundary**

The current relation normalization pipeline consumes a provider-shaped candidate structure currently declared inside `src/shared/openalex.py`. That boundary should become provider-neutral.

The shared relation candidate should continue to carry:

- `title`
- `direct_arxiv_url`
- `doi_url`
- `landing_page_url`
- provider-specific paper identity URL

The provider-specific paper identity URL should no longer be named `openalex_url` in the shared abstraction. It should be renamed to something provider-neutral such as `source_url` or `paper_url`.

Provider adapters then map raw provider rows into this shared candidate:

- `OpenAlex` adapter maps current work rows into the neutral candidate
- `Semantic Scholar` adapter maps Graph API rows into the same neutral candidate

This keeps all downstream normalization logic shared rather than creating a second relation-only path.

**Semantic Scholar Candidate Mapping**

For each fetched `Semantic Scholar` reference/citation paper:

1. set `title` from the returned paper title
2. if `externalIds.ArXiv` exists:
   - map directly to canonical versionless `https://arxiv.org/abs/<id>`
   - set `direct_arxiv_url`
3. else if `externalIds.DOI` exists:
   - map to canonical DOI URL
   - set `doi_url`
4. else if a stable Semantic Scholar paper URL can be constructed:
   - use that as the candidate landing/source URL
5. otherwise leave only the available identifier-bearing URL field populated

URL priority for unresolved non-arXiv rows remains the current relation-mode policy:

- DOI
- landing page
- source paper URL

**Client And Runtime Design**

Add a new shared client in `src/shared/` for the `Semantic Scholar Graph API`.

Expected responsibilities:

- fetch target paper by identifier
- fetch target paper by title search
- fetch references for a target paper
- fetch citations for a target paper
- adapt raw paper payloads to the shared relation-candidate structure

Runtime/config behavior:

- add optional `SEMANTIC_SCHOLAR_API_KEY`
- when present, send it on API requests
- when absent, continue with public unauthenticated access
- reuse the current `aiohttp` session, concurrency, retry, and min-interval patterns

The new client should live alongside the existing search-page HTML client used for Semantic Scholar URL mode. These are different surfaces and should remain separate.

**Logging**

Relation-mode logs should explicitly identify the active provider per side. For example:

- `Fetching Semantic Scholar references`
- `Semantic Scholar returned 43 references`
- `Semantic Scholar citations empty; falling back to OpenAlex`
- `Fetching OpenAlex citations`

This preserves the current runtime visibility goal and makes real-world provider performance observable without adding CSV columns.

**Failure Policy**

For relation-source fetches:

- `Semantic Scholar` target miss: fall back to `OpenAlex`
- `Semantic Scholar` transient/network/API failure: fall back to `OpenAlex`
- `Semantic Scholar` empty references: fall back to `OpenAlex` references only
- `Semantic Scholar` empty citations: fall back to `OpenAlex` citations only

Downstream relation-resolution cache behavior remains unchanged because that cache only concerns per-related-paper URL normalization after relation rows have already been fetched.

**Boundaries**

This design should not change:

- relation CSV headers or filenames
- row ordering and dedupe behavior after seeds are produced
- retained non-arXiv row policy
- shared DOI-to-arXiv resolution ladder
- GitHub discovery ladder
- stars lookup behavior
- content-cache warming behavior

The intended blast radius is restricted to target-paper resolution and relation-row retrieval in single-paper arXiv relation mode.

**Testing**

Add or update coverage for:

- `Semantic Scholar` DOI lookup winning before arXiv-id and title fallback
- arXiv-id lookup winning when DOI lookup misses
- title fallback accepting only normalized-title exact matches
- title fallback rejecting mismatched search results and triggering `OpenAlex` fallback
- `Semantic Scholar` references suppressing `OpenAlex` references when non-empty
- `Semantic Scholar` citations suppressing `OpenAlex` citations when non-empty
- side-specific fallback when only one `Semantic Scholar` side is empty
- fallback on `Semantic Scholar` transient failure
- preservation of existing normalization/export behavior once provider rows are converted into shared candidates

The real rerun of `2510.22706` should be the practical acceptance case: after the change, the export should use `Semantic Scholar` as the winning provider for whichever sides it returns non-empty rows.

**Rationale**

This is the smallest design that matches the current evidence and the chosen rollout policy:

- `Semantic Scholar` already fixes the real failing case that motivated the change
- the rest of the relation pipeline already handles heterogeneous relation rows
- keeping `OpenAlex` as side-specific fallback limits regression risk
- avoiding cross-source merge keeps behavior easy to reason about
- strict title-fallback acceptance avoids recreating the original wrong-target-paper failure mode under a new provider

So this design shifts the primary relation source where the empirical recall is better, while keeping the existing shared normalization/export architecture intact.
