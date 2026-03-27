import asyncio
import os

import aiohttp

from src.notion_sync.config import load_config_from_env
from src.notion_sync.notion_client import NotionClient
from src.notion_sync.pipeline import process_page
from src.shared.arxiv import ArxivClient
from src.shared.discovery import DiscoveryClient
from src.shared.github import GitHubClient, resolve_github_min_interval
from src.shared.http import build_timeout
from src.shared.progress import Colors, colored, print_summary
from src.shared.repo_cache import RepoCacheStore
from src.shared.settings import DEFAULT_CONCURRENT_LIMIT, REPO_CACHE_DB_PATH
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
    notion_client_cls=NotionClient,
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
    repo_cache = RepoCacheStore(REPO_CACHE_DB_PATH)

    try:
        async with session_factory(timeout=build_timeout()) as session:
            arxiv_client = arxiv_client_cls(
                session,
                max_concurrent=ARXIV_CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )
            discovery_client = discovery_client_cls(
                session,
                huggingface_token=config["huggingface_token"],
                alphaxiv_token=config["alphaxiv_token"],
                repo_cache=repo_cache,
                hf_exact_no_repo_recheck_days=config["hf_exact_no_repo_recheck_days"],
                max_concurrent=DISCOVERY_CONCURRENT_LIMIT,
                min_interval=REQUEST_DELAY,
            )
            github_client = github_client_cls(
                session,
                github_token=github_token,
                max_concurrent=GITHUB_CONCURRENT_LIMIT,
                min_interval=github_request_delay,
            )

            async with notion_client_cls(config["notion_token"], NOTION_CONCURRENT_LIMIT) as notion_client:
                data_source_id = await notion_client.get_data_source_id(config["database_id"])
                if not data_source_id:
                    print(colored("❌ Unable to get data_source_id; check DATABASE_ID", Colors.RED))
                    return 1

                print(f"📚 Data source ID: {data_source_id}")

                pages = await notion_client.query_pages(data_source_id)
                print(f"📝 Found {len(pages)} pages with Github field\n")

                results = {"updated": 0, "skipped": []}
                lock = asyncio.Lock()
                tasks = [
                    process_page(
                        page,
                        i,
                        len(pages),
                        discovery_client=discovery_client,
                        github_client=github_client,
                        notion_client=notion_client,
                        results=results,
                        lock=lock,
                        arxiv_client=arxiv_client,
                    )
                    for i, page in enumerate(pages, 1)
                ]
                await asyncio.gather(*tasks)
    finally:
        repo_cache.close()

    print_summary(
        "Updated",
        results["updated"],
        results["skipped"],
        is_minor_reason=is_minor_skip_reason,
        detail_label="Notion URL",
        minor_header="Skipped rows (non-GitHub URLs, can be ignored):",
    )

    return 0
