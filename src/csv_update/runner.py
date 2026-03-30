import os
import sys
from pathlib import Path

import aiohttp

from src.csv_update.pipeline import update_csv_file
from src.shared.alphaxiv_content import AlphaXivContentClient
from src.shared.arxiv import ArxivClient
from src.shared.crossref import CrossrefClient
from src.shared.datacite import DataCiteClient
from src.shared.discovery import DiscoveryClient
from src.shared.github import GitHubClient
from src.shared.openalex import OpenAlexClient
from src.shared.paper_content import PaperContentCache
from src.shared.progress import print_paper_progress, print_summary
from src.shared.runtime import build_client, load_runtime_config, open_runtime_clients
from src.shared.settings import CONTENT_CACHE_DIR, DEFAULT_CONCURRENT_LIMIT
from src.shared.skip_reasons import is_minor_skip_reason


CONCURRENT_LIMIT = DEFAULT_CONCURRENT_LIMIT
REQUEST_DELAY = 0.2


async def run_csv_mode(
    csv_path: Path | str,
    *,
    session_factory=aiohttp.ClientSession,
    arxiv_client_cls=ArxivClient,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
    openalex_client_cls=OpenAlexClient,
    crossref_client_cls=CrossrefClient,
    datacite_client_cls=DataCiteClient,
    content_client_cls=AlphaXivContentClient,
    content_cache_root: Path | str | None = None,
) -> int:
    csv_path = Path(csv_path).expanduser()
    if not csv_path.exists() or not csv_path.is_file():
        print(f"Input CSV not found: {csv_path}", file=sys.stderr)
        return 1

    config = load_runtime_config(dict(os.environ))
    async with open_runtime_clients(
        config,
        session_factory=session_factory,
        discovery_client_cls=discovery_client_cls,
        github_client_cls=github_client_cls,
        concurrent_limit=CONCURRENT_LIMIT,
        request_delay=REQUEST_DELAY,
        enable_relation_resolution_cache=True,
    ) as runtime:
        arxiv_client = build_client(
            arxiv_client_cls,
            runtime.session,
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        openalex_client = build_client(
            openalex_client_cls,
            runtime.session,
            openalex_api_key=config["openalex_api_key"],
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        crossref_client = build_client(
            crossref_client_cls,
            runtime.session,
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        datacite_client = build_client(
            datacite_client_cls,
            runtime.session,
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        content_client = build_client(
            content_client_cls,
            runtime.session,
            alphaxiv_token=config["alphaxiv_token"],
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        content_cache = PaperContentCache(
            cache_root=Path(content_cache_root) if content_cache_root is not None else Path(CONTENT_CACHE_DIR),
            content_client=content_client,
        )
        result = await update_csv_file(
            csv_path,
            discovery_client=runtime.discovery_client,
            github_client=runtime.github_client,
            arxiv_client=arxiv_client,
            openalex_client=openalex_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            content_cache=content_cache,
            relation_resolution_cache=runtime.relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=config["arxiv_relation_no_arxiv_recheck_days"],
            status_callback=lambda message: print(message, flush=True),
            progress_callback=lambda outcome, total: print_paper_progress(
                outcome,
                total,
                is_minor_reason=is_minor_skip_reason,
            ),
        )

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
