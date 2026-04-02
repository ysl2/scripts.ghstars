from dataclasses import dataclass

from src.core.record_model import Record
from src.core.record_sync import RecordSyncService


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
    record = Record.from_source(
        name=name,
        url=raw_url,
        github=existing_github_url,
        source="property_resolvers",
        trusted_fields={"github"} if trust_existing_github else set(),
    )
    service = RecordSyncService(
        discovery_client=discovery_client,
        github_client=None,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
    acquisition = await service.acquire_github(
        record,
        allow_title_search=allow_title_search,
        allow_github_discovery=allow_github_discovery,
        trust_existing_github=trust_existing_github,
        precomputed_normalized_url=precomputed_normalized_url,
        precomputed_canonical_arxiv_url=precomputed_canonical_arxiv_url,
        url_resolution_authoritative=url_resolution_authoritative,
    )

    return GithubAcquisitionResult(
        github_url=acquisition.github_url,
        github_source=acquisition.github_source,
        normalized_url=acquisition.normalized_url,
        canonical_arxiv_url=acquisition.canonical_arxiv_url,
        reason=acquisition.reason,
    )


async def resolve_repo_metadata_properties(
    *,
    github_url: str,
    github_client,
    repo_metadata_cache=None,
) -> RepoMetadataResolutionResult:
    service = RecordSyncService(
        discovery_client=None,
        github_client=github_client,
        repo_metadata_cache=repo_metadata_cache,
    )
    metadata = await service.resolve_repo_metadata(github_url)
    return RepoMetadataResolutionResult(
        github_url=metadata.github_url,
        stars=metadata.stars,
        created=metadata.created,
        about=metadata.about,
        reason=metadata.reason,
    )
