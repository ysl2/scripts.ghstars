from __future__ import annotations

from typing import Any

from src.core.record_model import (
    PropertyState as CorePropertyState,
    PropertyStatus,
    Record,
    RecordArtifacts,
    RecordContext,
    RecordFacts,
)


class PropertyState(CorePropertyState):
    def __init__(
        self,
        value: Any | None,
        status: PropertyStatus,
        source: str | None = None,
        reason: str | None = None,
        *,
        trusted: bool = False,
    ) -> None:
        super().__init__(
            value=value,
            status=status,
            source=source,
            trusted=trusted,
            reason=reason,
        )

    @classmethod
    def present(
        cls,
        value: Any,
        source: str | None = None,
        *,
        trusted: bool = False,
    ) -> "PropertyState":
        return cls(value, PropertyStatus.PRESENT, source, trusted=trusted)

    @classmethod
    def resolved(
        cls,
        value: Any,
        source: str | None = None,
        *,
        trusted: bool = False,
    ) -> "PropertyState":
        return cls(value, PropertyStatus.RESOLVED, source, trusted=trusted)

    @classmethod
    def skipped(
        cls,
        reason: str,
        source: str | None = None,
        *,
        trusted: bool = False,
    ) -> "PropertyState":
        return cls(
            None,
            PropertyStatus.SKIPPED,
            source,
            reason,
            trusted=trusted,
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
            None,
            PropertyStatus.BLOCKED,
            source,
            reason,
            trusted=trusted,
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
            None,
            PropertyStatus.FAILED,
            source,
            reason,
            trusted=trusted,
        )


def _wrap_core_state(state: CorePropertyState) -> PropertyState:
    return PropertyState(
        state.value,
        state.status,
        state.source,
        state.reason,
        trusted=state.trusted,
    )


class RecordState(Record):
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
        provenance: str = "source",
    ) -> "RecordState":
        record = Record.from_source(
            name=name,
            url=url,
            github=github,
            stars=stars,
            created=created,
            about=about,
            source=provenance,
        )
        return cls(
            name=_wrap_core_state(record.name),
            url=_wrap_core_state(record.url),
            github=_wrap_core_state(record.github),
            stars=_wrap_core_state(record.stars),
            created=_wrap_core_state(record.created),
            about=_wrap_core_state(record.about),
            facts=record.facts,
            artifacts=record.artifacts,
            context=record.context,
        )


__all__ = [
    "PropertyState",
    "PropertyStatus",
    "Record",
    "RecordState",
    "RecordFacts",
    "RecordArtifacts",
    "RecordContext",
]
