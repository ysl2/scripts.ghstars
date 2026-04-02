from dataclasses import dataclass

from src.core.record_model import Record
from src.core.record_sync import RecordSyncService
from src.shared.paper_identity import normalize_arxiv_url


@dataclass(frozen=True)
class PaperEnrichmentRequest:
    title: str
    raw_url: str
    existing_github_url: str | None
    allow_title_search: bool
    allow_github_discovery: bool
    trust_existing_github: bool = False
    precomputed_normalized_url: str | None = None
    precomputed_canonical_arxiv_url: str | None = None
    url_resolution_authoritative: bool = False


@dataclass(frozen=True)
class PaperEnrichmentResult:
    title: str
    raw_url: str
    normalized_url: str | None
    canonical_arxiv_url: str | None
    github_url: str | None
    github_source: str | None
    stars: int | None
    created: str | None
    about: str | None
    reason: str | None


async def process_single_paper(
    request: PaperEnrichmentRequest,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> PaperEnrichmentResult:
    title = (request.title or "").strip()
    raw_url = (request.raw_url or "").strip()
    record = Record.from_source(
        name=title,
        url=raw_url,
        github=request.existing_github_url,
        source="paper_enrichment",
        trusted_fields={"github"} if request.trust_existing_github else set(),
    )
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
    updated = await service.sync(
        record,
        allow_title_search=request.allow_title_search,
        allow_github_discovery=request.allow_github_discovery,
        trust_existing_github=request.trust_existing_github,
        precomputed_normalized_url=request.precomputed_normalized_url,
        precomputed_canonical_arxiv_url=request.precomputed_canonical_arxiv_url,
        url_resolution_authoritative=request.url_resolution_authoritative,
        before_repo_metadata=lambda synced_record: _warm_content_cache(
            synced_record.facts.canonical_arxiv_url,
            content_cache,
        ),
    )

    return PaperEnrichmentResult(
        title=title,
        raw_url=raw_url,
        normalized_url=updated.facts.normalized_url,
        canonical_arxiv_url=updated.facts.canonical_arxiv_url,
        github_url=updated.github.value if isinstance(updated.github.value, str) else None,
        github_source=updated.facts.github_source,
        stars=updated.stars.value if isinstance(updated.stars.value, int) else None,
        created=updated.created.value if isinstance(updated.created.value, str) else None,
        about=updated.about.value if isinstance(updated.about.value, str) else None,
        reason=_first_reason(updated.github, updated.stars, updated.created, updated.about),
    )


async def _warm_content_cache(normalized_url: str | None, content_cache) -> None:
    arxiv_url = normalize_arxiv_url(normalized_url or "")
    if arxiv_url is None or content_cache is None:
        return

    warmer = getattr(content_cache, "ensure_local_content_cache", None)
    if not callable(warmer):
        return

    try:
        await warmer(arxiv_url)
    except Exception:
        return


def _first_reason(*states) -> str | None:
    for state in states:
        if state.reason is None:
            continue
        if state.source == "paper_enrichment":
            continue
        return state.reason
    return None
