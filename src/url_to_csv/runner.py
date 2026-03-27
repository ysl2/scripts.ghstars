import os
import sys
from pathlib import Path

import aiohttp

from src.shared.arxiv import ArxivClient
from src.shared.discovery import DiscoveryClient
from src.shared.github import GitHubClient
from src.shared.http import build_timeout
from src.shared.progress import print_paper_progress, print_summary
from src.shared.repo_cache import RepoCacheStore
from src.shared.runtime import build_client, load_runtime_config
from src.shared.settings import DEFAULT_CONCURRENT_LIMIT, REPO_CACHE_DB_PATH
from src.shared.skip_reasons import is_minor_skip_reason
from src.url_to_csv.arxivxplorer import ArxivXplorerSearchClient
from src.url_to_csv.arxiv_org import ArxivOrgClient
from src.url_to_csv.huggingface_papers import HuggingFacePapersClient
from src.url_to_csv.pipeline import export_url_to_csv
from src.url_to_csv.semanticscholar import SemanticScholarSearchClient
from src.url_to_csv.sources import is_supported_url_source


CONCURRENT_LIMIT = DEFAULT_CONCURRENT_LIMIT
REQUEST_DELAY = 0.2


async def run_url_mode(
    input_url: str,
    *,
    output_dir: Path | None = None,
    session_factory=aiohttp.ClientSession,
    arxiv_client_cls=ArxivClient,
    search_client_cls=ArxivXplorerSearchClient,
    arxiv_org_client_cls=ArxivOrgClient,
    huggingface_papers_client_cls=HuggingFacePapersClient,
    semanticscholar_client_cls=SemanticScholarSearchClient,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
) -> int:
    if not is_supported_url_source(input_url):
        print(f"Unsupported URL: {input_url}", file=sys.stderr)
        return 1

    config = load_runtime_config(dict(os.environ))
    repo_cache = RepoCacheStore(REPO_CACHE_DB_PATH)

    try:
        async with session_factory(timeout=build_timeout()) as session:
            arxiv_client = build_client(
                arxiv_client_cls,
                session,
                max_concurrent=CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )
            search_client = build_client(
                search_client_cls,
                session,
                max_concurrent=CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )
            arxiv_org_client = build_client(
                arxiv_org_client_cls,
                session,
                max_concurrent=CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )
            huggingface_papers_client = build_client(
                huggingface_papers_client_cls,
                session,
                max_concurrent=CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )
            semanticscholar_client = build_client(
                semanticscholar_client_cls,
                session,
                max_concurrent=CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )
            discovery_client = build_client(
                discovery_client_cls,
                session,
                huggingface_token=config["huggingface_token"],
                alphaxiv_token=config["alphaxiv_token"],
                repo_cache=repo_cache,
                hf_exact_no_repo_recheck_days=config["hf_exact_no_repo_recheck_days"],
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

            result = await export_url_to_csv(
                input_url,
                output_dir=output_dir,
                search_client=search_client,
                arxiv_org_client=arxiv_org_client,
                huggingface_papers_client=huggingface_papers_client,
                semanticscholar_client=semanticscholar_client,
                arxiv_client=arxiv_client,
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
        "Resolved",
        result.resolved,
        result.skipped,
        is_minor_reason=is_minor_skip_reason,
        detail_label="Paper URL",
        minor_header="Skipped rows (CSV rows still written):",
    )
    print(f"Wrote CSV: {result.csv_path}", flush=True)
    return 0
