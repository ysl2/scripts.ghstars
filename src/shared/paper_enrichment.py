from dataclasses import dataclass
from typing import cast

from src.shared.paper_identity import normalize_arxiv_url
from src.shared.property_resolvers import acquire_github_property, resolve_repo_metadata_properties


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
    acquisition = await acquire_github_property(
        existing_github_url=request.existing_github_url,
        raw_url=raw_url,
        name=title,
        discovery_client=discovery_client,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        allow_title_search=request.allow_title_search,
        allow_github_discovery=request.allow_github_discovery,
        trust_existing_github=request.trust_existing_github,
        precomputed_normalized_url=request.precomputed_normalized_url,
        precomputed_canonical_arxiv_url=request.precomputed_canonical_arxiv_url,
        url_resolution_authoritative=request.url_resolution_authoritative,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    if acquisition.reason is not None:
        return PaperEnrichmentResult(
            title=title,
            raw_url=raw_url,
            normalized_url=acquisition.normalized_url,
            canonical_arxiv_url=acquisition.canonical_arxiv_url,
            github_url=acquisition.github_url,
            github_source=acquisition.github_source,
            stars=None,
            created=None,
            about=None,
            reason=acquisition.reason,
        )

    github_url = cast(str, acquisition.github_url)

    await _warm_content_cache(acquisition.canonical_arxiv_url, content_cache)

    metadata = await resolve_repo_metadata_properties(
        github_url=github_url,
        github_client=github_client,
        repo_metadata_cache=getattr(github_client, "repo_metadata_cache", None),
    )

    return PaperEnrichmentResult(
        title=title,
        raw_url=raw_url,
        normalized_url=acquisition.normalized_url,
        canonical_arxiv_url=acquisition.canonical_arxiv_url,
        github_url=metadata.github_url,
        github_source=acquisition.github_source,
        stars=metadata.stars,
        created=metadata.created,
        about=metadata.about,
        reason=metadata.reason,
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
