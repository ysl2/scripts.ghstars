from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from types import SimpleNamespace

from src.core.record_model import PropertyState, Record
from src.shared.arxiv_url_resolution import resolve_arxiv_url
from src.shared.discovery import resolve_github_url
from src.shared.github import extract_owner_repo, normalize_github_url


@dataclass(frozen=True)
class GithubAcquisition:
    github_url: str | None
    github_source: str | None
    normalized_url: str | None
    canonical_arxiv_url: str | None
    reason: str | None


@dataclass(frozen=True)
class RepoMetadataResolution:
    github_url: str
    stars: int | None
    created: str | None
    about: str | None
    reason: str | None


class PropertyPolicyService:
    def should_refresh_repo_metadata(self, record: Record) -> bool:
        return not (
            record.github.trusted
            and record.stars.trusted
            and record.created.trusted
            and record.about.trusted
        )

    def should_backfill_created(self, record: Record) -> bool:
        return record.created.value is None


class RecordSyncService:
    def __init__(
        self,
        *,
        discovery_client=None,
        github_client=None,
        arxiv_client=None,
        semanticscholar_graph_client=None,
        crossref_client=None,
        datacite_client=None,
        relation_resolution_cache=None,
        repo_metadata_cache=None,
        arxiv_relation_no_arxiv_recheck_days: int = 30,
        policy: PropertyPolicyService | None = None,
    ) -> None:
        self.discovery_client = discovery_client
        self.github_client = github_client
        self.arxiv_client = arxiv_client
        self.semanticscholar_graph_client = semanticscholar_graph_client
        self.crossref_client = crossref_client
        self.datacite_client = datacite_client
        self.relation_resolution_cache = relation_resolution_cache
        self.repo_metadata_cache = repo_metadata_cache
        if self.repo_metadata_cache is None and github_client is not None:
            self.repo_metadata_cache = getattr(github_client, "repo_metadata_cache", None)
        self.arxiv_relation_no_arxiv_recheck_days = arxiv_relation_no_arxiv_recheck_days
        self.policy = policy or PropertyPolicyService()

    async def sync(
        self,
        record: Record,
        *,
        allow_title_search: bool,
        allow_github_discovery: bool,
        trust_existing_github: bool = False,
        precomputed_normalized_url: str | None = None,
        precomputed_canonical_arxiv_url: str | None = None,
        url_resolution_authoritative: bool = False,
        before_repo_metadata: Callable[[Record], Awaitable[None]] | None = None,
    ) -> Record:
        acquisition = await self.acquire_github(
            record,
            allow_title_search=allow_title_search,
            allow_github_discovery=allow_github_discovery,
            trust_existing_github=trust_existing_github,
            precomputed_normalized_url=precomputed_normalized_url,
            precomputed_canonical_arxiv_url=precomputed_canonical_arxiv_url,
            url_resolution_authoritative=url_resolution_authoritative,
        )

        record = record.with_supporting_state(
            facts=replace(
                record.facts,
                canonical_arxiv_url=acquisition.canonical_arxiv_url,
                normalized_url=acquisition.normalized_url,
                github_source=acquisition.github_source,
            )
        )

        if acquisition.github_url and acquisition.github_source != "existing":
            record = record.with_property(
                "github",
                PropertyState.resolved(
                    acquisition.github_url,
                    source=acquisition.github_source or "github_acquisition",
                    trusted=record.github.trusted,
                ),
            )

        if acquisition.reason is not None:
            return self._with_reason(record, acquisition.reason, source=acquisition.github_source or "github_acquisition")

        if before_repo_metadata is not None:
            await before_repo_metadata(record)

        github_url = acquisition.github_url
        if not github_url or not self.policy.should_refresh_repo_metadata(record):
            return record

        metadata = await self.resolve_repo_metadata(github_url)
        if metadata.reason is not None:
            return self._with_reason(record, metadata.reason, source="github_api")

        if metadata.stars is not None:
            record = record.with_property("stars", PropertyState.resolved(metadata.stars, source="github_api"))
        if metadata.created is not None and self.policy.should_backfill_created(record):
            record = record.with_property("created", PropertyState.resolved(metadata.created, source="github_api"))
        if metadata.about is not None:
            record = record.with_property("about", PropertyState.resolved(metadata.about, source="github_api"))
        return record

    async def acquire_github(
        self,
        record: Record,
        *,
        allow_title_search: bool,
        allow_github_discovery: bool,
        trust_existing_github: bool = False,
        precomputed_normalized_url: str | None = None,
        precomputed_canonical_arxiv_url: str | None = None,
        url_resolution_authoritative: bool = False,
    ) -> GithubAcquisition:
        title = self._string_value(record.name)
        raw_url_value = self._string_value(record.url)
        existing_value = self._string_value(record.github)

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
                arxiv_client=self.arxiv_client,
                semanticscholar_graph_client=self.semanticscholar_graph_client,
                crossref_client=self.crossref_client,
                datacite_client=self.datacite_client,
                discovery_client=self.discovery_client,
                relation_resolution_cache=self.relation_resolution_cache,
                arxiv_relation_no_arxiv_recheck_days=self.arxiv_relation_no_arxiv_recheck_days,
                allow_title_search=allow_title_search,
            )
            normalized_url = url_resolution.resolved_url
            canonical_arxiv_url = url_resolution.canonical_arxiv_url

        if existing_value:
            if not extract_owner_repo(existing_value):
                return GithubAcquisition(
                    github_url=existing_value,
                    github_source="existing",
                    normalized_url=normalized_url,
                    canonical_arxiv_url=canonical_arxiv_url,
                    reason="Existing Github URL is not a valid GitHub repository",
                )
            return GithubAcquisition(
                github_url=existing_value,
                github_source="existing",
                normalized_url=normalized_url,
                canonical_arxiv_url=canonical_arxiv_url,
                reason=None,
            )

        if canonical_arxiv_url is None:
            return GithubAcquisition(
                github_url=None,
                github_source=None,
                normalized_url=normalized_url,
                canonical_arxiv_url=None,
                reason="No valid arXiv URL found",
            )

        github_url = None
        if allow_github_discovery:
            github_url = await self._resolve_github(title, canonical_arxiv_url)

        if not github_url:
            return GithubAcquisition(
                github_url=None,
                github_source=None,
                normalized_url=normalized_url,
                canonical_arxiv_url=canonical_arxiv_url,
                reason="No Github URL found from discovery",
            )

        normalized_github = normalize_github_url(github_url)
        if not normalized_github:
            return GithubAcquisition(
                github_url=github_url,
                github_source="discovered",
                normalized_url=normalized_url,
                canonical_arxiv_url=canonical_arxiv_url,
                reason="Discovered URL is not a valid GitHub repository",
            )

        return GithubAcquisition(
            github_url=normalized_github,
            github_source="discovered",
            normalized_url=normalized_url,
            canonical_arxiv_url=canonical_arxiv_url,
            reason=None,
        )

    async def resolve_repo_metadata(self, github_url: str) -> RepoMetadataResolution:
        owner_repo = extract_owner_repo(github_url)
        if not owner_repo:
            return RepoMetadataResolution(
                github_url=github_url,
                stars=None,
                created=None,
                about=None,
                reason="GitHub URL is not a valid GitHub repository",
            )

        metadata_getter = getattr(self.github_client, "get_repo_metadata", None)
        if callable(metadata_getter):
            metadata, error = await metadata_getter(*owner_repo)
            if error is not None:
                return RepoMetadataResolution(
                    github_url=github_url,
                    stars=None,
                    created=None,
                    about=None,
                    reason=error,
                )
            if metadata is None:
                return RepoMetadataResolution(
                    github_url=github_url,
                    stars=None,
                    created=None,
                    about=None,
                    reason="GitHub client returned no repo metadata",
                )

            stars = getattr(metadata, "stars", None)
            created = getattr(metadata, "created", None)
            about = getattr(metadata, "about", None)
            if created is None and self.repo_metadata_cache is not None:
                try:
                    entry = self.repo_metadata_cache.get(github_url)
                except Exception:
                    entry = None
                if entry is not None:
                    created = entry.created

            return RepoMetadataResolution(
                github_url=github_url,
                stars=stars,
                created=created,
                about=about,
                reason=None,
            )

        star_getter = getattr(self.github_client, "get_star_count", None)
        if callable(star_getter):
            stars, error = await star_getter(*owner_repo)
            return RepoMetadataResolution(
                github_url=github_url,
                stars=None if error is not None else stars,
                created=None,
                about=None,
                reason=error,
            )

        return RepoMetadataResolution(
            github_url=github_url,
            stars=None,
            created=None,
            about=None,
            reason="GitHub client does not support repo metadata lookup",
        )

    async def _resolve_github(self, name: str, canonical_arxiv_url: str) -> str | None:
        if self.discovery_client is None:
            return None

        seed = SimpleNamespace(name=name, url=canonical_arxiv_url)
        resolver = getattr(self.discovery_client, "resolve_github_url", None)
        if callable(resolver):
            return await resolver(seed)
        return await resolve_github_url(seed, self.discovery_client)

    def _with_reason(self, record: Record, reason: str | None, *, source: str | None) -> Record:
        if record.github.value is None:
            return record.with_property("github", PropertyState.blocked(reason, source=source))
        if record.stars.value is None:
            return record.with_property("stars", PropertyState.blocked(reason, source=source))
        if record.created.value is None:
            return record.with_property("created", PropertyState.blocked(reason, source=source))
        if record.about.value is None:
            return record.with_property("about", PropertyState.blocked(reason, source=source))
        return record

    @staticmethod
    def _string_value(state: PropertyState) -> str:
        if isinstance(state.value, str):
            return state.value.strip()
        return ""
