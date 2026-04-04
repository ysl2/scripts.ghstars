from pathlib import Path


def test_runtime_modules_are_available_under_src_layout():
    import src.app  # noqa: F401
    import src.csv_update.runner  # noqa: F401
    import src.notion_sync.runner  # noqa: F401
    import src.shared.runtime  # noqa: F401
    import src.url_to_csv.sources  # noqa: F401


def test_root_main_exposes_src_app_main():
    import main
    import src.app

    assert main.main is src.app.main


def test_converged_core_shared_boundary_is_documented():
    assert Path("src/core/record_model.py").exists()
    assert Path("src/core/record_sync.py").exists()
    assert not Path("src/shared/property_model.py").exists()
    assert not Path("src/shared/property_resolvers.py").exists()

    architecture = Path("ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "`src/core/*` is the only property/domain API." in architecture
    assert (
        "`src/shared/*` holds lower-level mechanics such as caches, HTTP, "
        "provider clients, and normalization primitives."
    ) in architecture
    assert (
        "`url_to_csv` may call the core normalization workflow for pre-filtering, "
        "but does not own a separate normalization semantics layer."
    ) in architecture
    assert (
        "compatibility wrappers formerly under `src/shared/property_*` and "
        "`src/shared/paper_enrichment.py` are removed."
    ) in architecture
