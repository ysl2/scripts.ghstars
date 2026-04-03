from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from src.core.input_adapters import PaperSeedInputAdapter
from src.core.record_model import PropertyState, Record
from src.core.record_sync import RecordSyncService


@dataclass(frozen=True)
class PaperSyncResult:
    record: Record
    reason: str | None


def _first_actionable_reason(*states, ignored_sources: Iterable[str] = ()) -> str | None:
    ignored = {source for source in ignored_sources if source}
    for state in states:
        if state.source in ignored:
            continue
        if state.reason is not None:
            return state.reason
    return None


def _build_content_warming_callback(content_cache) -> Callable[[Record], Awaitable[None]]:
    async def warm_content_cache(record: Record) -> None:
        canonical_arxiv_url = record.facts.canonical_arxiv_url
        if not canonical_arxiv_url or content_cache is None:
            return

        warmer = getattr(content_cache, "ensure_local_content_cache", None)
        if not callable(warmer):
            return

        try:
            await warmer(canonical_arxiv_url)
        except Exception:
            return

    return warm_content_cache


async def sync_paper_record(
    record: Record,
    *,
    allow_title_search,
    allow_github_discovery,
    trust_existing_github: bool = False,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> PaperSyncResult:
    service = RecordSyncService(
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    synced = await service.sync(
        record,
        allow_title_search=allow_title_search,
        allow_github_discovery=allow_github_discovery,
        trust_existing_github=trust_existing_github,
        precomputed_normalized_url=(
            record.facts.normalized_url if record.facts.url_resolution_authoritative else None
        ),
        precomputed_canonical_arxiv_url=record.facts.canonical_arxiv_url,
        url_resolution_authoritative=record.facts.url_resolution_authoritative,
        before_repo_metadata=_build_content_warming_callback(content_cache),
    )

    if synced.facts.normalized_url is not None:
        synced = synced.with_property(
            "url",
            PropertyState.resolved(
                synced.facts.normalized_url,
                source="url_resolution",
            ),
        )

    ignored_sources = {
        state.source
        for state in (record.github, record.stars, record.created, record.about)
        if state.source
    }

    return PaperSyncResult(
        record=synced,
        reason=_first_actionable_reason(
            synced.github,
            synced.stars,
            synced.created,
            synced.about,
            ignored_sources=ignored_sources,
        ),
    )


async def sync_paper_seed(seed, **kwargs) -> PaperSyncResult:
    record = PaperSeedInputAdapter().to_record(seed)
    return await sync_paper_record(
        record,
        allow_title_search=True,
        allow_github_discovery=True,
        **kwargs,
    )
