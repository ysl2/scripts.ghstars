import inspect
import os
import sys
from pathlib import Path

import aiohttp

from html_to_csv.pipeline import convert_html_to_csv
from shared.discovery import DiscoveryClient
from shared.github import GitHubClient, extract_owner_repo
from shared.http import build_timeout
from shared.progress import print_item_skip, print_item_success, print_summary
from shared.skip_reasons import is_minor_skip_reason


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


def print_progress(outcome, total: int) -> None:
    owner_repo = extract_owner_repo(outcome.record.github) if outcome.record.github else None
    if outcome.reason is None:
        print_item_success(
            outcome.index,
            total,
            outcome.record.name,
            owner_repo=owner_repo,
            current_stars=None,
            new_stars=outcome.record.stars if isinstance(outcome.record.stars, int) else None,
        )
        return

    print_item_skip(
        outcome.index,
        total,
        outcome.record.name,
        outcome.reason,
        owner_repo=owner_repo,
        minor=is_minor_skip_reason(outcome.reason),
    )


async def run_html_mode(
    html_path: Path | str,
    *,
    session_factory=aiohttp.ClientSession,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
) -> int:
    html_path = Path(html_path).expanduser()
    if not html_path.exists() or not html_path.is_file():
        print(f"Input HTML not found: {html_path}", file=sys.stderr)
        return 1

    config = load_runtime_config(dict(os.environ))

    async with session_factory(timeout=build_timeout()) as session:
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

        result = await convert_html_to_csv(
            html_path,
            discovery_client=discovery_client,
            github_client=github_client,
            status_callback=lambda message: print(message, flush=True),
            progress_callback=print_progress,
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
