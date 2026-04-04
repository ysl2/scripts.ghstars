# Relation Target Cache Warmup Tests

## Purpose

- Capture the missing regression coverage for single-paper relation exports so that warming the target paper itself behaves like the related-paper path.
- Document the two new tests (success path and best-effort failure) before touching production code, keeping the existing relation-export CSV assertions intact.

## Context

- `export_arxiv_relations_to_csv(...)` currently normalizes references and citations into seeds, exports two CSVs via `export_paper_seeds_to_csv(...)`, and only warms the related seeds through `export_paper_seeds_to_csv` / `sync_paper_seed`. The target paper `https://arxiv.org/abs/2603.23502` is resolved earlier but never flows through the shared `content_cache.ensure_local_content_cache` path, so there is no test to guard the desired warmup.
- The repo already has integration-style tests that exercise the normal Semantic Scholar + ArXiv fakes and read the generated CSV files to assert the row content and number of exports. We should reuse that harness to express the new expectations.

## Proposed tests

1. **Target warmup success path**
   - Reuse the `fake_export`-style harness from the existing relation-export integration tests: provide fake ArXiv/Semantic Scholar clients that return the known references/citations from `2603.23502`, a `RecordingContentCache` that logs each `ensure_local_content_cache` argument, and `tmp_path` for CSV output.
   - Run `export_arxiv_relations_to_csv(...)` and assert:
     * Both `references` and `citations` CSV files are emitted with the same final row sets as the existing test (i.e., `Direct Reference`, `Mapped Reference`, `Publisher Reference` for references, and `Citation With Missing Stars` for citations), so the CSV shape is unchanged.
     * `content_cache.ensure_local_content_cache` was invoked exactly once for `https://arxiv.org/abs/2603.23502` (the target) in addition to the related reference/citation URLs.
     * Only two CSV files exist under `tmp_path`, confirming no extra exports were added.
   - This makes sure we guard both the target warmup and the related output semantics.

2. **Target warmup best-effort failure**
   - Use the same fake clients so the reference/citation export works as before. Provide a content cache stub whose `ensure_local_content_cache` raises when the canonical target URL is passed but otherwise records calls.
   - Run `export_arxiv_relations_to_csv` and assert:
     * The call still succeeds (no exception).
     * The references CSV file contains the expected related reference rows, proving the main export is unaffected.
     * The citations export still occurs (two CSVs exist) and the overall result is returned.
     * The raised error on the target warmup is swallowed (i.e., the related seeds still warm themselves and the failure does not stop the process).

## Validation & follow-up

- After adding the tests, run `uv run pytest tests/test_arxiv_relations.py -k "target_paper_warmup or warms_target_paper_cache" -vv` to confirm they fail currently because the target warmup path does not exist yet.
- The new tests document both the expected normal behavior and the best-effort tolerance gap so a subsequent change can satisfy them.
