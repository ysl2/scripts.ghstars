import asyncio
import os

import aiohttp

from html_to_csv.arxiv import ArxivClient
from notion_sync.config import load_config_from_env
from notion_sync.notion_client import NotionClient
from notion_sync.pipeline import is_minor_skip_reason, process_page
from shared.discovery import DiscoveryClient
from shared.github import GitHubClient
from shared.http import build_timeout


GITHUB_CONCURRENT_LIMIT = 5
NOTION_CONCURRENT_LIMIT = 3
DISCOVERY_CONCURRENT_LIMIT = 5
ARXIV_CONCURRENT_LIMIT = 5
REQUEST_DELAY = 0.2


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    GRAY = "\033[90m"
    RESET = "\033[0m"


def colored(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"


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
    if github_token:
        print(colored("✅ GitHub Token configured (5000 requests/hour)", Colors.GREEN))
    else:
        print(colored("⚠️ No GitHub Token configured (60 requests/hour)", Colors.YELLOW))
        print("   Set GITHUB_TOKEN environment variable for higher rate limit")

    print(f"⚙️ Concurrency: GitHub={GITHUB_CONCURRENT_LIMIT}, Notion={NOTION_CONCURRENT_LIMIT}")
    print(f"⚙️ Request interval: {REQUEST_DELAY}s")
    print()

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
            max_concurrent=DISCOVERY_CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        github_client = github_client_cls(
            session,
            github_token=github_token,
            max_concurrent=GITHUB_CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
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

    print(f'\n{"=" * 60}')
    print(colored(f'✅ Updated: {results["updated"]}', Colors.GREEN))
    print(f'⏭️ Skipped: {len(results["skipped"])}')

    minor_skipped = [s for s in results["skipped"] if is_minor_skip_reason(s["reason"])]
    major_skipped = [s for s in results["skipped"] if not is_minor_skip_reason(s["reason"])]

    if major_skipped:
        print(f'\n{"=" * 60}')
        print(colored("❌ Failed rows (need attention):", Colors.RED))
        print(f'{"=" * 60}')
        for i, item in enumerate(major_skipped, 1):
            print(colored(f'\n{i}. {item["title"]}', Colors.RED))
            print(colored(f'   Reason:     {item["reason"]}', Colors.RED))
            if item["github_url"]:
                print(colored(f'   Github URL: {item["github_url"]}', Colors.RED))
            print(colored(f'   Notion URL: {item["notion_url"]}', Colors.RED))

    if minor_skipped:
        print(f'\n{"=" * 60}')
        print(colored("⏭️ Skipped rows (non-GitHub URLs, can be ignored):", Colors.GRAY))
        print(colored(f'{"=" * 60}', Colors.GRAY))
        for i, item in enumerate(minor_skipped, 1):
            print(colored(f'\n{i}. {item["title"]}', Colors.GRAY))
            print(colored(f'   Reason:     {item["reason"]}', Colors.GRAY))
            if item["github_url"]:
                print(colored(f'   Github URL: {item["github_url"]}', Colors.GRAY))
            print(colored(f'   Notion URL: {item["notion_url"]}', Colors.GRAY))

    return 0
