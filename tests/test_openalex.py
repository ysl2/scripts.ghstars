import pytest

from src.shared.openalex import OpenAlexClient
from src.shared.papers import PaperSeed


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
async def test_title_search_returns_first_result_by_relevance():
    first = {"id": "W1", "display_name": "First", "title": "First"}
    second = {"id": "W2", "display_name": "Second", "title": "Second"}
    session = FakeSession([FakeResponse({"results": [first, second]})])
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    result = await client.search_first_work("relevant title")

    assert result == first
    assert session.calls[0]["params"]["search"] == "relevant title"
    assert session.calls[0]["params"]["per_page"] == 5
    assert session.calls[0]["params"]["select"] == "id,referenced_works"


@pytest.mark.anyio
async def test_find_preprint_match_by_identifier_returns_direct_arxiv_for_doi():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "id": "https://openalex.org/W1",
                    "display_name": "Published Paper",
                    "ids": {"arxiv": "2501.12345v2"},
                }
            )
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_preprint_match_by_identifier(
        "https://doi.org/10.48550/arXiv.2501.12345",
        title="Published Paper",
    )

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"
    assert session.calls[0]["url"] == "https://api.openalex.org/works/https://doi.org/10.48550/arXiv.2501.12345"
    assert session.calls[0]["params"]["select"] == "id,display_name,title,ids,doi,locations"


@pytest.mark.anyio
async def test_find_preprint_match_by_identifier_uses_sibling_crosswalk_after_direct_lookup():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "id": "https://openalex.org/W-published",
                    "display_name": "Example Published Paper",
                    "doi": "https://doi.org/10.1145/example",
                }
            ),
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "https://openalex.org/W-published",
                            "display_name": "Example Published Paper",
                            "doi": "https://doi.org/10.1145/example",
                        },
                        {
                            "id": "https://openalex.org/W-preprint",
                            "display_name": "Example Published Paper",
                            "locations": [
                                {
                                    "landing_page_url": "https://arxiv.org/abs/2401.12345v2",
                                }
                            ],
                        },
                    ]
                }
            ),
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_preprint_match_by_identifier(
        "https://openalex.org/W-published",
        title="Example Published Paper",
    )

    assert arxiv_url == "https://arxiv.org/abs/2401.12345"
    assert resolved_title == "Example Published Paper"
    assert session.calls[0]["url"] == "https://api.openalex.org/works/W-published"
    assert session.calls[0]["params"]["select"] == "id,display_name,title,ids,doi,locations"


@pytest.mark.anyio
async def test_find_related_work_preprint_accepts_candidate_with_explicit_arxiv_location():
    work = {
        "id": "https://openalex.org/W-published",
        "display_name": "Example Published Paper",
    }
    session = FakeSession(
        [
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "https://openalex.org/W-published",
                            "display_name": "Example Published Paper",
                            "doi": "https://doi.org/10.1145/example",
                        },
                        {
                            "id": "https://openalex.org/W-preprint",
                            "display_name": "Example Published Paper",
                            "locations": [
                                {
                                    "landing_page_url": "https://arxiv.org/abs/2401.12345v2",
                                }
                            ],
                        },
                    ]
                }
            )
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    result = await client.find_related_work_preprint_arxiv_url(work, title="Example Published Paper")

    assert result == "https://arxiv.org/abs/2401.12345"
    assert session.calls[0]["params"]["search"] == "Example Published Paper"
    assert session.calls[0]["params"]["per_page"] == 5
    assert session.calls[0]["params"]["select"] == "id,display_name,title,ids,doi,locations"


@pytest.mark.anyio
async def test_find_related_work_preprint_rejects_publisher_only_candidates():
    work = {
        "id": "https://openalex.org/W-published",
        "display_name": "Example Published Paper",
    }
    session = FakeSession(
        [
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "https://openalex.org/W-publisher-version",
                            "display_name": "Example Published Paper",
                            "doi": "https://doi.org/10.1145/example",
                            "locations": [
                                {
                                    "landing_page_url": "https://publisher.example/paper",
                                }
                            ],
                        }
                    ]
                }
            )
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    result = await client.find_related_work_preprint_arxiv_url(work, title="Example Published Paper")

    assert result is None


@pytest.mark.anyio
async def test_find_related_work_preprint_rejects_similar_title_without_explicit_arxiv_evidence():
    work = {
        "id": "https://openalex.org/W-published",
        "display_name": "Example Published Paper",
    }
    session = FakeSession(
        [
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "https://openalex.org/W-similar",
                            "display_name": "Example Published Paper Extended",
                            "doi": "https://doi.org/10.1145/example-extended",
                        }
                    ]
                }
            )
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    result = await client.find_related_work_preprint_arxiv_url(work, title="Example Published Paper")

    assert result is None


@pytest.mark.anyio
async def test_find_related_work_preprint_returns_none_for_malformed_or_empty_payloads():
    work = {
        "id": "https://openalex.org/W-published",
        "display_name": "Example Published Paper",
    }
    session = FakeSession(
        [
            FakeResponse({"unexpected": "payload"}),
            FakeResponse({"results": []}),
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    malformed_result = await client.find_related_work_preprint_arxiv_url(work, title="Example Published Paper")
    empty_result = await client.find_related_work_preprint_arxiv_url(work, title="Example Published Paper")

    assert malformed_result is None
    assert empty_result is None


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


def test_build_related_work_candidate_prefers_direct_arxiv_identity():
    session = FakeSession([])
    client = OpenAlexClient(session, min_interval=0)
    work = {
        "display_name": "Direct Paper",
        "ids": {"arxiv": "2403.00001v2"},
        "doi": "https://doi.org/10.48550/arXiv.2403.00001",
        "locations": [{"landing_page_url": "https://example.com/direct"}],
        "id": "https://openalex.org/W1",
    }

    candidate = client.build_related_work_candidate(work)

    assert candidate.title == "Direct Paper"
    assert candidate.direct_arxiv_url == "https://arxiv.org/abs/2403.00001"
    assert candidate.doi_url == "https://doi.org/10.48550/arXiv.2403.00001"
    assert candidate.landing_page_url == "https://example.com/direct"
    assert candidate.openalex_url == "https://openalex.org/W1"


def test_build_related_work_candidate_retains_non_arxiv_fallback_fields():
    session = FakeSession([])
    client = OpenAlexClient(session, min_interval=0)
    work = {
        "display_name": "Non Arxiv Paper",
        "doi": "https://doi.org/10.1145/example",
        "locations": [{"landing_page_url": "https://publisher.example/paper"}],
        "id": "https://openalex.org/W9",
    }

    candidate = client.build_related_work_candidate(work)

    assert candidate.direct_arxiv_url is None
    assert candidate.doi_url == "https://doi.org/10.1145/example"
    assert candidate.landing_page_url == "https://publisher.example/paper"
    assert candidate.openalex_url == "https://openalex.org/W9"


@pytest.mark.anyio
async def test_references_hydrate_from_referenced_work_ids_via_single_batch_query():
    response = FakeResponse(
        {
            "results": [
                {"id": "https://openalex.org/W2", "display_name": "Ref 2"},
                {"id": "https://openalex.org/W1", "display_name": "Ref 1"},
            ]
        }
    )
    session = FakeSession([response])
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    work = {"referenced_works": ["https://openalex.org/works/W1", "W2"]}

    hydrated = await client.fetch_referenced_works(work)

    assert hydrated == [
        {"id": "https://openalex.org/W1", "display_name": "Ref 1"},
        {"id": "https://openalex.org/W2", "display_name": "Ref 2"},
    ]
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == "https://api.openalex.org/works"
    assert session.calls[0]["params"]["filter"] == "openalex:W1|W2"
    assert session.calls[0]["params"]["per_page"] == 2
    assert session.calls[0]["params"]["select"] == "id,display_name,title,ids,doi,locations"


@pytest.mark.anyio
async def test_references_skip_missing_work_ids_from_batch_results():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "results": [
                        {"id": "https://openalex.org/W3", "display_name": "Ref 3"},
                        {"id": "https://openalex.org/W1", "display_name": "Ref 1"},
                    ]
                }
            )
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    work = {
        "referenced_works": [
            "https://openalex.org/works/W1",
            "https://openalex.org/W404",
            "W3",
        ]
    }

    hydrated = await client.fetch_referenced_works(work)

    assert hydrated == [
        {"id": "https://openalex.org/W1", "display_name": "Ref 1"},
        {"id": "https://openalex.org/W3", "display_name": "Ref 3"},
    ]
    assert len(session.calls) == 1
    assert session.calls[0]["params"]["filter"] == "openalex:W1|W404|W3"


@pytest.mark.anyio
async def test_references_still_raise_non_404_hydration_errors():
    session = FakeSession([FakeResponse(status=403)])
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    work = {"referenced_works": ["W403"]}

    with pytest.raises(RuntimeError, match=r"OpenAlex API error \(403\)"):
        await client.fetch_referenced_works(work)


@pytest.mark.anyio
async def test_references_chunk_batch_queries_when_work_id_list_exceeds_chunk_limit(monkeypatch):
    import src.shared.openalex as openalex_module

    monkeypatch.setattr(openalex_module, "OPENALEX_REFERENCED_WORKS_CHUNK_SIZE", 2)
    session = FakeSession(
        [
            FakeResponse(
                {
                    "results": [
                        {"id": "https://openalex.org/W2", "display_name": "Ref 2"},
                        {"id": "https://openalex.org/W1", "display_name": "Ref 1"},
                    ]
                }
            ),
            FakeResponse({"results": [{"id": "https://openalex.org/W3", "display_name": "Ref 3"}]}),
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    work = {"referenced_works": ["W1", "W2", "W3"]}

    hydrated = await client.fetch_referenced_works(work)

    assert hydrated == [
        {"id": "https://openalex.org/W1", "display_name": "Ref 1"},
        {"id": "https://openalex.org/W2", "display_name": "Ref 2"},
        {"id": "https://openalex.org/W3", "display_name": "Ref 3"},
    ]
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["filter"] == "openalex:W1|W2"
    assert session.calls[0]["params"]["per_page"] == 2
    assert session.calls[1]["params"]["filter"] == "openalex:W3"
    assert session.calls[1]["params"]["per_page"] == 1


@pytest.mark.anyio
async def test_citations_paginate_across_multiple_pages():
    first_page = {"results": [{"id": "W1"}], "meta": {"next_cursor": "abc"}}
    second_page = {"results": [{"id": "W2"}], "meta": {"next_cursor": None}}
    session = FakeSession([FakeResponse(first_page), FakeResponse(second_page)])
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)
    work = {"id": "https://openalex.org/works/W0"}

    citations = await client.fetch_citations(work)

    assert citations == first_page["results"] + second_page["results"]
    assert session.calls[0]["params"]["per_page"] == 200
    assert session.calls[0]["params"]["filter"] == "cites:W0"
    assert session.calls[0]["params"]["cursor"] == "*"
    assert session.calls[0]["params"]["select"] == "id,display_name,title,ids,doi,locations"
    assert session.calls[1]["params"]["cursor"] == "abc"
    assert session.calls[1]["params"]["filter"] == "cites:W0"
    assert session.calls[1]["params"]["select"] == "id,display_name,title,ids,doi,locations"


@pytest.mark.anyio
async def test_query_params_include_openalex_api_key_when_present():
    session = FakeSession([FakeResponse({"results": []})])
    client = OpenAlexClient(session, openalex_api_key="oa_key", min_interval=0, max_concurrent=1)
    await client.search_first_work("title")

    assert session.calls[0]["params"]["api_key"] == "oa_key"


@pytest.mark.anyio
async def test_title_search_honors_short_retry_after_header_on_429(monkeypatch):
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr("src.shared.openalex.asyncio.sleep", fake_sleep)

    session = FakeSession(
        [
            FakeResponse(
                {"error": "Rate limit exceeded"},
                status=429,
                headers={"Retry-After": "3", "X-RateLimit-Remaining": "0"},
            ),
            FakeResponse({"results": [{"id": "W1", "referenced_works": []}]}),
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    result = await client.search_first_work("title")

    assert result == {"id": "W1", "referenced_works": []}
    assert len(session.calls) == 2
    assert sleep_calls == [3.0]


@pytest.mark.anyio
async def test_title_search_falls_back_to_retry_after_from_429_body(monkeypatch):
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr("src.shared.openalex.asyncio.sleep", fake_sleep)

    session = FakeSession(
        [
            FakeResponse(
                {"error": "Rate limit exceeded", "retryAfter": 4},
                status=429,
            ),
            FakeResponse({"results": [{"id": "W1", "referenced_works": []}]}),
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    result = await client.search_first_work("title")

    assert result == {"id": "W1", "referenced_works": []}
    assert len(session.calls) == 2
    assert sleep_calls == [4.0]


@pytest.mark.anyio
async def test_title_search_stops_retrying_when_retry_after_exceeds_bounded_limit(monkeypatch):
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr("src.shared.openalex.asyncio.sleep", fake_sleep)

    session = FakeSession(
        [
            FakeResponse(
                {"error": "Rate limit exceeded", "retryAfter": 55035},
                status=429,
                headers={"Retry-After": "55035", "X-RateLimit-Remaining": "0"},
            ),
            FakeResponse({"results": [{"id": "W-should-not-run"}]}),
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    with pytest.raises(RuntimeError, match=r"OpenAlex API error \(429\)"):
        await client.search_first_work("title")

    assert len(session.calls) == 1
    assert sleep_calls == []


def test_normalizes_related_work_from_location_url():
    session = FakeSession([])
    client = OpenAlexClient(session, min_interval=0)
    work = {
        "display_name": "Location Paper",
        "locations": [
            {
                "landing_page_url": "https://arxiv.org/abs/2403.00002v1",
                "pdf_url": "https://arxiv.org/pdf/2403.00002.pdf",
            }
        ],
    }

    seed = client.normalize_related_work(work)

    assert seed == PaperSeed(name="Location Paper", url="https://arxiv.org/abs/2403.00002")


def test_normalizes_related_work_from_location_pdf_url():
    session = FakeSession([])
    client = OpenAlexClient(session, min_interval=0)
    work = {
        "display_name": "PDF Location Paper",
        "locations": [{"pdf_url": "https://arxiv.org/pdf/2403.00005.pdf"}],
    }

    seed = client.normalize_related_work(work)

    assert seed == PaperSeed(name="PDF Location Paper", url="https://arxiv.org/abs/2403.00005")


def test_normalizes_related_work_from_doi():
    session = FakeSession([])
    client = OpenAlexClient(session, min_interval=0)
    work = {"doi": "https://doi.org/10.48550/arXiv.2403.00003"}

    seed = client.normalize_related_work(work)

    assert seed == PaperSeed(name="https://arxiv.org/abs/2403.00003", url="https://arxiv.org/abs/2403.00003")
