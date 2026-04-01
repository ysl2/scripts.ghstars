import asyncio
import os
import sys
from pathlib import Path

import aiohttp

from src.arxiv_relations.pipeline import export_arxiv_relations_to_csv
from src.shared.alphaxiv_content import AlphaXivContentClient
from src.shared.arxiv import ArxivClient
from src.shared.crossref import CrossrefClient
from src.shared.datacite import DataCiteClient
from src.shared.discovery import DiscoveryClient
from src.shared.github import GitHubClient
from src.shared.paper_content import PaperContentCache
from src.shared.progress import print_paper_progress, print_relation_progress, print_summary
from src.shared.runtime import build_client, load_runtime_config, open_runtime_clients
from src.shared.semantic_scholar_graph import (
    SemanticScholarGraphClient,
    resolve_semantic_scholar_min_interval,
)
from src.shared.settings import CONTENT_CACHE_DIR, DEFAULT_CONCURRENT_LIMIT
from src.shared.skip_reasons import is_minor_skip_reason


CONCURRENT_LIMIT = DEFAULT_CONCURRENT_LIMIT
REQUEST_DELAY = 0.2


async def run_arxiv_relations_mode(
    arxiv_input: str,
    *,
    output_dir: Path | None = None,
    session_factory=aiohttp.ClientSession,
    arxiv_client_cls=ArxivClient,
    crossref_client_cls=CrossrefClient,
    datacite_client_cls=DataCiteClient,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
    content_client_cls=AlphaXivContentClient,
    semanticscholar_graph_client_cls=SemanticScholarGraphClient,
) -> int:
    try:
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
            content_cache = PaperContentCache(
                cache_root=Path(CONTENT_CACHE_DIR),
                content_client=content_client,
            )

            result = await export_arxiv_relations_to_csv(
                arxiv_input,
                output_dir=output_dir,
                arxiv_client=arxiv_client,
                openalex_client=None,
                crossref_client=crossref_client,
                datacite_client=datacite_client,
                discovery_client=runtime.discovery_client,
                github_client=runtime.github_client,
                semanticscholar_graph_client=semanticscholar_graph_client,
                content_cache=content_cache,
                relation_resolution_cache=runtime.relation_resolution_cache,
                arxiv_relation_no_arxiv_recheck_days=config["arxiv_relation_no_arxiv_recheck_days"],
                status_callback=lambda message: print(message, flush=True),
                normalization_progress_callback=lambda outcome, total: print_relation_progress(outcome, total),
                progress_callback=lambda outcome, total: print_paper_progress(
                    outcome,
                    total,
                    is_minor_reason=is_minor_skip_reason,
                ),
            )
    except Exception as exc:
        print(f"ArXiv relation export failed: {exc}", file=sys.stderr)
        return 1

    print_summary(
        "References resolved",
        result.references.resolved,
        result.references.skipped,
        is_minor_reason=is_minor_skip_reason,
        detail_label="Paper URL",
        minor_header="Skipped reference rows (CSV rows still written):",
    )
    print(f"Wrote references CSV: {result.references.csv_path}", flush=True)
    print_summary(
        "Citations resolved",
        result.citations.resolved,
        result.citations.skipped,
        is_minor_reason=is_minor_skip_reason,
        detail_label="Paper URL",
        minor_header="Skipped citation rows (CSV rows still written):",
    )
    print(f"Wrote citations CSV: {result.citations.csv_path}", flush=True)

    return 0
