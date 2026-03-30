from pathlib import Path


def test_gitignore_ignores_html_and_csv_files_globally():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "*.html" in gitignore
    assert "*.csv" in gitignore


def test_pyproject_uses_scripts_ghstars_project_name():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'name = "scripts.ghstars"' in pyproject


def test_alphaxiv_doc_uses_public_paper_endpoint():
    script = Path("docs/find_alphaxiv_github.sh").read_text(encoding="utf-8")

    assert "/papers/v3/legacy/" not in script
    assert "/papers/v3/" in script


def test_alphaxiv_helper_lives_under_shared_modules():
    assert Path("src/shared/alphaxiv.py").exists()
    assert not Path("src/legacy/alphaxiv.py").exists()


def test_no_python_source_files_live_under_src_legacy():
    legacy_dir = Path("src/legacy")
    if not legacy_dir.exists():
        return

    python_files = sorted(
        path.relative_to(legacy_dir).as_posix()
        for path in legacy_dir.rglob("*.py")
    )
    assert python_files == []
