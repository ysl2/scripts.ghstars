# arXiv.org Generic Collection Support Design

**Goal**

Expand `arxiv.org -> csv` support from a few known collection URL shapes to a broader “generic collection page” model that accepts structurally valid arXiv paper collections, while preserving one hard rule:

- if the program cannot reliably prove it is exporting the full collection, it must fail instead of exporting a partial result

**Current State**

The existing arXiv adapter explicitly supports:

- `/list/...`
- `/search/...`

This already covers examples such as:

- `https://arxiv.org/list/cs.CV/new`
- `https://arxiv.org/list/cs.CV/recent?show=1000`
- `https://arxiv.org/search/?query=streaming+semantic+3d+reconstruction&searchtype=all&abstracts=show&order=-submitted_date&size=200`

What is still missing or underdefined:

- `/catchup/...`
- month archive pages such as `/list/cs.CV/2026-03` are accepted at the URL boundary, but their support is implicit rather than intentionally modeled and tested

**User Requirement**

The adapter should be as permissive as possible for arXiv collection pages, but it must not silently export an incomplete subset when pagination cannot be determined reliably.

That means:

1. Prefer structural compatibility over a narrow allowlist of URL names.
2. Reuse existing logic where the page structure matches an existing supported family.
3. If current-page parsing succeeds but full-collection traversal cannot be proven, fail loudly.

**Design Principles**

1. Separate collection-page structure from URL shape.
   - URL shape is only a routing hint
   - HTML structure determines which parser/pagination strategy applies

2. Keep “full export or fail” as a first-class invariant.
   - no best-effort partial export for ambiguous multi-page collections

3. Reuse existing `list-like` and `search-like` parsing logic where possible.
   - avoid per-URL-shape special cases unless the page structure genuinely differs

4. Be permissive at detection, strict at traversal.
   - accept more arXiv collection URLs
   - require enough evidence to guarantee full export

**Proposed Collection Families**

Treat arXiv collection pages as three routed families:

**1. List-like collections**

Examples:

- `/list/cs.CV/recent`
- `/list/cs.CV/new`
- `/list/cs.CV/recent?show=1000`
- `/list/cs.CV/2026-03`

Structural signals:

- article pairs rendered as `dt` + `dd`
- titles rendered inside `.list-title`
- summary metadata contains `Total of N entries`
- page-size controls may expose `show=...`

Traversal strategy:

- parse current page with the existing list parser
- infer total count from `Total of N entries`
- infer page size from:
  - explicit `show` query parameter, or
  - `Showing up to N entries per page`, or
  - current extracted row count as a last structural fallback
- generate subsequent pages using `skip/show`

**2. Catchup collections**

Examples:

- `/catchup/cs.CV/2026-03-26`

Structural signals observed today:

- article pairs are still rendered as `dt` + `dd`
- titles still use `.list-title`
- page summary uses wording like `Total of N entries for Thu, 26 Mar 2026`

Traversal strategy:

- reuse the same list-entry parser
- treat catchup pages as `list-like` only if:
  - total count can be parsed, and
  - the current page already contains the full collection, or
  - a reliable pagination mechanism can be identified

Safety rule:

- if a catchup page yields `current_page_count < total_count` but no reliable next-page construction rule is known, fail with an explicit error rather than exporting a partial CSV

This matches the user requirement not to export partial results.

**3. Search collections**

Examples:

- `/search/?query=streaming+semantic+3d+reconstruction&searchtype=all&abstracts=show&order=-submitted_date&size=200`

Structural signals:

- results rendered as `li.arxiv-result`
- title block `.title.is-5.mathjax`
- paper link under `.list-title ... /abs/...`
- result summary `Showing X-Y of N results`

Traversal strategy:

- keep the current search parser
- keep current `start`-based pagination

**URL Detection Boundary**

Update URL detection to intentionally support:

- `/list/...`
- `/catchup/...`
- `/search`

Still reject:

- single-paper pages like `/abs/...`
- unrelated arXiv pages that are not collection views

**Parsing and Pagination Architecture**

Keep one adapter module, but make the routing more explicit inside it:

- detect whether a page is `list-like` or `search-like`
- route to the correct parser + traversal strategy
- distinguish:
  - “unsupported collection type”
  - “recognized collection type but cannot prove full traversal”

This preserves the existing adapter boundary and avoids spreading arXiv-specific logic across the pipeline.

**Error Handling**

Use explicit failure modes:

- unsupported URL shape:
  - `Unsupported arXiv collection URL: <url>`

- URL shape accepted but page structure unrecognized:
  - `Unsupported arXiv collection page structure: <url>`

- page structure recognized, current page parsed, but full traversal cannot be guaranteed:
  - `Cannot guarantee complete export for arXiv collection: <url>`

- standard list/search pagination metadata missing:
  - keep the current hard failure behavior

**Output Naming**

Preserve current readable source-specific naming:

- `/list/cs.CV/new`
  - `arxiv-cs.CV-new-<timestamp>.csv`
- `/list/cs.CV/2026-03`
  - `arxiv-cs.CV-2026-03-<timestamp>.csv`
- `/catchup/cs.CV/2026-03-26`
  - `arxiv-cs.CV-2026-03-26-<timestamp>.csv`
- `/search/...`
  - keep existing `arxiv-search-...-<timestamp>.csv`

The current filename model already supports this with only minor extension for catchup-like paths.

**Testing**

Add focused coverage for:

- URL detection now accepting `/catchup/...`
- month archive `/list/.../YYYY-MM`
- catchup page current-page extraction
- catchup full-export success when current page count equals total
- catchup explicit failure when total exceeds current parsed rows and no reliable pagination path exists
- existing `/list/...` and `/search/...` behavior remaining unchanged

**Why this design**

- It keeps the permissive input boundary the user wants.
- It preserves the safety guarantee the user explicitly requested.
- It reuses the existing list/search parsing instead of duplicating logic per URL shape.
- It improves extensibility by modeling arXiv collections by structure, not only by a narrow handpicked URL list.
