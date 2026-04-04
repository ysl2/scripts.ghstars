from __future__ import annotations

from src.core.input_adapters import PaperSeedInputAdapter
from src.core.record_model import Record
from src.core.record_sync_workflow import (
    RecordSyncPolicy,
    RecordSyncWorkflowResult,
    sync_record_with_policy,
)

PaperSyncResult = RecordSyncWorkflowResult


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
    return await sync_record_with_policy(
        record,
        policy=RecordSyncPolicy(
            allow_title_search=allow_title_search,
            allow_github_discovery=allow_github_discovery,
            trust_existing_github=trust_existing_github,
            apply_normalized_url=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )


async def sync_paper_seed(seed, **kwargs) -> PaperSyncResult:
    record = PaperSeedInputAdapter().to_record(seed)
    return await sync_paper_record(
        record,
        allow_title_search=True,
        allow_github_discovery=True,
        **kwargs,
    )
