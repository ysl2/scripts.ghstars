from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import main


@pytest.mark.anyio
async def test_async_main_runs_notion_mode_when_no_args(monkeypatch):
    notion_runner = AsyncMock(return_value=0)
    html_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_html_mode", html_runner)

    exit_code = await main.async_main([])

    assert exit_code == 0
    notion_runner.assert_awaited_once_with()
    html_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_runs_html_mode_when_given_existing_html_path(tmp_path: Path, monkeypatch):
    html_path = tmp_path / "papers.html"
    html_path.write_text("<html></html>", encoding="utf-8")

    notion_runner = AsyncMock(return_value=0)
    html_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_html_mode", html_runner)

    exit_code = await main.async_main([str(html_path)])

    assert exit_code == 0
    notion_runner.assert_not_awaited()
    html_runner.assert_awaited_once_with(html_path)


@pytest.mark.anyio
async def test_async_main_returns_error_when_given_missing_html_path(tmp_path: Path, capsys, monkeypatch):
    html_path = tmp_path / "missing.html"

    notion_runner = AsyncMock(return_value=0)
    html_runner = AsyncMock(return_value=0)
    monkeypatch.setattr(main, "run_notion_mode", notion_runner)
    monkeypatch.setattr(main, "run_html_mode", html_runner)

    exit_code = await main.async_main([str(html_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert str(html_path) in captured.err
    notion_runner.assert_not_awaited()
    html_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_async_main_returns_usage_error_for_multiple_args(capsys):
    exit_code = await main.async_main(["a.html", "b.html"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Expected 0 or 1 positional arguments" in captured.err
