from contextlib import asynccontextmanager
from dataclasses import dataclass
import inspect
import sqlite3

from src.shared.http import build_timeout
from src.shared.relation_resolution_cache import RelationResolutionCacheStore
from src.shared.repo_cache import RepoCacheStore
from src.shared.settings import (
    ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS,
    REPO_DISCOVERY_NO_REPO_RECHECK_DAYS,
    REPO_CACHE_DB_PATH,
)


@dataclass(frozen=True)
class RuntimeClients:
    session: object
    repo_cache: RepoCacheStore
    relation_resolution_cache: RelationResolutionCacheStore | None
    discovery_client: object
    github_client: object


def load_runtime_config(env: dict[str, str]) -> dict[str, str | int]:
    repo_discovery_recheck_days_raw = (
        env.get("REPO_DISCOVERY_NO_REPO_RECHECK_DAYS")
        or env.get("HF_EXACT_NO_REPO_RECHECK_DAYS")
    )
    return {
        "github_token": (env.get("GITHUB_TOKEN") or "").strip(),
        "huggingface_token": (env.get("HUGGINGFACE_TOKEN") or "").strip(),
        "alphaxiv_token": (env.get("ALPHAXIV_TOKEN") or "").strip(),
        "openalex_api_key": (env.get("OPENALEX_API_KEY") or "").strip(),
        "arxiv_relation_no_arxiv_recheck_days": _parse_positive_int(
            env.get("ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS"),
            default=ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS,
        ),
        "repo_discovery_no_repo_recheck_days": _parse_positive_int(
            repo_discovery_recheck_days_raw,
            default=REPO_DISCOVERY_NO_REPO_RECHECK_DAYS,
        ),
    }


def load_notion_config(env: dict[str, str]) -> dict[str, str | int]:
    runtime_config = load_runtime_config(env)
    notion_token = (env.get("NOTION_TOKEN") or "").strip()
    database_id = (env.get("DATABASE_ID") or "").strip()

    missing = []
    if not notion_token:
        missing.append("NOTION_TOKEN")
    if not database_id:
        missing.append("DATABASE_ID")

    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {joined}")

    return {
        "notion_token": notion_token,
        "github_token": runtime_config["github_token"],
        "database_id": database_id,
        "huggingface_token": runtime_config["huggingface_token"],
        "alphaxiv_token": runtime_config["alphaxiv_token"],
        "openalex_api_key": runtime_config["openalex_api_key"],
        "repo_discovery_no_repo_recheck_days": runtime_config["repo_discovery_no_repo_recheck_days"],
    }


def build_client(factory, session, **kwargs):
    parameters = inspect.signature(factory).parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        accepted_kwargs = kwargs
    else:
        accepted_names = {parameter.name for parameter in parameters}
        accepted_kwargs = {key: value for key, value in kwargs.items() if key in accepted_names}

    return factory(session, **accepted_kwargs)


@asynccontextmanager
async def open_runtime_clients(
    config: dict[str, str | int],
    *,
    session_factory,
    discovery_client_cls,
    github_client_cls,
    concurrent_limit: int,
    request_delay: float,
    github_min_interval: float | None = None,
    enable_relation_resolution_cache: bool = False,
):
    repo_cache = None
    relation_resolution_cache = None

    try:
        repo_cache = RepoCacheStore(REPO_CACHE_DB_PATH)
        if enable_relation_resolution_cache:
            try:
                relation_resolution_cache = RelationResolutionCacheStore(REPO_CACHE_DB_PATH)
            except sqlite3.Error:
                relation_resolution_cache = None

        async with session_factory(timeout=build_timeout()) as session:
            discovery_client = build_client(
                discovery_client_cls,
                session,
                huggingface_token=config["huggingface_token"],
                alphaxiv_token=config["alphaxiv_token"],
                repo_cache=repo_cache,
                repo_discovery_no_repo_recheck_days=config["repo_discovery_no_repo_recheck_days"],
                hf_exact_no_repo_recheck_days=config["repo_discovery_no_repo_recheck_days"],
                max_concurrent=concurrent_limit,
                min_interval=request_delay,
            )
            github_client = build_client(
                github_client_cls,
                session,
                github_token=config["github_token"],
                max_concurrent=concurrent_limit,
                min_interval=github_min_interval if github_min_interval is not None else request_delay,
            )
            yield RuntimeClients(
                session=session,
                repo_cache=repo_cache,
                relation_resolution_cache=relation_resolution_cache,
                discovery_client=discovery_client,
                github_client=github_client,
            )
    finally:
        if relation_resolution_cache is not None:
            relation_resolution_cache.close()
        if repo_cache is not None:
            repo_cache.close()


def _parse_positive_int(raw_value, *, default: int) -> int:
    text = str(raw_value or "").strip()
    if not text:
        return default

    try:
        value = int(text)
    except ValueError:
        return default

    if value <= 0:
        return default
    return value
