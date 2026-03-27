import pytest

from src.shared.arxiv import (
    ArxivClient,
    extract_best_arxiv_id_from_feed,
    extract_best_arxiv_id_from_search_html,
    extract_submitted_date_from_abs_html,
)


def test_extract_best_arxiv_id_from_feed_prefers_exact_title_match():
    feed_xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2501.00001v1</id>
        <title>Other Paper</title>
      </entry>
      <entry>
        <id>http://arxiv.org/abs/2501.00002v2</id>
        <title>Exact Match Paper</title>
      </entry>
    </feed>
    """

    assert extract_best_arxiv_id_from_feed(feed_xml, "Exact Match Paper") == (
        "2501.00002",
        "title_search_exact",
    )


def test_extract_submitted_date_from_abs_html_reads_exact_submission_date():
    html = "<div class='submission-history'>[Submitted on 7 Jul 2024 (v1)]</div>"

    assert extract_submitted_date_from_abs_html(html) == "2024-07-07"


def test_extract_best_arxiv_id_from_search_html_tolerates_punctuation_spacing():
    search_html = """
    <ol class="breathe-horizontal" start="1">
      <li class="arxiv-result">
        <p class="list-title is-inline-block">
          <a href="https://arxiv.org/abs/2501.13928v2">arXiv:2501.13928</a>
        </p>
        <p class="title is-5 mathjax">
          Fast3R : Towards 3D Reconstruction of 1000 + Images in One Forward Pass
        </p>
      </li>
    </ol>
    """

    assert extract_best_arxiv_id_from_search_html(
        search_html,
        "Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass",
    ) == (
        "2501.13928",
        "title_search_exact",
    )


def test_extract_best_arxiv_id_from_search_html_rejects_non_matching_related_title():
    search_html = """
    <ol class="breathe-horizontal" start="1">
      <li class="arxiv-result">
        <p class="list-title is-inline-block">
          <a href="https://arxiv.org/abs/2512.21883">arXiv:2512.21883</a>
        </p>
        <p class="title is-5 mathjax">
          Reloc-VGGT: Visual Re-localization with Geometry Grounded Transformer
        </p>
      </li>
    </ol>
    """

    assert extract_best_arxiv_id_from_search_html(
        search_html,
        "VGGT: Visual Geometry Grounded Transformer",
    ) == (None, None)


@pytest.mark.anyio
async def test_get_arxiv_id_by_title_uses_search_html_results():
    class FakeResponse:
        def __init__(self, status: int, text: str):
            self.status = status
            self._text = text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return self._text

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None):
            self.calls.append((url, params))
            assert url == "https://arxiv.org/search/"
            return FakeResponse(
                200,
                """
                <ol class="breathe-horizontal" start="1">
                  <li class="arxiv-result">
                    <p class="list-title is-inline-block">
                      <a href="https://arxiv.org/abs/2501.13928v2">arXiv:2501.13928</a>
                    </p>
                    <p class="title is-5 mathjax">
                      Fast3R : Towards 3D Reconstruction of 1000 + Images in One Forward Pass
                    </p>
                  </li>
                </ol>
                """,
            )

    session = FakeSession()
    client = ArxivClient(session, max_concurrent=1, min_interval=0)

    arxiv_id, source, error = await client.get_arxiv_id_by_title(
        "Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass"
    )

    assert (arxiv_id, source, error) == ("2501.13928", "title_search_exact", None)
    assert session.calls == [
        (
            "https://arxiv.org/search/",
            {
                "query": "Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass",
                "searchtype": "title",
                "abstracts": "show",
                "order": "-announced_date_first",
                "size": "50",
            },
        )
    ]


@pytest.mark.anyio
async def test_get_arxiv_title_from_metadata_feed():
    class FakeResponse:
        def __init__(self, status: int, text: str):
            self.status = status
            self._text = text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return self._text

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None):
            self.calls.append((url, params))
            assert url == "https://export.arxiv.org/api/query"
            return FakeResponse(
                200,
                """
                <feed xmlns="http://www.w3.org/2005/Atom">
                  <entry>
                    <id>http://arxiv.org/abs/2501.12345v1</id>
                    <title>
                      Example Paper Title
                    </title>
                  </entry>
                </feed>
                """,
            )

    session = FakeSession()
    client = ArxivClient(session, max_concurrent=1, min_interval=0)

    title, error = await client.get_title("https://arxiv.org/abs/2501.12345v1")

    assert (title, error) == ("Example Paper Title", None)
    assert session.calls == [
        (
            "https://export.arxiv.org/api/query",
            {"id_list": "2501.12345"},
        )
    ]


@pytest.mark.anyio
async def test_get_arxiv_title_accepts_id_only_input():
    class FakeResponse:
        def __init__(self, status: int, text: str):
            self.status = status
            self._text = text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return self._text

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None):
            self.calls.append((url, params))
            assert url == "https://export.arxiv.org/api/query"
            return FakeResponse(
                200,
                """
                <feed xmlns="http://www.w3.org/2005/Atom">
                  <entry>
                    <id>http://arxiv.org/abs/2501.12345v1</id>
                    <title>
                      Example Paper Title
                    </title>
                  </entry>
                </feed>
                """,
            )

    session = FakeSession()
    client = ArxivClient(session, max_concurrent=1, min_interval=0)

    title, error = await client.get_title("2501.12345v1")

    assert (title, error) == ("Example Paper Title", None)
    assert session.calls == [
        (
            "https://export.arxiv.org/api/query",
            {"id_list": "2501.12345"},
        )
    ]


@pytest.mark.anyio
async def test_get_arxiv_title_accepts_pdf_url_input():
    class FakeResponse:
        def __init__(self, status: int, text: str):
            self.status = status
            self._text = text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return self._text

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None):
            self.calls.append((url, params))
            assert url == "https://export.arxiv.org/api/query"
            return FakeResponse(
                200,
                """
                <feed xmlns="http://www.w3.org/2005/Atom">
                  <entry>
                    <id>http://arxiv.org/abs/2501.12345v1</id>
                    <title>Example Paper Title</title>
                  </entry>
                </feed>
                """,
            )

    session = FakeSession()
    client = ArxivClient(session, max_concurrent=1, min_interval=0)

    title, error = await client.get_title("https://arxiv.org/pdf/2501.12345v1.pdf")

    assert (title, error) == ("Example Paper Title", None)
    assert session.calls == [
        (
            "https://export.arxiv.org/api/query",
            {"id_list": "2501.12345"},
        )
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/2501.12345v1/",
        "https://arxiv.org/abs/2501.12345v1?context=copy",
        "https://arxiv.org/abs/2501.12345v1#references",
    ],
)
async def test_get_arxiv_title_accepts_single_paper_url_variants(url: str):
    class FakeResponse:
        def __init__(self, status: int, text: str):
            self.status = status
            self._text = text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return self._text

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None):
            self.calls.append((url, params))
            assert url == "https://export.arxiv.org/api/query"
            return FakeResponse(
                200,
                """
                <feed xmlns="http://www.w3.org/2005/Atom">
                  <entry>
                    <id>http://arxiv.org/abs/2501.12345v1</id>
                    <title>Example Paper Title</title>
                  </entry>
                </feed>
                """,
            )

    session = FakeSession()
    client = ArxivClient(session, max_concurrent=1, min_interval=0)

    title, error = await client.get_title(url)

    assert (title, error) == ("Example Paper Title", None)
    assert session.calls == [
        (
            "https://export.arxiv.org/api/query",
            {"id_list": "2501.12345"},
        )
    ]
