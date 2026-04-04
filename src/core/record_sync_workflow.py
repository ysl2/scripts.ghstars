from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.core.record_model import PropertyState, Record
from src.core.record_sync import RecordSyncService
from src.shared.paper_identity import (
    build_arxiv_abs_url,
    extract_arxiv_id_from_single_paper_url,
)


@dataclass(frozen=True)
class RecordSyncPolicy:
    allow_title_search: bool
    allow_github_discovery: bool
    trust_existing_github: bool = False
    apply_normalized_url: bool = False


@dataclass(frozen=True)
class RecordSyncWorkflowResult:
    record: Record
    reason: str | None


def first_actionable_reason(original: Record, synced: Record) -> str | None:
    fields = ("github", "stars", "created", "about")
    for field in fields:
        original_state = getattr(original, field)
        synced_state = getattr(synced, field)
        if synced_state.reason is None:
            continue
        if synced_state == original_state:
            continue
        return synced_state.reason
    return synced.facts.repo_metadata_error


def build_content_warming_callback(content_cache) -> Callable[[Record], Awaitable[None]]:
    async def warm_content_cache(record: Record) -> None:
        arxiv_id = extract_arxiv_id_from_single_paper_url(
            record.facts.canonical_arxiv_url or ""
        )
        if not arxiv_id or content_cache is None:
            return

        warmer = getattr(content_cache, "ensure_local_content_cache", None)
        if not callable(warmer):
            return

        try:
            await warmer(build_arxiv_abs_url(arxiv_id))
        except Exception:
            return

    return warm_content_cache


async def sync_record_with_policy(
    record: Record,
    *,
    policy: RecordSyncPolicy,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> RecordSyncWorkflowResult:
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
        allow_title_search=policy.allow_title_search,
        allow_github_discovery=policy.allow_github_discovery,
        trust_existing_github=policy.trust_existing_github,
        precomputed_normalized_url=(
            record.facts.normalized_url
            if record.facts.url_resolution_authoritative
            else None
        ),
        precomputed_canonical_arxiv_url=record.facts.canonical_arxiv_url,
        url_resolution_authoritative=record.facts.url_resolution_authoritative,
        before_repo_metadata=build_content_warming_callback(content_cache),
    )

    if (
        policy.apply_normalized_url
        and synced.facts.normalized_url is not None
        and synced.facts.url_resolution_authoritative
    ):
        synced = synced.with_property(
            "url",
            PropertyState.resolved(
                synced.facts.normalized_url,
                source="url_resolution",
            ),
        )

    return RecordSyncWorkflowResult(
        record=synced,
        reason=first_actionable_reason(record, synced),
    )
