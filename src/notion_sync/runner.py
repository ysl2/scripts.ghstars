import asyncio
import os

import aiohttp

from src.notion_sync.config import load_config_from_env
from src.notion_sync.notion_client import NotionClient
from src.notion_sync.pipeline import process_page
from src.shared.alphaxiv_content import AlphaXivContentClient
from src.shared.arxiv import ArxivClient
from src.shared.async_batch import iter_bounded_as_completed
from src.shared.crossref import CrossrefClient
from src.shared.datacite import DataCiteClient
from src.shared.discovery import DiscoveryClient
from src.shared.github import GitHubClient, resolve_github_min_interval
from src.shared.paper_content import PaperContentCache
from src.shared.progress import Colors, colored, print_summary
from src.shared.runtime import build_client, open_runtime_clients
from src.shared.semantic_scholar_graph import (
    SemanticScholarGraphClient,
    resolve_semantic_scholar_min_interval,
)
from src.shared.settings import CONTENT_CACHE_DIR, DEFAULT_CONCURRENT_LIMIT
from src.shared.skip_reasons import is_minor_skip_reason


CONCURRENT_LIMIT = DEFAULT_CONCURRENT_LIMIT
GITHUB_CONCURRENT_LIMIT = CONCURRENT_LIMIT
NOTION_CONCURRENT_LIMIT = CONCURRENT_LIMIT
DISCOVERY_CONCURRENT_LIMIT = CONCURRENT_LIMIT
ARXIV_CONCURRENT_LIMIT = CONCURRENT_LIMIT
REQUEST_DELAY = 0.2


async def run_notion_mode(
    *,
    session_factory=aiohttp.ClientSession,
    arxiv_client_cls=ArxivClient,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
    semanticscholar_graph_client_cls=SemanticScholarGraphClient,
    crossref_client_cls=CrossrefClient,
    datacite_client_cls=DataCiteClient,
    notion_client_cls=NotionClient,
    content_client_cls=AlphaXivContentClient,
) -> int:
    config = load_config_from_env(dict(os.environ))

    github_token = config["github_token"]
    github_request_delay = resolve_github_min_interval(github_token, REQUEST_DELAY)
    if github_token:
        print(colored("✅ GitHub Token configured (5000 requests/hour)", Colors.GREEN))
    else:
        print(colored("⚠️ No GitHub Token configured (60 requests/hour)", Colors.YELLOW))
        print("   Set GITHUB_TOKEN environment variable for higher rate limit")

    print(f"⚙️ Concurrency: GitHub={GITHUB_CONCURRENT_LIMIT}, Notion={NOTION_CONCURRENT_LIMIT}")
    print(f"⚙️ Request interval: general={REQUEST_DELAY}s, GitHub={github_request_delay}s")
    print()

    async with open_runtime_clients(
        config,
        session_factory=session_factory,
        discovery_client_cls=discovery_client_cls,
        github_client_cls=github_client_cls,
        concurrent_limit=CONCURRENT_LIMIT,
        request_delay=REQUEST_DELAY,
        github_min_interval=github_request_delay,
        enable_relation_resolution_cache=True,
    ) as runtime:
        arxiv_client = arxiv_client_cls(
            runtime.session,
            max_concurrent=ARXIV_CONCURRENT_LIMIT,
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
            cache_root=CONTENT_CACHE_DIR,
            content_client=content_client,
        )

        async with notion_client_cls(config["notion_token"], NOTION_CONCURRENT_LIMIT) as notion_client:
            data_source_id = await notion_client.get_data_source_id(config["database_id"])
            if not data_source_id:
                print(colored("❌ Unable to get data_source_id; check DATABASE_ID", Colors.RED))
                return 1

            print(f"📚 Data source ID: {data_source_id}")
            try:
                await notion_client.ensure_sync_properties(data_source_id)
            except ValueError as exc:
                print(colored(f"❌ {exc}", Colors.RED))
                return 1

            pages = await notion_client.query_pages(data_source_id)
            print(f"📝 Found {len(pages)} pages with Github field\n")

            results = {"updated": 0, "skipped": []}
            lock = asyncio.Lock()

            async def process_page_item(item: tuple[int, dict]) -> None:
                i, page = item
                await process_page(
                    page,
                    i,
                    len(pages),
                    discovery_client=runtime.discovery_client,
                    github_client=runtime.github_client,
                    notion_client=notion_client,
                    results=results,
                    lock=lock,
                    arxiv_client=arxiv_client,
                    semanticscholar_graph_client=semanticscholar_graph_client,
                    crossref_client=crossref_client,
                    datacite_client=datacite_client,
                    content_cache=content_cache,
                    relation_resolution_cache=runtime.relation_resolution_cache,
                    arxiv_relation_no_arxiv_recheck_days=config["arxiv_relation_no_arxiv_recheck_days"],
                )

            async for _ in iter_bounded_as_completed(
                enumerate(pages, 1),
                process_page_item,
                max_concurrent=NOTION_CONCURRENT_LIMIT,
            ):
                pass

    print_summary(
        "Updated",
        results["updated"],
        results["skipped"],
        is_minor_reason=is_minor_skip_reason,
        detail_label="Notion URL",
        minor_header="Skipped rows (non-GitHub URLs, can be ignored):",
    )

    return 0
