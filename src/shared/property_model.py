from __future__ import annotations

from typing import Any

from src.core.record_model import (
    PropertyState,
    PropertyStatus,
    Record,
    RecordArtifacts,
    RecordContext,
    RecordFacts,
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
            name=record.name,
            url=record.url,
            github=record.github,
            stars=record.stars,
            created=record.created,
            about=record.about,
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
