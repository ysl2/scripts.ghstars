# Zotero Notion, CSV, And URL GitHub Stars

One CLI, three modes:

- No positional argument: sync GitHub links and star counts into Notion
- One existing `.csv` file path: update that CSV in place
- One supported `https://arxivxplorer.com/?...` URL: fetch the full search result set and write a CSV in the current working directory

The CSV and URL modes keep the existing repository discovery policy:

- Hugging Face first
- AlphaXiv second

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
```

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
- if `Github` is blank, repository discovery still uses Hugging Face first and AlphaXiv second
- missing `Github` or `Stars` columns are appended automatically
- writes use a temp file and atomic replace

### Arxivxplorer URL to CSV mode

Reads a supported `arxivxplorer.com` search URL, fetches all paginated results from the backing search API, and writes a CSV in the current working directory.

```bash
uv run main.py 'https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026&year=2025&year=2024'
```

Output example:

- `./arxivxplorer-streaming-semantic-3d-reconstruction-cs.CV-2026-2025-2024.csv`

URL mode behavior:

- uses the site’s paging API instead of trying to click `Show More` in a browser
- keeps fetching pages until the API returns an empty page, so it is not limited by the frontend button behavior
- only arXiv search results are converted into papers for downstream Github/Stars enrichment
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

When `Github` is empty or `WIP`, the sync flow tries to discover the repo from the paper:

1. Hugging Face paper page
2. Hugging Face paper search
3. AlphaXiv legacy API

## Notes

- Invalid file path does not fall back to Notion mode
- Unsupported URLs fail instead of falling back to another mode
- More than one positional argument is treated as a usage error
- Concurrency and rate limiting remain enabled in all modes
- `*.html` and `*.csv` are gitignored globally; use `git add -f` only if you intentionally want to track one

## Tests

```bash
uv run pytest
```
