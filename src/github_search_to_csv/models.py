from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SearchRequest:
    query: str
    sort: str = "stars"
    order: str = "desc"


@dataclass(frozen=True)
class RepositorySearchRow:
    github: str
    stars: int
    about: str
    created: str


@dataclass(frozen=True)
class SearchPartition:
    request: SearchRequest
    stars_min: int | None = None
    stars_max: int | None = None
    created_after: date | None = None
    created_before: date | None = None
