from typing import Iterable

from src.shared.paper_identity import (
    extract_arxiv_id_from_single_paper_url,
    is_arxiv_hosted_url,
    is_single_arxiv_paper_url,
    normalize_doi_url,
    normalize_openalex_work_url,
    normalize_arxiv_url,
)


def ensure_rejected(urls: Iterable[str | None]) -> None:
    for url in urls:
        assert extract_arxiv_id_from_single_paper_url(url) is None
        assert not is_single_arxiv_paper_url(url)


def test_normalize_single_paper_abs_url():
    url = "https://arxiv.org/abs/2603.12345"
    assert is_single_arxiv_paper_url(url)
    assert normalize_arxiv_url(url) == "https://arxiv.org/abs/2603.12345"


def test_normalize_single_paper_pdf_url():
    url = "https://arxiv.org/pdf/2603.12345.pdf"
    assert is_single_arxiv_paper_url(url)
    assert normalize_arxiv_url(url) == "https://arxiv.org/abs/2603.12345"


def test_collection_urls_are_not_single_papers():
    ensure_rejected(
        [
            "https://arxiv.org/list/cs.CV/recent",
            "https://arxiv.org/search/?query=foo",
            "https://arxiv.org/catchup/2001-01/",
        ]
    )


def test_malformed_arxiv_urls_are_rejected():
    ensure_rejected(
        [
            "https://arxiv.org/abs/foo",
            "https://example.com/abs/2603.12345",
            "",
            None,
        ]
    )


def test_embedded_arxiv_path_on_non_arxiv_host_is_rejected():
    url = "https://example.com/archive/arxiv.org/abs/2603.12345"
    assert extract_arxiv_id_from_single_paper_url(url) is None
    assert not is_single_arxiv_paper_url(url)


def test_is_arxiv_hosted_url_detects_existing_abs_and_pdf_urls():
    assert is_arxiv_hosted_url("https://arxiv.org/abs/2603.12345v2")
    assert is_arxiv_hosted_url("https://arxiv.org/pdf/2603.12345v2.pdf")
    assert not is_arxiv_hosted_url("https://example.com/abs/2603.12345")


def test_normalize_doi_url_accepts_bare_and_prefixed_doi_values():
    assert normalize_doi_url("10.48550/arXiv.2312.03203") == "https://doi.org/10.48550/arXiv.2312.03203"
    assert normalize_doi_url("https://doi.org/10.1145/example") == "https://doi.org/10.1145/example"
    assert normalize_doi_url("http://dx.doi.org/10.1145/example") == "https://doi.org/10.1145/example"
    assert normalize_doi_url("https://example.com/not-a-doi") is None


def test_normalize_openalex_work_url_accepts_public_and_api_forms():
    assert normalize_openalex_work_url("https://openalex.org/W1234567890") == "https://openalex.org/W1234567890"
    assert (
        normalize_openalex_work_url("https://api.openalex.org/works/W1234567890")
        == "https://openalex.org/W1234567890"
    )
    assert normalize_openalex_work_url("https://example.com/W1234567890") is None
