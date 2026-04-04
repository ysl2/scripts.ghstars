# Single-Paper Target Cache Warmup Design

**Goal**

When the CLI input is one single-paper arXiv URL, continue exporting the
paper's references and citations exactly as today, but also run the shared
single-paper processing chain for the target paper itself as a best-effort
cache-warm step.

The target paper warmup must:

- attempt GitHub discovery for the input paper itself
- reuse the existing `cache.db` repo-discovery path
- reuse the existing repo-metadata path
- ensure local `overview` / `abs` content cache when the paper ends with both a
  canonical arXiv identity and a valid GitHub repo
- produce no CSV row and no Notion write for the target paper itself
- never fail the main relation-export command when the warmup misses or errors

**User Decisions Captured In This Design**

- the feature should fit the current architecture cleanly and maximize reuse
- a large refactor is not required unless the existing architecture blocks a
  clean integration
- the target paper itself should also be treated as worth caching because the
  project's cache/content layers exist to accumulate reusable information
- target-paper caching is strictly best-effort:
  - cache hits are a gain
  - cache misses or transient failures must not fail or abort relation export
- the target-paper warmup should run alongside the current relation-export
  chain rather than serializing the whole command behind one extra pre-step
- do not introduce new cache tables for speculative metadata in this round;
  reuse the current durable cache surfaces that already have a clear
  architectural role

## Current State

The repository already has one shared single-paper sync boundary for paper
family exports:

`PaperSeed -> src/core/paper_export_sync.py -> RecordSyncService`

That boundary already owns the important side effects the new requirement wants:

- arXiv-backed GitHub discovery through `src/shared/discovery.py`
- `repo_cache` writeback for `arxiv_url -> github_url`
- shared GitHub repo metadata lookup through `src/shared/github.py`
- `repo_metadata_cache` writeback for durable repo metadata (`Created`)
- local `overview` / `abs` warmup through `src/shared/paper_content.py`

The gap is architectural rather than capability-related:

- in single-paper relation mode, only the derived reference/citation papers are
  converted into `PaperSeed` objects and sent through the shared sync layer
- the input target paper itself is resolved for title lookup and relation fetch,
  but it never enters the shared sync boundary
- because it never enters that boundary, it never participates in the existing
  cache/content side effects

So the missing behavior is not a new discovery or metadata mechanism. The
missing behavior is that relation mode does not currently schedule one shared
"process this paper" run for the target paper itself.

## Options Considered

### Option A: Add one relation-local best-effort target-paper sync helper

In `src/arxiv_relations/pipeline.py`, build a `PaperSeed` for the target paper
once its normalized arXiv URL and resolved title are known, then send it
through existing `sync_paper_seed(...)` as a best-effort side-effect-only step.

Pros:

- maximum reuse of the already-approved shared single-paper sync layer
- no broad core refactor
- preserves the current architecture boundary:
  - core/shared code owns "how to sync one paper"
  - relation mode owns "when to schedule target-paper warmup"
- uses existing caches with no schema expansion

Cons:

- adds one small relation-mode helper whose only purpose is side-effect-only
  scheduling

### Option B: Generalize `paper_export` to support null-output jobs

Teach `src/shared/paper_export.py` to handle both CSV-writing jobs and
side-effect-only jobs, then model the target paper warmup as an export task with
no sink.

Pros:

- superficially more uniform batch orchestration

Cons:

- pollutes an export-focused layer with a non-export use case
- weakens the current separation between "sync a paper" and "write export rows"
- solves a narrow need by broadening the wrong abstraction

### Option C: Introduce a generic paper job/sink framework

Create a new reusable job model where every mode describes a paper-processing
job plus a sink, with relation-target warmup using a null sink.

Pros:

- theoretically very uniform

Cons:

- over-designed for the current requirement
- larger migration surface and higher regression risk
- unnecessary given the already-existing `sync_paper_seed(...)` boundary

**Recommendation:** choose **Option A**.

The project already has the right shared single-paper processing boundary. The
cleanest change is to let relation mode schedule one additional best-effort use
of that boundary for the target paper itself.

## Recommended Design

### 1. Treat the target paper as one more `PaperSeed`

Once `export_arxiv_relations_to_csv(...)` has:

- normalized the single-paper input to canonical `arxiv_url`
- resolved the target paper title

it should construct a target-paper seed:

- `name = resolved title`
- `url = normalized arxiv_url`
- `canonical_arxiv_url = normalized arxiv_url`
- `url_resolution_authoritative = True`

This is an honest use of the existing `PaperSeed` abstraction. The target paper
is a paper-family input with authoritative arXiv identity, so it should reuse
the same input contract instead of inventing a relation-specific side model.

### 2. Add one relation-local best-effort warmup helper

Add a small helper in `src/arxiv_relations/pipeline.py` conceptually equivalent
to:

- accept the target-paper `PaperSeed`
- call `sync_paper_seed(...)`
- swallow misses and exceptions
- optionally emit a lightweight status line
- return nothing used by the main export result

This helper is intentionally relation-local because the behavior itself is
mode-specific:

- only single-paper relation mode wants to process the input target paper
  without exporting it
- the shared sync layer should stay focused on processing one paper, not on
  mode-local scheduling policy

### 3. Run target warmup concurrently with the existing relation chain

After title resolution succeeds, relation mode should start the target-paper
warmup and then continue with the existing target-paper lookup and
references/citations export flow.

Recommended shape:

- create an `asyncio.Task` for target-paper warmup
- run the existing relation flow unchanged
- await the warmup task before returning, but suppress any warmup failures

This preserves both goals:

- warmup is "in parallel with the current chain" instead of serially blocking
  relation processing
- the command still gives the warmup task a chance to finish before process exit

Awaiting at the end matters. If the command returned immediately after writing
the two CSVs, a still-running warmup task could be cancelled by event-loop
shutdown and lose the intended cache writes.

### 4. Reuse existing shared side effects exactly as they are

The target warmup should not implement its own discovery, metadata, or content
logic. Its job is only to schedule one shared single-paper sync.

Because it reuses `sync_paper_seed(...)`, the target warmup automatically gets:

- GitHub discovery for arXiv-backed papers
- `repo_cache` persistence of `canonical arxiv_url -> github_url`
- repo metadata lookup through the shared GitHub client
- `repo_metadata_cache` persistence of durable `Created`
- `overview` / `abs` local cache warming once a valid repo URL exists

No new write path should be introduced for:

- CSV rows
- Notion properties
- standalone target-paper output files

This is a cache warmup, not a new user-facing export artifact.

### 5. Keep failure semantics strictly best-effort

Warmup failure categories include:

- no GitHub repo found
- invalid discovered repo URL
- GitHub metadata API failure
- AlphaXiv content fetch failure
- transient network exceptions from any warmup stage

None of these should fail the relation export command.

Operational rule:

- if warmup succeeds, caches are improved
- if warmup partially succeeds, whatever was written may remain
- if warmup fails entirely, references/citations export continues unchanged

The main relation flow should keep its current failure semantics for its own
critical path. Only the target-paper warmup is best-effort.

## What Should Be Cached In This Round

This round should intentionally reuse only the cache surfaces that already exist
and already match the repository's durable-cache policy:

- `repo_cache`
  - stores `canonical arxiv_url -> github_url` or negative discovery state
- `repo_metadata_cache`
  - stores durable repo metadata needed later, currently `github_url -> created`
- `cache/overview/<arxiv_id>.md`
- `cache/abs/<arxiv_id>.md`

These four surfaces are already wired into the shared sync path and directly
support the user's goal of accumulating reusable paper/repo information.

## What Should Not Be Added To Cache In This Round

Do **not** expand cache scope in this change to store:

- `Stars`
  - intentionally treated as dynamic/current data, not durable cache
- `About`
  - also dynamic enough that the project refreshes it live rather than treating
    it as durable cached fact
- derived GitHub owner/repo pieces
  - redundant with `Github URL`
- AlphaXiv `versionId`
  - intermediate fetch detail with no established cross-run contract
- a new target-paper metadata table for title/date/abstract
  - possible in theory, but not justified by the current architecture or reuse
    needs

This keeps the design aligned with the current durable-cache boundary: cache
stable reusable facts, not fast-changing display metadata.

## Logging Behavior

The new warmup should keep user-visible logging lightweight and non-disruptive.

Recommended behavior:

- optional status line when target warmup starts
- optional status line when target warmup writes useful cache state
- optional low-priority status line when target warmup is skipped or fails

It should not:

- alter the existing per-reference/per-citation progress numbering
- pollute the final CSV summary with a synthetic extra paper row
- present warmup misses as hard errors for the overall command

This is a maintenance-oriented cache side effect, not a new primary product
output.

## File-Level Changes

### Modify

- `src/arxiv_relations/pipeline.py`
  - build the target-paper `PaperSeed`
  - add the best-effort target-paper warmup helper
  - schedule the warmup concurrently with the existing relation flow
- `tests/test_arxiv_relations.py`
  - cover target-paper warmup behavior
  - cover best-effort failure behavior
  - confirm references/citations CSV outputs remain unchanged

### No cache-schema changes

Do not modify:

- `src/shared/repo_cache.py`
- `src/shared/repo_metadata_cache.py`
- `src/shared/relation_resolution_cache.py`
- `cache.py`

This feature should work entirely by routing the target paper through the
existing shared sync path.

## Testing Requirements

Add focused relation-mode tests that verify:

1. when the input paper has a discoverable GitHub repo, the target warmup calls
   the shared path and warms content cache for the target paper itself in
   addition to any related papers
2. target warmup does not create any extra CSV row or extra output file
3. if target warmup hits a GitHub discovery miss or repo metadata failure, the
   command still exports references/citations successfully
4. target warmup runs with authoritative target-paper arXiv facts and does not
   redundantly send the target paper back through non-authoritative URL
   normalization
5. existing references/citations behavior remains unchanged

## Preserved Semantics

The implementation must preserve all of the following:

- the single-paper CLI still writes exactly two CSV files:
  references and citations
- the target paper itself is not added into either CSV
- existing relation normalization, deduplication, and export ordering stay
  unchanged
- target warmup does not change the current cache policy for `Stars` / `About`
- warmup failures do not fail the command

## Why No Larger Refactor Is Needed

The repository already has the correct shared processing boundary for one paper.
This feature does not reveal an architectural inability to add the behavior
cleanly. It only reveals that relation mode had not yet scheduled the target
paper itself through that already-existing boundary.

So the correct response is a small architectural completion, not a new framework
or a broad redesign.
