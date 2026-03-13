# Zotero Notion (Notero) GitHub Stars Updater

A Python script that automatically updates GitHub repository star counts in your Notion database. It fetches the latest star counts from GitHub API and updates your Notion pages with the current data.

<p><img src=".assets/README/img/2026-03-01-16-02-06.png" alt="" width=100% style="display: block; margin: auto;"></p>

## Features

- **Automatic Star Count Updates**: Fetches the latest star counts for GitHub repositories stored in Notion
- **Fallback GitHub Discovery**: For rows whose `Github` field is empty or `WIP`, tries to discover the repo through the AlphaXiv legacy API using the arXiv URL in your Notion `URL` column
- **Concurrent Processing**: Uses async/await for efficient parallel API requests
- **Rate Limiting**: Built-in rate limiting to respect API quotas for both GitHub and Notion
- **GitHub Token Support**: Optional GitHub authentication for higher rate limits (5000 vs 60 requests/hour)
- **Smart URL Handling**: Validates and extracts owner/repo from various GitHub URL formats
- **Detailed Reporting**: Shows updated counts with color-coded diff indicators (±changes)
- **Error Handling**: Categorizes and displays skipped/failed items with clear reasons
- **Progress Tracking**: Real-time progress updates during execution

## Prerequisites

- Python 3.12 or higher
- A Notion Integration with API access to your database
- (Optional) GitHub Personal Access Token for higher rate limits

## Installation

1. **Clone or download this project**

2. **Install dependencies using UV** (recommended):
   ```bash
   uv sync
   ```

   Or using pip:
   ```bash
   pip install aiohttp notion-client requests
   ```

## Configuration

### 1. Set up Notion Integration

1. Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Create a new integration and copy your **Internal Integration Token**
3. Share your database with this integration (click "..." on your database → "Add connections" → select your integration)

### 2. Configure Your Database

Your Notion database must have these properties:

- **Name** (Title type): Page title
- **Github** (URL or Rich Text type): GitHub repository URL or `WIP`
- **Github stars** (Number type): Star count (will be updated by the script)

Optional but recommended properties for fallback discovery:
- **URL** (preferred), or **Arxiv** / **arXiv** / **Paper URL** / **Link**: a field containing the arXiv URL, used for AlphaXiv legacy API fallback

### 3. Set Environment Variables

Copy `.env.example` to `.env`, then fill in your values (or set environment variables directly):

```bash
# Required: Your Notion Integration Token
NOTION_TOKEN=your_notion_token_here

# Required: Your Notion Database ID
DATABASE_ID=your_database_id_here

# Optional: Your GitHub Personal Access Token (for higher rate limits)
GITHUB_TOKEN=your_github_token_here

# Optional but required if you want AlphaXiv fallback discovery for empty/WIP Github fields
ALPHAXIV_API_KEY=your_alphaxiv_api_key_here
```

To find your database ID:
1. Open your database in Notion
2. Copy the ID from the URL (32-character string after `/database/` or after `?v=`)
3. Example: In `https://notion.so/workspace/database?v=abcd1234...`, the ID is the 32-char string

## Usage

Run the script:

```bash
python main.py
```

Or with UV:

```bash
uv run python main.py
```

### What Happens During Execution

1. **Authentication Check**: Verifies GitHub Token status and rate limits
2. **Database Query**: Fetches pages from the data source
3. **Concurrent Processing**: For each page:
   - If `Github` is a valid GitHub repo URL, fetches star count and updates `Github stars`
   - If `Github` is empty or `WIP`, the script first tries to extract an arXiv ID from the Notion `URL`-style field
   - If the URL field is not an arXiv link, the script falls back to searching arXiv by the paper title to recover the best-matching arXiv ID
   - Once an arXiv ID is available, it queries `https://api.alphaxiv.org/papers/v3/legacy/{arxiv_id}`
   - The script looks for GitHub links in legacy fields like `paper.implementation`, `paper.marimo_implementation`, `paper.paper_group.resources`, `paper.resources`, then falls back to recursive scanning of the returned JSON
   - External HTTP requests use explicit timeouts and limited retries for transient failures like `429` / `502` / `503` / `504`
   - If AlphaXiv API discovery succeeds, updates both `Github` and `Github stars`
   - If `Github` contains any other non-empty value, leaves the row unchanged
4. **Results Summary**: Displays updated count and skipped items with reasons

### Example Output

```
✅ GitHub Token configured (5000 requests/hour)
⚙️ Concurrency: GitHub=5, Notion=3
⚙️ Request interval: 0.2s

📊 GitHub API Rate Limit: 4999/5000 remaining

📚 Data source ID: abcd1234...
📝 Found 25 pages with Github field

[1/25] awesome-project
  📍 facebook/react | Current stars: 180000
  ✅ Updated: 180000 → 182345 (+2345)

[2/25] my-tool
  📍 microsoft/vscode | Current stars: N/A
  ✅ Updated: N/A → 145678

============================================================
✅ Updated: 23
⏭️ Skipped: 2

============================================================
⏭️ Skipped rows (non-GitHub URLs, can be ignored):
============================================================

1. Local Project
   Reason:     Invalid Github URL format
   Github URL: http://localhost:3000
   Notion URL: https://notion.so/...

============================================================
📊 GitHub API Rate Limit: 4976/5000 remaining
```

## Rate Limiting

The script implements rate limiting to avoid hitting API quotas:

### GitHub API
- **Without token**: 60 requests/hour
- **With token**: 5000 requests/hour
- **Concurrent requests**: 5 (configurable via `GITHUB_CONCURRENT_LIMIT`)
- **Request delay**: 0.2s (configurable via `REQUEST_DELAY`)

### Notion API
- **Concurrent requests**: 3 (configurable via `NOTION_CONCURRENT_LIMIT`)

If you hit the GitHub rate limit, the script will automatically wait for it to reset before continuing.

## Configuration Parameters

You can adjust these constants in `main.py`:

```python
GITHUB_CONCURRENT_LIMIT = 5      # Max concurrent GitHub API requests
NOTION_CONCURRENT_LIMIT = 3      # Max concurrent Notion API requests
REQUEST_DELAY = 0.2              # Minimum delay between requests (seconds)
```

## Supported GitHub URL Formats

The script handles various GitHub URL formats:

- `https://github.com/owner/repo`
- `http://github.com/owner/repo`
- `www.github.com/owner/repo`
- `github.com/owner/repo`
- `https://github.com/owner/repo.git`
- Any of the above with trailing slashes

## Error Categories

Skipped items are categorized into two types:

### Minor (shown in gray)
- Unsupported Github field content
- Missing ALPHAXIV_API_KEY
- No arXiv ID found for AlphaXiv API lookup
- No Github URL found in AlphaXiv API
- Discovered URL is not a valid GitHub repository

These can typically be ignored (e.g., rows intentionally left without code links, or non-GitHub values you want to preserve).

### Major (shown in red)
- Repository not found
- Rate limit exceeded
- Request timeout
- API errors

These may need attention to fix invalid URLs or API issues.

## Troubleshooting

**"Missing required environment variables" error**
- Ensure `NOTION_TOKEN` and `DATABASE_ID` are set in `.env` or shell environment

**"Cannot get data_source_id" error**
- Verify your `DATABASE_ID` is correct
- Ensure your Notion integration has access to the database

**"Repository not found" errors**
- Check that the GitHub URL is correct and the repository exists
- Private repositories require a GitHub token with access

**Rate limit errors**
- Set a `GITHUB_TOKEN` environment variable for higher limits
- Consider reducing `GITHUB_CONCURRENT_LIMIT` if you still hit limits

**Timeout errors**
- Increase `REQUEST_DELAY` to reduce request frequency
- Check your internet connection

## Dependencies

- **aiohttp**: Async HTTP client for API requests
- **notion-client**: Official Notion API Python client
- **requests**: Additional HTTP support (fallback)
- **python-dotenv**: Optional, for loading environment variables from .env files

## License

This project is open source and available under the MIT License.

## Contributing

Contributions are welcome! Feel free to submit issues or pull requests.
