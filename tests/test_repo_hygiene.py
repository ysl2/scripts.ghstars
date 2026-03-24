from pathlib import Path


def test_gitignore_ignores_html_and_csv_files_globally():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "*.html" in gitignore
    assert "*.csv" in gitignore
