import inspect
import os
import sys
from pathlib import Path

import aiohttp

from html_to_csv.arxiv import ArxivClient
from html_to_csv.pipeline import convert_html_to_csv
from shared.discovery import DiscoveryClient
from shared.github import GitHubClient
from shared.http import build_timeout


ARXIV_CONCURRENT_LIMIT = 5
DISCOVERY_CONCURRENT_LIMIT = 5
GITHUB_CONCURRENT_LIMIT = 5
REQUEST_DELAY = 0.2


def load_runtime_config(env: dict[str, str]) -> dict[str, str]:
    return {
        "github_token": (env.get("GITHUB_TOKEN") or "").strip(),
        "huggingface_token": (env.get("HUGGINGFACE_TOKEN") or "").strip(),
        "alphaxiv_token": (env.get("ALPHAXIV_TOKEN") or "").strip(),
    }


def build_client(factory, session, **kwargs):
    parameters = inspect.signature(factory).parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        accepted_kwargs = kwargs
    else:
        accepted_names = {parameter.name for parameter in parameters}
        accepted_kwargs = {key: value for key, value in kwargs.items() if key in accepted_names}

    return factory(session, **accepted_kwargs)


def print_progress(completed: int, total: int, record) -> None:
    print(f"Processed {completed}/{total}: {record.name}", flush=True)


async def run_html_mode(
    html_path: Path | str,
    *,
    session_factory=aiohttp.ClientSession,
    arxiv_client_cls=ArxivClient,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
) -> int:
    html_path = Path(html_path).expanduser()
    if not html_path.exists() or not html_path.is_file():
        print(f"Input HTML not found: {html_path}", file=sys.stderr)
        return 1

    config = load_runtime_config(dict(os.environ))

    async with session_factory(timeout=build_timeout()) as session:
        arxiv_client = build_client(
            arxiv_client_cls,
            session,
            max_concurrent=ARXIV_CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        discovery_client = build_client(
            discovery_client_cls,
            session,
            huggingface_token=config["huggingface_token"],
            alphaxiv_token=config["alphaxiv_token"],
            max_concurrent=DISCOVERY_CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )
        github_client = build_client(
            github_client_cls,
            session,
            github_token=config["github_token"],
            max_concurrent=GITHUB_CONCURRENT_LIMIT,
            min_interval=REQUEST_DELAY,
        )

        csv_path = await convert_html_to_csv(
            html_path,
            arxiv_client=arxiv_client,
            discovery_client=discovery_client,
            github_client=github_client,
            status_callback=lambda message: print(message, flush=True),
            progress_callback=print_progress,
        )

    print(f"Wrote CSV: {csv_path}", flush=True)
    return 0
