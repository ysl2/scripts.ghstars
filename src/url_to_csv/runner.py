import os
import sys
from pathlib import Path

import aiohttp

from src.shared.alphaxiv_content import AlphaXivContentClient
from src.shared.arxiv import ArxivClient
from src.shared.crossref import CrossrefClient
from src.shared.datacite import DataCiteClient
from src.shared.discovery import DiscoveryClient
from src.shared.github import GitHubClient
from src.shared.paper_content import PaperContentCache
from src.shared.progress import print_paper_progress, print_summary
from src.shared.runtime import build_client, load_runtime_config, open_runtime_clients
from src.shared.semantic_scholar_graph import (
    SemanticScholarGraphClient,
    resolve_semantic_scholar_min_interval,
)
from src.shared.settings import CONTENT_CACHE_DIR, DEFAULT_CONCURRENT_LIMIT
from src.shared.skip_reasons import is_minor_skip_reason
from src.url_to_csv.arxivxplorer import ArxivXplorerSearchClient
from src.url_to_csv.arxiv_org import ArxivOrgClient
from src.url_to_csv.huggingface_papers import HuggingFacePapersClient
from src.url_to_csv.pipeline import export_url_to_csv
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
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
    semanticscholar_graph_client_cls=SemanticScholarGraphClient,
    crossref_client_cls=CrossrefClient,
    datacite_client_cls=DataCiteClient,
    content_client_cls=AlphaXivContentClient,
    content_cache_root: Path | str | None = None,
) -> int:
    if not is_supported_url_source(input_url):
        print(f"Unsupported URL: {input_url}", file=sys.stderr)
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
        search_client = build_client(
            search_client_cls,
            runtime.session,
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        arxiv_org_client = build_client(
            arxiv_org_client_cls,
            runtime.session,
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        huggingface_papers_client = build_client(
            huggingface_papers_client_cls,
            runtime.session,
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        semanticscholar_graph_client = build_client(
            semanticscholar_graph_client_cls,
            runtime.session,
            semantic_scholar_api_key=config["semantic_scholar_api_key"],
            aiforscholar_token=config["aiforscholar_token"],
            max_concurrent=CONCURRENT_LIMIT,
            min_interval=resolve_semantic_scholar_min_interval(
                config["semantic_scholar_api_key"],
                config["aiforscholar_token"],
                REQUEST_DELAY,
            ),
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

        result = await export_url_to_csv(
            input_url,
            output_dir=output_dir,
            search_client=search_client,
            arxiv_org_client=arxiv_org_client,
            huggingface_papers_client=huggingface_papers_client,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            discovery_client=runtime.discovery_client,
            github_client=runtime.github_client,
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
        "Resolved",
        result.resolved,
        result.skipped,
        is_minor_reason=is_minor_skip_reason,
        detail_label="Paper URL",
        minor_header="Skipped rows (CSV rows still written):",
    )
    print(f"Wrote CSV: {result.csv_path}", flush=True)
    return 0
