from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

CORE_PROPERTY_NAMES = (
    "name",
    "url",
    "github",
    "stars",
    "created",
    "about",
)
CORE_PROPERTY_NAME_SET = frozenset(CORE_PROPERTY_NAMES)


class PropertyStatus(str, Enum):
    PRESENT = "present"
    RESOLVED = "resolved"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class PropertyState:
    value: Any | None
    status: PropertyStatus
    source: str | None = None
    trusted: bool = False
    reason: str | None = None

    def __init__(
        self,
        value: Any | None,
        status: PropertyStatus,
        source: str | None = None,
        reason: str | None = None,
        *,
        trusted: bool = False,
    ) -> None:
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "trusted", trusted)
        object.__setattr__(self, "reason", reason)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.status in (PropertyStatus.PRESENT, PropertyStatus.RESOLVED):
            if self.value is None:
                raise ValueError(f"{self.status.name} state requires a value")
            if self.reason is not None:
                raise ValueError(f"{self.status.name} state cannot carry a reason")
        if self.status in (PropertyStatus.SKIPPED, PropertyStatus.FAILED):
            if not self.reason:
                raise ValueError(f"{self.status.name} state requires a reason")
            if self.value is not None:
                raise ValueError(f"{self.status.name} state cannot carry a value")
        if self.status is PropertyStatus.BLOCKED and self.value is not None:
            raise ValueError("BLOCKED state cannot carry a value")

    @classmethod
    def present(
        cls,
        value: Any,
        source: str | None = None,
        *,
        trusted: bool = False,
    ) -> "PropertyState":
        return cls(
            value=value,
            status=PropertyStatus.PRESENT,
            source=source,
            trusted=trusted,
        )

    @classmethod
    def resolved(
        cls,
        value: Any,
        source: str | None = None,
        *,
        trusted: bool = False,
    ) -> "PropertyState":
        return cls(
            value=value,
            status=PropertyStatus.RESOLVED,
            source=source,
            trusted=trusted,
        )

    @classmethod
    def skipped(
        cls,
        reason: str,
        source: str | None = None,
        *,
        trusted: bool = False,
    ) -> "PropertyState":
        return cls(
            value=None,
            status=PropertyStatus.SKIPPED,
            source=source,
            trusted=trusted,
            reason=reason,
        )

    @classmethod
    def blocked(
        cls,
        reason: str | None = None,
        source: str | None = None,
        *,
        trusted: bool = False,
    ) -> "PropertyState":
        return cls(
            value=None,
            status=PropertyStatus.BLOCKED,
            source=source,
            trusted=trusted,
            reason=reason,
        )

    @classmethod
    def failed(
        cls,
        reason: str,
        source: str | None = None,
        *,
        trusted: bool = False,
    ) -> "PropertyState":
        return cls(
            value=None,
            status=PropertyStatus.FAILED,
            source=source,
            trusted=trusted,
            reason=reason,
        )


@dataclass(frozen=True)
class RecordFacts:
    canonical_arxiv_url: str | None = None
    normalized_url: str | None = None
    github_source: str | None = None
    repo_metadata_error: str | None = None
    url_resolution_authoritative: bool = False


@dataclass(frozen=True)
class RecordArtifacts:
    overview_path: str | None = None
    abs_path: str | None = None


@dataclass(frozen=True)
class RecordContext:
    csv_row_index: int | None = None
    notion_page_id: str | None = None


@dataclass(frozen=True)
class Record:
    name: PropertyState
    url: PropertyState
    github: PropertyState
    stars: PropertyState
    created: PropertyState
    about: PropertyState
    facts: RecordFacts = field(default_factory=RecordFacts)
    artifacts: RecordArtifacts = field(default_factory=RecordArtifacts)
    context: RecordContext = field(default_factory=RecordContext)

    @classmethod
    def from_source(
        cls,
        *,
        name: Any | None = None,
        url: Any | None = None,
        github: Any | None = None,
        stars: Any | None = None,
        created: Any | None = None,
        about: Any | None = None,
        source: str,
        trusted_fields: set[str] | None = None,
    ) -> "Record":
        trusted_fields = trusted_fields or set()

        def seed(field_name: str, value: Any | None) -> PropertyState:
            if value is None or (isinstance(value, str) and not value.strip()):
                return PropertyState.blocked(
                    f"{field_name} missing from source",
                    source=source,
                )
            return PropertyState.present(
                value,
                source=source,
                trusted=field_name in trusted_fields,
            )

        return cls(
            name=seed("name", name),
            url=seed("url", url),
            github=seed("github", github),
            stars=seed("stars", stars),
            created=seed("created", created),
            about=seed("about", about),
        )

    def with_property(self, property_name: str, state: PropertyState) -> "Record":
        if property_name not in CORE_PROPERTY_NAME_SET:
            allowed = ", ".join(CORE_PROPERTY_NAMES)
            raise ValueError(
                f"with_property only supports core property fields: {allowed}"
            )
        return replace(self, **{property_name: state})

    def with_supporting_state(
        self,
        *,
        facts: RecordFacts | None = None,
        artifacts: RecordArtifacts | None = None,
        context: RecordContext | None = None,
    ) -> "Record":
        return replace(
            self,
            facts=self.facts if facts is None else facts,
            artifacts=self.artifacts if artifacts is None else artifacts,
            context=self.context if context is None else context,
        )
