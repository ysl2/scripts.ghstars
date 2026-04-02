from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Optional


class PropertyStatus(Enum):
    PRESENT = auto()
    RESOLVED = auto()
    SKIPPED = auto()
    BLOCKED = auto()
    FAILED = auto()


@dataclass(frozen=True)
class PropertyState:
    value: Optional[Any]
    status: PropertyStatus
    source: Optional[str] = None
    reason: Optional[str] = None

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
        if self.status == PropertyStatus.BLOCKED:
            if self.value is not None:
                raise ValueError("BLOCKED state cannot carry a value")

    @classmethod
    def present(cls, value: Any, source: Optional[str] = None) -> "PropertyState":
        return cls(value=value, status=PropertyStatus.PRESENT, source=source)

    @classmethod
    def resolved(cls, value: Any, source: Optional[str] = None) -> "PropertyState":
        return cls(value=value, status=PropertyStatus.RESOLVED, source=source)

    @classmethod
    def skipped(cls, reason: str, source: Optional[str] = None) -> "PropertyState":
        return cls(value=None, status=PropertyStatus.SKIPPED, source=source, reason=reason)

    @classmethod
    def blocked(cls, reason: Optional[str] = None, source: Optional[str] = None) -> "PropertyState":
        return cls(value=None, status=PropertyStatus.BLOCKED, source=source, reason=reason)

    @classmethod
    def failed(cls, reason: str, source: Optional[str] = None) -> "PropertyState":
        return cls(value=None, status=PropertyStatus.FAILED, source=source, reason=reason)


@dataclass(frozen=True)
class RecordState:
    name: PropertyState
    url: PropertyState
    github: PropertyState
    stars: PropertyState
    created: PropertyState
    about: PropertyState

    @classmethod
    def from_source(
        cls,
        *,
        name: Optional[Any] = None,
        url: Optional[Any] = None,
        github: Optional[Any] = None,
        stars: Optional[Any] = None,
        created: Optional[Any] = None,
        about: Optional[Any] = None,
    ) -> "RecordState":
        def seed(field_name: str, value: Optional[Any]) -> PropertyState:
            if value is None or (isinstance(value, str) and value == ""):
                return PropertyState.blocked(
                    reason=f"{field_name} missing from source",
                    source=field_name,
                )
            return PropertyState.present(value, source=field_name)

        return cls(
            name=seed("name", name),
            url=seed("url", url),
            github=seed("github", github),
            stars=seed("stars", stars),
            created=seed("created", created),
            about=seed("about", about),
        )
