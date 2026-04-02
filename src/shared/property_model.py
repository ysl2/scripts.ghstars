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
    status: PropertyStatus
    value: Optional[Any] = None
    source: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def present(cls, value: Any, source: Optional[str] = None) -> "PropertyState":
        return cls(status=PropertyStatus.PRESENT, value=value, source=source)

    @classmethod
    def resolved(cls, value: Any, source: Optional[str] = None) -> "PropertyState":
        return cls(status=PropertyStatus.RESOLVED, value=value, source=source)

    @classmethod
    def skipped(cls, reason: str, source: Optional[str] = None) -> "PropertyState":
        return cls(status=PropertyStatus.SKIPPED, reason=reason, source=source)

    @classmethod
    def blocked(cls, source: Optional[str] = None) -> "PropertyState":
        return cls(status=PropertyStatus.BLOCKED, source=source)

    @classmethod
    def failed(cls, reason: str, source: Optional[str] = None) -> "PropertyState":
        return cls(status=PropertyStatus.FAILED, reason=reason, source=source)


@dataclass(frozen=True)
class RecordState:
    name: PropertyState
    url: PropertyState
    github: PropertyState
    stars: PropertyState
    created: PropertyState
    about: PropertyState

    @classmethod
    def from_source(cls, **kwargs: Any) -> "RecordState":
        def seed(field_name: str) -> PropertyState:
            if kwargs.get(field_name) is not None:
                return PropertyState.present(kwargs[field_name], source=field_name)
            return PropertyState.blocked(source=field_name)

        return cls(
            name=seed("name"),
            url=seed("url"),
            github=seed("github"),
            stars=seed("stars"),
            created=seed("created"),
            about=seed("about"),
        )
