# Zotero Notion And HTML GitHub Stars

One CLI, two modes:

- No positional argument: sync GitHub links and star counts into Notion
- One existing `.html` file path: convert paper cards in that HTML file into a same-name CSV

The HTML mode keeps the existing repository discovery policy:

- Hugging Face first
- AlphaXiv second

GitHub and star lookup use normalized, versionless arXiv URLs as the paper identity.

## Install

```bash
uv sync
```

## Environment

Copy `.env.example` to `.env` and fill in the variables you need.

### Used by both modes

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

### HTML to CSV mode

Reads one HTML file and writes a CSV with the same basename in the same directory.

```bash
uv run main.py /path/to/papers.html
```

Input:

- one `.html` file

Output:

- `/path/to/papers.csv`

CSV columns:

- `Name`
- `Url`
- `Github`
- `Stars`

HTML mode behavior:

- arXiv URLs are canonicalized to versionless `https://arxiv.org/abs/<id>`
- rows are sorted by canonical arXiv `Url` descending
- missing GitHub or stars values are left blank
- progress is printed incrementally in the terminal during processing
- writes use a temp file and atomic replace

## HTML expectations

The HTML parser currently targets card-style markup like:

- `div.chakra-card__root`
- title inside `h2`
- arXiv link inside `a[href]`

Duplicate papers are deduplicated by canonical arXiv URL, not by title.

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
- More than one positional argument is treated as a usage error
- Concurrency and rate limiting remain enabled in both modes
- `*.html` and `*.csv` are gitignored globally; use `git add -f` only if you intentionally want to track one

## Tests

```bash
uv run pytest
```
