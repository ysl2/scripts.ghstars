# scripts.ghstars

One CLI, four modes:

- No positional argument: sync GitHub links and star counts into Notion
- One existing `.csv` file path: update that CSV in place
- One supported papers collection URL: fetch the full result set and write a CSV under `./output` in the current working directory
- One supported single-paper arXiv URL: export related references and citations into two CSV files under `./output` in the current working directory

Repository discovery for arXiv-backed papers now uses:

- shared `./cache.db` in the current working directory first
- Hugging Face exact API `GET /api/papers/{arxiv_id}` on cache miss
- AlphaXiv paper API on Hugging Face exact misses
- no additional search fallback for GitHub repo discovery

GitHub and star lookup use normalized, versionless arXiv URLs as the paper identity.

## Install

Requires Python 3.12+.

```bash
uv sync
```

## Environment

Copy `.env.example` to `.env` and fill in the variables you need.

### Optional in all modes

```bash
GITHUB_TOKEN=
HUGGINGFACE_TOKEN=
ALPHAXIV_TOKEN=
REPO_DISCOVERY_NO_REPO_RECHECK_DAYS=7
```

`HUGGINGFACE_TOKEN` enables both Hugging Face exact repo discovery and the optional single-paper relation-mode title-search fallback.
`ALPHAXIV_TOKEN` is optional. When set, AlphaXiv paper and overview requests send `Authorization: Bearer <token>`; when empty, they keep using the current anonymous public API behavior.

`cache.db` is created automatically in the current working directory and shared across URL, CSV, and Notion runs.
`HF_EXACT_NO_REPO_RECHECK_DAYS` is still accepted as a backward-compatible alias, but `REPO_DISCOVERY_NO_REPO_RECHECK_DAYS` is the preferred name.

### Optional only for single-paper arXiv relation mode

```bash
OPENALEX_API_KEY=
ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS=30
```

`OPENALEX_API_KEY` is optional but recommended for single-paper relation export. Even with a key, OpenAlex can still return budget-exhausted `429` responses; the client honors `Retry-After` / `retryAfter` signals and avoids long pointless retry loops when the server asks for a much later retry.

`ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS` controls how long single-paper relation mode keeps a cached "no arXiv match found" result before retrying arXiv title search.

### Optional override only for Semantic Scholar URL mode

Semantic Scholar search pages are rendered with a local Chrome binary in headless mode.

Normally you do not need to configure anything on macOS if Chrome is installed at:

- `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`

`GOOGLE_CHROME_BIN` is only an override. Set it only if you want to force a specific Chrome binary, or if Chrome is installed outside the default macOS location.

When set, `GOOGLE_CHROME_BIN` may be either:

- an absolute path to the Chrome binary
- a command name resolvable via `PATH`

### Required only for Notion mode

```bash
NOTION_TOKEN=
DATABASE_ID=
```

## Usage

### Shared enrichment behavior

CSV update, collection URL export, and single-paper relation export reuse the same downstream enrichment path once a row has a canonical arXiv URL.

- GitHub discovery checks `cache.db` first, then does one Hugging Face exact lookup on cache miss when discovery is allowed
- when Hugging Face exact returns no repo, discovery does one AlphaXiv paper lookup before giving up
- when a row ends with both a canonical arXiv URL and a valid GitHub repo URL, local `overview` / `abs` markdown is ensured under `./cache/overview/<arxiv_id>.md` and `./cache/abs/<arxiv_id>.md`
- existing local content files are reused; only missing files are fetched
- overview uses AlphaXiv's overview API; abs uses AlphaXiv's paper API
- `ALPHAXIV_TOKEN` is optional for both repo discovery and content fetches; without it, the same anonymous AlphaXiv requests are used

### Cache maintenance

Use the standalone cache maintenance script when you want to inspect or clear GitHub repo discovery negative cache entries from `cache.db`.

A negative cache entry means:

- `github_url` is `NULL` or blank
- `last_repo_discovery_checked_at` is non-null

The script is dry-run by default, so it prints how many negative entries would be deleted without changing the database.

It clears both negative cache families together:

- GitHub repo discovery negatives in `repo_cache`
- URL-normalization negatives in `relation_resolution_cache`

```bash
uv run python cache.py
```

Delete all negative cache entries while keeping positive cache rows:

```bash
uv run python cache.py --apply
```

Optional: point at a specific SQLite file instead of the current working directory's `./cache.db`.

```bash
uv run python cache.py --db /path/to/cache.db --apply
```

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

- if `Url` is already an arXiv URL, it is preserved exactly as-is
- if `Url` is non-arXiv, the shared resolver first tries explicit DOI/OpenAlex metadata crosswalks and then title-based fallback to normalize it to arXiv when possible
- requires `Url`; `Name` is optional
- if `Github` is already present and valid, its exact value is preserved and only `Stars` is refreshed
- if `Github` is blank, discovery checks `cache.db` first, then does one Hugging Face exact lookup on cache miss
- if Hugging Face exact returns no repo, discovery does one AlphaXiv paper lookup before leaving `Github` blank
- missing `Github` or `Stars` columns are added automatically at the end of the CSV
- existing custom columns are left untouched, including any preexisting `Overview` / `Abs` columns
- current CSV mode does not write content-cache paths back into the CSV
- writes use a temp file and atomic replace

### Collection URL to CSV mode

Reads a supported collection URL and writes a CSV under `./output` in the current working directory by default.

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

- single paper pages are handled by single-paper arXiv relation mode, not collection URL mode
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
- CLI URL exports default to `./output` in the current working directory and create that directory automatically if needed
- URL exports always write the standard columns: `Name`, `Url`, `Github`, `Stars`
- standard arXiv `list/...` and `search/...` collection pages, including `/search/advanced`, are crawled across all pages, not just the first page
- archive-style arXiv `list/<category>/YYYY-MM` pages reuse the same multi-page `list/...` crawling path
- arXiv `new` pages include all visible sections, including new submissions, cross-lists, and replacements
- arXiv `catchup/<category>/YYYY-MM-DD` is supported only for that exact path shape
- arXiv catchup exports fail explicitly when the page reports more entries than are present on the page, rather than guessing pagination and writing a partial CSV
- arXiv Xplorer uses the site’s paging API instead of trying to click `Show More` in a browser
- Hugging Face Papers parses the collection page’s embedded papers payload from the frontend response
- Semantic Scholar crawls the search result pages, then keeps only rows that can be normalized to canonical arXiv URLs
- Semantic Scholar crawling requires a working local Chrome/Chromium binary as described above
- all URL modes normalize rows to canonical, versionless arXiv URLs before downstream enrichment
- rows that cannot be mapped to arXiv are dropped from the final CSV
- repo discovery reuses the shared `cache.db` mapping of canonical arXiv URL to GitHub repo
- downstream repository discovery, star lookup, sorting, progress printing, and CSV writing reuse the same shared export logic as CSV update mode where applicable

### Single-paper arXiv relation export mode

Reads one arXiv paper URL, resolves related works through OpenAlex, normalizes them to canonical arXiv rows when possible, retains unresolved non-arXiv rows otherwise, and writes two CSV files under `./output` in the current working directory by default.

Command shape:

```bash
uv run main.py '<single-paper-arxiv-url>'
```

Accepted URL shapes:

- `https://arxiv.org/abs/<arxiv-id>`
- `https://www.arxiv.org/abs/<arxiv-id>`
- `https://arxiv.org/pdf/<arxiv-id>.pdf`
- `https://www.arxiv.org/pdf/<arxiv-id>.pdf`

Input normalization notes:

- version suffixes such as `v4` are accepted and normalized away in downstream arXiv URLs and output filenames
- trailing slash, query string, or fragment on a supported single-paper arXiv URL is ignored for normalization
- collection pages such as `list/...`, `search/...`, and `catchup/...` are rejected by this mode

Examples:

```bash
uv run main.py 'https://arxiv.org/abs/2603.23502'
```

```bash
uv run main.py 'https://arxiv.org/pdf/2603.23502v4.pdf?download=1'
```

Output example:

- `./output/arxiv-2603.23502-references-20260326113045.csv`
- `./output/arxiv-2603.23502-citations-20260326113045.csv`

Single-paper mode behavior:

- resolves the input paper title from arXiv metadata first, then searches OpenAlex by title
- accepts the first OpenAlex work returned by relevance order
- keeps direct arXiv-backed related works as canonical, versionless arXiv `abs` rows
- otherwise first tries an OpenAlex exact-title preprint/sibling crosswalk on the related work itself
- on crosswalk miss, tries arXiv API title search and takes the first most relevant hit
- after an arXiv API title-search miss, may try Hugging Face Papers title search when `HUGGINGFACE_TOKEN` is configured
- mapped rows use the matched arXiv title and canonical arXiv `abs` URL
- when `HUGGINGFACE_TOKEN` is absent, the Hugging Face fallback is skipped silently and unresolved rows keep the current retained-row behavior
- if still unresolved, keeps the non-arXiv row with `Url` priority `DOI > landing page > OpenAlex URL`
- relation normalization reuses `./cache.db` to cache non-direct relation resolution by OpenAlex work URL and DOI
- cached positive matches store canonical arXiv `abs` URLs; cached negative matches are written only after the active relation-resolution ladder for the current environment confirms no accepted arXiv match, then retried after `ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS`
- referenced and citing works are deduplicated by final normalized URL before export
- both CSVs use the standard columns: `Name`, `Url`, `Github`, `Stars`
- shared GitHub discovery, local overview / abs cache warming, and star enrichment are reused, so resolved and unresolved rows remain in the CSV even when no repo is found; in that case `Github` and `Stars` are left blank
- a missing OpenAlex detail record for one referenced work currently causes that one related work to be skipped while the rest of the export continues
- a budget-exhausted OpenAlex `429` can still fail the run even with `OPENALEX_API_KEY`; the client respects upstream retry signals instead of looping on very long waits
- the CLI reports success only after both CSV files are written; other arXiv or OpenAlex hard failures still return a nonzero exit code

## Notion expectations

Your Notion database should have:

- `Name` or `Title` as title property
- `Github` as URL or rich text
- `Stars` as number

If `Github` or `Stars` is missing from the data source schema, Notion mode creates them automatically before querying pages.
Newly created `Github` is a URL property; newly created `Stars` is a number property.

Optional arXiv source fields for fallback discovery:

- `URL`
- `Arxiv`
- `arXiv`
- `Paper URL`
- `Link`

When `Github` is empty, the sync flow:

1. resolves the paper to a canonical arXiv URL from URL fields first, then title search fallback
2. uses that canonical arXiv URL as the paper identity for repo discovery and stars lookup
3. checks `cache.db` for that canonical arXiv URL
4. if needed, calls Hugging Face exact paper API for that arXiv id
5. if Hugging Face exact misses, calls AlphaXiv paper API for the same arXiv id
6. stores confirmed repos in `cache.db`

When the full arXiv repo-discovery chain completes successfully but finds no repo, the cache stores the successful check timestamp.
`REPO_DISCOVERY_NO_REPO_RECHECK_DAYS` controls how many days to wait before re-checking that no-repo cache entry again. `HF_EXACT_NO_REPO_RECHECK_DAYS` remains accepted as a legacy alias.

Notion page updates still target the original page id; only the paper-identity portion of discovery uses canonical arXiv URLs.

## Notes

- Invalid file path does not fall back to Notion mode
- Unsupported URLs fail instead of falling back to another mode
- More than one positional argument is treated as a usage error
- Concurrency and rate limiting remain enabled in all modes
- `cache/`, `cache.db`, `*.html`, and `*.csv` are gitignored globally; use `git add -f` only if you intentionally want to track one

## Tests

```bash
uv run pytest
```
