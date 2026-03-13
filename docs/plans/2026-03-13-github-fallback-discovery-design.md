# GitHub Fallback Discovery Design

**Goal:** Extend the current Notion → GitHub stars updater so pages whose `Github` property is empty or `WIP` can automatically discover a repository URL from the paper abstract first, then from AlphaXiv resources, and update both `Github` and `Github stars` when discovery succeeds.

**Architecture:** Refactor the per-page processing flow into a single repo-resolution pipeline. Existing valid GitHub URLs continue through the same star update path; only empty/`WIP` values enter fallback discovery. The pipeline resolves a final repository candidate first, then runs the existing GitHub star lookup and a unified Notion property update.

**Scope / Rules:**
- Only touch pages whose `Github` field is empty or equals `WIP` (case-insensitive after trim), plus the existing valid-GitHub case for star refresh.
- Any other non-empty value stays untouched, including non-GitHub URLs.
- Fallback order is: abstract text → AlphaXiv resources page.
- If a valid repository is discovered, update `Github` and `Github stars` together in one Notion update.
- If discovery fails, skip with a clear reason.

## Proposed Flow

1. Read the current `Github` value and classify it as one of:
   - `valid_github`
   - `empty`
   - `wip`
   - `other`
2. Resolve a final GitHub URL candidate:
   - `valid_github`: use existing value directly.
   - `empty` / `wip`: discover from page content.
   - `other`: skip unchanged.
3. Extract owner/repo from the final URL candidate.
4. Query GitHub API for stars.
5. Update Notion:
   - existing GitHub URL: update `Github stars` only
   - discovered GitHub URL: update `Github` + `Github stars`

## Data Sources Needed For Discovery

### Abstract extraction
Read from the Notion page properties if available. Support likely property names in a tolerant way, preferring common names such as:
- `Abstract`
- `Summary`
- `TL;DR`
- `Notes`

The helper should safely read `rich_text` / `title` / `url` / `formula(string)` values where available, but discovery should only use textual values.

### AlphaXiv lookup
Use the paper's arXiv identifier when available.
Potential sources, in order:
- arXiv URL from a page property like `Link`, `URL`, `Paper URL`, `Arxiv`, `arXiv`
- arXiv ID embedded in the page title as a last resort is not required for v1

Build lookup URL as:
- `https://www.alphaxiv.org/resources/<arxiv_id>`

Then search the returned HTML/text for a GitHub repository URL.

## Implementation Approach Options

### Option A — Minimal branching inside `process_page`
Keep most logic inline and add fallback branches for empty/`WIP`.
- Pros: smallest diff
- Cons: duplicate logic for URL resolution, GitHub parsing, and Notion updates; harder to maintain

### Option B — Unified repo-resolution pipeline (recommended)
Introduce helper functions for field classification, text extraction, GitHub discovery, and Notion property updates; keep clients as-is.
- Pros: merges old and new flows into one main path, minimizes duplication, keeps changes local to `main.py`
- Cons: moderate refactor of `process_page`

### Option C — Split into multiple modules/services
Move GitHub discovery, Notion parsing, and page processing into separate files.
- Pros: cleanest long-term structure
- Cons: unnecessary for the current small repo; higher change surface

**Recommendation:** Option B.

## Error Handling / Reporting

Add explicit skip/failure reasons such as:
- `Unsupported Github field content`
- `No Github URL found in abstract`
- `No arXiv ID found for AlphaXiv lookup`
- `No Github URL found in AlphaXiv`
- `Discovered URL is not a valid GitHub repository`

When discovery succeeds, output should make it obvious whether the repo came from:
- existing GitHub field
- abstract fallback
- AlphaXiv fallback

## Testing Focus

Add unit tests for pure helpers only (fast, no network):
- classify GitHub field state
- find GitHub URL in text
- extract arXiv ID from URL
- resolve property text from a mocked Notion page property structure

Keep networked discovery logic lightweight and integration-free for now.
