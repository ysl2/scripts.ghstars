from typing import Iterable

from src.shared.paper_identity import (
    extract_arxiv_id_from_single_paper_url,
    is_single_arxiv_paper_url,
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
