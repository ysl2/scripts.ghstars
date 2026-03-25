from pathlib import Path


def test_gitignore_ignores_html_and_csv_files_globally():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "*.html" in gitignore
    assert "*.csv" in gitignore


def test_pyproject_uses_scripts_ghstars_project_name():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'name = "scripts.ghstars"' in pyproject
