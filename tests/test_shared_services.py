import asyncio
from dataclasses import dataclass

import pytest

from src.shared.discovery import (
    find_github_url_in_alphaxiv_legacy_payload,
    find_huggingface_paper_id_in_search_html,
    find_github_url_in_huggingface_paper_html,
    resolve_arxiv_id_by_title,
    resolve_github_url,
)
from src.shared.http import RateLimiter
from src.shared.github import GitHubClient, extract_owner_repo, normalize_github_url


@dataclass(frozen=True)
class FakeSeed:
    name: str
    url: str


def test_normalize_github_url_returns_canonical_repo_url():
    assert normalize_github_url(" https://github.com/foo/bar.git ") == "https://github.com/foo/bar"


def test_normalize_github_url_rejects_non_repo_urls_with_extra_suffix_segments():
    assert normalize_github_url("https://github.com/foo/bar/xgit") is None


def test_extract_owner_repo_reads_owner_and_repo():
    assert extract_owner_repo("https://github.com/foo/bar") == ("foo", "bar")


def test_github_client_enforces_unauthenticated_rate_limit_floor():
    client = GitHubClient(session=object(), github_token="", min_interval=0.2)

    assert client.rate_limiter.min_interval == 60.0


def test_github_client_keeps_requested_rate_limit_when_token_is_configured():
    client = GitHubClient(session=object(), github_token="ghp_xxx", min_interval=0.2)

    assert client.rate_limiter.min_interval == 0.2


def test_find_github_url_in_huggingface_paper_html_prefers_embedded_repo_field():
    html = '<script>window.__DATA__={"githubRepo":"https://github.com/foo/bar"}</script>'
    assert find_github_url_in_huggingface_paper_html(html) == "https://github.com/foo/bar"


def test_find_github_url_in_huggingface_paper_html_reads_html_escaped_repo_field_before_discussion_links():
    html = (
        "Discussion mentions https://github.com/naver/dust3r/pull/16 first. "
        "&quot;githubRepo&quot;:&quot;https://github.com/facebookresearch/fast3r&quot;"
    )

    assert find_github_url_in_huggingface_paper_html(html) == "https://github.com/facebookresearch/fast3r"


def test_find_github_url_in_huggingface_paper_html_ignores_arbitrary_discussion_links_without_explicit_repo_field():
    html = "Discussion mentions https://github.com/naver/dust3r/pull/16 but no explicit repo metadata."

    assert find_github_url_in_huggingface_paper_html(html) is None


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


def test_find_huggingface_paper_id_in_search_html_matches_exact_title_from_payload():
    html = """
    <div
      data-target="DailyPapers"
      data-props="{
        &quot;query&quot;:{&quot;q&quot;:&quot;Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass&quot;},
        &quot;searchResults&quot;:[
          {
            &quot;title&quot;:&quot;Speed3R: Sparse Feed-forward 3D Reconstruction Models&quot;,
            &quot;paper&quot;:{&quot;id&quot;:&quot;2603.08055&quot;,&quot;title&quot;:&quot;Speed3R: Sparse Feed-forward 3D Reconstruction Models&quot;}
          },
          {
            &quot;title&quot;:&quot;Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass&quot;,
            &quot;paper&quot;:{&quot;id&quot;:&quot;2501.13928&quot;,&quot;title&quot;:&quot;Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass&quot;}
          }
        ]
      }">
    </div>
    """

    assert find_huggingface_paper_id_in_search_html(
        html,
        "Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass",
    ) == "2501.13928"


@pytest.mark.anyio
async def test_resolve_arxiv_id_by_title_prefers_huggingface_search_before_arxiv():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.calls = []

        async def get_huggingface_search_html(self, title):
            self.calls.append(title)
            return (
                """
                <div
                  data-target="DailyPapers"
                  data-props="{
                    &quot;query&quot;:{&quot;q&quot;:&quot;Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass&quot;},
                    &quot;searchResults&quot;:[
                      {
                        &quot;title&quot;:&quot;Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass&quot;,
                        &quot;paper&quot;:{&quot;id&quot;:&quot;2501.13928&quot;,&quot;title&quot;:&quot;Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass&quot;}
                      }
                    ]
                  }">
                </div>
                """,
                None,
            )

    class FakeArxivClient:
        async def get_arxiv_id_by_title(self, title):
            raise AssertionError("arXiv fallback should not be used when Hugging Face already matched")

    arxiv_id, source, error = await resolve_arxiv_id_by_title(
        "Fast3R: Towards 3D Reconstruction of 1000+ Images in One Forward Pass",
        discovery_client=FakeDiscoveryClient(),
        arxiv_client=FakeArxivClient(),
    )

    assert (arxiv_id, source, error) == ("2501.13928", "title_search_huggingface_exact", None)


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
        ("hf_paper", "2603.18493"),
        ("alphaxiv", "2603.18493"),
    ]


@pytest.mark.anyio
async def test_resolve_github_url_keeps_huggingface_title_search_when_direct_paper_page_request_fails():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.alphaxiv_token = "ax_token"
            self.calls = []

        async def get_huggingface_paper_html_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper", arxiv_id))
            if self.calls == [("hf_paper", "2603.18493")]:
                return None, "Hugging Face Papers timeout"
            return '<script>window.__DATA__={"githubRepo":"https://github.com/foo/bar"}</script>', None

        async def get_huggingface_search_html(self, title):
            self.calls.append(("hf_search", title))
            return '<a href="/papers/2603.18493">Paper Title</a>', None

        async def get_alphaxiv_paper_legacy(self, arxiv_id):
            self.calls.append(("alphaxiv", arxiv_id))
            raise AssertionError("AlphaXiv fallback should not be used when Hugging Face retry succeeds")

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == [
        ("hf_paper", "2603.18493"),
        ("hf_search", "Paper Title"),
        ("hf_paper", "2603.18493"),
    ]


@pytest.mark.anyio
async def test_resolve_github_url_uses_title_matched_huggingface_search_result_instead_of_first_search_hit():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.alphaxiv_token = "ax_token"
            self.calls = []

        async def get_huggingface_paper_html_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper", arxiv_id))
            if self.calls == [("hf_paper", "2603.18493")]:
                return None, "Hugging Face Papers timeout"
            return '<script>window.__DATA__={"githubRepo":"https://github.com/foo/bar"}</script>', None

        async def get_huggingface_search_html(self, title):
            self.calls.append(("hf_search", title))
            return (
                """
                <div
                  data-target="DailyPapers"
                  data-props="{
                    &quot;query&quot;:{&quot;q&quot;:&quot;Paper Title&quot;},
                    &quot;searchResults&quot;:[
                      {
                        &quot;title&quot;:&quot;Different Paper&quot;,
                        &quot;paper&quot;:{&quot;id&quot;:&quot;2603.99999&quot;,&quot;title&quot;:&quot;Different Paper&quot;}
                      },
                      {
                        &quot;title&quot;:&quot;Paper Title&quot;,
                        &quot;paper&quot;:{&quot;id&quot;:&quot;2603.18493&quot;,&quot;title&quot;:&quot;Paper Title&quot;}
                      }
                    ]
                  }">
                </div>
                """,
                None,
            )

        async def get_alphaxiv_paper_legacy(self, arxiv_id):
            self.calls.append(("alphaxiv", arxiv_id))
            raise AssertionError("AlphaXiv fallback should not run when Hugging Face search found the exact paper title")

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == [
        ("hf_paper", "2603.18493"),
        ("hf_search", "Paper Title"),
        ("hf_paper", "2603.18493"),
    ]


@pytest.mark.anyio
async def test_resolve_github_url_reads_semanticscholar_detail_pages():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = ""
            self.alphaxiv_token = ""
            self.calls = []

        async def get_semanticscholar_paper_html(self, url):
            self.calls.append(url)
            return (
                '<meta name="description" '
                'content="Code available at https://github.com/foo/bar.">',
                None,
            )

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://www.semanticscholar.org/paper/Foo/abc123"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == ["https://www.semanticscholar.org/paper/Foo/abc123"]


@pytest.mark.anyio
async def test_discovery_client_caches_concurrent_github_resolution_for_same_paper():
    from src.shared.discovery import DiscoveryClient

    client = DiscoveryClient(session=object(), huggingface_token="hf_token")
    calls = []

    async def fake_get_huggingface_paper_html_by_arxiv_id(arxiv_id):
        calls.append(arxiv_id)
        await asyncio.sleep(0)
        return '<script>window.__DATA__={"githubRepo":"https://github.com/foo/bar"}</script>', None

    async def fake_get_huggingface_search_html(title):
        raise AssertionError("title search should not run when direct paper page already contains the repo")

    client.get_huggingface_paper_html_by_arxiv_id = fake_get_huggingface_paper_html_by_arxiv_id
    client.get_huggingface_search_html = fake_get_huggingface_search_html

    seed = FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493")
    first, second = await asyncio.gather(
        client.resolve_github_url(seed),
        client.resolve_github_url(seed),
    )

    assert first == "https://github.com/foo/bar"
    assert second == "https://github.com/foo/bar"
    assert calls == ["2603.18493"]


@pytest.mark.anyio
async def test_discovery_client_does_not_serialize_different_upstreams_under_one_shared_semaphore():
    from src.shared.discovery import DiscoveryClient

    release_response = asyncio.Event()

    class FakeResponse:
        def __init__(self, payload, *, status=200):
            self.payload = payload
            self.status = status

        async def __aenter__(self):
            await release_response.wait()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return self.payload

        async def json(self):
            return self.payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None, params=None):
            self.calls.append(url)
            if "api.alphaxiv.org" in url:
                return FakeResponse({"paper": {"implementation": "https://github.com/foo/bar"}})
            return FakeResponse('<script>window.__DATA__={"githubRepo":"https://github.com/foo/bar"}</script>')

    session = FakeSession()
    client = DiscoveryClient(
        session=session,
        huggingface_token="hf_token",
        alphaxiv_token="ax_token",
        max_concurrent=1,
        min_interval=0,
    )

    hf_task = asyncio.create_task(client.get_huggingface_paper_html_by_arxiv_id("2603.18493"))
    alphaxiv_task = asyncio.create_task(client.get_alphaxiv_paper_legacy("2603.18493"))

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert session.calls == [
        "https://huggingface.co/papers/2603.18493",
        "https://api.alphaxiv.org/papers/v3/legacy/2603.18493",
    ]

    release_response.set()
    hf_result, alphaxiv_result = await asyncio.gather(hf_task, alphaxiv_task)

    assert hf_result == ('<script>window.__DATA__={"githubRepo":"https://github.com/foo/bar"}</script>', None)
    assert alphaxiv_result == ({"paper": {"implementation": "https://github.com/foo/bar"}}, None)


@pytest.mark.anyio
async def test_github_client_caches_concurrent_star_lookup_for_same_repo():
    class FakeResponse:
        status = 200

        async def __aenter__(self):
            await asyncio.sleep(0)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"stargazers_count": 123}

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None):
            self.calls.append(url)
            return FakeResponse()

    session = FakeSession()
    client = GitHubClient(session=session)

    first, second = await asyncio.gather(
        client.get_star_count("foo", "bar"),
        client.get_star_count("foo", "bar"),
    )

    assert first == (123, None)
    assert second == (123, None)
    assert session.calls == ["https://api.github.com/repos/foo/bar"]


@pytest.mark.anyio
async def test_rate_limiter_allows_multiple_waiters_to_sleep_without_holding_lock(monkeypatch):
    limiter = RateLimiter(min_interval=0.5)
    loop = asyncio.get_running_loop()
    limiter.last_request_time = loop.time()
    real_sleep = asyncio.sleep

    entered_sleeps = []
    release_sleep = asyncio.Event()

    async def fake_sleep(delay):
        entered_sleeps.append(delay)
        await release_sleep.wait()

    monkeypatch.setattr("src.shared.http.asyncio.sleep", fake_sleep)

    first = asyncio.create_task(limiter.acquire())
    second = asyncio.create_task(limiter.acquire())
    await real_sleep(0)
    await real_sleep(0)

    assert len(entered_sleeps) == 2

    release_sleep.set()
    await asyncio.gather(first, second)


@pytest.mark.anyio
async def test_resolve_github_url_retries_same_huggingface_page_once_after_successful_no_repo_result():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.alphaxiv_token = "ax_token"
            self.calls = []

        async def get_huggingface_paper_html_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper", arxiv_id))
            if self.calls == [("hf_paper", "2603.18493")]:
                return "<html><body>No repo here</body></html>", None
            return '<script>window.__DATA__={"githubRepo":"https://github.com/foo/bar"}</script>', None

        async def get_huggingface_search_html(self, title):
            self.calls.append(("hf_search", title))
            raise AssertionError("title search should not run when a second direct paper fetch already recovers the repo")

        async def get_alphaxiv_paper_legacy(self, arxiv_id):
            self.calls.append(("alphaxiv", arxiv_id))
            raise AssertionError("AlphaXiv fallback should not run when the second direct paper fetch recovers the repo")

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == [
        ("hf_paper", "2603.18493"),
        ("hf_paper", "2603.18493"),
    ]
