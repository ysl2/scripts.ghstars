import pytest

from src.shared.openalex import OpenAlexClient
from src.shared.papers import PaperSeed


class FakeResponse:
    def __init__(self, json_data=None, status=200):
        self.status = status
        self._json_data = json_data or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
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
async def test_title_search_returns_first_result_by_relevance():
    first = {"id": "W1", "display_name": "First", "title": "First"}
    second = {"id": "W2", "display_name": "Second", "title": "Second"}
    session = FakeSession([FakeResponse({"results": [first, second]})])
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    result = await client.search_first_work("relevant title")

    assert result == first
    assert session.calls[0]["params"]["search"] == "relevant title"
    assert session.calls[0]["params"]["per_page"] == 5


def test_extracts_canonical_arxiv_url_from_related_work():
    session = FakeSession([])
    client = OpenAlexClient(session, min_interval=0)
    work = {"display_name": "Paper", "ids": {"arxiv": "2403.00001v2"}}

    seed = client.normalize_related_work(work)

    assert seed == PaperSeed(name="Paper", url="https://arxiv.org/abs/2403.00001")


def test_ignores_related_work_without_arxiv_metadata():
    session = FakeSession([])
    client = OpenAlexClient(session, min_interval=0)
    work = {"display_name": "Paper", "ids": {}}

    assert client.normalize_related_work(work) is None


@pytest.mark.anyio
async def test_references_hydrate_from_referenced_work_ids():
    responses = [
        FakeResponse({"id": "https://openalex.org/W1", "display_name": "Ref 1"}),
        FakeResponse({"id": "https://openalex.org/W2", "display_name": "Ref 2"}),
    ]
    session = FakeSession(responses)
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    work = {"referenced_works": ["https://openalex.org/works/W1", "W2"]}

    hydrated = await client.fetch_referenced_works(work)

    assert hydrated == [responses[0]._json_data, responses[1]._json_data]
    assert len(session.calls) == 2
    assert "W1" in session.calls[0]["url"]


@pytest.mark.anyio
async def test_citations_paginate_across_multiple_pages():
    first_page = {"results": [{"id": "W1"}], "meta": {"next_cursor": "abc"}}
    second_page = {"results": [{"id": "W2"}], "meta": {"next_cursor": None}}
    session = FakeSession([FakeResponse(first_page), FakeResponse(second_page)])
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    work = {"cited_by_api_url": "https://api.openalex.org/works/W0/cited_by"}

    citations = await client.fetch_citations(work)

    assert citations == first_page["results"] + second_page["results"]
    assert session.calls[0]["params"]["per_page"] == 200
    assert "cursor" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["cursor"] == "abc"


@pytest.mark.anyio
async def test_request_headers_include_openalex_api_key_when_present():
    session = FakeSession([FakeResponse({"results": []})])
    client = OpenAlexClient(session, openalex_api_key="oa_key", min_interval=0, max_concurrent=1)
    await client.search_first_work("title")

    assert session.calls[0]["headers"]["Authorization"] == "Bearer oa_key"
