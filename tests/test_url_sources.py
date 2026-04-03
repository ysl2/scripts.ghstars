from src.url_to_csv.sources import UrlSource, detect_url_source, is_supported_url_source


def test_detect_url_source_identifies_supported_sites():
    assert detect_url_source("https://arxivxplorer.com/?q=test&cats=cs.CV") == UrlSource.ARXIVXPLORER
    assert detect_url_source("https://arxiv.org/list/cs.CV/recent") == UrlSource.ARXIV_ORG
    assert detect_url_source("https://arxiv.org/list/cs.CV/new") == UrlSource.ARXIV_ORG
    assert (
        detect_url_source(
            "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=50&order=-submitted_date"
        )
        == UrlSource.ARXIV_ORG
    )
    assert (
        detect_url_source(
            "https://arxiv.org/search/advanced?advanced=&terms-0-operator=AND&terms-0-term=reconstruction&terms-0-field=all&terms-1-operator=AND&terms-1-term=semantic&terms-1-field=all&terms-2-operator=AND&terms-2-term=streaming&terms-2-field=all&classification-computer_science=y&classification-include_cross_list=include&date-filter_by=past_12&date-date_type=submitted_date&abstracts=hide&size=50&order=-submitted_date"
        )
        == UrlSource.ARXIV_ORG
    )
    assert detect_url_source("https://arxiv.org/catchup/cs.CV/2026-03-26") == UrlSource.ARXIV_ORG
    assert detect_url_source("https://arxiv.org/list/cs.CV/2026-03") == UrlSource.ARXIV_ORG
    assert detect_url_source("https://huggingface.co/papers/trending?q=semantic") == UrlSource.HUGGINGFACE_PAPERS
    assert (
        detect_url_source(
            "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date"
        )
        == UrlSource.SEMANTIC_SCHOLAR
    )


def test_detect_url_source_returns_none_for_unsupported_url():
    assert detect_url_source("https://arxiv.org/abs/2603.23502") is None
    assert detect_url_source("https://example.com/search?q=test") is None
    assert not is_supported_url_source("https://arxiv.org/abs/2603.23502")
    assert not is_supported_url_source("https://example.com/search?q=test")


def test_detect_url_source_rejects_missing_required_query_text():
    assert detect_url_source("https://arxivxplorer.com/?cats=cs.CV") is None
    assert detect_url_source("https://www.semanticscholar.org/search?sort=pub-date") is None
    assert not is_supported_url_source("https://arxivxplorer.com/?cats=cs.CV")
    assert not is_supported_url_source("https://www.semanticscholar.org/search?sort=pub-date")
