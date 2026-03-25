from dataclasses import dataclass
from types import SimpleNamespace

from src.shared.discovery import resolve_github_url
from src.shared.github import extract_owner_repo, normalize_github_url
from src.shared.paper_identity import normalize_arxiv_url, normalize_semanticscholar_paper_url


@dataclass(frozen=True)
class EnrichedPaper:
    name: str
    url: str
    github_url: str
    stars: int | None
    source: str | None
    reason: str | None


async def enrich_paper(
    *,
    name: str,
    url: str,
    discovery_client,
    github_client,
    existing_github: str | None = None,
) -> EnrichedPaper:
    normalized_url = normalize_arxiv_url(url) or normalize_semanticscholar_paper_url(url)
    if normalized_url is None:
        return EnrichedPaper(
            name=name,
            url=url or "",
            github_url=(existing_github or "").strip(),
            stars=None,
            source=None,
            reason="No valid arXiv URL found",
        )

    existing_value = (existing_github or "").strip()
    if existing_value:
        normalized_existing = normalize_github_url(existing_value)
        if not normalized_existing:
            return EnrichedPaper(
                name=name,
                url=normalized_url,
                github_url=existing_value,
                stars=None,
                source="existing",
                reason="Existing Github URL is not a valid GitHub repository",
            )
        github_url = normalized_existing
        source = "existing"
    else:
        github_url = await _resolve_github(name, normalized_url, discovery_client)
        if not github_url:
            return EnrichedPaper(
                name=name,
                url=normalized_url,
                github_url="",
                stars=None,
                source=None,
                reason="No Github URL found from discovery",
            )
        normalized_github = normalize_github_url(github_url)
        if not normalized_github:
            return EnrichedPaper(
                name=name,
                url=normalized_url,
                github_url=github_url,
                stars=None,
                source="discovered",
                reason="Discovered URL is not a valid GitHub repository",
            )
        github_url = normalized_github
        source = "discovered"

    owner_repo = extract_owner_repo(github_url)
    if not owner_repo:
        reason = "Existing Github URL is not a valid GitHub repository"
        if source == "discovered":
            reason = "Discovered URL is not a valid GitHub repository"
        return EnrichedPaper(
            name=name,
            url=normalized_url,
            github_url=github_url,
            stars=None,
            source=source,
            reason=reason,
        )

    stars, error = await github_client.get_star_count(*owner_repo)
    if error:
        return EnrichedPaper(
            name=name,
            url=normalized_url,
            github_url=github_url,
            stars=None,
            source=source,
            reason=error,
        )

    return EnrichedPaper(
        name=name,
        url=normalized_url,
        github_url=github_url,
        stars=stars,
        source=source,
        reason=None,
    )


async def _resolve_github(name: str, url: str, discovery_client) -> str | None:
    seed = SimpleNamespace(name=name, url=url)
    resolver = getattr(discovery_client, "resolve_github_url", None)
    if callable(resolver):
        return await resolver(seed)
    return await resolve_github_url(seed, discovery_client)
