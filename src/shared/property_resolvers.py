from dataclasses import dataclass
from types import SimpleNamespace

from src.shared.arxiv_url_resolution import resolve_arxiv_url
from src.shared.discovery import resolve_github_url
from src.shared.github import extract_owner_repo, normalize_github_url


@dataclass(frozen=True)
class GithubAcquisitionResult:
    github_url: str | None
    github_source: str | None
    normalized_url: str | None
    canonical_arxiv_url: str | None
    reason: str | None


@dataclass(frozen=True)
class RepoMetadataResolutionResult:
    github_url: str
    stars: int | None
    created: str | None
    about: str | None
    reason: str | None


async def acquire_github_property(
    *,
    existing_github_url: str | None,
    raw_url: str | None,
    name: str | None,
    discovery_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    relation_resolution_cache=None,
    allow_title_search: bool = True,
    allow_github_discovery: bool = True,
    trust_existing_github: bool = False,
    precomputed_normalized_url: str | None = None,
    precomputed_canonical_arxiv_url: str | None = None,
    url_resolution_authoritative: bool = False,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
) -> GithubAcquisitionResult:
    title = (name or "").strip()
    raw_url_value = (raw_url or "").strip()
    existing_value = (existing_github_url or "").strip()

    skip_url_resolution = bool(existing_value) and (trust_existing_github or not raw_url_value)
    if url_resolution_authoritative:
        normalized_url = precomputed_normalized_url or raw_url_value or None
        canonical_arxiv_url = precomputed_canonical_arxiv_url
    elif skip_url_resolution:
        normalized_url = None
        canonical_arxiv_url = None
    else:
        url_resolution = await resolve_arxiv_url(
            title,
            raw_url_value,
            arxiv_client=arxiv_client,
            semanticscholar_graph_client=semanticscholar_graph_client,
            crossref_client=crossref_client,
            datacite_client=datacite_client,
            discovery_client=discovery_client,
            relation_resolution_cache=relation_resolution_cache,
            arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
            allow_title_search=allow_title_search,
        )
        normalized_url = url_resolution.resolved_url
        canonical_arxiv_url = url_resolution.canonical_arxiv_url

    if existing_value:
        if not extract_owner_repo(existing_value):
            return GithubAcquisitionResult(
                github_url=existing_value,
                github_source="existing",
                normalized_url=normalized_url,
                canonical_arxiv_url=canonical_arxiv_url,
                reason="Existing Github URL is not a valid GitHub repository",
            )
        return GithubAcquisitionResult(
            github_url=existing_value,
            github_source="existing",
            normalized_url=normalized_url,
            canonical_arxiv_url=canonical_arxiv_url,
            reason=None,
        )

    if canonical_arxiv_url is None:
        return GithubAcquisitionResult(
            github_url=None,
            github_source=None,
            normalized_url=normalized_url,
            canonical_arxiv_url=None,
            reason="No valid arXiv URL found",
        )

    github_url = None
    if allow_github_discovery:
        github_url = await _resolve_github(title, canonical_arxiv_url, discovery_client)

    if not github_url:
        return GithubAcquisitionResult(
            github_url=None,
            github_source=None,
            normalized_url=normalized_url,
            canonical_arxiv_url=canonical_arxiv_url,
            reason="No Github URL found from discovery",
        )

    normalized_github = normalize_github_url(github_url)
    if not normalized_github:
        return GithubAcquisitionResult(
            github_url=github_url,
            github_source="discovered",
            normalized_url=normalized_url,
            canonical_arxiv_url=canonical_arxiv_url,
            reason="Discovered URL is not a valid GitHub repository",
        )

    return GithubAcquisitionResult(
        github_url=normalized_github,
        github_source="discovered",
        normalized_url=normalized_url,
        canonical_arxiv_url=canonical_arxiv_url,
        reason=None,
    )


async def resolve_repo_metadata_properties(
    *,
    github_url: str,
    github_client,
    repo_metadata_cache=None,
) -> RepoMetadataResolutionResult:
    owner_repo = extract_owner_repo(github_url)
    if not owner_repo:
        return RepoMetadataResolutionResult(
            github_url=github_url,
            stars=None,
            created=None,
            about=None,
            reason="GitHub URL is not a valid GitHub repository",
        )

    metadata_getter = getattr(github_client, "get_repo_metadata", None)
    if callable(metadata_getter):
        metadata, error = await metadata_getter(*owner_repo)
        if error is not None:
            return RepoMetadataResolutionResult(
                github_url=github_url,
                stars=None,
                created=None,
                about=None,
                reason=error,
            )
        if metadata is None:
            return RepoMetadataResolutionResult(
                github_url=github_url,
                stars=None,
                created=None,
                about=None,
                reason="GitHub client returned no repo metadata",
            )
        stars = getattr(metadata, "stars", None)
        created = getattr(metadata, "created", None)
        about = getattr(metadata, "about", None)
        if created is None and repo_metadata_cache is not None:
            try:
                entry = repo_metadata_cache.get(github_url)
            except Exception:
                entry = None
            if entry is not None:
                created = entry.created
        return RepoMetadataResolutionResult(
            github_url=github_url,
            stars=stars,
            created=created,
            about=about,
            reason=None,
        )

    star_getter = getattr(github_client, "get_star_count", None)
    if callable(star_getter):
        stars, error = await star_getter(*owner_repo)
        return RepoMetadataResolutionResult(
            github_url=github_url,
            stars=None if error is not None else stars,
            created=None,
            about=None,
            reason=error,
        )

    return RepoMetadataResolutionResult(
        github_url=github_url,
        stars=None,
        created=None,
        about=None,
        reason="GitHub client does not support repo metadata lookup",
    )


async def _resolve_github(name: str, url: str, discovery_client) -> str | None:
    if discovery_client is None:
        return None
    seed = SimpleNamespace(name=name, url=url)
    resolver = getattr(discovery_client, "resolve_github_url", None)
    if callable(resolver):
        return await resolver(seed)
    return await resolve_github_url(seed, discovery_client)
