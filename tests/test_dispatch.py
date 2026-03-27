from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.app as app


@pytest.mark.anyio
async def test_async_main_runs_notion_mode_when_no_args(monkeypatch):
    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([])

    assert exit_code == 0
    notion_runner.assert_awaited_once_with()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_rejects_existing_html_path(tmp_path: Path, monkeypatch, capsys):
    html_path = tmp_path / "papers.html"
    html_path.write_text("<html></html>", encoding="utf-8")

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([str(html_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert f"Input file not found or invalid: {html_path}" in captured.err
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_runs_csv_mode_when_given_existing_csv_path(tmp_path: Path, monkeypatch):
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text("Name,Url\nPaper,https://arxiv.org/abs/2603.20000\n", encoding="utf-8")

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([str(csv_path)])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_awaited_once_with(csv_path)
    url_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_runs_url_mode_when_given_supported_arxivxplorer_url(monkeypatch):
    input_url = "https://arxivxplorer.com/?q=streaming+semantic+3d+reconstruction&cats=cs.CV&year=2026"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_runs_url_mode_when_given_supported_arxiv_list_url(monkeypatch):
    input_url = "https://arxiv.org/list/cs.CV/recent"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_runs_url_mode_when_given_supported_arxiv_catchup_url(monkeypatch):
    input_url = "https://arxiv.org/catchup/cs.CV/2026-03-26"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_runs_url_mode_when_given_supported_arxiv_archive_url(monkeypatch):
    input_url = "https://arxiv.org/list/cs.CV/2026-03"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_runs_url_mode_when_given_supported_arxiv_search_url(monkeypatch):
    input_url = "https://arxiv.org/search/?searchtype=all&query=reconstruction&abstracts=show&size=50&order=-submitted_date"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_runs_url_mode_when_given_supported_arxiv_advanced_search_url(monkeypatch):
    input_url = (
        "https://arxiv.org/search/advanced?advanced=&terms-0-operator=AND&terms-0-term=reconstruction"
        "&terms-0-field=all&terms-1-operator=AND&terms-1-term=semantic&terms-1-field=all"
        "&terms-2-operator=AND&terms-2-term=streaming&terms-2-field=all"
        "&classification-computer_science=y&classification-include_cross_list=include"
        "&date-filter_by=past_12&date-date_type=submitted_date&abstracts=hide&size=50&order=-submitted_date"
    )

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_routes_arxiv_abs_urls_to_relations_mode(monkeypatch):
    input_url = "https://arxiv.org/abs/2603.23502"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    arxiv_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)
    monkeypatch.setattr(app, "run_arxiv_relations_mode", arxiv_runner)
    monkeypatch.setattr(app, "_HAS_ARXIV_RELATIONS_RUNNER", True)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()
    arxiv_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_routes_arxiv_pdf_urls_to_relations_mode(monkeypatch):
    input_url = "https://arxiv.org/pdf/2603.23502.pdf"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    arxiv_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)
    monkeypatch.setattr(app, "run_arxiv_relations_mode", arxiv_runner)
    monkeypatch.setattr(app, "_HAS_ARXIV_RELATIONS_RUNNER", True)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()
    arxiv_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_fails_when_arxiv_runner_not_installed(monkeypatch, capsys):
    input_url = "https://arxiv.org/abs/2603.23502"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    arxiv_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)
    monkeypatch.setattr(app, "run_arxiv_relations_mode", arxiv_runner)
    monkeypatch.setattr(app, "_HAS_ARXIV_RELATIONS_RUNNER", False)

    exit_code = await app.async_main([input_url])
    captured = capsys.readouterr()

    assert exit_code == app.ARXIV_RUNNER_UNAVAILABLE_EXIT_CODE
    assert app.ARXIV_RUNNER_UNAVAILABLE_MESSAGE in captured.err
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()
    arxiv_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_treats_abs_help_as_unsupported(monkeypatch, capsys):
    input_url = "https://arxiv.org/abs/help"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    arxiv_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)
    monkeypatch.setattr(app, "run_arxiv_relations_mode", arxiv_runner)
    monkeypatch.setattr(app, "_HAS_ARXIV_RELATIONS_RUNNER", True)

    exit_code = await app.async_main([input_url])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Input file or URL not supported" in captured.err
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()
    arxiv_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_runs_url_mode_when_given_supported_huggingface_papers_url(monkeypatch):
    input_url = "https://huggingface.co/papers/trending?q=semantic"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_runs_url_mode_when_given_supported_semanticscholar_url(monkeypatch):
    input_url = "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&sort=pub-date"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([input_url])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_awaited_once_with(input_url)


@pytest.mark.anyio
async def test_async_main_returns_error_when_given_missing_html_path(tmp_path: Path, capsys, monkeypatch):
    html_path = tmp_path / "missing.html"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([str(html_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert f"Input file not found or invalid: {html_path}" in captured.err
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_returns_error_when_given_missing_csv_path(tmp_path: Path, capsys, monkeypatch):
    csv_path = tmp_path / "missing.csv"

    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main([str(csv_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert f"Input file not found or invalid: {csv_path}" in captured.err
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_returns_error_for_unsupported_url(capsys, monkeypatch):
    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(app, "run_notion_mode", notion_runner)
    monkeypatch.setattr(app, "run_csv_mode", csv_runner)
    monkeypatch.setattr(app, "run_url_mode", url_runner, raising=False)

    exit_code = await app.async_main(["https://example.com/search?q=test"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Input file or URL not supported" in captured.err
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_returns_usage_error_for_multiple_args(capsys):
    exit_code = await app.async_main(["a.csv", "b.csv"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Expected 0 or 1 positional arguments" in captured.err
