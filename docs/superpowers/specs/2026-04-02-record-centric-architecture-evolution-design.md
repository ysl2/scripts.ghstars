# Record-Centric Architecture Evolution Design

**Goal**

Evolve `scripts.ghstars` from a repository organized primarily around
mode-specific process chains into a repository organized around a shared
record-centric domain model.

The center of the architecture should become one `Record` object with a stable
set of core properties:

- `Name`
- `Url`
- `Github`
- `Stars`
- `Created`
- `About`

The system should no longer be described primarily as "mode X does process Y."
It should instead be described as:

1. an input shape is adapted into one or more `Record` objects
2. shared property synchronization services reconcile those records
3. output adapters persist those records into CSV, Notion, or fresh exports

This design is intentionally **evolution-oriented**. It does not propose a
one-shot rewrite. It defines the end-state boundaries and a realistic staged
migration from the current repository.

## Product Framing

The external product interface remains:

- `uv run main.py`
- no positional argument: Notion sync
- one positional input: route by input shape

This design does **not** treat current internal modes as the top-level product
concept. The top-level product concept is:

- one CLI
- input-shape routing
- shared record/property synchronization
- target-specific persistence adapters

Current "modes" remain useful as thin orchestration shells, but they are not the
architectural center of the repository.

## Architectural Recommendation

Three broad directions were considered:

### Option A: Conservative shared-helper growth

- keep the current `runner/pipeline` organization as the real architecture
- continue adding shared helper functions and small services

Pros:

- lowest immediate migration risk

Cons:

- does not actually remove mode-centric thinking
- leaves the repository half process-oriented and half property-oriented
- long-term extensibility remains weak

### Option B: Evolutionary record-centric architecture

- keep thin outer orchestration shells
- move the architectural center to domain objects and service boundaries
- converge all input families on shared `Record` objects
- converge all persistence paths on shared output adapter contracts

Pros:

- keeps migration risk manageable
- matches the already-approved property-centric direction
- gives the repository one stable conceptual center

Cons:

- requires a systematic multi-phase refactor rather than isolated patches

### Option C: One-shot framework-style rewrite

- replace current shells, flows, and helpers with one new generalized engine

Pros:

- theoretically cleanest end state

Cons:

- highest risk
- easiest path to over-design
- least compatible with the current repository and test surface

**Recommendation:** choose **Option B**.

## Core Design Principles

- Core domain state should be represented with lightweight objects, not loose
  dicts.
- The repository should be organized around records and properties, not around
  mode-specific process chains.
- Existing thin orchestration shells may remain, but they should be treated as
  routing and composition layers rather than business-logic centers.
- Core domain state should be modeled as immutable or near-immutable value
  objects.
- Services, adapters, repositories, clients, caches, and runtime infrastructure
  may remain ordinary mutable objects.
- Input adapters and output adapters should stay thin. Shared business rules
  belong in services.
- `Overview` and `Abs` should remain supporting artifacts, not promoted into the
  core six-property model.

## Core Object Model

### `PropertyState`

Each core property should be represented by a lightweight value object with at
least:

- `value`
- `status`
- `source`
- `trusted`
- `reason`

Recommended status set:

- `present`
- `resolved`
- `skipped`
- `blocked`
- `failed`

`PropertyState` is a domain-state object. It does not fetch from external APIs
or write to target systems.

### `Record`

`Record` is the central domain object. It holds the six core properties as peer
members:

- `name`
- `url`
- `github`
- `stars`
- `created`
- `about`

It also holds:

- `facts`
- `artifacts`
- `context`

`Record` is a state container, not a workflow engine. It should support creating
updated copies, but should not own networking, persistence, or external
side-effects.

### `RecordFacts`

`RecordFacts` holds internal derived facts that help services but are not part
of the public six-property contract. Typical examples:

- canonical arXiv identity
- normalized owner/repo tuple
- GitHub acquisition provenance
- URL-normalization provenance
- repo-metadata freshness facts

This prevents internal helper state from polluting the core visible properties.

### `RecordArtifacts`

`RecordArtifacts` holds non-core derived assets such as:

- overview path
- abstract path
- content-cache availability

These are intentionally not first-class core properties. They are attached to
records, but they do not belong in the core six-property model.

### `RecordContext`

`RecordContext` holds adapter-facing handles needed for persistence or routing,
for example:

- CSV row index and fieldname context
- Notion page id and property map
- fresh-export output metadata

These handles belong to adapters, not to the domain core itself.

## Service Layer

The main shared behavior should move into explicit services that accept a
`Record` and return an updated `Record`.

Recommended service boundaries:

- `GithubAcquisitionService`
- `RepoMetadataSyncService`
- `PropertyPolicyService`
- `RecordSyncService`

### `GithubAcquisitionService`

Responsible for reconciling the `Github` property using the approved evidence
priority:

- existing `Github`
- `Url`
- `Name`

Important rule:

- these are **availability-checked evidence sources**, not required inputs
- once higher-priority evidence yields a trusted GitHub value, lower-priority
  evidence should not continue to compete

### `RepoMetadataSyncService`

Responsible for reconciling:

- `Stars`
- `Created`
- `About`

Important rules:

- `Stars`: overwrite when fresh trusted metadata is available
- `About`: overwrite when fresh trusted metadata is available, including
  overwrite-to-empty
- `Created`: backfill only when the current value is empty

### `PropertyPolicyService`

Responsible for encoding write policy, trust policy, and source precedence in
one place rather than scattering it across CSV/Notion/paper pipelines.

### `RecordSyncService`

Coordinates the above services and applies them to a `Record` without embedding
all business logic inside the `Record` object itself.

## Trusted Input Semantics

The system must distinguish between:

- trusted input values
- resolver-derived values
- missing values

Important preserved rules:

- existing non-empty `Github` from CSV or Notion is source-of-truth input
- existing source-of-truth `Github` should skip discovery and remain unchanged
- `GitHub search`-sourced `Github`, `Stars`, `Created`, and `About` are trusted
  inputs because they already come directly from GitHub
- trusted repo-side values from GitHub search should enter the unified property
  layer directly without redundant re-fetch by default

The architecture should unify property semantics, not force every input family
to repeat the same external requests.

## Input Adapter Layer

All supported input families should converge on:

`InputAdapter -> Iterable[Record]`

Recommended input adapters:

- `PaperCollectionInputAdapter`
- `RelationsInputAdapter`
- `GithubSearchInputAdapter`
- `CsvInputAdapter`
- `NotionInputAdapter`

Their responsibility is to:

- parse one input family honestly
- produce initial `Record` objects
- mark initial properties as trusted or untrusted
- attach source/target context handles

They should **not** own the main property synchronization rules.

### Paper-like adapters

`PaperCollectionInputAdapter` and `RelationsInputAdapter` usually provide:

- `Name`
- `Url`

Then rely on shared services for `Github`, `Stars`, `Created`, and `About`.

### GitHub-search adapter

`GithubSearchInputAdapter` usually provides trusted:

- `Github`
- `Stars`
- `Created`
- `About`

`Name` and `Url` may remain empty by design.

It should not be forced into the paper/arXiv acquisition path.

### Record-source adapters

`CsvInputAdapter` and `NotionInputAdapter` provide:

- whichever properties already exist
- update handles needed by the matching output adapters

They should not require all possible properties to exist up front.

## Output Adapter Layer

The persistence layer should converge on a small number of explicit output
adapters:

- `FreshCsvExportAdapter`
- `CsvUpdateAdapter`
- `NotionUpdateAdapter`

### `FreshCsvExportAdapter`

Used by:

- paper collections
- relations export
- GitHub search export

It writes the standard fresh-export six-column shape:

- `Name`
- `Url`
- `Github`
- `Stars`
- `Created`
- `About`

### `CsvUpdateAdapter`

Used by existing CSV update flows.

It must preserve the current CSV-specific rules:

- existing columns keep their original order
- unrelated columns stay untouched
- missing standard columns are appended
- appended standard columns preserve canonical relative order

### `NotionUpdateAdapter`

Used by Notion sync.

It must preserve the current Notion-specific rules:

- missing `Github`, `Stars`, `Created`, and `About` properties may be
  auto-created
- same-name wrong-type properties are hard failures
- canonical property types remain:
  - `Github = url`
  - `Stars = number`
  - `Created = date`
  - `About = rich_text`

Output adapters should share write-policy inputs, but not collapse their target
mechanics into one fake unified writer.

## Repository Layer

Persistence and cache access should move toward explicit repository objects.

Recommended repositories:

- `RepoDiscoveryRepository`
- `RepoMetadataRepository`
- `RelationResolutionRepository`

Responsibilities:

- schema management
- key normalization
- row mapping
- read/write of persisted facts

Repositories should not own business policy decisions.

Important persisted-fact rules to preserve:

- persist durable GitHub-related facts such as discovered GitHub URLs and
  durable `Created` values
- do not persist dynamic/current fields such as `Stars` or `About`
- do not write user-provided source-of-truth `Github` or arXiv values into
  caches as if the script discovered them

## Supporting Infrastructure

Routing, clients, and runtime shells remain necessary, but they are not the
architectural center.

The current `runner/pipeline` structure may remain as a long-lived thin shell
layer responsible for:

- input-shape routing
- config loading
- HTTP client construction
- repository/cache construction
- progress reporting
- adapter and service composition

This shell should be preserved as a boundary layer, but its business logic
should continue shrinking.

## What Should Be Merged

### Merge at the property core

These should converge:

- property state model
- GitHub acquisition policy
- repo-metadata synchronization policy
- write-policy decisions

### Merge at the input/output contracts

These should converge:

- all inputs producing `Record`
- all outputs consuming `Record`

### Do not force-merge upstream collectors

These should remain separate where their source semantics differ honestly:

- paper collection fetchers
- single-paper relation fetchers
- GitHub repository search collectors
- CSV record readers
- Notion page readers

### Do not force-merge target mechanics

These should remain separate where their persistence semantics differ honestly:

- fresh CSV export
- CSV in-place update
- Notion page update

## Migration Plan

This design should be implemented in phases.

### Phase 1: stabilize core objects

Introduce and adopt:

- `PropertyState`
- `Record`
- `RecordFacts`
- `RecordArtifacts`
- `RecordContext`

Allow existing shared code to use compatibility wrappers during this phase.

### Phase 2: unify input adapters

Converge current input families on shared `Record` creation, in this order:

1. paper collections + relations
2. CSV + Notion record sources
3. GitHub search

### Phase 3: service-ify property synchronization

Split the current shared enrichment path into explicit services and reduce
monolithic compatibility wrappers.

### Phase 4: unify output adapters

Converge fresh CSV export, CSV update, and Notion update around shared `Record`
consumption plus target-specific persistence rules.

### Phase 5: reshape repositories and cache boundaries

Only after the domain model and adapters stabilize should the cache/database
schema be fully realigned to the new repository boundaries.

## Critical Contracts To Preserve

- existing non-empty `Github` remains exact source-of-truth input
- GitHub search values enter as trusted repo-side properties
- CSV update preserves existing column order and custom columns
- Notion wrong-type collisions remain hard failures
- Notion must not rewrite the stored literature URL
- non-Notion outputs may continue to rewrite normalized URLs where existing
  product behavior already requires it
- `Overview` / `Abs` remain supporting artifacts, not core properties

## Out Of Scope

- changing the external single-CLI product contract
- forcing every input family into one collector
- forcing every target into one writer implementation
- promoting `Overview` / `Abs` into the core six-property domain model
- a one-shot rewrite of the entire repository
- a framework-style DAG engine or plugin system

## Architectural Judgment

The current direction is sound.

The right end state is not "pure process pipelines with a few helpers," and it
is also not "a giant OOP framework where every field owns its own mini-engine."

The right end state is:

- a light object-oriented core
- immutable or near-immutable domain state
- explicit services for business logic
- explicit adapters for system boundaries
- explicit repositories for persistence
- a thin shell for routing and runtime composition

That structure is the clearest path to long-term maintainability and future
extension for this repository.
