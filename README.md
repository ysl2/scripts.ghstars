# scripts.ghstars

One CLI, three modes:

- No positional argument: sync GitHub links and star counts into Notion
- One existing `.csv` file path: update that CSV in place
- One supported papers collection URL: fetch the full result set and write a CSV under `./output`

Repository discovery for arXiv-backed papers now uses:

- project-level cache in `./cache.db` first
- Hugging Face exact API `GET /api/papers/{arxiv_id}` on cache miss
- no Hugging Face search fallback for GitHub repo discovery

GitHub and star lookup use normalized, versionless arXiv URLs as the paper identity.

## Install

```bash
uv sync
```

## Environment

Copy `.env.example` to `.env` and fill in the variables you need.

### Used by CSV and URL modes

```bash
GITHUB_TOKEN=your_github_token_here
HUGGINGFACE_TOKEN=your_huggingface_token_here
ALPHAXIV_TOKEN=your_alphaxiv_token_here
HF_EXACT_NO_REPO_THRESHOLD=10
```

`cache.db` is created automatically at the project root and shared across URL, CSV, and Notion runs.

### Required only for Notion mode

```bash
NOTION_TOKEN=your_notion_token_here
DATABASE_ID=your_database_id_here
```

## Usage

### Notion mode

Runs the original Notion sync flow.

```bash
uv run main.py
```

### CSV update mode

Reads one CSV file, keeps unrelated columns untouched, and updates `Github` / `Stars` in place.

```bash
uv run main.py /path/to/papers.csv
```

CSV mode behavior:

- uses canonical arXiv `Url` as the paper identity
- if `Github` is already present and valid, only `Stars` is refreshed
- if `Github` is blank, discovery checks `cache.db` first, then does one Hugging Face exact lookup on cache miss
- missing `Github` or `Stars` columns are appended automatically
- writes use a temp file and atomic replace

### Collection URL to CSV mode

Reads a supported collection URL and writes a CSV under `./output` by default.

Command shape:

```bash
uv run main.py '<collection-url>'
```

Representative supported URL shapes:

- arXiv Xplorer:
  `https://arxivxplorer.com/?q=...`
  optional repeated `cats=...` and `year=...`
- arXiv.org list pages:
  `https://arxiv.org/list/<category>/recent`
  `https://arxiv.org/list/<category>/new`
  `https://arxiv.org/list/<category>/YYYY-MM`
  optional paging parameter such as `?show=1000`
- arXiv.org catchup pages:
  `https://arxiv.org/catchup/<category>/YYYY-MM-DD`
- arXiv.org search pages:
  `https://arxiv.org/search/?...`
  `https://arxiv.org/search/advanced?...`
- Hugging Face Papers collections:
  `https://huggingface.co/papers/trending`
  `https://huggingface.co/papers/trending?q=...`
  `https://huggingface.co/papers/month/YYYY-MM`
  `https://huggingface.co/papers/month/YYYY-MM?q=...`
- Semantic Scholar search pages:
  `https://www.semanticscholar.org/search?q=...`
  optional indexed filters such as `year[0]=...`, `fos[0]=...`, `venue[0]=...`, plus `sort=...`

Source-specific notes:

- arXiv Xplorer requires a non-empty `q` parameter
- Semantic Scholar requires a non-empty `q` parameter
- the CLI examples below are representative, not an exhaustive list of every supported query-parameter combination

Common arXiv.org examples:

- latest category page:
  `https://arxiv.org/list/cs.CV/recent`
- latest category page with explicit page size:
  `https://arxiv.org/list/cs.CV/recent?show=1000`
- new submissions page:
  `https://arxiv.org/list/cs.CV/new`
- monthly archive page:
  `https://arxiv.org/list/cs.CV/2026-03`
- daily catchup page:
  `https://arxiv.org/catchup/cs.CV/2026-03-26`
- search results page:
  `https://arxiv.org/search/?query=streaming+semantic+3d+reconstruction&searchtype=all&abstracts=show&order=-submitted_date&size=200`
- advanced search results page:
  `https://arxiv.org/search/advanced?advanced=&terms-0-operator=AND&terms-0-term=reconstruction&terms-0-field=all&terms-1-operator=AND&terms-1-term=semantic&terms-1-field=all&terms-2-operator=AND&terms-2-term=streaming&terms-2-field=all&classification-computer_science=y&classification-include_cross_list=include&date-filter_by=past_12&date-date_type=submitted_date&abstracts=hide&size=200&order=-submitted_date`

Not supported:

- single paper pages such as `https://arxiv.org/abs/2603.23502`
- malformed catchup paths such as `https://arxiv.org/catchup/cs.CV/2026/03/26`
- Hugging Face single paper pages such as `https://huggingface.co/papers/2501.12345`
- non-search Semantic Scholar pages such as `https://www.semanticscholar.org/paper/Foo/123`
- arXiv Xplorer URLs without a non-empty `q`

```bash
uv run main.py 'https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026&year=2025&year=2024'
```

```bash
uv run main.py 'https://huggingface.co/papers/trending'
```

```bash
uv run main.py 'https://huggingface.co/papers/trending?q=semantic'
```

```bash
uv run main.py 'https://arxiv.org/list/cs.CV/recent'
```

```bash
uv run main.py 'https://arxiv.org/list/cs.CV/new'
```

```bash
uv run main.py 'https://arxiv.org/list/cs.CV/2026-03'
```

```bash
uv run main.py 'https://arxiv.org/catchup/cs.CV/2026-03-26'
```

```bash
uv run main.py 'https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=50&order=-submitted_date'
```

```bash
uv run main.py 'https://arxiv.org/search/advanced?advanced=&terms-0-operator=AND&terms-0-term=reconstruction&terms-0-field=all&terms-1-operator=AND&terms-1-term=semantic&terms-1-field=all&terms-2-operator=AND&terms-2-term=streaming&terms-2-field=all&classification-computer_science=y&classification-include_cross_list=include&date-filter_by=past_12&date-date_type=submitted_date&abstracts=hide&size=200&order=-submitted_date'
```

```bash
uv run main.py 'https://huggingface.co/papers/month/2026-03?q=semantic'
```

```bash
uv run main.py 'https://www.semanticscholar.org/search?year%5B0%5D=2025&year%5B1%5D=2026&fos%5B0%5D=computer-science&venue%5B0%5D=Computer%20Vision%20and%20Pattern%20Recognition&q=semantic%203d%20reconstruction&sort=pub-date'
```

Output example:

- `./output/arxivxplorer-streaming-semantic-3d-reconstruction-cs.CV-2026-2025-2024-20260326113045.csv`
- `./output/arxiv-cs.CV-recent-20260326113045.csv`
- `./output/arxiv-cs.CV-new-20260326113045.csv`
- `./output/arxiv-cs.CV-2026-03-20260326113045.csv`
- `./output/arxiv-cs.CV-catchup-2026-03-26-20260326113045.csv`
- `./output/arxiv-search-reconstruction-all-submitted-date-20260326113045.csv`
- `./output/arxiv-search-reconstruction-semantic-streaming-all-submitted-date-20260326113045.csv`
- `./output/huggingface-papers-trending-semantic-20260326113045.csv`
- `./output/huggingface-papers-month-2026-03-semantic-20260326113045.csv`
- `./output/semanticscholar-semantic-3d-reconstruction-2025-2026-computer-science-Computer-Vision-and-Pattern-Recognition-20260326113045.csv`

URL mode behavior:

- source-specific fetching is kept in separate adapters under `url_to_csv/`
- every URL export appends a run timestamp in `YYYYMMDDHHMMSS` form before `.csv`
- CLI URL exports default to `./output` and create that directory automatically if needed
- standard arXiv `list/...` and `search/...` collection pages, including `/search/advanced`, are crawled across all pages, not just the first page
- archive-style arXiv `list/<category>/YYYY-MM` pages reuse the same multi-page `list/...` crawling path
- arXiv `new` pages include all visible sections, including new submissions, cross-lists, and replacements
- arXiv `catchup/<category>/YYYY-MM-DD` is supported only for that exact path shape
- arXiv catchup exports fail explicitly when the page reports more entries than are present on the page, rather than guessing pagination and writing a partial CSV
- arXiv Xplorer uses the site’s paging API instead of trying to click `Show More` in a browser
- Hugging Face Papers parses the collection page’s embedded papers payload from the frontend response
- Semantic Scholar crawls the search result pages, then keeps only rows that can be normalized to canonical arXiv URLs
- all URL modes normalize rows to canonical, versionless arXiv URLs before downstream enrichment
- rows that cannot be mapped to arXiv are dropped from the final CSV
- repo discovery reuses the shared `cache.db` mapping of canonical arXiv URL to GitHub repo
- downstream repository discovery, star lookup, sorting, progress printing, and CSV writing reuse the same shared export logic as CSV update mode where applicable

## Notion expectations

Your Notion database should have:

- `Name` or `Title` as title property
- `Github` as URL or rich text
- `Stars` as number

Optional arXiv source fields for fallback discovery:

- `URL`
- `Arxiv`
- `arXiv`
- `Paper URL`
- `Link`

When `Github` is empty, the sync flow:

1. checks `cache.db` for the canonical arXiv URL
2. if needed, calls Hugging Face exact paper API for that arXiv id
3. stores confirmed repos in `cache.db`
4. stores successful exact no-repo outcomes as a counter in `cache.db`

The no-repo counter is only incremented when Hugging Face exact responds successfully but without a `githubRepo`. Transient request failures do not increment it.
`HF_EXACT_NO_REPO_THRESHOLD` controls how many successful exact no-repo results are required before the cache will stop re-checking that arXiv URL.

## Notes

- Invalid file path does not fall back to Notion mode
- Unsupported URLs fail instead of falling back to another mode
- More than one positional argument is treated as a usage error
- Concurrency and rate limiting remain enabled in all modes
- `cache.db`, `*.html`, and `*.csv` are gitignored globally; use `git add -f` only if you intentionally want to track one

## Tests

```bash
uv run pytest
```
