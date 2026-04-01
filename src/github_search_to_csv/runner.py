import os
import sys
from pathlib import Path

import aiohttp

from src.github_search_to_csv.pipeline import export_github_search_to_csv
from src.github_search_to_csv.search import (
    GitHubRepositorySearchClient,
    is_supported_github_search_url,
)
from src.shared.http import build_timeout
from src.shared.runtime import load_runtime_config


MAX_CONCURRENT = 1
REQUEST_DELAY = 0.2


async def run_github_search_mode(
    input_url: str,
    *,
    output_dir: Path | None = None,
    session_factory=aiohttp.ClientSession,
    search_client_cls=GitHubRepositorySearchClient,
) -> int:
    if not is_supported_github_search_url(input_url):
        print(f"Unsupported URL: {input_url}", file=sys.stderr)
        return 1

    config = load_runtime_config(dict(os.environ))
    async with session_factory(timeout=build_timeout()) as session:
        search_client = search_client_cls(
            session,
            github_token=str(config["github_token"]),
            max_concurrent=MAX_CONCURRENT,
            min_interval=REQUEST_DELAY,
            progress=lambda message: print(message, flush=True),
        )
        result = await export_github_search_to_csv(
            input_url,
            search_client=search_client,
            output_dir=output_dir,
            status_callback=lambda message: print(message, flush=True),
        )

    print(f"Resolved: {result.resolved}", flush=True)
    print(f"Wrote CSV: {result.csv_path}", flush=True)
    return 0
