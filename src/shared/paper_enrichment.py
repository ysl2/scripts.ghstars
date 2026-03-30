from dataclasses import dataclass
from types import SimpleNamespace

from src.shared.arxiv_url_resolution import resolve_arxiv_url
from src.shared.discovery import resolve_github_url
from src.shared.github import extract_owner_repo, normalize_github_url
from src.shared.paper_identity import normalize_arxiv_url, normalize_semanticscholar_paper_url


@dataclass(frozen=True)
class PaperEnrichmentRequest:
    title: str
    raw_url: str
    existing_github_url: str | None
    allow_title_search: bool
    allow_github_discovery: bool


@dataclass(frozen=True)
class PaperEnrichmentResult:
    title: str
    raw_url: str
    normalized_url: str | None
    canonical_arxiv_url: str | None
    github_url: str | None
    github_source: str | None
    stars: int | None
    reason: str | None


async def process_single_paper(
    request: PaperEnrichmentRequest,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    openalex_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> PaperEnrichmentResult:
    title = (request.title or "").strip()
    raw_url = (request.raw_url or "").strip()

    url_resolution = await resolve_arxiv_url(
        title,
        raw_url,
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
        allow_title_search=request.allow_title_search,
    )
    normalized_url = url_resolution.resolved_url
    canonical_arxiv_url = url_resolution.canonical_arxiv_url

    github_url = None
    github_source = None
    existing_value = (request.existing_github_url or "").strip()
    if existing_value:
        github_source = "existing"
        github_url = existing_value
        if not extract_owner_repo(github_url):
            return PaperEnrichmentResult(
                title=title,
                raw_url=raw_url,
                normalized_url=normalized_url,
                canonical_arxiv_url=canonical_arxiv_url,
                github_url=existing_value,
                github_source=github_source,
                stars=None,
                reason="Existing Github URL is not a valid GitHub repository",
            )
    else:
        if canonical_arxiv_url is None:
            return PaperEnrichmentResult(
                title=title,
                raw_url=raw_url,
                normalized_url=normalized_url,
                canonical_arxiv_url=None,
                github_url=None,
                github_source=None,
                stars=None,
                reason="No valid arXiv URL found",
            )

        if request.allow_github_discovery:
            github_url = await _resolve_github(title, canonical_arxiv_url, discovery_client)

        if not github_url:
            return PaperEnrichmentResult(
                title=title,
                raw_url=raw_url,
                normalized_url=normalized_url,
                canonical_arxiv_url=canonical_arxiv_url,
                github_url=None,
                github_source=None,
                stars=None,
                reason="No Github URL found from discovery",
            )

        github_source = "discovered"
        normalized_github = normalize_github_url(github_url)
        if not normalized_github:
            return PaperEnrichmentResult(
                title=title,
                raw_url=raw_url,
                normalized_url=normalized_url,
                canonical_arxiv_url=canonical_arxiv_url,
                github_url=github_url,
                github_source=github_source,
                stars=None,
                reason="Discovered URL is not a valid GitHub repository",
            )
        github_url = normalized_github

    owner_repo = extract_owner_repo(github_url)
    if not owner_repo:
        reason = "Existing Github URL is not a valid GitHub repository"
        if github_source == "discovered":
            reason = "Discovered URL is not a valid GitHub repository"
        return PaperEnrichmentResult(
            title=title,
            raw_url=raw_url,
            normalized_url=normalized_url,
            canonical_arxiv_url=canonical_arxiv_url,
            github_url=github_url,
            github_source=github_source,
            stars=None,
            reason=reason,
        )

    await _warm_content_cache(canonical_arxiv_url, content_cache)

    stars, error = await github_client.get_star_count(*owner_repo)
    if error:
        return PaperEnrichmentResult(
            title=title,
            raw_url=raw_url,
            normalized_url=normalized_url,
            canonical_arxiv_url=canonical_arxiv_url,
            github_url=github_url,
            github_source=github_source,
            stars=None,
            reason=error,
        )

    return PaperEnrichmentResult(
        title=title,
        raw_url=raw_url,
        normalized_url=normalized_url,
        canonical_arxiv_url=canonical_arxiv_url,
        github_url=github_url,
        github_source=github_source,
        stars=stars,
        reason=None,
    )


async def _resolve_github(name: str, url: str, discovery_client) -> str | None:
    if discovery_client is None:
        return None
    seed = SimpleNamespace(name=name, url=url)
    resolver = getattr(discovery_client, "resolve_github_url", None)
    if callable(resolver):
        return await resolver(seed)
    return await resolve_github_url(seed, discovery_client)


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
