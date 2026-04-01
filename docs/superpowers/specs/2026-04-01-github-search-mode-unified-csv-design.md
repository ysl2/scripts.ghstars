# GitHub Search Mode And Unified CSV Design

**Goal**

Absorb `scripts.ghcsv` into `scripts.ghstars` without polluting the existing paper/arXiv enrichment core.

The final product surface should feel unified:

- one CLI entry shape: `uv run main.py <input>`
- one CSV schema across CSV-producing modes
- one CSV update story for refreshing `Stars`

The final implementation should still keep a clean internal boundary between:

- paper-collection ingestion that produces paper rows after normalization/discovery
- GitHub repository-search ingestion that produces repo rows directly

**User Decisions Captured In This Design**

- GitHub repository search should be merged into `scripts.ghstars`
- user-facing CLI stays `uv run main.py <github search url>`
- internally, GitHub search stays a separate dedicated mode/boundary rather than being forced into the current paper-collection pipeline
- all CSV-producing modes should share one fixed column order
- do not hide all-empty columns
- unified column order starts with:
  `Name, Url, Github, Stars, Created, About`
- for GitHub-search exports, `Name` and `Url` remain empty
- GitHub-search output rows should be sorted by `Created` descending
- `csv_update` should update rows by using whichever inputs are available
- if a row already has a non-empty `Github`, treat it as trusted source-of-truth and only refresh `Stars`
- `Created` / `About` from the new GitHub-search mode are export-time metadata and should not be refreshed by `csv_update`
- do not add special compatibility work for old CSV layouts
- output filenames should follow the broader `ghstars` style while still incorporating useful GitHub-search query/sort information

**Scope**

In scope:

- add GitHub repository-search URL support to the main CLI
- keep that support on a dedicated internal repo-search collector path
- define a unified CSV row schema and writer contract across CSV-producing modes
- adapt fresh-export modes to emit the unified schema
- redefine `csv_update` around per-row available inputs rather than a hard file-wide `Url` requirement
- preserve the existing rule that a preexisting non-empty `Github` value is trusted and never rediscovered/overwritten
- define output ordering and filename behavior for the new GitHub-search path
- define tests and docs changes needed for the merged product

Out of scope:

- changing Notion property schema
- changing the single-paper relation export product shape beyond adopting the unified CSV schema if it already writes through shared export helpers
- adding a second non-URL GitHub-search CLI input form such as raw query/sort arguments
- refreshing `Created` / `About` in `csv_update`
- compatibility shims to preserve every old `ghcsv` filename or row-order detail

**Observed Current State**

`scripts.ghcsv` today:

- accepts one GitHub repository-search URL
- uses GitHub Search API harvesting with recursive `stars` / `created` partition splitting to get under the 1,000-result cap
- writes a fixed CSV schema:
  `Github, Created, Stars, About`
- sorts final rows by `Github` URL

`scripts.ghstars` today:

- routes `main.py <url>` only to supported paper-collection URLs
- fresh export is built around paper-centric models such as `PaperSeed` and `PaperRecord`
- shared CSV writing is fixed to:
  `Name, Url, Github, Stars`
- `csv_update` keeps unrelated columns intact but currently requires a `Url` column at file level
- `csv_update` already trusts a preexisting non-empty `Github` and uses it directly for `Stars` refresh

The architectural mismatch is clear:

- GitHub-search rows are terminal repo rows, not paper seeds
- the current paper chain assumes optional arXiv normalization and optional GitHub discovery before `Stars`

That is why the merge should happen at the CLI/product layer and CSV/update layer, not by pretending repo-search rows are paper inputs.

**Architectural Decision**

Use one unified product surface with two ingestion families.

1. paper-family ingestion
2. GitHub-search ingestion

They share:

- top-level CLI dispatch
- output directory conventions
- CSV schema and writer
- row-update rules for `csv_update`
- GitHub star refresh capability

They do not share:

- source-specific collection crawling
- paper normalization/discovery steps that are irrelevant to direct repo-search rows

This keeps the system unified where it matters to the user while preserving honest internal boundaries.

**Why This Is Not Just Another `url_to_csv` Source**

It would be technically possible to bolt GitHub search URLs into the existing `url_to_csv` source switch, but that is the wrong boundary for this codebase.

`url_to_csv` currently means:

- fetch paper seeds from a paper collection source
- optionally normalize to canonical arXiv papers
- run shared paper enrichment/export

GitHub search URL ingestion means:

- fetch repository rows directly
- no paper normalization
- no repo discovery
- only optional later `Stars` refresh in update mode

So the recommended structure is:

- top-level `main.py` detects GitHub search URLs
- dispatch to a dedicated internal runner/pipeline for GitHub-search export
- keep paper-collection URL handling in the existing `url_to_csv` package

This is still a unified CLI. The separation is internal, not user-facing.

**Final CLI Contract**

The CLI should remain:

```bash
uv run main.py <input>
```

Dispatch rules after this change:

- no positional argument: Notion sync
- one existing `.csv` file path: CSV update
- one supported single-paper arXiv URL: single-paper relation export
- one supported paper collection URL: paper collection export
- one supported GitHub repository-search URL: GitHub-search export

For the user, GitHub-search input is merged into the same main entrypoint.

**GitHub Search Input Contract**

First version input contract:

- only GitHub repository-search URLs
- no separate query/sort CLI arguments

Representative shape:

```text
https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc
```

The dispatcher should reject unsupported GitHub search URLs the same way other unsupported URLs are rejected.

The source-specific collector should keep `ghcsv`’s real value:

- parse query/sort/order from the GitHub search URL
- recursively partition requests by `stars` and `created`
- fetch repository rows through the GitHub Search API

That logic should move into `scripts.ghstars` as GitHub-search-specific ingestion, not be reimplemented through ad hoc paging.

**Unified CSV Schema**

All CSV-producing modes should converge on one fixed ordered schema.

Initial schema for this merge:

1. `Name`
2. `Url`
3. `Github`
4. `Stars`
5. `Created`
6. `About`

Rules:

- all fresh exports write the full schema in that order
- no mode hides all-empty columns
- new repo-search exports leave `Name` and `Url` empty
- existing paper-family exports leave `Created` and `About` empty unless later work explicitly populates them

This intentionally simplifies the product:

- fixed headers are easier for downstream scripts
- no split behavior between fresh export and update
- no per-run dynamic-column decisions

**Unified Export Row Model**

Do not turn `PaperRecord` into a dishonest “universal record” by stuffing repo-search semantics into paper fields.

Instead, introduce a separate shared CSV row model dedicated to export/update shape. Conceptually:

- one shared row object with the CSV-facing fields
- paper-specific models such as `PaperSeed` remain paper-specific
- paper enrichment continues to return paper/business results
- export adapters convert those results into the shared CSV row shape
- GitHub-search ingestion converts repo results directly into the same shared CSV row shape

Recommended field set for the shared CSV row model:

- `name`
- `url`
- `github`
- `stars`
- `created`
- `about`
- optional internal sort/index metadata if needed for writer ordering

This keeps:

- paper-domain logic honest
- repo-domain logic honest
- export contract unified

**Fresh Export Behavior By Family**

Paper-family exports:

- keep existing paper normalization/enrichment behavior
- map output into the unified schema
- leave `Created` / `About` empty for now

GitHub-search export:

- ingest repository rows directly from GitHub Search API harvesting
- map them into the unified schema:
  - `Name = ""`
  - `Url = ""`
  - `Github = repository html_url`
  - `Stars = repository stargazers_count`
  - `Created = repository created_at`
  - `About = repository description or ""`
- sort rows by `Created` descending before write

The GitHub-search path should not call paper normalization or repository discovery.

**CSV Update Redesign**

The current file-level `Url` requirement should be removed.

That requirement is a paper-first artifact, not a real business requirement for `Stars` refresh.

New `csv_update` rule:

- update each row using whichever inputs are available

Per-row behavior:

1. if row has a non-empty `Github`
   - trust it as source-of-truth
   - do not rediscover or overwrite it
   - validate it as a GitHub repo URL
   - refresh `Stars` from it
   - do not use `Url` for rediscovery in this case

2. else if row has no usable `Github` but has a non-empty `Url`
   - use the existing paper normalization/discovery path
   - if discovery succeeds, fill `Github`
   - then fetch `Stars`

3. else
   - skip the row with a clear reason

File/header behavior:

- the CSV file no longer needs a `Url` column to be considered valid
- `Github` and `Stars` remain managed columns
- if the unified full schema is adopted for future exports, `csv_update` should preserve that schema when rewriting
- if an old file is missing some newer columns, no special compatibility work is required beyond what the implementation naturally supports

This makes the update chain match the actual business rule:

- `Url` is only needed when repo discovery is needed
- `Github` is sufficient for `Stars` refresh

**Field Preservation Rules**

Preserve these existing semantics:

- a preexisting non-empty `Github` is source-of-truth and must not be reformatted, rediscovered, or replaced
- if that existing `Github` is invalid, report the invalid-repo reason rather than silently replacing it
- `Created` and `About` are not refreshed by `csv_update`
- `Name` and `Url` in GitHub-search exports remain empty unless future explicit product decisions change that

This keeps the update path conservative and consistent with prior agreements about not overwriting user/source-provided GitHub values.

**Output Filename Design**

GitHub-search export filenames should follow the broader `ghstars` output conventions while retaining useful search identity from `ghcsv`.

Required properties:

- written under `./output` in the current working directory, consistent with other fresh exports
- include readable information derived from the GitHub search URL query and sort/order parameters
- remain deterministic enough that a user can tell what search produced the file

The exact filename helper can evolve during implementation, but the design intent is:

- `ghstars`-style output location and timestamping
- `ghcsv`-style query/sort readability

**Ordering Rules**

Fresh GitHub-search exports should sort by `Created` descending.

That is a product requirement and intentionally differs from the old `ghcsv` final sort by GitHub URL.

Paper-family exports should keep their current ordering semantics unless explicitly changed by separate design work.

This means ordering remains family-specific even though the schema is shared.

**Documentation Changes Required**

Update at least:

- `README.md`
- `ARCHITECTURE.md`

The docs should describe:

- the new GitHub-search input support on the main CLI
- the new shared CSV schema
- the fact that `csv_update` can refresh `Stars` from an existing `Github` without requiring `Url`
- the fact that GitHub-search exports leave `Name` / `Url` empty by design

**Testing Requirements**

At minimum, add or adjust tests for:

1. top-level CLI dispatch
   - GitHub search URL is accepted by `main.py`
   - unsupported GitHub URLs are rejected cleanly

2. GitHub-search ingestion
   - URL parsing
   - partition splitting behavior
   - final repo row mapping into unified CSV rows

3. unified CSV writing
   - full fixed header order:
     `Name, Url, Github, Stars, Created, About`
   - paper exports write empty `Created` / `About`
   - GitHub-search exports write empty `Name` / `Url`

4. GitHub-search output ordering
   - final rows sorted by `Created` descending

5. CSV update behavior
   - file without `Url` column but with valid `Github` still updates `Stars`
   - row with both `Github` and `Url` trusts existing `Github`
   - row with blank `Github` and present `Url` still uses paper discovery
   - row with neither `Github` nor `Url` is skipped with a reason
   - `Created` / `About` remain unchanged during update

6. README/architecture examples where applicable

**Implementation Shape**

This design is focused enough for one implementation plan, but the implementation itself should still proceed in a clear order:

1. introduce the unified CSV row/schema/writer layer
2. adapt current fresh-export paths to that schema
3. add GitHub-search ingestion/runner/dispatch
4. relax and redesign `csv_update` around per-row available inputs
5. update docs and tests

That order keeps core shared output contracts stable before wiring the new GitHub-search path into the CLI.

**Rejected Alternatives**

Rejected: force GitHub-search rows through the current paper-collection pipeline.

Why:

- repo-search rows are not paper seeds
- it creates fake paper semantics
- it increases coupling between unrelated ingestion families

Rejected: dynamic “hide all-empty columns” CSV output.

Why:

- it complicates product rules
- it conflicts with the desire for a fixed unified schema
- it makes update behavior less predictable

Rejected: make GitHub-search a user-visible separate subcommand.

Why:

- the user explicitly wants the simple `main.py <url>` entry shape
- top-level URL dispatch already exists and is the right product surface

**Final Design Summary**

`scripts.ghstars` should absorb `scripts.ghcsv` as a new GitHub-search ingestion family behind the same top-level CLI entrypoint.

The merge point is:

- unified CLI dispatch
- unified CSV schema
- unified CSV update behavior

The non-merge point is:

- source-specific ingestion and paper normalization responsibilities

This gives the user one project and one entrypoint without collapsing two different domains into one misleading internal pipeline.
