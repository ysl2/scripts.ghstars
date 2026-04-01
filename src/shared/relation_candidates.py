from dataclasses import dataclass


@dataclass(frozen=True)
class RelatedWorkCandidate:
    title: str
    direct_arxiv_url: str | None
    doi_url: str | None
    landing_page_url: str | None
    source_url: str

    @property
    def openalex_url(self) -> str:
        return self.source_url
