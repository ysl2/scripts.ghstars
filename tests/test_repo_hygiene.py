from pathlib import Path


def test_gitignore_ignores_html_and_csv_files_globally():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "*.html" in gitignore
    assert "*.csv" in gitignore


def test_pyproject_uses_scripts_ghstars_project_name():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'name = "scripts.ghstars"' in pyproject


def test_readme_does_not_link_maintainer_docs_from_top_level_usage_flow():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Maintainer-oriented docs:" not in readme
    assert "- [`ARCHITECTURE.md`](ARCHITECTURE.md)" not in readme
    assert "- [`docs/README.md`](docs/README.md)" not in readme


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


def test_docs_readme_explains_current_vs_historical_docs():
    docs_readme = Path("docs/README.md")

    assert docs_readme.exists()

    content = docs_readme.read_text(encoding="utf-8")
    assert "README.md" in content
    assert "ARCHITECTURE.md" in content
    assert "historical" in content.lower()


def test_orphan_readme_asset_is_removed():
    assert not Path(".assets/README/img/2026-03-01-16-02-06.png").exists()


def test_src_legacy_directory_is_absent():
    assert not Path("src/legacy").exists()


def test_readme_documents_the_runnable_pytest_command():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "uv run pytest" not in readme
    assert "uv run python -m pytest -q" in readme


def test_property_compatibility_facades_are_removed():
    assert not Path("src/shared/property_model.py").exists()
    assert not Path("src/shared/property_resolvers.py").exists()
    assert not Path("src/shared/paper_enrichment.py").exists()
