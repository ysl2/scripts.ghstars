from dataclasses import dataclass


@dataclass(frozen=True, init=False)
class RelatedWorkCandidate:
    title: str
    direct_arxiv_url: str | None
    doi_url: str | None
    landing_page_url: str | None
    source_url: str

    def __init__(
        self,
        title: str,
        direct_arxiv_url: str | None,
        doi_url: str | None,
        landing_page_url: str | None,
        source_url: str | None = None,
        *,
        openalex_url: str | None = None,
    ):
        resolved_source_url = source_url if source_url is not None else openalex_url
        if resolved_source_url is None:
            raise TypeError("RelatedWorkCandidate requires source_url")

        object.__setattr__(self, "title", title)
        object.__setattr__(self, "direct_arxiv_url", direct_arxiv_url)
        object.__setattr__(self, "doi_url", doi_url)
        object.__setattr__(self, "landing_page_url", landing_page_url)
        object.__setattr__(self, "source_url", resolved_source_url)

    @property
    def openalex_url(self) -> str:
        return self.source_url
