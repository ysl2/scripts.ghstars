import os
import sys
from pathlib import Path

import aiohttp

from src.csv_update.pipeline import update_csv_file
from src.shared.discovery import DiscoveryClient
from src.shared.github import GitHubClient
from src.shared.http import build_timeout
from src.shared.progress import print_paper_progress, print_summary
from src.shared.repo_cache import RepoCacheStore
from src.shared.runtime import build_client, load_runtime_config
from src.shared.settings import DEFAULT_CONCURRENT_LIMIT, REPO_CACHE_DB_PATH
from src.shared.skip_reasons import is_minor_skip_reason


CONCURRENT_LIMIT = DEFAULT_CONCURRENT_LIMIT
REQUEST_DELAY = 0.2


async def run_csv_mode(
    csv_path: Path | str,
    *,
    session_factory=aiohttp.ClientSession,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
) -> int:
    csv_path = Path(csv_path).expanduser()
    if not csv_path.exists() or not csv_path.is_file():
        print(f"Input CSV not found: {csv_path}", file=sys.stderr)
        return 1

    config = load_runtime_config(dict(os.environ))
    repo_cache = RepoCacheStore(REPO_CACHE_DB_PATH)

    try:
        async with session_factory(timeout=build_timeout()) as session:
            discovery_client = build_client(
                discovery_client_cls,
                session,
                huggingface_token=config["huggingface_token"],
                alphaxiv_token=config["alphaxiv_token"],
                repo_cache=repo_cache,
                hf_exact_no_repo_threshold=config["hf_exact_no_repo_threshold"],
                max_concurrent=CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )
            github_client = build_client(
                github_client_cls,
                session,
                github_token=config["github_token"],
                max_concurrent=CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )

            result = await update_csv_file(
                csv_path,
                discovery_client=discovery_client,
                github_client=github_client,
                status_callback=lambda message: print(message, flush=True),
                progress_callback=lambda outcome, total: print_paper_progress(
                    outcome,
                    total,
                    is_minor_reason=is_minor_skip_reason,
                ),
            )
    finally:
        repo_cache.close()

    print_summary(
        "Updated",
        result.updated,
        result.skipped,
        is_minor_reason=is_minor_skip_reason,
        detail_label="Paper URL",
        minor_header="Skipped rows (CSV rows preserved):",
    )
    print(f"Updated CSV: {result.csv_path}", flush=True)
    return 0
