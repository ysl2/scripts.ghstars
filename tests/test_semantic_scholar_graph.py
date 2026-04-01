import pytest

from src.shared.relation_candidates import RelatedWorkCandidate as SharedRelatedWorkCandidate
from src.shared.semantic_scholar_graph import (
    AIFORSCHOLAR_GRAPH_URL,
    SEMANTIC_SCHOLAR_GRAPH_URL,
    SemanticScholarGraphClient,
)


class FakeResponse:
    def __init__(self, json_data=None, status=200, headers=None):
        self.status = status
        self._json_data = json_data or {}
        self.headers = dict(headers or {})

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, *args, **kwargs):
        return self._json_data


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, *, headers=None, params=None):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers or {}),
                "params": dict(params or {}),
            }
        )
        if not self._responses:
            raise RuntimeError("No fake response configured")
        return self._responses.pop(0)


@pytest.mark.anyio
async def test_fetch_paper_by_identifier_uses_graph_identifier_endpoint():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "paperId": "abc123",
                    "title": "Example Paper",
                    "externalIds": {"DOI": "10.1000/example"},
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    paper = await client.fetch_paper_by_identifier("DOI:10.1000/example")

    assert paper == {
        "paperId": "abc123",
        "title": "Example Paper",
        "externalIds": {"DOI": "10.1000/example"},
    }
    assert session.calls[0]["url"] == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/DOI:10.1000/example"
    assert session.calls[0]["params"]["fields"] == "paperId,title,externalIds"


@pytest.mark.anyio
async def test_fetch_paper_by_identifier_returns_none_on_404():
    session = FakeSession([FakeResponse(status=404)])
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    paper = await client.fetch_paper_by_identifier("DOI:10.1000/missing")

    assert paper is None


@pytest.mark.anyio
async def test_find_arxiv_match_by_identifier_prefers_doi_exact_lookup():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "paperId": "abc123",
                    "title": "Published Paper",
                    "externalIds": {"ArXiv": "2501.12345v2", "DOI": "10.1000/example"},
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title, source = await client.find_arxiv_match_by_identifier(
        "https://doi.org/10.1000/example",
        title="Published Paper",
    )

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"
    assert source == "semantic_scholar_exact_doi"
    assert session.calls[0]["url"] == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/DOI:10.1000/example"


@pytest.mark.anyio
async def test_find_arxiv_match_by_identifier_can_use_source_url_exact_lookup():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "paperId": "abc123",
                    "title": "Published Paper",
                    "externalIds": {"ArXiv": "2501.12345"},
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title, source = await client.find_arxiv_match_by_identifier(
        "https://semanticscholar.org/paper/Foo/abc123/",
        title="Published Paper",
    )

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"
    assert source == "semantic_scholar_exact_source_url"
    assert (
        session.calls[0]["url"]
        == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/URL:https://www.semanticscholar.org/paper/Foo/abc123"
    )


@pytest.mark.anyio
async def test_find_arxiv_match_by_identifier_can_run_in_exact_only_mode():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "paperId": "published",
                    "title": "Published Paper",
                    "externalIds": {"DOI": "10.1145/example"},
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title, source = await client.find_arxiv_match_by_identifier(
        "https://doi.org/10.1145/example",
        title="Published Paper",
        allow_title_fallback=False,
    )

    assert arxiv_url is None
    assert resolved_title is None
    assert source is None
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/DOI:10.1145/example"


@pytest.mark.anyio
async def test_find_arxiv_match_by_identifier_falls_back_to_normalized_title_exact_search():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "paperId": "published",
                    "title": "Published Paper",
                    "externalIds": {"DOI": "10.1145/example"},
                }
            ),
            FakeResponse(
                {
                    "data": [
                        {
                            "paperId": "partial",
                            "title": "Published Paper Extended",
                            "externalIds": {"ArXiv": "2999.99999"},
                        },
                        {
                            "paperId": "exact",
                            "title": "Published   Paper",
                            "externalIds": {"ArXiv": "2501.12345v2"},
                        },
                    ]
                }
            ),
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title, source = await client.find_arxiv_match_by_identifier(
        "https://doi.org/10.1145/example",
        title="Published Paper",
    )

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"
    assert source == "semantic_scholar_title_exact"
    assert session.calls[1]["url"] == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/search"
    assert session.calls[1]["params"]["query"] == "Published Paper"


@pytest.mark.anyio
async def test_find_arxiv_match_by_title_rejects_non_exact_normalized_matches():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [
                        {
                            "paperId": "p1",
                            "title": "Generalizable Visual Localization for Gaussian Splatting",
                            "externalIds": {"ArXiv": "2501.12345"},
                        }
                    ]
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title, source = await client.find_arxiv_match_by_title(
        "Generalizable Visual Localization",
    )

    assert arxiv_url is None
    assert resolved_title is None
    assert source is None


@pytest.mark.anyio
async def test_fetch_references_unwraps_cited_paper_rows():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [
                        {"citedPaper": {"paperId": "p1", "title": "Ref 1", "externalIds": {"ArXiv": "2401.12345"}}},
                        {"ignored": {"paperId": "missing"}},
                        {"citedPaper": {"paperId": "p2", "title": "Ref 2", "externalIds": {"DOI": "10.1000/ref2"}}},
                    ]
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    references = await client.fetch_references({"paperId": "target-id"})

    assert references == [
        {"paperId": "p1", "title": "Ref 1", "externalIds": {"ArXiv": "2401.12345"}},
        {"paperId": "p2", "title": "Ref 2", "externalIds": {"DOI": "10.1000/ref2"}},
    ]
    assert session.calls[0]["url"] == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/target-id/references"
    assert session.calls[0]["params"]["fields"] == "citedPaper.paperId,citedPaper.title,citedPaper.externalIds"


@pytest.mark.anyio
async def test_fetch_references_follows_next_offset_for_second_page():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "offset": 0,
                    "next": 2,
                    "data": [
                        {"citedPaper": {"paperId": "p1", "title": "Ref 1", "externalIds": {}}},
                        {"citedPaper": {"paperId": "p2", "title": "Ref 2", "externalIds": {}}},
                    ],
                }
            ),
            FakeResponse(
                {
                    "offset": 2,
                    "data": [
                        {"citedPaper": {"paperId": "p3", "title": "Ref 3", "externalIds": {}}},
                    ],
                }
            ),
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    references = await client.fetch_references({"paperId": "target-id"})

    assert [row["paperId"] for row in references] == ["p1", "p2", "p3"]
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["offset"] == 0
    assert session.calls[1]["params"]["offset"] == 2


@pytest.mark.anyio
async def test_fetch_citations_unwraps_citing_paper_rows():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [
                        {"citingPaper": {"paperId": "c1", "title": "Cit 1", "externalIds": {"DOI": "10.1000/c1"}}},
                        {"ignored": {"paperId": "missing"}},
                        {"citingPaper": {"paperId": "c2", "title": "Cit 2", "externalIds": {}}},
                    ]
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    citations = await client.fetch_citations({"paperId": "target-id"})

    assert citations == [
        {"paperId": "c1", "title": "Cit 1", "externalIds": {"DOI": "10.1000/c1"}},
        {"paperId": "c2", "title": "Cit 2", "externalIds": {}},
    ]
    assert session.calls[0]["url"] == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/target-id/citations"
    assert session.calls[0]["params"]["fields"] == "citingPaper.paperId,citingPaper.title,citingPaper.externalIds"


@pytest.mark.anyio
async def test_fetch_references_skips_malformed_nested_related_papers_without_identity_or_title():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [
                        {"citedPaper": {"paperId": "", "title": "", "externalIds": {}}},
                        {"citedPaper": {"paperId": "p1", "title": "", "externalIds": {}}},
                        {"citedPaper": {"paperId": "", "title": "Named Ref", "externalIds": {}}},
                    ]
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    references = await client.fetch_references({"paperId": "target-id"})

    assert references == [
        {"paperId": "p1", "title": "", "externalIds": {}},
        {"paperId": "", "title": "Named Ref", "externalIds": {}},
    ]


@pytest.mark.anyio
async def test_search_papers_by_title_uses_search_endpoint_and_filters_rows():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "offset": 0,
                    "next": 2,
                    "data": [
                        {"paperId": "p1", "title": "Paper 1", "externalIds": {}},
                        "not-a-dict",
                        {"paperId": "p2", "title": "Paper 2", "externalIds": {"DOI": "10.1000/p2"}},
                    ],
                }
            )
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    rows = await client.search_papers_by_title("example title", limit=2)

    assert rows == [
        {"paperId": "p1", "title": "Paper 1", "externalIds": {}},
        {"paperId": "p2", "title": "Paper 2", "externalIds": {"DOI": "10.1000/p2"}},
    ]
    assert session.calls[0]["url"] == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/search"
    assert session.calls[0]["params"]["query"] == "example title"
    assert session.calls[0]["params"]["limit"] == 2


@pytest.mark.anyio
async def test_build_headers_injects_x_api_key_when_configured():
    session = FakeSession([FakeResponse({"data": []})])
    client = SemanticScholarGraphClient(
        session,
        semantic_scholar_api_key="secret-key",
        min_interval=0,
        max_concurrent=1,
    )

    await client.search_papers_by_title("example")

    assert session.calls[0]["headers"]["x-api-key"] == "secret-key"


@pytest.mark.anyio
async def test_search_papers_by_title_uses_ai4scholar_relay_when_only_relay_token_is_configured():
    session = FakeSession([FakeResponse({"data": []})])
    client = SemanticScholarGraphClient(
        session,
        aiforscholar_token="relay-token",
        min_interval=0,
        max_concurrent=1,
    )

    await client.search_papers_by_title("example")

    assert session.calls[0]["url"] == f"{AIFORSCHOLAR_GRAPH_URL}/paper/search"
    assert session.calls[0]["headers"]["Authorization"] == "Bearer relay-token"
    assert "x-api-key" not in session.calls[0]["headers"]


@pytest.mark.anyio
async def test_search_papers_by_title_prefers_official_api_key_over_ai4scholar_token():
    session = FakeSession([FakeResponse({"data": []})])
    client = SemanticScholarGraphClient(
        session,
        semantic_scholar_api_key="official-key",
        aiforscholar_token="relay-token",
        min_interval=0,
        max_concurrent=1,
    )

    await client.search_papers_by_title("example")

    assert session.calls[0]["url"] == f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/search"
    assert session.calls[0]["headers"]["x-api-key"] == "official-key"
    assert "Authorization" not in session.calls[0]["headers"]


@pytest.mark.anyio
async def test_get_json_retries_429_using_retry_after_header(monkeypatch):
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr("src.shared.semantic_scholar_graph.asyncio.sleep", fake_sleep)
    session = FakeSession(
        [
            FakeResponse(status=429, headers={"Retry-After": "1.5"}),
            FakeResponse({"data": []}, status=200),
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    payload = await client._get_json(f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/search")

    assert payload == {"data": []}
    assert sleep_calls == [1.5]
    assert len(session.calls) == 2


@pytest.mark.anyio
async def test_get_json_retries_retryable_5xx(monkeypatch):
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr("src.shared.semantic_scholar_graph.asyncio.sleep", fake_sleep)
    session = FakeSession(
        [
            FakeResponse(status=503),
            FakeResponse({"data": []}, status=200),
        ]
    )
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    payload = await client._get_json(f"{SEMANTIC_SCHOLAR_GRAPH_URL}/paper/search")

    assert payload == {"data": []}
    assert sleep_calls == [0.5]
    assert len(session.calls) == 2


def test_build_related_work_candidate_prefers_arxiv_then_doi_then_paper_url():
    session = FakeSession([])
    client = SemanticScholarGraphClient(session, min_interval=0, max_concurrent=1)

    arxiv_candidate = client.build_related_work_candidate(
        {
            "paperId": "arxiv-paper",
            "title": "ArXiv Paper",
            "externalIds": {"ArXiv": "2401.12345v2", "DOI": "10.1000/example"},
        }
    )
    doi_candidate = client.build_related_work_candidate(
        {
            "paperId": "doi-paper",
            "title": "DOI Paper",
            "externalIds": {"DOI": "10.1145/example"},
        }
    )
    fallback_candidate = client.build_related_work_candidate(
        {
            "paperId": "paper-only",
            "title": "Paper URL Fallback",
            "externalIds": {},
        }
    )

    assert arxiv_candidate == SharedRelatedWorkCandidate(
        title="ArXiv Paper",
        direct_arxiv_url="https://arxiv.org/abs/2401.12345",
        doi_url="https://doi.org/10.1000/example",
        landing_page_url="https://arxiv.org/abs/2401.12345",
        source_url="https://www.semanticscholar.org/paper/arxiv-paper",
    )
    assert doi_candidate == SharedRelatedWorkCandidate(
        title="DOI Paper",
        direct_arxiv_url=None,
        doi_url="https://doi.org/10.1145/example",
        landing_page_url="https://doi.org/10.1145/example",
        source_url="https://www.semanticscholar.org/paper/doi-paper",
    )
    assert fallback_candidate == SharedRelatedWorkCandidate(
        title="Paper URL Fallback",
        direct_arxiv_url=None,
        doi_url=None,
        landing_page_url="https://www.semanticscholar.org/paper/paper-only",
        source_url="https://www.semanticscholar.org/paper/paper-only",
    )
    assert not hasattr(arxiv_candidate, "openalex_url")
