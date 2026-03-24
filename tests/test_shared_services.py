from dataclasses import dataclass

import pytest

from shared.discovery import (
    find_github_url_in_alphaxiv_legacy_payload,
    find_github_url_in_huggingface_paper_html,
    resolve_github_url,
)
from shared.github import extract_owner_repo, normalize_github_url


@dataclass(frozen=True)
class FakeSeed:
    name: str
    url: str


def test_normalize_github_url_returns_canonical_repo_url():
    assert normalize_github_url(" https://github.com/foo/bar.git ") == "https://github.com/foo/bar"


def test_extract_owner_repo_reads_owner_and_repo():
    assert extract_owner_repo("https://github.com/foo/bar") == ("foo", "bar")


def test_find_github_url_in_huggingface_paper_html_prefers_embedded_repo_field():
    html = '<script>window.__DATA__={"githubRepo":"https://github.com/foo/bar"}</script>'
    assert find_github_url_in_huggingface_paper_html(html) == "https://github.com/foo/bar"


def test_find_github_url_in_alphaxiv_legacy_payload_prefers_known_fields():
    payload = {
        "paper": {
            "implementation": "https://github.com/foo/bar",
            "marimo_implementation": None,
            "paper_group": {"resources": []},
            "resources": [],
        }
    }

    assert find_github_url_in_alphaxiv_legacy_payload(payload) == "https://github.com/foo/bar"


@pytest.mark.anyio
async def test_resolve_github_url_falls_back_from_huggingface_to_alphaxiv():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.alphaxiv_token = "ax_token"
            self.calls = []

        async def get_huggingface_paper_html_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper", arxiv_id))
            return "<html><body>No repo here</body></html>", None

        async def get_huggingface_search_html(self, title):
            self.calls.append(("hf_search", title))
            return "<html><body>No paper hit</body></html>", None

        async def get_alphaxiv_paper_legacy(self, arxiv_id):
            self.calls.append(("alphaxiv", arxiv_id))
            return {"paper": {"implementation": "https://github.com/foo/bar"}}, None

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == [
        ("hf_paper", "2603.18493"),
        ("hf_search", "Paper Title"),
        ("alphaxiv", "2603.18493"),
    ]
