import pytest

from src.shared.relation_candidates import RelatedWorkCandidate as SharedRelatedWorkCandidate
from src.shared.semantic_scholar_graph import (
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
