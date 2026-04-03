# scripts.ghstars

One CLI, five input shapes:

- No positional argument: sync GitHub links and repo metadata into Notion
- One existing `.csv` file path: update that CSV in place
- One supported papers collection URL: fetch the full result set and write a CSV under `./output` in the current working directory
- One supported GitHub repository-search URL: fetch the full repository result set and write a CSV under `./output` in the current working directory
- One supported single-paper arXiv URL: export related references and citations into two CSV files under `./output` in the current working directory

Internally, `uv run main.py [input]` now routes by input shape into a shared record-centric core:

- `src/app.py` detects the input shape
- input adapters turn source data into `Record` objects
- shared sync services acquire `Github`, `Stars`, `Created`, and `About`
- output adapters write those properties back to CSV or Notion
- runtime exposes repository wrappers over durable cache-backed facts such as repo `Created`

Fresh CSV exports from collection URL mode, GitHub repository-search mode, and single-paper arXiv relation mode all use the same fixed column order:

- `Name`, `Url`, `Github`, `Stars`, `Created`, `About`

Mode-specific export notes:

- GitHub-search exports leave `Name` and `Url` empty by design
- paper-family fresh exports populate `Created` and `About` through the shared GitHub repo-metadata path when a GitHub repository is available

Repository discovery for arXiv-backed papers now uses:

- shared `./cache.db` in the current working directory first
- Hugging Face exact API `GET /api/papers/{arxiv_id}` on cache miss
- AlphaXiv paper page HTML on Hugging Face exact misses
- no additional search fallback for GitHub repo discovery

GitHub discovery and repo-metadata lookup use normalized, versionless arXiv URLs as the paper identity.

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

`HUGGINGFACE_TOKEN` enables both Hugging Face exact repo discovery and the shared final-stage Hugging Face paper title-search fallback inside arXiv URL resolution.
`ALPHAXIV_TOKEN` is optional. When set, AlphaXiv page fetches and API requests send `Authorization: Bearer <token>`; when empty, they keep using the current anonymous public behavior.

`cache.db` is created automatically in the current working directory and shared across all five input shapes.
`HF_EXACT_NO_REPO_RECHECK_DAYS` is still accepted as a backward-compatible alias, but `REPO_DISCOVERY_NO_REPO_RECHECK_DAYS` is the preferred name.

### Optional in modes that may normalize non-arXiv paper URLs

```bash
SEMANTIC_SCHOLAR_API_KEY=
AIFORSCHOLAR_TOKEN=
ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS=30
```

`SEMANTIC_SCHOLAR_API_KEY` is optional for the shared Semantic Scholar Graph API resolver used by CSV update, collection URL export, Notion sync, and single-paper relation export.

`AIFORSCHOLAR_TOKEN` is optional for the `https://ai4scholar.net/graph/v1` relay, which mirrors the official Graph API paths behind a Bearer token. Transport priority is: `SEMANTIC_SCHOLAR_API_KEY` -> `AIFORSCHOLAR_TOKEN` -> anonymous official API.

`ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS` controls how long the shared arXiv-resolution negative cache stays fresh before the resolver retries that unresolved identifier.

Semantic Scholar collection URLs now use Graph API bulk search directly. No browser binary is required.

### Required only for Notion mode

```bash
NOTION_TOKEN=
DATABASE_ID=
```

## Usage

### Shared enrichment behavior

CSV update, collection URL export, Notion sync, and single-paper relation export reuse the same downstream enrichment path once a row has a canonical arXiv URL.

- GitHub discovery checks `cache.db` first, then does one Hugging Face exact lookup on cache miss when discovery is allowed
- when Hugging Face exact returns no repo, discovery does one AlphaXiv paper-page HTML lookup before giving up
- once a valid GitHub repository URL is available, shared repo-metadata resolution fills `Stars` and, when available, `Created` / `About`
- when a row ends with both a canonical arXiv URL and a valid GitHub repo URL, local `overview` / `abs` markdown is ensured under `./cache/overview/<arxiv_id>.md` and `./cache/abs/<arxiv_id>.md`
- existing local content files are reused; only missing files are fetched
- overview uses AlphaXiv's overview API; abs uses AlphaXiv's paper API
- `ALPHAXIV_TOKEN` is optional for both repo discovery and content fetches; without it, the same anonymous AlphaXiv requests are used

### Cache maintenance

Use the standalone cache maintenance script when you want to inspect or clear negative cache entries from `cache.db`.

A negative cache entry means:

- `github_url` is `NULL` or blank
- `last_repo_discovery_checked_at` is non-null

The script is dry-run by default, so it prints how many negative entries would be deleted without changing the database.

It clears both negative cache families together:

- GitHub repo discovery negatives in `repo_cache`
- URL-normalization negatives in `relation_resolution_cache`

```bash
uv run cache.py
```

Delete all negative cache entries while keeping positive cache rows:

```bash
uv run cache.py --apply
```

Optional: point at a specific SQLite file instead of the current working directory's `./cache.db`.

```bash
uv run cache.py --db /path/to/cache.db --apply
```

### Notion mode

Runs the original Notion sync flow.

```bash
uv run main.py
```

### CSV update mode

Reads one CSV file, keeps unrelated columns untouched, and applies the shared property write policy in place.

```bash
uv run main.py /path/to/papers.csv
```

CSV mode behavior:

- if `Url` is already an arXiv URL, it is preserved exactly as-is
- if `Url` is non-arXiv, the shared resolver uses `cache -> Semantic Scholar exact -> Semantic Scholar title exact -> arXiv title search (HTML -> API) -> Crossref -> DataCite -> Hugging Face` to normalize it to arXiv when possible
- requires at least one of `Github` or `Url`; `Name` is optional
- if `Github` is already present and valid, its exact value is preserved as the source-of-truth; `Url` is not required in that case
- if `Github` is blank, discovery checks `cache.db` first, then does one Hugging Face exact lookup on cache miss
- if Hugging Face exact returns no repo, discovery does one AlphaXiv paper-page HTML lookup before leaving `Github` blank
- missing `Github`, `Stars`, `Created`, or `About` columns are added automatically at the end of the CSV
- existing custom columns are left untouched, including any preexisting `Overview` / `Abs` columns
- `Stars` and `About` are refreshed from shared repo metadata whenever metadata resolution succeeds
- `Created` is backfilled only when the current CSV cell is blank
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
- URL exports always write the standard columns: `Name`, `Url`, `Github`, `Stars`, `Created`, `About`
- when shared enrichment reaches a valid GitHub repository, collection URL exports populate `Created` and `About` through shared repo-metadata resolution
- standard arXiv `list/...` and `search/...` collection pages, including `/search/advanced`, are crawled across all pages, not just the first page
- archive-style arXiv `list/<category>/YYYY-MM` pages reuse the same multi-page `list/...` crawling path
- arXiv `new` pages include all visible sections, including new submissions, cross-lists, and replacements
- arXiv `catchup/<category>/YYYY-MM-DD` is supported only for that exact path shape
- arXiv catchup exports fail explicitly when the page reports more entries than are present on the page, rather than guessing pagination and writing a partial CSV
- arXiv Xplorer uses the site’s paging API instead of trying to click `Show More` in a browser
- Hugging Face Papers parses the collection page’s embedded papers payload from the frontend response
- Semantic Scholar search URLs are executed through Graph API bulk search, then keep only rows that can be normalized to canonical arXiv URLs
- URL modes use canonical, versionless arXiv URLs as internal identity and dedupe keys during downstream enrichment; existing arXiv URLs are preserved as-is in the final CSV, while non-arXiv rows are rewritten only when they are resolved to arXiv
- rows that cannot be mapped to arXiv are dropped from the final CSV
- repo discovery reuses the shared `cache.db` mapping of canonical arXiv URL to GitHub repo
- the shared resolver uses `cache -> Semantic Scholar exact -> Semantic Scholar title exact -> arXiv title search (HTML -> API) -> Crossref -> DataCite -> Hugging Face` for non-arXiv rows before downstream repo discovery
- downstream repository discovery, star lookup, sorting, progress printing, and CSV writing reuse the same shared export logic as CSV update mode where applicable

### GitHub repository search to CSV mode

Reads one supported GitHub repository-search URL and writes a CSV under `./output` in the current working directory by default.

Command shape:

```bash
uv run main.py '<github-search-url>'
```

Representative supported URL shape:

- `https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc`

Mode-specific behavior:

- only GitHub repository-search URLs are supported in this first version
- output rows use the shared six-column export schema: `Name`, `Url`, `Github`, `Stars`, `Created`, `About`
- `Name` and `Url` are intentionally empty for GitHub-search exports
- rows are sorted by `Created` descending before the CSV is written
- the output filename includes the GitHub search query and sort/order parameters

```bash
uv run main.py 'https://github.com/search?q=cvpr+2026&type=repositories&s=stars&o=desc'
```

Output example:

- `./output/github-search-cvpr-2026-o-desc-s-stars-type-repositories-20260326113045.csv`

### Single-paper arXiv relation export mode

Reads one arXiv paper URL, resolves related works through the Semantic Scholar Graph API / ai4scholar relay, normalizes them to canonical arXiv rows when possible, retains unresolved non-arXiv rows otherwise, and writes two CSV files under `./output` in the current working directory by default.

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

- resolves the input paper title from arXiv metadata first, then resolves the Semantic Scholar target by exact arXiv DOI / arXiv id with exact-title fallback
- keeps direct arXiv-backed related works as canonical, versionless arXiv `abs` rows
- otherwise reuses the shared resolver `cache -> Semantic Scholar exact -> Semantic Scholar title exact -> arXiv title search (HTML -> API) -> Crossref -> DataCite -> Hugging Face`
- mapped rows use the matched arXiv title and canonical arXiv `abs` URL
- when `HUGGINGFACE_TOKEN` is absent, the Hugging Face fallback is skipped silently and unresolved rows keep the current retained-row behavior
- if still unresolved, keeps the non-arXiv row with `Url` priority `DOI > landing page > source URL`
- relation normalization reuses `./cache.db` to cache non-direct relation resolution by source URL and DOI
- cached positive matches store canonical arXiv `abs` URLs; cached negative matches are written only when all actually attempted resolver stages finish without transient/network failure and still find no accepted arXiv match, then retried after `ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS`
- referenced and citing works are deduplicated by final normalized URL before export
- both CSVs use the standard columns: `Name`, `Url`, `Github`, `Stars`, `Created`, `About`
- when shared enrichment reaches a valid GitHub repository, single-paper relation exports populate `Created` and `About` through shared repo-metadata resolution
- shared GitHub discovery, repo-metadata enrichment, and local overview / abs cache warming are reused, so resolved and unresolved rows remain in the CSV even when no repo is found; in that case `Github` and `Stars` are left blank
- the CLI reports success only after both CSV files are written; other arXiv or Semantic Scholar hard failures still return a nonzero exit code

## Notion expectations

Your Notion database should have:

- `Name` or `Title` as title property
- `Github` as URL when present
- `Stars` as number when present
- `Created` as date when present
- `About` as rich text when present

If `Github`, `Stars`, `Created`, or `About` is missing from the data source schema, Notion mode creates it automatically before querying pages.
Newly created properties use these types: `Github` = URL, `Stars` = number, `Created` = date, `About` = rich text.
If one of those property names already exists with the wrong type, Notion mode fails fast instead of attempting to repurpose it.

Optional arXiv source fields for fallback discovery:

- `URL`
- `Arxiv`
- `arXiv`
- `Paper URL`
- `Link`

When `Github` is empty, the sync flow:

1. resolves the paper to a canonical arXiv URL from URL fields first; for non-arXiv URLs, the shared resolver uses `cache -> Semantic Scholar exact -> Semantic Scholar title exact -> arXiv title search (HTML -> API) -> Crossref -> DataCite -> Hugging Face`
2. uses that canonical arXiv URL as the paper identity for repo discovery and stars lookup
3. checks `cache.db` for that canonical arXiv URL
4. if needed, calls Hugging Face exact paper API for that arXiv id
5. if Hugging Face exact misses, fetches the AlphaXiv paper page HTML for the same arXiv id
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
