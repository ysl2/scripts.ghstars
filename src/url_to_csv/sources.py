from enum import StrEnum

from src.url_to_csv.arxivxplorer import is_supported_arxivxplorer_url
from src.url_to_csv.arxiv_org import is_supported_arxiv_org_url
from src.url_to_csv.huggingface_papers import is_supported_huggingface_papers_url
from src.url_to_csv.semanticscholar import is_supported_semanticscholar_url


class UrlSource(StrEnum):
    ARXIVXPLORER = "arxivxplorer"
    ARXIV_ORG = "arxiv_org"
    HUGGINGFACE_PAPERS = "huggingface_papers"
    SEMANTIC_SCHOLAR = "semantic_scholar"


def detect_url_source(raw_url: str) -> UrlSource | None:
    if is_supported_arxivxplorer_url(raw_url):
        return UrlSource.ARXIVXPLORER
    if is_supported_arxiv_org_url(raw_url):
        return UrlSource.ARXIV_ORG
    if is_supported_huggingface_papers_url(raw_url):
        return UrlSource.HUGGINGFACE_PAPERS
    if is_supported_semanticscholar_url(raw_url):
        return UrlSource.SEMANTIC_SCHOLAR
    return None


def is_supported_url_source(raw_url: str) -> bool:
    return detect_url_source(raw_url) is not None
