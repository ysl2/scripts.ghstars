# Paper Export Core Cleanup Design

**Goal**

Finish the second cleanup pass after the record-centric migration by removing
the thickest remaining compatibility path in the fresh paper-export flows.

The concrete target is the paper-family export chain used by:

- `src/url_to_csv/pipeline.py`
- `src/arxiv_relations/pipeline.py`

Today those paths still run through a mixed bridge:

`PaperSeed -> PaperEnrichmentRequest -> PaperEnrichmentResult -> Record -> CsvRow`

This design collapses that into the runtime shape that the repository already
claims as its center:

`PaperSeedInputAdapter -> RecordSyncService -> FreshCsvExportAdapter`

The purpose of this round is architectural cleanup, not behavior redesign.
Resolver order, discovery order, cache semantics, and external CLI behavior stay
unchanged.

## Current Problem

The previous refactor successfully made `src/core/*` the runtime center, but
paper-family fresh exports still take a compatibility-heavy path:

- `src/shared/paper_enrichment.py` reconstructs a `Record`, runs
  `RecordSyncService`, then flattens the result back into a compatibility
  result object.
- `src/shared/paper_export.py` immediately turns that compatibility result back
  into a `Record` so `FreshCsvExportAdapter` can build the row.
- `src/url_to_csv/pipeline.py` and `src/arxiv_relations/pipeline.py` each keep a
  duplicate `_adapt_paper_seeds_for_export()` helper even though
  `PaperSeedInputAdapter` already exists.
- content-cache warming and "first actionable reason" selection are redefined
  in multiple modules with slightly different local rules.

This leaves the repository in a mixed state:

- the real sync logic is already in `src/core/*`
- the fresh paper-export path still treats `src/shared/*` compatibility wrappers
  as the production entrypoint
- duplicated helpers increase maintenance surface without adding product value

## Options Considered

### Option A: Deduplicate helpers only

Delete duplicated helpers, but keep `paper_enrichment` and `paper_export` as the
main production bridge.

Pros:

- lowest code churn
- minimal test impact

Cons:

- preserves the thick compatibility path
- does not make the runtime structure clearer
- leaves `PaperSeed -> Record` indirect in the two most important fresh-export
  paths

### Option B: Collapse the fresh paper-export bridge into a direct core flow

Make paper-family exports run directly through the existing core adapter/service
stack. Keep `src/shared/paper_enrichment.py` only as a thin compatibility facade
for callers and tests that still import it.

Pros:

- removes the highest-value remaining compatibility detour
- keeps scope small and localized
- improves clarity for both `url_to_csv` and `arxiv_relations` at once
- preserves the already-approved record-centric architecture

Cons:

- requires careful preservation of fresh-export skip semantics
- requires touching both runtime code and tests around the compatibility layer

### Option C: Expand the cleanup to CSV and Notion policy presets too

Use this round to also centralize the remaining CSV/Notion local sync presets,
skip-reason helpers, and Notion property parsing duplication.

Pros:

- more complete architectural cleanup

Cons:

- scope expands materially
- higher regression risk
- mixes two cleanup goals that can be done independently

**Recommendation:** choose **Option B**.

It removes the thickest leftover bridge, gives the paper-family paths a direct
record-centric flow, and stays within a low-risk second-round cleanup.

## Scope

This round includes:

- collapsing the paper-family fresh-export runtime path to a direct
  `PaperSeed -> Record -> CsvRow` flow
- deleting the duplicated `_adapt_paper_seeds_for_export()` helpers from
  `url_to_csv` and `arxiv_relations`
- centralizing shared content-cache warming and shared "first actionable reason"
  selection for record-sync callers
- thinning `src/shared/paper_enrichment.py` so it becomes a compatibility facade
  rather than the production center
- thinning `src/shared/paper_export.py` so it orchestrates the shared export
  flow without reconstructing compatibility DTOs

This round explicitly does **not** include:

- changing resolver order in `src/shared/arxiv_url_resolution.py`
- changing repository-discovery order in `src/shared/discovery.py`
- changing cache schema or negative-cache semantics
- changing GitHub metadata write policy
- changing Notion schema validation behavior
- broader `src/core` repository/runtime redesign

## Recommended Design

### 1. Introduce one core-owned fresh paper sync helper

Add a small core module dedicated to the paper-family fresh-export path. Its job
is narrow:

- accept a `PaperSeed`
- adapt it into a `Record`
- run `RecordSyncService`
- return the synced `Record` plus one normalized skip reason

This helper should own the shared fresh-export policy:

- `allow_title_search=True`
- `allow_github_discovery=True`
- trust only explicit existing trusted fields that come from the input adapter
- warm content cache before repo metadata when a canonical arXiv URL is known

This keeps the orchestration rule in one place instead of re-expressing it
through compatibility request/result objects.

### 2. Preserve `PaperSeed` facts through the adapter, not by rebuilding seeds

`PaperSeedInputAdapter` should carry the existing seed-specific supporting facts
into the `Record` so downstream code does not need to re-wrap seeds just to keep
their metadata.

Important seed facts to preserve:

- `canonical_arxiv_url`
- `url_resolution_authoritative`

Once those facts are attached during adaptation, `url_to_csv` and
`arxiv_relations` can pass normalized seeds directly into the export path and
delete their duplicated `_adapt_paper_seeds_for_export()` helpers entirely.

### 3. Move fresh-export row creation to `Record -> CsvRow` only

`src/shared/paper_export.py` should stop reconstructing a `Record` from a
compatibility result. Instead, it should:

1. call the new core fresh paper sync helper
2. receive a synced `Record`
3. pass that `Record` directly to `FreshCsvExportAdapter`

Fresh-export-specific behavior stays explicit in this layer:

- if the shared sync result has a skip reason, suppress `Stars` in the final CSV
  row exactly as today
- continue writing resolved `Created` and `About` when available
- continue preferring the normalized URL when the shared sync path resolved one

This preserves current output semantics while removing the DTO ping-pong.

### 4. Reduce `paper_enrichment` to a compatibility facade

`src/shared/paper_enrichment.py` should no longer be the main production
runtime path for paper exports.

It should become a thin wrapper that:

- adapts its compatibility request to the same core fresh paper sync helper
- converts the synced `Record` into `PaperEnrichmentResult`
- preserves its current public fields and behavior for tests or external imports

This allows the repository to keep compatibility where it still matters without
forcing the main export path to go through compatibility DTOs.

### 5. Centralize sync-call support helpers

The repeated content-cache warming and reason-picking helpers should move to a
shared helper module near the record-sync layer.

That helper should provide two stable behaviors:

- build the `before_repo_metadata` callback used by sync callers that want local
  content warming
- compute the first actionable non-local reason from a synced `Record`

The logic should remain source-aware so different callers can ignore their own
input-source states while still using the same implementation.

This round only needs to migrate the fresh paper-export path to that helper. CSV
update and Notion can adopt the same helper in a later cleanup.

## File-Level Plan

### Create

- `src/core/paper_export_sync.py`
  - core-owned helper for `PaperSeed -> synced Record + reason`
- `tests/test_paper_export_sync.py`
  - focused coverage for the new helper's policy and fact preservation

### Modify

- `src/core/input_adapters.py`
  - preserve `PaperSeed` supporting facts on the adapted `Record`
- `src/shared/paper_export.py`
  - route directly through the new core helper and `FreshCsvExportAdapter`
- `src/shared/paper_enrichment.py`
  - thin wrapper over the new core helper
- `src/url_to_csv/pipeline.py`
  - delete `_adapt_paper_seeds_for_export()` and pass seeds directly
- `src/arxiv_relations/pipeline.py`
  - same cleanup as `url_to_csv`
- `tests/test_paper_export.py`
  - assert the direct core-owned flow instead of compatibility DTO round-trips
- `tests/test_paper_enrichment.py`
  - keep compatibility API coverage, but verify it is now a thin facade
- `tests/test_url_to_csv.py`
  - adjust any assertions tied to the removed seed-adapter helper
- `tests/test_arxiv_relations.py`
  - adjust any assertions tied to the removed seed-adapter helper

### Optional follow-up, not this round

- `src/csv_update/pipeline.py`
- `src/notion_sync/pipeline.py`

These should later adopt the shared sync-call helper too, but they are
deliberately deferred to keep this round bounded.

## Preserved Semantics

The implementation must preserve all of the following:

- existing non-empty `Github` remains source-of-truth input and is never
  rediscovered or rewritten
- fresh paper-family exports still use the six-column schema
  `Name, Url, Github, Stars, Created, About`
- fresh exports still suppress `Stars` in rows that end with a skip reason
- fresh exports still write `Created` and `About` when shared repo metadata is
  available
- content warming still happens only when a canonical arXiv URL is available
- relation and collection exports keep their current normalization, dedupe, and
  selection semantics
- no CLI routing or output filename behavior changes in this round

## Risks And Mitigations

### Risk 1: Skip-reason behavior changes subtly

Fresh export currently depends on compatibility-layer reason selection.

Mitigation:

- add focused tests around the new helper's reason selection
- preserve the current "ignore the caller's own source state" rule explicitly

### Risk 2: Seed supporting facts get lost during direct adaptation

If `canonical_arxiv_url` or `url_resolution_authoritative` are dropped,
downstream normalization and cache-warming behavior may regress.

Mitigation:

- make fact preservation an explicit adapter responsibility
- add unit coverage for adapted records carrying both fields

### Risk 3: `paper_enrichment` compatibility contracts regress

Even if production export no longer depends on `PaperEnrichmentResult`, tests or
external imports may still do so.

Mitigation:

- keep the module and public dataclasses in place
- move only the execution center, not the compatibility surface

## Success Criteria

This cleanup is complete when all of the following are true:

- `url_to_csv` and `arxiv_relations` no longer define local
  `_adapt_paper_seeds_for_export()` helpers
- `src/shared/paper_export.py` no longer converts
  `PaperEnrichmentResult -> Record -> CsvRow`
- the fresh paper-export runtime path is visibly
  `PaperSeedInputAdapter -> RecordSyncService -> FreshCsvExportAdapter`
- `src/shared/paper_enrichment.py` remains available but is no longer the
  production center of fresh paper export
- the full test suite still passes

## Deferred Cleanup

After this round, the next reasonable cleanup target is not another broad
rewrite. It is a smaller policy cleanup:

- move CSV and Notion sync presets onto shared helpers
- centralize Notion property parsing/constants
- then reevaluate whether the compatibility shims
  `src/shared/property_model.py` and `src/shared/property_resolvers.py` still
  justify their existence

That follow-up should be a separate spec and implementation cycle.
