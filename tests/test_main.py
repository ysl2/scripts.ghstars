from pathlib import Path

import pytest

from html_to_csv.runner import load_runtime_config, run_html_mode


def test_load_runtime_config_reads_only_optional_tokens():
    config = load_runtime_config(
        {
            "GITHUB_TOKEN": "gh_token",
            "HUGGINGFACE_TOKEN": "hf_token",
            "ALPHAXIV_TOKEN": "ax_token",
        }
    )

    assert config == {
        "github_token": "gh_token",
        "huggingface_token": "hf_token",
        "alphaxiv_token": "ax_token",
    }


def test_load_runtime_config_defaults_missing_values_to_empty_strings():
    assert load_runtime_config({}) == {
        "github_token": "",
        "huggingface_token": "",
        "alphaxiv_token": "",
    }


@pytest.mark.anyio
async def test_run_html_mode_runs_pipeline_and_writes_same_name_csv(tmp_path: Path):
    html_path = tmp_path / "papers.html"
    html_path.write_text(
        """
        <div class="chakra-card__root">
          <h2 class="chakra-heading">Test Paper</h2>
          <a href="https://arxiv.org/abs/2603.20000v2">View</a>
        </div>
        """,
        encoding="utf-8",
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token
            self.alphaxiv_token = alphaxiv_token

        async def resolve_github_url(self, seed):
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        def __init__(self, session, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.github_token = github_token

        async def get_star_count(self, owner, repo):
            return 42, None

    exit_code = await run_html_mode(
        html_path,
        session_factory=lambda **kwargs: FakeSession(),
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    assert exit_code == 0
    assert (tmp_path / "papers.csv").exists()


@pytest.mark.anyio
async def test_run_html_mode_prints_progress_before_completion(tmp_path: Path, capsys):
    html_path = tmp_path / "papers.html"
    html_path.write_text(
        """
        <div class="chakra-card__root">
          <h2 class="chakra-heading">Test Paper</h2>
          <a href="https://arxiv.org/abs/2603.20000v2">View</a>
        </div>
        """,
        encoding="utf-8",
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token
            self.alphaxiv_token = alphaxiv_token

        async def resolve_github_url(self, seed):
            return "https://github.com/foo/bar"

    class FakeGitHubClient:
        def __init__(self, session, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.github_token = github_token

        async def get_star_count(self, owner, repo):
            return 0, None

    exit_code = await run_html_mode(
        html_path,
        session_factory=lambda **kwargs: FakeSession(),
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Found 1 papers" in captured.out
    assert "[1/1] Test Paper" in captured.out
    assert "foo/bar" in captured.out
    assert "Updated: N/A → 0" in captured.out


@pytest.mark.anyio
async def test_run_html_mode_prints_skip_progress_for_unresolved_rows(tmp_path: Path, capsys):
    html_path = tmp_path / "papers.html"
    html_path.write_text(
        """
        <div class="chakra-card__root">
          <h2 class="chakra-heading">Test Paper</h2>
          <a href="https://arxiv.org/abs/2603.20000v2">View</a>
        </div>
        """,
        encoding="utf-8",
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDiscoveryClient:
        def __init__(self, session, *, huggingface_token="", alphaxiv_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.huggingface_token = huggingface_token
            self.alphaxiv_token = alphaxiv_token

        async def resolve_github_url(self, seed):
            return ""

    class FakeGitHubClient:
        def __init__(self, session, github_token="", max_concurrent=0, min_interval=0):
            self.session = session
            self.github_token = github_token

        async def get_star_count(self, owner, repo):
            raise AssertionError("star lookup should not be reached")

    exit_code = await run_html_mode(
        html_path,
        session_factory=lambda **kwargs: FakeSession(),
        discovery_client_cls=FakeDiscoveryClient,
        github_client_cls=FakeGitHubClient,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[1/1] Test Paper" in captured.out
    assert "Skipped: No Github URL found from discovery" in captured.out
    assert "Resolved: 0" in captured.out


@pytest.mark.anyio
async def test_run_html_mode_returns_error_when_input_is_missing(tmp_path: Path, capsys):
    missing_path = tmp_path / "missing.html"

    exit_code = await run_html_mode(missing_path)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert str(missing_path) in captured.err
