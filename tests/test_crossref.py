import pytest

from src.shared.crossref import CrossrefClient


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers=None, params=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "params": dict(params or {})})
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_crossref_client_returns_arxiv_url_from_message_relation():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "message": {
                        "title": ["Published Paper"],
                        "relation": {
                            "is-preprint-of": [{"id": "https://arxiv.org/abs/2501.12345v2"}]
                        },
                    }
                }
            )
        ]
    )
    client = CrossrefClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_arxiv_match_by_doi("https://doi.org/10.1145/example")

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"


@pytest.mark.anyio
async def test_crossref_client_returns_none_when_no_arxiv_relation_exists():
    session = FakeSession([FakeResponse({"message": {"title": ["Published Paper"], "relation": {}}})])
    client = CrossrefClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_arxiv_match_by_doi("https://doi.org/10.1145/example")

    assert arxiv_url is None
    assert resolved_title == "Published Paper"
