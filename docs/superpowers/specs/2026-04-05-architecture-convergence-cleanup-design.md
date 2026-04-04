# Architecture Convergence Cleanup Design

**Goal**

Finish the current closeout cleanup by collapsing the repository's real runtime
and maintenance surface onto the architecture it already claims to have:

`input-shape orchestration -> core record/domain workflows -> target adapters`

This round adopts the more aggressive cleanup path:

- keep `src/core/*` as the only property/domain API
- remove compatibility facades instead of preserving them
- maximize reuse by moving shared normalization and shared sync-call workflows
  into `src/core/*`
- keep `src/shared/*` focused on lower-level mechanics such as HTTP, caches,
  provider clients, and transport helpers

The purpose of this round is architectural convergence, not product behavior
redesign. CLI shape, resolver order, discovery order, metadata write policy,
and cache semantics remain unchanged unless a localized refactor requires
equivalent mechanical relocation.

## Current Problem

The repository's top-level direction is already good: `Record`,
`PropertyState`, and `RecordSyncService` are the true shared center.

The remaining problem is that the runtime still exposes more than one
architectural language:

- paper normalization semantics are partly owned by `url_to_csv` pre-processing
  and partly owned by the shared sync path
- Notion input parsing exists both in `src/core/input_adapters.py` and in local
  `notion_sync` helpers
- shared sync-call orchestration is repeated in multiple pipelines
- compatibility modules under `src/shared/*` still re-wrap core types and
  workflows, making it unclear which API new code should extend

As a result:

- code reuse is lower than it should be
- behavior-preserving cleanup still requires touching multiple layers
- future contributors can still add new logic to the wrong layer
- tests protect historical facades in addition to the actual runtime center

## Options Considered

### Option A: Keep compatibility facades, but make them thin

- move more runtime code to `src/core/*`
- keep `shared/property_*` and `shared/paper_enrichment` as compatibility
  facades

Pros:

- lower immediate churn
- lower test migration cost

Cons:

- still leaves two architectural surfaces alive
- later contributors can keep extending the wrong layer
- the repository remains partially converged

### Option B: Fully collapse the public/runtime architecture onto `src/core/*`

- move remaining shared workflows into `src/core/*`
- migrate runtime callers and tests to the core-owned surface
- delete compatibility facades once the runtime no longer needs them

Pros:

- clearest long-term architecture
- best support for future maintenance
- strongest code reuse signal because there is only one real extension surface

Cons:

- more migration work now
- requires coordinated test updates, not just runtime edits

### Option C: Expand this round to include broader relation-pipeline splitting

- do Option B
- also split `src/arxiv_relations/pipeline.py` substantially

Pros:

- broader architectural cleanup

Cons:

- scope expands materially
- higher regression risk
- mixes two valid but separable cleanup goals

**Recommendation:** choose **Option B**.

That is the adopted design for this round.

## Scope

This round includes:

- moving paper-seed normalization ownership into a shared `src/core/*`
  workflow
- reusing one shared core-owned normalization workflow from `url_to_csv`
  instead of letting `url_to_csv` own a separate normalization semantics layer
- moving shared sync-call orchestration for paper-like records into `src/core/*`
- making Notion page parsing depend on `NotionPageInputAdapter` only
- deleting runtime-unused compatibility facades in `src/shared/*`
- migrating tests so they protect the real runtime center instead of historical
  wrapper APIs
- updating maintainer docs to reflect the converged architecture

This round explicitly does **not** include:

- redesigning resolver order in `src/shared/arxiv_url_resolution.py`
- redesigning repository-discovery order in `src/shared/discovery.py`
- changing cache schema or negative-cache policy
- changing the six-property model into a dynamic property system
- major splitting of `src/arxiv_relations/pipeline.py`
- introducing a plugin architecture for providers

## Adopted Design

### 1. `src/core/*` becomes the only property/domain API

After this cleanup, new code should only extend the domain/property surface
through:

- `src/core/record_model.py`
- `src/core/record_sync.py`
- `src/core/input_adapters.py`
- `src/core/output_adapters.py`
- `src/core/paper_export_sync.py`
- any new focused helper modules placed in `src/core/*`

`src/shared/*` remains available for:

- HTTP and retry helpers
- caches and repositories that are purely infrastructural
- provider clients
- lower-level URL/repo discovery and normalization primitives

It should no longer define alternative property/domain wrapper APIs.

### 2. Paper-seed normalization is core-owned, but mode-callable

`url_to_csv` legitimately needs a pre-export normalization/filtering step
because it decides:

- which seeds remain in the export set
- how records are deduplicated before export

That does **not** mean the mode should own a separate normalization semantics
layer.

The adopted rule is:

- the normalization workflow itself is owned by `src/core/*`
- `url_to_csv` may call that workflow before export
- `url_to_csv` keeps only mode-specific decisions such as retain/drop and
  deduplication

This preserves reuse while keeping architectural ownership clear.

Concretely, the shared workflow should:

- accept one `PaperSeed`
- resolve it to canonical arXiv identity when possible
- return the normalized seed plus authoritative facts needed downstream

`url_to_csv` should stop implementing normalization as a mode-owned business
rule. It should instead consume the core-owned normalization result.

### 3. One shared core-owned sync-call workflow for paper-like records

The following orchestration pattern is currently repeated across fresh paper
exports and update flows:

- build a `RecordSyncService`
- call `sync(...)`
- warm content cache before repo metadata
- optionally write normalized URL facts back into the `Record`
- compute one actionable skip reason

That pattern should be centralized in `src/core/*` as a reusable workflow
helper.

The helper's responsibility is narrow:

- accept a `Record` or `PaperSeed`
- apply the approved shared sync policy
- return the synced `Record` plus one normalized reason

This helper should own the common sync-call behavior. Mode pipelines should
retain only mode-specific policy such as:

- whether a normalized URL should be written back to a target
- whether missing GitHub should be treated as a skip or as a retained row
- how success/skip is rendered in target-specific logs

### 4. Notion input parsing becomes adapter-only

`NotionPageInputAdapter` should become the only code that translates a raw
Notion page into initial domain state.

`src/notion_sync/pipeline.py` should no longer duplicate parsing helpers for:

- title extraction
- GitHub extraction
- stars/created/about extraction
- paper URL extraction

After convergence, the Notion pipeline should own only:

- schema validation
- mode-specific update policy
- calling the shared core sync workflow
- building the final Notion patch
- logging and result accumulation

This ensures that future Notion field-rule changes happen in one parsing
boundary rather than two.

### 5. Compatibility facades are deleted, not retained

This round intentionally chooses deletion over long-term coexistence.

The following modules should be removed once runtime callers and tests are
migrated:

- `src/shared/property_model.py`
- `src/shared/property_resolvers.py`
- `src/shared/paper_enrichment.py`

If any type or option object from those modules still has real value after the
migration, it should be either:

- moved into `src/core/*` as part of the canonical API, or
- localized to the one mode that still needs it

No compatibility shell should remain simply to preserve an internal historical
name.

### 6. Tests must protect the converged architecture

The cleanup is not complete unless the tests also move to the new center.

After this round:

- `tests/test_record_model.py`
- `tests/test_record_sync.py`
- `tests/test_input_adapters.py`
- `tests/test_output_adapters.py`
- `tests/test_paper_export_sync.py`
- targeted mode tests

should become the primary protection surface.

Historical wrapper tests should be removed or rewritten so they validate real
runtime behavior instead of preserving deleted compatibility APIs.

## File-Level Design

### Create

- `src/core/paper_seed_normalization.py`
  - core-owned workflow for `PaperSeed` normalization to canonical arXiv-backed
    identity
- `tests/test_paper_seed_normalization.py`
  - focused tests for the new core-owned normalization workflow

### Modify

- `src/url_to_csv/pipeline.py`
  - replace mode-owned normalization semantics with calls into the new core
    normalization workflow
  - keep only retain/drop and dedupe rules that are specific to this mode
- `src/core/paper_export_sync.py`
  - expand into the canonical shared sync-call workflow for paper-like records
    and paper seeds
- `src/csv_update/pipeline.py`
  - reuse the core-owned sync-call workflow instead of locally rebuilding the
    same orchestration shape
- `src/notion_sync/pipeline.py`
  - remove duplicated page-parsing helpers
  - reuse adapter output plus the shared core sync-call workflow
- `src/core/input_adapters.py`
  - ensure Notion parsing coverage and any supporting helpers needed by the new
    adapter-only boundary
- `ARCHITECTURE.md`
  - update maintainer guidance so it matches the converged architecture
- `tests/test_url_to_csv.py`
  - update expectations around mode-owned normalization helpers and shared core
    normalization reuse
- `tests/test_csv_update.py`
  - update assertions to reflect the shared core sync-call workflow
- `tests/test_notion_mode.py`
  - move parsing expectations to the adapter-backed boundary and retain
    mode-specific patch/update expectations
- `tests/test_paper_export_sync.py`
  - expand coverage for the converged shared sync-call workflow

### Delete

- `src/shared/property_model.py`
- `src/shared/property_resolvers.py`
- `src/shared/paper_enrichment.py`
- `tests/test_property_model.py`
- `tests/test_property_resolvers.py`
- `tests/test_paper_enrichment.py`

## Migration Order

The order matters. The cleanup should proceed in this sequence:

1. add or update tests for the target architecture first
2. introduce the core-owned paper-seed normalization workflow
3. migrate `url_to_csv` to consume that workflow
4. converge shared paper-like sync-call orchestration in `src/core/*`
5. migrate `csv_update`
6. migrate `notion_sync`
7. delete compatibility facades
8. delete or rewrite historical wrapper tests
9. update `ARCHITECTURE.md`

This ensures each deletion happens only after the real runtime and tests are
already standing on the new center.

## Preserved Semantics

The implementation must preserve all of the following:

- existing CLI routing and mode selection remain unchanged
- resolver order remains unchanged
- repo-discovery order remains unchanged
- existing non-empty `Github` remains source-of-truth input
- CSV update still refreshes `Stars` and `About`, and backfills `Created` only
  when blank
- Notion still does not overwrite the stored literature URL
- Notion still only writes `Github` when discovery found a new repo and the mode
  allows that write
- paper-family fresh exports still use the six-column schema
- paper-like flows still warm local content cache before repo metadata when a
  canonical arXiv identity is known
- `url_to_csv` still filters out papers that cannot be normalized to arXiv-backed
  identity

## Risks and Mitigations

### Risk 1: deleting facades before all callers are migrated

Mitigation:

- migrate runtime callers first
- remove facade tests only after new tests are in place
- run the full test suite before deletion is considered complete

### Risk 2: accidentally moving mode-specific policy into core

Mitigation:

- core owns normalization and shared sync-call workflows
- modes keep only retain/drop, target writeback, and target-specific logging
- review every new helper against the question: "is this true for multiple
  modes, or just this target?"

### Risk 3: Notion cleanup changes parsing behavior unintentionally

Mitigation:

- move parsing to the adapter, not to a new abstraction layer
- preserve all current supported property candidates
- keep focused Notion mode tests around page parsing and patch generation

## Verification

At minimum, verification should include:

- focused tests for the new core normalization workflow
- focused tests for the converged core sync-call workflow
- targeted mode tests for `url_to_csv`, `csv_update`, and `notion_sync`
- a full `uv run python -m pytest -q`

The round should not be considered complete unless:

- deleted facades are absent from the tree
- runtime code no longer imports them
- tests no longer depend on them
- `ARCHITECTURE.md` reflects the converged boundary accurately
