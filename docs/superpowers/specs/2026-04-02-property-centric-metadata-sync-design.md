# Property-Centric Metadata Sync Design

**Goal**

Refactor `scripts.ghstars` away from mode-specific process chains and toward one
shared property-centric core.

The core design should revolve around the six user-facing record properties:

- `Name`
- `Url`
- `Github`
- `Stars`
- `Created`
- `About`

Each property should have:

- a current value
- a provenance/status
- an acquisition strategy
- a write policy

The top-level architecture should no longer be "how does mode X crawl and then
patch mode-specific fields?" The top-level architecture should instead be "for
this record, which properties are present, which can be derived, and which may
be written back?"

This change is motivated by a real architectural mismatch in current `master`:

- source-specific collection logic often cannot be merged honestly
- but once a record enters the shared business layer, many property rules are
  already the same across:
  - `url_to_csv`
  - `csv_update`
  - `notion_sync`
  - single-paper `arxiv_relations`

The design goal is to merge those property rules without forcing all upstream
sources into one dishonest ingestion pipeline.

**User Decisions Captured In This Design**

- the architecture should be reframed around property acquisition and write
  policies rather than around mode-specific process chains
- all core properties should be treated as peer-level in the central flow
- source-specific keys needed for CSV row updates or Notion page updates belong
  to adapters, not to the core property model
- keep `GitHub search` as a separate collection family; do not force it into the
  paper/arXiv ingestion chain
- still extract one shared `Github URL -> repo metadata` capability and reuse it
  across:
  - `url_to_csv`
  - `csv_update`
  - `notion_sync`
  - `arxiv_relations`
- `GitHub search` may conceptually produce Stage-B-shaped property data directly
  without re-querying the repo metadata API for every result
- the property/value rules are:
  - `Stars`: always overwrite
  - `About`: always overwrite, including overwrite-to-empty when the remote repo
    currently has no description
  - `Created`: only backfill when currently empty
- CSV and Notion should not require all possible output columns/properties to
  exist up front
- each step should validate only the inputs it actually needs
- output columns/properties may be added on write when missing
- for `csv -> csv` updates:
  - do not reorder existing columns
  - append newly introduced columns after existing ones
  - among newly appended columns, preserve canonical relative order:
    `Name, Url, Github, Stars, Created, About`
- for Notion:
  - missing properties may be auto-created
  - required property types are:
    - `Github = url`
    - `Stars = number`
    - `Created = date`
    - `About = rich_text`
  - if a same-name property already exists with the wrong type, that is a hard
    error rather than a compatibility write
- this write policy should also apply to single-paper relation exports, not only
  to CSV update
- source-specific details such as URL normalization still matter, but they are
  secondary to the property-centric architecture

**Scope**

In scope:

- define one shared property-centric business layer
- define the core record/property model
- define property acquisition and write policies for:
  - `Name`
  - `Url`
  - `Github`
  - `Stars`
  - `Created`
  - `About`
- define the shared `Github URL -> repo metadata` capability
- define source adapters for current product families:
  - paper collection export
  - CSV update
  - Notion sync
  - single-paper relations
  - GitHub search export
- define target adapters for:
  - fresh CSV export
  - CSV update in place
  - Notion page update
- define risky existing contracts that must be preserved or intentionally changed
- define a phased migration plan realistic to the current codebase

Out of scope:

- changing CLI entry shapes
- merging all sources into one ingestion crawler
- building a dynamic plugin framework or general-purpose DAG engine
- changing the user-facing GitHub-search collector contract
- broad cache redesign beyond what is required by the new property boundaries
- changing product-level CSV/Notion semantics beyond the decisions captured above

**Observed Current State**

Current `master` still organizes the shared business layer around chained
"process one paper" functions instead of around properties.

Concrete evidence:

- [`src/shared/paper_enrichment.py`](../../src/shared/paper_enrichment.py)
  currently fuses:
  - URL/arXiv resolution
  - GitHub validation/discovery
  - content cache warming
  - stars lookup
- [`src/shared/github.py`](../../src/shared/github.py) currently exposes only a
  stars lookup API, not a full repo metadata API
- [`src/shared/paper_export.py`](../../src/shared/paper_export.py) still hardcodes
  `Created` and `About` to empty for paper-family fresh exports
- [`src/csv_update/pipeline.py`](../../src/csv_update/pipeline.py) still behaves
  like a `Github/Stars` patcher with append-only handling for those columns only
- [`src/notion_sync/notion_client.py`](../../src/notion_sync/notion_client.py)
  currently provisions only `Github` and `Stars`
- `GitHub search` already produces output shaped like:
  - `Github`
  - `Stars`
  - `Created`
  - `About`
  but it does so through its own dedicated family rather than through a shared
  repo metadata layer

The result is that current `master` already contains many of the right local
rules, but the rules are split across mode-specific chains rather than centered
on the properties themselves.

**Architectural Judgment**

The correct fix is not "merge every data source into one process chain."

The correct fix is:

1. keep source-specific ingestion where it is honest
2. move shared business logic into a property-centric core
3. treat CSV/Notion update mechanics as target adapters
4. keep source/target identity handles outside the core model

This means:

- upstream source crawling can stay different
- downstream persistence mechanics can stay different
- the core property rules become shared

That is the right abstraction boundary for this repository.

**Core Model**

The central shared business model should be a `RecordState` over six peer-level
properties:

- `Name`
- `Url`
- `Github`
- `Stars`
- `Created`
- `About`

These properties are peer-level in the core flow even though some depend on
others for acquisition.

The core should not be modeled as "mode X enters stage A then stage B." Instead,
the core should answer:

- which properties are already known?
- which properties may be derived?
- which writes are allowed by policy?

Recommended conceptual structure:

- `RecordState`
  - one slot per property
  - one internal facts bag for non-user-facing derived facts
- `PropertyState`
  - `value`
  - `status`
  - `source`
  - optional `reason`
- `RecordFacts`
  - internal derived facts used by resolvers
  - not part of the user-facing six-column contract

Recommended property status set:

- `present`
  - source adapter provided a usable value directly
- `resolved`
  - shared resolver derived the value
- `skipped`
  - policy intentionally chose not to mutate or refresh the value
- `blocked`
  - the property could not be attempted because dependencies were missing
- `failed`
  - the resolver ran and failed

This status model is important because current `master` collapses too many
different situations into one `reason` string on a monolithic enrichment result.

**Core Properties vs Internal Facts**

The six core properties are the user-facing columns/properties.

The core may still compute internal facts that are not themselves treated as
first-class user-facing properties. Examples:

- canonical arXiv identity
- normalized GitHub owner/repo tuple
- GitHub acquisition provenance
- whether a URL value was preserved vs script-derived

These derived facts exist to support property resolvers.

They are not target keys and they are not sink identity handles. For example:

- CSV row index belongs to the CSV target adapter
- Notion page ID belongs to the Notion target adapter
- Notion property type map belongs to the Notion target adapter

That separation keeps the core property model honest and reusable.

**Property Rules**

The following rules define the business behavior of each core property.

**`Name`**

Role:

- peer-level property
- candidate evidence for acquiring `Github`

Acquisition:

- primarily source-provided
- no new shared refresh pipeline is required in this refactor

Dependencies:

- none

Write policy:

- fresh export writes the currently known value
- update modes do not overwrite non-empty `Name` as part of this refactor

Failure semantics:

- empty/missing `Name` is not itself a fatal record failure
- it only removes one acquisition route for `Github`

**`Url`**

Role:

- peer-level property
- candidate evidence for acquiring `Github`
- may also be normalized according to existing arXiv URL rules

Acquisition:

- source-provided initially
- may be transformed through the existing shared arXiv resolution layer when the
  relevant mode allows normalization/writeback

Dependencies:

- none as a stored property
- URL normalization may use title and metadata services internally

Write policy:

- fresh export writes the final property value
- non-Notion CSV flows may continue to write back script-derived normalized URLs
  when allowed by the existing normalization contract
- Notion keeps the current product rule: normalization may be used internally but
  does not rewrite the stored literature URL property

Failure semantics:

- empty/missing `Url` is not itself a fatal record failure
- it only removes one acquisition route for `Github`

**`Github`**

Role:

- peer-level property
- bridge property for repo metadata acquisition

Acquisition:

- shared acquisition rule checks available evidence in priority order:
  1. existing `Github`
  2. `Url`
  3. `Name`

This does not mean all three inputs are required. It means the resolver may try
each available evidence path in that order.

Dependencies:

- may use existing shared URL/arXiv resolution and discovery helpers internally

Write policy:

- fresh export writes the final property value
- CSV/Notion update modes treat an existing non-empty `Github` as trusted
  source-of-truth and do not overwrite it
- if `Github` is empty, an acquired value may be written

Normalization policy:

- script-discovered GitHub values may be normalized to the repository root URL
- existing non-empty user/source-provided `Github` values remain preserved
  exactly, matching the existing project rule

Failure semantics:

- if no evidence paths are available or all available paths fail, `Github`
  becomes `failed`
- `Stars`, `Created`, and `About` then become `blocked` unless they already have
  source-provided values and policy says to leave them untouched

**`Stars`**

Role:

- peer-level property
- derived from `Github`

Acquisition:

- through the shared `Github repo metadata` resolver

Dependencies:

- `Github`

Write policy:

- always overwrite on successful refresh

Failure semantics:

- if `Github` is unavailable, `Stars` is `blocked`
- if repo metadata fetch fails, `Stars` is `failed`
- update modes do not clear existing stored stars on failure; they simply do not
  write a new value

**`Created`**

Role:

- peer-level property
- derived from `Github`

Acquisition:

- through the shared `Github repo metadata` resolver

Dependencies:

- `Github`

Write policy:

- fresh exports write the resolved value directly
- update modes only backfill when the current stored value is empty

Failure semantics:

- if `Github` is unavailable, `Created` is `blocked`
- if repo metadata fetch fails, `Created` is `failed`
- update modes do not clear existing stored values on failure

**`About`**

Role:

- peer-level property
- derived from `Github`

Acquisition:

- through the shared `Github repo metadata` resolver

Dependencies:

- `Github`

Write policy:

- always overwrite on successful refresh
- if the remote repository description is empty, overwrite the local value to
  empty as well so local state matches the repository

Failure semantics:

- if `Github` is unavailable, `About` is `blocked`
- if repo metadata fetch fails, `About` is `failed`
- update modes do not clear the local value on fetch failure; only a successful
  empty remote result clears it

**Resolver Graph**

Although the six properties are peer-level in the core model, property resolvers
still have explicit dependencies.

Recommended resolver graph:

1. source adapter seeds any directly available property values
2. URL/identity helper facts may be computed as needed
3. `Github` resolver runs using available evidence in priority order:
   - existing `Github`
   - `Url`
   - `Name`
4. repo metadata resolver runs from `Github`
5. target adapter applies per-property write policy

Two clarifications matter here.

First, the graph should be explicit but not generic-framework-heavy. This
repository does not need a runtime-registered DAG engine. Static explicit code
for the current six properties is sufficient and preferable.

Second, source-specific details such as:

- arXiv URL normalization
- DOI/OpenAlex resolution
- GitHub-search response fields

still exist, but they should be implementation details inside source adapters or
resolvers, not the top-level architecture.

**Shared `Github -> Metadata` Capability**

Introduce one shared repo metadata capability under `src/shared/` that resolves:

- normalized `github_url` or owner/repo identity
- `stars`
- `created`
- `about`

This should become the canonical shared metadata source for:

- `url_to_csv`
- `csv_update`
- `notion_sync`
- `arxiv_relations`

It does not have to replace the upstream GitHub-search collector.

`GitHub search` may keep using GitHub Search API response fields directly because
those rows already arrive with `Github`, `Stars`, `Created`, and `About`.

The important architectural alignment is:

- search mode produces property states that match the shared property contract
- other modes use the shared repo metadata resolver to reach the same contract

The repository does not need artificial duplicate repo metadata lookups just to
make the code "look uniform."

**Source Adapters**

Each top-level product family should map its local input into the core
`RecordState` model.

Source adapters own:

- how initial values are read
- source-specific evidence availability
- source-specific internal handles

They do not own shared business rules for property acquisition and write policy.

Recommended source adapters:

**Paper-family export source adapter**

Used by:

- `url_to_csv`
- `arxiv_relations` after relation normalization has already produced paper-like
  rows

Seeds initial properties such as:

- `Name`
- `Url`

Then relies on shared property resolvers for:

- `Github`
- `Stars`
- `Created`
- `About`

**CSV row source adapter**

Seeds whatever properties are already present in the row.

No global "all columns must exist" rule should remain.

The adapter should simply report which property columns are currently present and
which values are non-empty.

**Notion page source adapter**

Seeds whatever of the six properties can already be read from the page.

The adapter also carries Notion-specific sink handles and schema/type information,
but those belong to adapter state, not to the core property model.

**GitHub search source adapter**

Seeds:

- `Github`
- `Stars`
- `Created`
- `About`
- and empty `Name` / `Url` per current product contract

This adapter conceptually emits core property states directly without needing the
shared repo metadata resolver.

**Target Adapters**

Target adapters are responsible for turning a final `RecordState` into writes.

They own:

- target identity handles
- target-specific schema checks
- target-specific creation/update mechanics
- target-specific "do not rewrite this field" policies where applicable

**Fresh CSV export adapter**

Rules:

- always write all six columns
- canonical order remains:
  - `Name`
  - `Url`
  - `Github`
  - `Stars`
  - `Created`
  - `About`

This applies to:

- paper-family fresh exports
- `arxiv_relations` fresh exports
- GitHub-search fresh exports

**CSV update adapter**

Rules:

- do not require all six columns to exist up front
- only require the columns needed for whichever property writes are actually
  attempted
- preserve existing column order exactly
- append missing output columns when needed
- appended columns must follow canonical relative order:
  - `Name`
  - `Url`
  - `Github`
  - `Stars`
  - `Created`
  - `About`

Example:

- input columns: `Url,Name`
- output columns after an update that now needs repo metadata:
  `Url,Name,Github,Stars,Created,About`

The adapter does not reorder `Url,Name` into canonical order. It only appends
newly introduced columns at the end in canonical relative order.

**Notion target adapter**

Rules:

- do not require all six properties to exist up front
- create missing output properties when needed with these exact types:
  - `Github = url`
  - `Stars = number`
  - `Created = date`
  - `About = rich_text`
- if a same-name property exists with the wrong type, raise a hard error
- preserve the existing rule that Notion does not rewrite the stored paper `Url`
  property as part of URL normalization
- preserve the existing rule that a preexisting non-empty `Github` value is
  trusted and not overwritten

**Risky Existing Contracts**

The following current contracts are important enough to state explicitly so the
refactor does not accidentally erase them.

**Existing non-empty `Github` values are source-of-truth**

This rule already exists in current `master` and should remain.

- existing values are consumed as evidence
- they are not rediscovered or reformatted in update modes
- they are not written into caches as if the script discovered them

**User-provided arXiv URLs and script-derived normalized URLs are not the same**

Current shared URL normalization work already distinguishes between:

- preserved user/source-provided arXiv URL strings
- script-derived normalized arXiv URLs used for writeback/internal identity

The new property-centric core must keep that distinction.

**Notion URL writeback remains different from CSV writeback**

Current product behavior intentionally keeps Notion from rewriting the stored
paper URL field even when normalization is used internally.

The property-centric design does not erase that difference. That difference
belongs in the Notion target adapter.

**Current monolithic `process_single_paper` failure semantics are too fused**

Current `master` still tends to collapse:

- GitHub acquisition failure
- invalid existing GitHub value
- repo metadata failure

into one shared result/reason flow.

The new design must split those concerns per property so that update adapters can
make intentional choices for:

- partial success
- overwrite vs preserve
- skip vs fail reporting

**Current content warming is coupled to monolithic enrichment**

Current `paper_enrichment` warms content before star lookup.

That ordering may still be correct, but it should no longer live as an implicit
side effect of a monolithic enrichment contract. It should instead be tied to the
relevant internal fact and property resolution path explicitly.

**Compatibility With Existing Product Families**

The property-centric design does not remove the need for family-specific source
logic.

The intended mapping is:

- `url_to_csv`
  - source adapter: paper collection seeds
  - target adapter: fresh CSV export
- `arxiv_relations`
  - source adapter: relation-normalized paper rows
  - target adapter: fresh CSV export
- `csv_update`
  - source adapter: existing CSV row
  - target adapter: CSV in-place update
- `notion_sync`
  - source adapter: Notion page
  - target adapter: Notion page update
- `github_search_to_csv`
  - source adapter: GitHub search row
  - target adapter: fresh CSV export

The architecture changes the shared core, not the honest existence of these
different product families.

**Migration Strategy**

This refactor should be delivered in phases. A one-shot rewrite would be risky
because too many current call sites still depend on monolithic enrichment
contracts.

**Phase 1: Introduce the property-centric core contract**

- add `RecordState`, `PropertyState`, and explicit per-property status/result
  types
- keep `process_single_paper(...)` as a compatibility wrapper for now
- do not change product behavior yet

**Phase 2: Introduce shared GitHub repo metadata resolution**

- add a shared `Github -> {Stars, Created, About}` capability
- keep current `get_star_count()` compatibility behavior available during
  migration
- do not yet migrate every consumer at once

**Phase 3: Migrate paper-family fresh exports**

- migrate `paper_export`
- this automatically covers:
  - `url_to_csv`
  - `arxiv_relations`
- update fresh export tests so paper-family rows can now populate
  `Created/About` through the shared metadata layer

This is the best first behavioral migration because:

- `paper_export` is already a shared export seam
- fresh export semantics are simpler than in-place update semantics

**Phase 4: Migrate CSV update**

- replace the current `Github/Stars` patcher shape with a property-aware adapter
- add append-on-write behavior for missing `Created/About` columns
- implement per-property overwrite/backfill rules

**Phase 5: Migrate Notion**

- extend schema management to all four managed repo properties:
  - `Github`
  - `Stars`
  - `Created`
  - `About`
- enforce hard errors on wrong existing property types
- migrate page updates to the new per-property policy model

Notion should be migrated last because it combines:

- external API behavior
- schema creation behavior
- intentional product-specific exceptions such as URL non-rewrite

**Phase 6: Remove legacy dual models where practical**

- clean up remaining monolithic wrappers and unused compatibility code
- remove stale four-column-only helpers when no longer needed
- shrink tests that only exist to preserve superseded transitional contracts

**Testing Priorities**

The migration should add tests in the same property-centric shape as the design.

Highest-priority tests:

1. `Github` acquisition outcome tests
   - existing `Github`
   - `Url`-based acquisition
   - `Name` fallback acquisition
   - complete acquisition miss

2. repo metadata tests
   - successful `{Stars, Created, About}` fetch
   - `About` overwrite-to-empty
   - metadata failure after successful `Github` acquisition

3. fresh export tests
   - paper-family exports now fill `Created/About`
   - GitHub-search export still writes its direct Stage-B-shaped data without a
     second repo lookup

4. CSV update policy tests
   - existing columns preserved in order
   - missing output columns appended in canonical relative order
   - `Stars` overwrite
   - `About` overwrite, including empty remote overwrite
   - `Created` backfill-only

5. Notion tests
   - missing property auto-creation for all four managed repo properties
   - wrong-type hard errors
   - `Url` non-rewrite preserved
   - existing non-empty `Github` trusted and not overwritten

6. partial-success tests
   - `Github` acquired, metadata fetch fails
   - update modes preserve old values on failed metadata fetch
   - blocked vs failed statuses remain distinct

**Design Constraints**

This design is intentionally not a general framework.

To avoid overengineering:

- property definitions may be static code, not dynamic registration
- dependency relationships may be explicit code, not a generic runtime DAG
- only current product-relevant properties need to exist
- source and target adapters should stay close to their current modules unless a
  tiny shared helper is clearly justified

The architectural shift is fundamental, but the implementation style should stay
concrete and repository-shaped.

**Final Design Summary**

The repository should evolve toward this shape:

- honest source-specific ingestion remains where needed
- one shared property-centric core manages the six record properties
- shared resolvers acquire/update properties according to explicit dependency and
  write rules
- target adapters own persistence mechanics and sink-specific exceptions

That is clearer than the current chain-oriented design because it matches the
real business question:

- not "which mode is this?"
- but "which properties are known, which properties can be derived, and which
  property writes are allowed?"
