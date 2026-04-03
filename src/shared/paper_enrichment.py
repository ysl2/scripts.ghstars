from dataclasses import dataclass

from src.core.paper_export_sync import sync_paper_record
from src.core.record_model import Record, RecordFacts


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
    ).with_supporting_state(
        facts=RecordFacts(
            normalized_url=request.precomputed_normalized_url,
            canonical_arxiv_url=request.precomputed_canonical_arxiv_url,
            url_resolution_authoritative=request.url_resolution_authoritative,
        )
    )
    synced = await sync_paper_record(
        record,
        allow_title_search=request.allow_title_search,
        allow_github_discovery=request.allow_github_discovery,
        trust_existing_github=request.trust_existing_github,
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
    updated = synced.record

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
        reason=synced.reason,
    )
