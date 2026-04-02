import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.shared.alphaxiv as alphaxiv
from src.shared.alphaxiv_content import AlphaXivContentClient
from src.shared.discovery import (
    DiscoveryClient,
    find_huggingface_paper_id_in_search_html,
    find_github_url_in_huggingface_paper_html,
    resolve_github_url,
)
from src.shared.http import RateLimiter
from src.shared.github import GitHubClient, extract_owner_repo, normalize_github_url
from src.shared.repo_metadata_cache import RepoMetadataCacheStore


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


def test_alphaxiv_module_no_longer_exposes_legacy_client():
    assert not hasattr(alphaxiv, "AlphaXivLegacyClient")


def test_find_github_url_in_alphaxiv_payload_prefers_known_fields():
    payload = {
        "paper": {
            "implementation": "https://github.com/foo/bar",
            "marimo_implementation": None,
            "paper_group": {"resources": []},
            "resources": [],
        }
    }

    assert alphaxiv.find_github_url_in_alphaxiv_payload(payload) == "https://github.com/foo/bar"


def test_find_github_url_in_alphaxiv_page_html_prefers_embedded_resource_repo_over_feedback_link():
    html = """
    <a href="https://github.com/alphaxiv/feedback">Feedback</a>
    <script>
      resources:$R[1123]={github:$R[1124]={url:"https://github.com/YOUNG-bit/open_semantic_slam",description:"ICRA2025 repo"}}
    </script>
    """

    assert (
        alphaxiv.find_github_url_in_alphaxiv_page_html(html)
        == "https://github.com/YOUNG-bit/open_semantic_slam"
    )


def test_find_github_url_in_alphaxiv_html_reads_github_resource_from_page_state():
    html = """
    resources:$R[1123]={github:$R[1124]={url:"https://github.com/YOUNG-bit/open_semantic_slam",description:"ICRA2025"}}
    """

    assert (
        alphaxiv.find_github_url_in_alphaxiv_html(html)
        == "https://github.com/YOUNG-bit/open_semantic_slam"
    )


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


def test_discovery_client_enforces_huggingface_rate_limit_floor():
    client = DiscoveryClient(session=object(), min_interval=0.2)

    assert client.rate_limiter.min_interval == 0.5


@pytest.mark.anyio
async def test_discovery_client_queries_public_alphaxiv_paper_endpoint():
    class FakeResponse:
        status = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {}

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None, params=None):
            self.calls.append((url, headers, params))
            return FakeResponse()

    session = FakeSession()
    client = DiscoveryClient(session=session)

    payload, error = await client.get_alphaxiv_paper_payload_by_arxiv_id("2603.18493")

    assert (payload, error) == (None, None)
    assert session.calls == [
        (
            "https://api.alphaxiv.org/papers/v3/2603.18493",
            {
                "Accept": "application/json",
                "User-Agent": "scripts.ghstars",
            },
            None,
        )
    ]


@pytest.mark.anyio
async def test_discovery_client_adds_bearer_token_to_alphaxiv_paper_endpoint():
    class FakeResponse:
        status = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {}

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None, params=None):
            self.calls.append((url, headers, params))
            return FakeResponse()

    session = FakeSession()
    client = DiscoveryClient(session=session, alphaxiv_token="ax_token")

    payload, error = await client.get_alphaxiv_paper_payload_by_arxiv_id("2603.18493")

    assert (payload, error) == (None, None)
    assert session.calls == [
        (
            "https://api.alphaxiv.org/papers/v3/2603.18493",
            {
                "Accept": "application/json",
                "User-Agent": "scripts.ghstars",
                "Authorization": "Bearer ax_token",
            },
            None,
        )
    ]


@pytest.mark.anyio
async def test_discovery_client_queries_public_alphaxiv_page_endpoint():
    class FakeResponse:
        status = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return ""

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None, params=None):
            self.calls.append((url, headers, params))
            return FakeResponse()

    session = FakeSession()
    client = DiscoveryClient(session=session)

    html, error = await client.get_alphaxiv_paper_html_by_arxiv_id("2603.18493")

    assert (html, error) == (None, None)
    assert session.calls == [
        (
            "https://www.alphaxiv.org/abs/2603.18493",
            {
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "scripts.ghstars",
            },
            None,
        )
    ]


@pytest.mark.anyio
async def test_alphaxiv_content_client_keeps_anonymous_requests_without_token():
    class FakeResponse:
        status = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {}

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None):
            self.calls.append((url, headers))
            return FakeResponse()

    session = FakeSession()
    client = AlphaXivContentClient(session=session)

    paper_payload, paper_error = await client.get_paper_payload_by_arxiv_id("2603.18493")
    overview_payload, overview_error = await client.get_overview_payload_by_version_id("v2603.18493")

    assert (paper_payload, paper_error) == (None, None)
    assert (overview_payload, overview_error) == (None, None)
    assert session.calls == [
        (
            "https://api.alphaxiv.org/papers/v3/2603.18493",
            {
                "Accept": "application/json",
                "User-Agent": "scripts.ghstars",
            },
        ),
        (
            "https://api.alphaxiv.org/papers/v3/v2603.18493/overview/en",
            {
                "Accept": "application/json",
                "User-Agent": "scripts.ghstars",
            },
        ),
    ]


@pytest.mark.anyio
async def test_alphaxiv_content_client_adds_bearer_token_when_configured():
    class FakeResponse:
        status = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {}

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None):
            self.calls.append((url, headers))
            return FakeResponse()

    session = FakeSession()
    client = AlphaXivContentClient(session=session, alphaxiv_token="ax_token")

    paper_payload, paper_error = await client.get_paper_payload_by_arxiv_id("2603.18493")
    overview_payload, overview_error = await client.get_overview_payload_by_version_id("v2603.18493")

    assert (paper_payload, paper_error) == (None, None)
    assert (overview_payload, overview_error) == (None, None)
    assert session.calls == [
        (
            "https://api.alphaxiv.org/papers/v3/2603.18493",
            {
                "Accept": "application/json",
                "User-Agent": "scripts.ghstars",
                "Authorization": "Bearer ax_token",
            },
        ),
        (
            "https://api.alphaxiv.org/papers/v3/v2603.18493/overview/en",
            {
                "Accept": "application/json",
                "User-Agent": "scripts.ghstars",
                "Authorization": "Bearer ax_token",
            },
        ),
    ]


@pytest.mark.anyio
async def test_resolve_github_url_uses_huggingface_exact_api_payload_before_search_or_legacy_html():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.calls = []

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper_api", arxiv_id))
            return {"id": arxiv_id, "githubRepo": "https://github.com/foo/bar"}, None

        async def get_huggingface_paper_search_results(self, title, *, limit=1):
            self.calls.append(("hf_search_api", title, limit))
            raise AssertionError("search API should not run when exact API payload already has the repo")

        async def get_huggingface_paper_html_by_arxiv_id(self, arxiv_id):
            raise AssertionError("legacy Hugging Face HTML paper fetch should not run")

        async def get_huggingface_search_html(self, title):
            raise AssertionError("legacy Hugging Face HTML search should not run")

        async def get_alphaxiv_paper_payload_by_arxiv_id(self, arxiv_id):
            raise AssertionError("AlphaXiv should not run when HF exact already has the repo")

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == [
        ("hf_paper_api", "2603.18493"),
    ]


@pytest.mark.anyio
async def test_resolve_github_url_falls_back_to_alphaxiv_html_after_hf_exact_no_repo():
    class FakeRepoCache:
        def __init__(self):
            self.found_calls = []
            self.no_repo_calls = []

        def get(self, arxiv_url):
            return None

        def record_found_repo(self, arxiv_url, github_url):
            self.found_calls.append((arxiv_url, github_url))

        def record_exact_no_repo(self, arxiv_url):
            self.no_repo_calls.append(arxiv_url)

    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.calls = []
            self.repo_cache = FakeRepoCache()

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper_api", arxiv_id))
            return {"id": arxiv_id, "githubRepo": None}, None

        async def get_alphaxiv_paper_html_by_arxiv_id(self, arxiv_id):
            self.calls.append(("alphaxiv_paper_html", arxiv_id))
            return (
                'resources:$R[1123]={github:$R[1124]={url:"https://github.com/foo/bar"}}',
                None,
            )

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == [
        ("hf_paper_api", "2603.18493"),
        ("alphaxiv_paper_html", "2603.18493"),
    ]
    assert client.repo_cache.found_calls == [
        ("https://arxiv.org/abs/2603.18493", "https://github.com/foo/bar"),
    ]
    assert client.repo_cache.no_repo_calls == []


@pytest.mark.anyio
async def test_resolve_github_url_falls_back_to_alphaxiv_html_after_hf_exact_404():
    class FakeRepoCache:
        def __init__(self):
            self.found_calls = []
            self.no_repo_calls = []

        def get(self, arxiv_url):
            return None

        def record_found_repo(self, arxiv_url, github_url):
            self.found_calls.append((arxiv_url, github_url))

        def record_exact_no_repo(self, arxiv_url):
            self.no_repo_calls.append(arxiv_url)

    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.calls = []
            self.repo_cache = FakeRepoCache()

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper_api", arxiv_id))
            return None, None

        async def get_alphaxiv_paper_html_by_arxiv_id(self, arxiv_id):
            self.calls.append(("alphaxiv_paper_html", arxiv_id))
            return (
                'resources:$R[1123]={github:$R[1124]={url:"https://github.com/foo/bar"}}',
                None,
            )

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == [
        ("hf_paper_api", "2603.18493"),
        ("alphaxiv_paper_html", "2603.18493"),
    ]
    assert client.repo_cache.found_calls == [
        ("https://arxiv.org/abs/2603.18493", "https://github.com/foo/bar"),
    ]
    assert client.repo_cache.no_repo_calls == []


@pytest.mark.anyio
async def test_resolve_github_url_falls_back_to_alphaxiv_page_html_after_hf_no_repo():
    class FakeRepoCache:
        def __init__(self):
            self.found_calls = []
            self.no_repo_calls = []

        def get(self, arxiv_url):
            return None

        def record_found_repo(self, arxiv_url, github_url):
            self.found_calls.append((arxiv_url, github_url))

        def record_exact_no_repo(self, arxiv_url):
            self.no_repo_calls.append(arxiv_url)

    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.calls = []
            self.repo_cache = FakeRepoCache()

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper_api", arxiv_id))
            return {"id": arxiv_id, "githubRepo": None}, None

        async def get_alphaxiv_paper_html_by_arxiv_id(self, arxiv_id):
            self.calls.append(("alphaxiv_paper_html", arxiv_id))
            return (
                '<a href="https://github.com/alphaxiv/feedback">Feedback</a>'
                'resources:$R[1123]={github:$R[1124]={url:"https://github.com/YOUNG-bit/open_semantic_slam",description:"ICRA2025 repo"}}',
                None,
            )

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="OpenGS-SLAM", url="https://arxiv.org/abs/2503.01646"),
        client,
    )

    assert github_url == "https://github.com/YOUNG-bit/open_semantic_slam"
    assert client.calls == [
        ("hf_paper_api", "2503.01646"),
        ("alphaxiv_paper_html", "2503.01646"),
    ]
    assert client.repo_cache.found_calls == [
        ("https://arxiv.org/abs/2503.01646", "https://github.com/YOUNG-bit/open_semantic_slam"),
    ]
    assert client.repo_cache.no_repo_calls == []


@pytest.mark.anyio
async def test_resolve_github_url_falls_back_to_alphaxiv_html_after_hf_no_repo():
    class FakeRepoCache:
        def __init__(self):
            self.found_calls = []
            self.no_repo_calls = []

        def get(self, arxiv_url):
            return None

        def record_found_repo(self, arxiv_url, github_url):
            self.found_calls.append((arxiv_url, github_url))

        def record_exact_no_repo(self, arxiv_url):
            self.no_repo_calls.append(arxiv_url)

    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.calls = []
            self.repo_cache = FakeRepoCache()

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper_api", arxiv_id))
            return {"id": arxiv_id, "githubRepo": None}, None

        async def get_alphaxiv_paper_html_by_arxiv_id(self, arxiv_id):
            self.calls.append(("alphaxiv_paper_html", arxiv_id))
            return (
                'resources:$R[1123]={github:$R[1124]={url:"https://github.com/YOUNG-bit/open_semantic_slam"}}',
                None,
            )

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/YOUNG-bit/open_semantic_slam"
    assert client.calls == [
        ("hf_paper_api", "2603.18493"),
        ("alphaxiv_paper_html", "2603.18493"),
    ]
    assert client.repo_cache.found_calls == [
        ("https://arxiv.org/abs/2603.18493", "https://github.com/YOUNG-bit/open_semantic_slam"),
    ]
    assert client.repo_cache.no_repo_calls == []


@pytest.mark.anyio
async def test_resolve_github_url_uses_cached_repo_before_hf_exact():
    class FakeDiscoveryClient:
        def __init__(self):
            self.repo_cache = SimpleNamespace(
                get=lambda arxiv_url: SimpleNamespace(
                    arxiv_url=arxiv_url,
                    github_url="https://github.com/foo/bar",
                    created_at="2026-03-27T00:00:00+00:00",
                    updated_at="2026-03-27T00:00:00+00:00",
                    last_repo_discovery_checked_at="2026-03-27T00:00:00+00:00",
                )
            )

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            raise AssertionError("HF exact should not run when cache already has a repo")

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"


@pytest.mark.anyio
async def test_resolve_github_url_skips_hf_exact_when_cached_no_repo_is_still_fresh():
    recent = datetime.now(timezone.utc).isoformat()

    class FakeDiscoveryClient:
        def __init__(self):
            self.repo_cache = SimpleNamespace(
                get=lambda arxiv_url: SimpleNamespace(
                    arxiv_url=arxiv_url,
                    github_url=None,
                    created_at=recent,
                    updated_at=recent,
                    last_repo_discovery_checked_at=recent,
                )
            )
            self.repo_discovery_no_repo_recheck_days = 7

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            raise AssertionError("HF exact should not run while cached no-repo entry is still fresh")

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url is None


@pytest.mark.anyio
async def test_resolve_github_url_queries_hf_exact_when_cached_no_repo_entry_is_stale():
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()

    class FakeRepoCache:
        def __init__(self):
            self.found_calls = []
            self.no_repo_calls = []

        def get(self, arxiv_url):
            return SimpleNamespace(
                arxiv_url=arxiv_url,
                github_url=None,
                created_at=stale,
                updated_at=stale,
                last_repo_discovery_checked_at=stale,
            )

        def record_found_repo(self, arxiv_url, github_url):
            self.found_calls.append((arxiv_url, github_url))

        def record_discovery_no_repo(self, arxiv_url):
            self.no_repo_calls.append(arxiv_url)

    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.calls = []
            self.repo_cache = FakeRepoCache()
            self.repo_discovery_no_repo_recheck_days = 7

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper_api", arxiv_id))
            return {"id": arxiv_id, "githubRepo": "https://github.com/foo/bar"}, None

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url == "https://github.com/foo/bar"
    assert client.calls == [("hf_paper_api", "2603.18493")]
    assert client.repo_cache.found_calls == [
        ("https://arxiv.org/abs/2603.18493", "https://github.com/foo/bar"),
    ]
    assert client.repo_cache.no_repo_calls == []


@pytest.mark.anyio
async def test_resolve_github_url_records_successful_exact_no_repo_in_cache():
    class FakeRepoCache:
        def __init__(self):
            self.no_repo_calls = []

        def get(self, arxiv_url):
            return None

        def record_discovery_no_repo(self, arxiv_url):
            self.no_repo_calls.append(arxiv_url)

    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.repo_cache = FakeRepoCache()

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            return {"id": arxiv_id, "githubRepo": None}, None

        async def get_alphaxiv_paper_html_by_arxiv_id(self, arxiv_id):
            return '<a href="https://github.com/alphaxiv/feedback">Feedback</a>', None

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url is None
    assert client.repo_cache.no_repo_calls == ["https://arxiv.org/abs/2603.18493"]


@pytest.mark.anyio
async def test_resolve_github_url_does_not_record_no_repo_when_exact_api_errors():
    class FakeRepoCache:
        def __init__(self):
            self.no_repo_calls = []

        def get(self, arxiv_url):
            return None

        def record_discovery_no_repo(self, arxiv_url):
            self.no_repo_calls.append(arxiv_url)

    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.repo_cache = FakeRepoCache()

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            return None, "Hugging Face Papers API timeout"

        async def get_alphaxiv_paper_html_by_arxiv_id(self, arxiv_id):
            return '<a href="https://github.com/alphaxiv/feedback">Feedback</a>', None

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url is None
    assert client.repo_cache.no_repo_calls == []


@pytest.mark.anyio
async def test_resolve_github_url_does_not_record_no_repo_when_alphaxiv_html_errors_after_hf_no_repo():
    class FakeRepoCache:
        def __init__(self):
            self.no_repo_calls = []

        def get(self, arxiv_url):
            return None

        def record_discovery_no_repo(self, arxiv_url):
            self.no_repo_calls.append(arxiv_url)

    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.repo_cache = FakeRepoCache()

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            return {"id": arxiv_id, "githubRepo": None}, None

        async def get_alphaxiv_paper_html_by_arxiv_id(self, arxiv_id):
            return None, "AlphaXiv page timeout"

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url is None
    assert client.repo_cache.no_repo_calls == []


@pytest.mark.anyio
@pytest.mark.anyio
async def test_resolve_github_url_reads_semanticscholar_detail_pages():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = ""
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

    async def fake_get_huggingface_paper_payload_by_arxiv_id(arxiv_id):
        calls.append(arxiv_id)
        await asyncio.sleep(0)
        return {"id": arxiv_id, "githubRepo": "https://github.com/foo/bar"}, None

    async def fake_get_huggingface_paper_search_results(title, *, limit=1):
        raise AssertionError("search API should not run when exact paper payload already contains the repo")

    client.get_huggingface_paper_payload_by_arxiv_id = fake_get_huggingface_paper_payload_by_arxiv_id
    client.get_huggingface_paper_search_results = fake_get_huggingface_paper_search_results

    first_seed = FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493")
    second_seed = FakeSeed(name="Totally Different Display Name", url="https://arxiv.org/abs/2603.18493")
    first, second = await asyncio.gather(
        client.resolve_github_url(first_seed),
        client.resolve_github_url(second_seed),
    )

    assert first == "https://github.com/foo/bar"
    assert second == "https://github.com/foo/bar"
    assert calls == ["2603.18493"]


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
async def test_github_client_fetches_repo_metadata_with_created_and_about():
    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "stargazers_count": 123,
                "created_at": "2024-01-01T00:00:00Z",
                "description": "repo",
            }

    class FakeSession:
        def get(self, url, headers=None):
            return FakeResponse()

    session = FakeSession()
    client = GitHubClient(session=session)

    metadata, error = await client.get_repo_metadata("foo", "bar")

    assert error is None
    assert metadata.stars == 123
    assert metadata.created == "2024-01-01T00:00:00Z"
    assert metadata.about == "repo"


def test_repo_metadata_cache_store_round_trips_created_value(tmp_path):
    store = RepoMetadataCacheStore(tmp_path / "cache.db")
    store.record_created("https://github.com/foo/bar", "2024-01-01T00:00:00Z")

    entry = store.get("https://github.com/foo/bar")
    store.close()

    assert entry is not None
    assert entry.github_url == "https://github.com/foo/bar"
    assert entry.created == "2024-01-01T00:00:00Z"


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
async def test_resolve_github_url_returns_none_when_exact_api_returns_404():
    class FakeDiscoveryClient:
        def __init__(self):
            self.huggingface_token = "hf_token"
            self.calls = []

        async def get_huggingface_paper_payload_by_arxiv_id(self, arxiv_id):
            self.calls.append(("hf_paper_api", arxiv_id))
            return None, None

    client = FakeDiscoveryClient()
    github_url = await resolve_github_url(
        FakeSeed(name="Paper Title", url="https://arxiv.org/abs/2603.18493"),
        client,
    )

    assert github_url is None
    assert client.calls == [
        ("hf_paper_api", "2603.18493"),
    ]
