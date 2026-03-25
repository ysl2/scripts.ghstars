from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import main


@pytest.mark.anyio
async def test_async_main_runs_notion_mode_when_no_args(monkeypatch):
    notion_runner = AsyncMock(return_value=0)
    csv_runner = AsyncMock(return_value=0)
    url_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_csv_mode", csv_runner)
    monkeypatch.setattr(main, "run_url_mode", url_runner, raising=False)

    exit_code = await main.async_main([])

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
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_csv_mode", csv_runner)
    monkeypatch.setattr(main, "run_url_mode", url_runner, raising=False)

    exit_code = await main.async_main([str(html_path)])
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
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_csv_mode", csv_runner)
    monkeypatch.setattr(main, "run_url_mode", url_runner, raising=False)

    exit_code = await main.async_main([str(csv_path)])

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
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_csv_mode", csv_runner)
    monkeypatch.setattr(main, "run_url_mode", url_runner, raising=False)

    exit_code = await main.async_main([input_url])

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
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_csv_mode", csv_runner)
    monkeypatch.setattr(main, "run_url_mode", url_runner, raising=False)

    exit_code = await main.async_main([str(html_path)])

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
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_csv_mode", csv_runner)
    monkeypatch.setattr(main, "run_url_mode", url_runner, raising=False)

    exit_code = await main.async_main([str(csv_path)])

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
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_csv_mode", csv_runner)
    monkeypatch.setattr(main, "run_url_mode", url_runner, raising=False)

    exit_code = await main.async_main(["https://example.com/search?q=test"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Input file or URL not supported" in captured.err
    notion_runner.assert_not_awaited()
    csv_runner.assert_not_awaited()
    url_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_returns_usage_error_for_multiple_args(capsys):
    exit_code = await main.async_main(["a.csv", "b.csv"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Expected 0 or 1 positional arguments" in captured.err
