# Shared Single-Paper Processing Refactor Design

**Goal**

Refactor current `master` toward one shared single-paper processing architecture so the per-paper business rules live in one place and are reused across:

- `csv_update`
- `notion_sync`
- `url -> csv`
- single-paper `arxiv_relations` export

The target is maximum code reuse on current `master`, not revival of `unify-csv-content-fetch` as-is. The right idea from that branch is the shared single-paper boundary. The wrong part is its exact implementation shape: it pushed too much adapter/sink detail into a generic framework and predated the newer relation-resolution work now on `master`.

**Scope**

In scope:

- define the final target architecture for current `master`
- define one shared single-paper engine boundary
- separate shared business rules from mode-specific parsing and writeback
- make `overview` / `abs` a global rule keyed off the final valid GitHub repo outcome
- define where relation export fits without moving relation-specific normalization out of `src/arxiv_relations/`
- define phased delivery with `Phase A` and `Phase B`
- keep the design realistic to the existing modules: evolve `paper_enrichment` and `paper_export` instead of replacing them with a broad new abstraction layer

Out of scope:

- merging `unify-csv-content-fetch`
- rewriting all runners into one framework
- changing CLI dispatch or input shapes
- changing CSV schemas or Notion property schema
- moving relation cache or relation normalization into shared generic code
- broadening non-relation behavior beyond what is needed for the shared engine boundary

**Current Problem**

Current `master` already has partial reuse, but the single-paper logic is still split in the wrong places.

Today:

- `src/shared/paper_enrichment.py` owns URL normalization, GitHub discovery, validation, and stars for some flows
- `src/shared/paper_export.py` reuses that path for `url -> csv` and `arxiv_relations`
- `src/csv_update/pipeline.py` reuses `enrich_paper`, but also owns `overview` / `abs` side effects itself
- `src/notion_sync/pipeline.py` still duplicates repo resolution, GitHub validation, and star lookup outside the shared path

That creates three concrete problems:

1. cross-cutting rules drift across modes
2. `overview` / `abs` is currently a CSV-local behavior instead of a global business rule
3. the new relation-resolution work on `master` makes it more important to keep relation-local normalization separate from generic single-paper enrichment

The most obvious drift is `overview` / `abs`:

- `csv_update` currently kicks content-cache work in parallel with enrichment
- that means the cache attempt is tied to the CSV path rather than to the final GitHub outcome
- `notion_sync`, `url -> csv`, and relation export do not participate in the same rule

The repository needs one shared definition of "process one paper", with modes only responsible for:

- turning source data into a request
- deciding source-specific policy knobs
- formatting the result back to their own sink

**Architectural Judgment**

The boundary idea from `unify-csv-content-fetch` is worth keeping:

- one shared single-paper engine
- mode-local adapters
- mode-local sinks

Its exact implementation should not be adopted directly.

Do not revive the old branch by reintroducing a generic `paper_engine + paper_task_builders + paper_sinks` framework wholesale. That shape is too framework-like for this codebase, and it pushes source-specific context into shared models that do not need it.

The better direction on current `master` is:

- evolve `src/shared/paper_enrichment.py` into the shared single-paper engine
- keep `src/shared/paper_export.py` as export-specific batch orchestration
- keep adapters and sinks close to their modes unless a tiny shared helper is clearly justified
- keep relation-specific normalization and caching inside `src/arxiv_relations/`

**Final Target Architecture**

The final architecture has four layers:

1. mode-local input adapters
2. relation-local normalization stage where applicable
3. shared single-paper engine
4. mode-local sinks plus shared export orchestration

The final per-mode flow should look like this.

`csv_update`

- CSV row adapter
- shared single-paper engine
- CSV row sink

`notion_sync`

- Notion page adapter
- shared single-paper engine
- Notion update sink

`url -> csv`

- collection/source adapter logic in `src/url_to_csv/*`
- normalized `PaperSeed` list
- shared export orchestration in `src/shared/paper_export.py`
- shared single-paper engine per seed
- CSV export record sink inside `paper_export`

`arxiv_relations`

- relation fetch / normalization / cache ladder in `src/arxiv_relations/*`
- normalized or retained `PaperSeed` list
- shared export orchestration in `src/shared/paper_export.py`
- shared single-paper engine per seed
- CSV export record sink inside `paper_export`

This keeps the final architecture broad enough to unify business rules, but narrow enough to respect the codebase’s existing structure.

**Shared Engine Boundary**

The shared engine should own all generic per-paper business rules:

1. paper identity normalization
2. optional title-based arXiv resolution when the caller allows it
3. GitHub resolution and validation
4. global `overview` / `abs` cache-warming decision
5. star lookup

The engine should not own:

- CSV parsing
- Notion property parsing
- collection-page crawling
- OpenAlex relation fetching
- relation-specific normalization ladders
- sink-specific writeback formatting

Recommended shared request contract:

- `title`
- `raw_url`
- `existing_github_url`
- `allow_title_search`
- `allow_github_discovery`

Recommended shared result contract:

- `title`
- `raw_url`
- `normalized_url`
- `github_url`
- `github_source`
- `stars`
- `reason`

Two boundary choices are important here.

First, the shared request/result contract should stay business-focused. Do not carry mode-local `source_ref`, page payloads, CSV rows, or output formatting hints through the engine. Callers can keep their own context next to the request.

Second, the result should preserve both `raw_url` and `normalized_url`. That is necessary because export flows, especially retained non-arXiv relation rows, may still need to write the original URL even when generic enrichment cannot normalize it to canonical arXiv form.

**Shared vs Mode-Specific Logic**

Shared logic:

- URL normalization through current `paper_identity` helpers
- optional title-to-arXiv resolution through current shared discovery/arXiv helpers
- GitHub repo resolution from either existing repo URL or discovery
- repo URL normalization and owner/repo extraction
- star lookup
- `overview` / `abs` cache warming after final valid repo resolution
- shared reason semantics for generic failures such as invalid repo URL, discovery miss, or GitHub API failure

Mode-specific logic:

- reading CSV columns and preserving row order
- reading Notion properties, including property-type details
- deciding whether a source-specific GitHub field should be treated as:
  - empty
  - existing repo candidate
  - mode-local unsupported value
- deciding whether title search is allowed for that mode
- choosing sink behavior on failure
- progress/output formatting

This means, for example:

- `notion_sync` should continue to classify `WIP`-style or other non-repo `Github` property content before entering the engine
- `csv_update` should continue to own row mutation rules and field preservation
- `paper_export` should continue to own CSV record creation and batch writing

**Global `overview` / `abs` Rule**

This rule is global in the final architecture:

- whenever a paper ends up with a final valid GitHub repo URL
- check whether local `overview` and `abs` files already exist
- fetch only the missing files
- do not special-case `csv -> csv`
- do not tie the rule to any one mode

The trigger is the final valid repo outcome, not the caller type.

More precisely, the shared engine should behave like this:

1. determine the final GitHub repo URL
2. validate that it is a real repo URL
3. if there is no valid repo URL, do not touch `overview` / `abs`
4. if there is a valid repo URL, attempt cache warming only when a canonical arXiv URL is available
5. cache warming should happen before star lookup, so a GitHub API failure does not suppress `overview` / `abs`
6. cache warming should reuse local files when already present and fetch only missing files

Two clarifications matter.

First, the global rule is still arXiv-backed. `overview` / `abs` caching needs a canonical arXiv identity. If a caller can produce a valid repo URL but cannot produce an arXiv identity under its own normal rules, the engine should still return the GitHub/stars result and simply skip content warming.

Second, the rule is not "warm content only on fully successful enrichment". A valid repo is enough. Star lookup failure is a separate concern.

**Paper Content Cache Responsibility**

`src/shared/paper_content.py` should be treated as a shared capability layer, not a CSV helper.

In the final shape it should expose one engine-friendly operation conceptually equivalent to:

- ensure local content cache for this canonical arXiv paper

That operation should:

- check whether `cache/overview/<arxiv_id>.md` already exists
- check whether `cache/abs/<arxiv_id>.md` already exists
- fetch only the missing artifact(s)
- stay agnostic about caller mode

The current per-kind path helpers may remain as implementation details or compatibility wrappers during migration, but the engine should not keep mode-specific path-handling concerns in its main contract.

**Where Relation Export Fits**

Relation export stays split into two responsibilities.

`src/arxiv_relations/*` continues to own:

- single-paper arXiv input validation
- OpenAlex fetches
- relation-local normalization
- relation-resolution cache use
- arXiv / Hugging Face fallback ladder
- retained non-arXiv row decisions
- relation-specific deduplication and ordering

The shared single-paper engine owns only what happens after relation normalization has already produced one related-paper candidate at a time:

- generic paper identity normalization
- generic GitHub resolution and validation
- global `overview` / `abs` rule
- star lookup
- generic CSV record population via `paper_export`

In other words:

- relation-specific normalization remains in `arxiv_relations`
- relation export hands off into the generic layer only after it has already decided what each related paper row is

This preserves the current `master` judgment that relation resolution is a special domain and should not be folded into the generic engine.

**Module Evolution**

Recommended module responsibilities in the final state:

`src/shared/paper_enrichment.py`

- home of the shared single-paper request/result contract
- home of the shared single-paper engine entrypoint
- may keep `enrich_paper(...)` as a thin compatibility wrapper during migration

`src/shared/paper_export.py`

- stays the shared export orchestrator for `PaperSeed -> CSV`
- owns batch concurrency, progress handoff, and record writing
- does not own single-paper business rules
- in the final state, calls the shared single-paper engine for each seed

`src/csv_update/pipeline.py`

- owns CSV row parsing
- owns CSV row writeback
- stops owning `overview` / `abs` orchestration

`src/notion_sync/pipeline.py`

- owns Notion page parsing
- owns Notion update payload construction
- stops owning duplicated repo-resolution and star-lookup flow

`src/arxiv_relations/pipeline.py`

- remains owner of relation normalization and retained-row behavior
- does not gain generic GitHub/stars/content logic

Avoid adding large shared adapter/sink modules unless the duplication remains clearly justified after the migration. The preferred default is local adapter/sink helpers near each mode.

**Phase A**

Phase A is the first implementation phase and should reshape `csv_update` and `notion_sync` around the final shared engine boundary.

Phase A changes:

- introduce the shared single-paper request/result contract in `paper_enrichment`
- move the generic single-paper flow into that shared engine
- keep `enrich_paper(...)` as a compatibility wrapper if that reduces blast radius
- refactor `csv_update` so it:
  - builds a mode-local request from each row
  - calls the shared engine
  - applies a mode-local row sink
- refactor `notion_sync` so it:
  - parses Notion-specific fields locally
  - performs any Notion-only Github-field classification locally
  - calls the shared engine for generic work
  - applies a Notion-specific update sink
- wire `PaperContentCache` into `notion_sync.runner` so the global content rule applies there too
- remove CSV-local `overview` / `abs` task orchestration from `csv_update`

Phase A does not need to migrate `url -> csv` or `arxiv_relations` onto the new direct request/result contract yet. The point of Phase A is to establish the boundary cleanly on the two pipelines that currently have the most drift.

Phase A should preserve:

- CSV schema and row preservation behavior
- Notion property update behavior
- current CLI routing
- current relation export behavior

**Phase B**

Phase B brings `url -> csv` and relation export into the same final architecture that Phase A already established.

Phase B changes:

- update `paper_export` so it uses the shared single-paper engine directly for each `PaperSeed`
- thread `PaperContentCache` into `url_to_csv.runner` and `arxiv_relations.runner`
- make the final global `overview` / `abs` rule apply to exported rows too
- keep `url_to_csv` collection adapters source-specific
- keep relation normalization and cache behavior in `arxiv_relations/*`
- remove any temporary compatibility layer that is no longer needed after all call sites share the engine boundary

Phase B is the point where the final architecture becomes fully true across:

- `csv_update`
- `notion_sync`
- `url -> csv`
- relation export

**Why This Phase Cut Is Correct**

This phase cut matches the current codebase rather than an abstract ideal.

`csv_update` and `notion_sync` are the places where the business-rule drift is worst:

- `csv_update` still owns content side effects itself
- `notion_sync` still duplicates generic resolution logic

`url -> csv` and `arxiv_relations` already converge through `paper_export`, so they are the natural second wave:

- first define the engine boundary clearly
- then fold the export path into it

That is safer than starting with export mode just because it already shares some code. Phase A should fix the worst architectural split first.

**Behavior Preservation**

The refactor should preserve these current-master behaviors unless a change is explicitly required by the new architecture:

- CLI mode dispatch
- CSV headers and output files
- Notion property names and update semantics
- relation normalization ladder and relation cache semantics
- retained non-arXiv relation row behavior
- current shared runtime and cache locations

The main intentional behavior change is:

- `overview` / `abs` becomes a global final-repo rule rather than a CSV-local side effect

**Testing**

The test split should mirror the architecture.

Engine tests:

- existing valid GitHub repo
- discovered GitHub repo
- invalid existing GitHub repo
- invalid discovered GitHub repo
- discovery miss
- title search allowed vs disabled
- valid repo + canonical arXiv URL triggers content warming
- valid repo + missing stars still warms content
- no valid repo skips content warming

Mode regression tests:

- CSV mode still preserves row order and fields
- Notion mode still preserves property-type behavior
- URL export still writes the same CSV shape
- relation export still preserves current normalization and retained-row behavior

Integration checks:

- `url -> csv` and relation export both participate in the same content rule by the end of Phase B
- repeated runs reuse local `overview` / `abs` files instead of refetching them

**Rationale**

This design is intentionally opinionated:

- one shared single-paper engine
- local adapters and sinks
- relation-local normalization stays relation-local
- `paper_enrichment` and `paper_export` are evolved, not replaced

That is the smallest architectural redesign that matches current `master`.

It salvages the right idea from `unify-csv-content-fetch` without importing the old branch’s over-generic shape, and it gives Phase A a clean boundary that will still be correct when Phase B brings `url -> csv` and relation export into the same final architecture.
