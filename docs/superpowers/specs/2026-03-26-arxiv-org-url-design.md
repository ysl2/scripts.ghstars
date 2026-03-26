# arXiv.org URL Mode Design

**Goal**

Add a new `arxiv.org -> csv` URL input mode that exports arXiv collection pages to CSV while reusing the existing normalization, Github discovery, Stars enrichment, progress printing, and CSV-writing pipeline.

**User Input Contract**

The input will always be an `arxiv.org` collection page, not a single-paper page.

Examples include:

- `https://arxiv.org/list/cs.CV/recent`
- `https://arxiv.org/list/cs.CV/new`
- `https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=50&order=-submitted_date`

The exact URL shape is not guaranteed beyond:

- host is `arxiv.org`
- the page represents a paper collection
- if the collection spans multiple pages, the export should include the full result set

**Design Principles**

1. Keep source-specific crawling isolated.
   - `arxiv.org` gets its own adapter under `url_to_csv/`
   - downstream enrichment logic stays shared

2. Be permissive at the input boundary and strict in extraction.
   - accept arXiv collection URLs broadly
   - only keep rows that can be extracted into stable `(title, canonical abs url)` pairs

3. Make complete pagination a first-class requirement for standard arXiv collection pages.
   - `list/...`
   - `search/...`

4. Avoid browser rendering unless strictly necessary.
   - arXiv collection pages are static HTML
   - use direct HTTP fetch + HTML parsing

**Architecture**

- `src/url_to_csv/arxiv_org.py`
  - new arXiv adapter
  - validates supported collection URLs
  - derives output CSV file names
  - fetches collection pages
  - extracts `PaperSeed`
  - follows pagination for full result export

- `src/url_to_csv/sources.py`
  - add `ARXIV_ORG` source enum value
  - route `arxiv.org` collection URLs to the new adapter

- `src/url_to_csv/pipeline.py`
  - dispatch to the new adapter when the detected source is `ARXIV_ORG`

- shared export path remains unchanged
  - normalize to canonical arXiv URLs
  - discover Github repos
  - fetch star counts
  - sort and write CSV

**Page Types**

The adapter should explicitly support two primary arXiv collection families.

**1. List pages**

Examples:

- `/list/cs.CV/recent`
- `/list/cs.CV/new`
- `/list/cs.CV/recent?skip=50&show=50`

Parsing rules:

- read entries from the article list under `dl#articles`
- pair each `dt` with its matching `dd`
- extract the paper URL from the `/abs/<id>` link in `dt`
- extract the title from the `.list-title` block in `dd`
- normalize URLs to canonical versionless `https://arxiv.org/abs/<id>`

Inclusion rules:

- `recent` exports all entries across all pages
- `new` exports all visible sections, including:
  - new submissions
  - cross-lists
  - replacements

The adapter should not filter by section heading for `new`; it should collect every valid paper entry shown on the page.

**2. Search pages**

Examples:

- `/search/?searchtype=all&query=reconstruction&abstracts=show&size=50&order=-submitted_date`
- `/search/?query=3d+reconstruction&searchtype=title`

Parsing rules:

- read entries from `li.arxiv-result`
- extract the paper URL from `.list-title a[href*="/abs/"]`
- extract the title from `.title.is-5.mathjax`
- normalize URLs to canonical versionless `https://arxiv.org/abs/<id>`

**Pagination Strategy**

**List pages**

- infer total entries from the `Total of N entries` text
- infer page size from the current `show` parameter or the page state
- fetch all pages by walking `skip` in page-size increments
- preserve the original base path and query semantics while overriding `skip` as needed

**Search pages**

- infer total entries from the `Showing X-Y of N results` text
- infer page size from the current `size` parameter or the page state
- fetch all pages by walking `start` in page-size increments
- preserve the original query and sort parameters while overriding `start`

**Fallback behavior**

For nonstandard arXiv collection URLs:

- if the page still exposes multiple stable arXiv abs links with matching titles, extract the current page as a best-effort collection
- do not promise automatic cross-page crawling for nonstandard layouts unless pagination can be identified reliably
- if the page behaves like a single-paper page or cannot be parsed as a collection, fail with an unsupported-URL error

This keeps the input boundary permissive without making pagination logic brittle.

**Output CSV Naming**

Use a distinct `arxiv-...` prefix so outputs do not collide with existing `arxivxplorer-...` exports.

Examples:

- `https://arxiv.org/list/cs.CV/recent`
  - `arxiv-cs.CV-recent.csv`
- `https://arxiv.org/list/cs.CV/new`
  - `arxiv-cs.CV-new.csv`
- `https://arxiv.org/search/?searchtype=all&query=reconstruction&order=-submitted_date`
  - `arxiv-search-reconstruction-all-submitted-date.csv`

Rules:

- prefer readable slugs over encoding every raw query parameter
- for `list` pages, derive the file name from the category and list kind
- for `search` pages, derive the file name from `query`, `searchtype`, and a compact sort token
- for permissive fallback collection pages, use a generic but stable `arxiv-collection-...` form

**Error Handling**

- arXiv host but not a recognizable collection page:
  - raise `Unsupported arXiv collection URL: <url>`
- valid collection page but zero extractable papers:
  - allow an empty export result
- malformed row inside an otherwise valid page:
  - skip the row
- network timeout or retryable upstream failure:
  - reuse existing retry and backoff behavior
- standard `list` or `search` pagination cannot be parsed:
  - fail rather than silently exporting a partial result set

**Testing**

Add focused adapter tests in `tests/test_arxiv_org.py` covering:

- supported and unsupported arXiv URL detection
- output file naming for `list` and `search` URLs
- paper extraction from representative `list` HTML
- paper extraction from representative `search` HTML
- `new` page behavior that includes new submissions, cross-lists, and replacements
- full pagination for `recent`
- full pagination for `search`
- deduplication when the same canonical abs URL appears more than once

Extend integration-level coverage in:

- `tests/test_url_sources.py`
  - source detection includes `arxiv.org`
- `tests/test_dispatch.py`
  - CLI dispatch accepts supported arXiv collection URLs
- `tests/test_url_to_csv.py`
  - URL mode export works end-to-end with the new adapter

**Why this design**

- Matches the repository’s existing “one adapter per source” structure.
- Reuses the mature shared enrichment pipeline instead of duplicating logic.
- Delivers full-result exports for the two arXiv collection page families users are most likely to copy.
- Keeps the input boundary broad enough for real-world arXiv URLs without overcommitting to fragile heuristics.
