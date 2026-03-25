from src.url_to_csv.sources import UrlSource, detect_url_source, is_supported_url_source


def test_detect_url_source_identifies_supported_sites():
    assert detect_url_source("https://arxivxplorer.com/?q=test&cats=cs.CV") == UrlSource.ARXIVXPLORER
    assert detect_url_source("https://huggingface.co/papers/trending?q=semantic") == UrlSource.HUGGINGFACE_PAPERS
    assert (
        detect_url_source(
            "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date"
        )
        == UrlSource.SEMANTIC_SCHOLAR
    )


def test_detect_url_source_returns_none_for_unsupported_url():
    assert detect_url_source("https://example.com/search?q=test") is None
    assert not is_supported_url_source("https://example.com/search?q=test")
