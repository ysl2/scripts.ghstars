import pytest

from src.shared.datacite import DataCiteClient


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
async def test_datacite_client_returns_arxiv_url_from_related_identifiers():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": {
                        "attributes": {
                            "titles": [{"title": "Published Paper"}],
                            "relatedIdentifiers": [
                                {
                                    "relatedIdentifierType": "arXiv",
                                    "relatedIdentifier": "2501.12345v2",
                                    "relationType": "IsVersionOf",
                                }
                            ],
                        }
                    }
                }
            )
        ]
    )
    client = DataCiteClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_arxiv_match_by_doi("https://doi.org/10.5555/example")

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"


@pytest.mark.anyio
async def test_datacite_client_returns_none_when_no_arxiv_relation_exists():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": {
                        "attributes": {
                            "titles": [{"title": "Published Paper"}],
                            "relatedIdentifiers": [],
                        }
                    }
                }
            )
        ]
    )
    client = DataCiteClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_arxiv_match_by_doi("https://doi.org/10.5555/example")

    assert arxiv_url is None
    assert resolved_title == "Published Paper"
