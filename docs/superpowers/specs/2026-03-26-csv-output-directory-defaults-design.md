# CSV Output Directory Defaults Design

**Goal**

Keep project-generated CSV files out of the repository root by default, while preserving the existing in-place update behavior for `csv -> csv` and leaving Notion mode unchanged.

**Current State**

- `url -> csv` exports derive a readable filename and, when no `output_dir` is provided, write into the current working directory.
- `csv -> csv` updates rewrite the input CSV path in place.
- Notion mode does not produce CSV files.

**Design Decision**

Change the default destination only for flows that create a new CSV file.

That means:

1. `url -> csv` writes to `./output/<generated-name>.csv` by default.
2. `csv -> csv` keeps rewriting the input file in place.
3. Notion mode stays exactly as it is today.

**Why this boundary**

The mode split already matches user intent:

- URL input creates a new export artifact.
- CSV input enriches an existing artifact.
- Notion input updates remote state instead of writing a CSV.

Applying one global default output directory at the shared CSV-writing layer would force exceptions back in for update-in-place behavior and would make the code harder to reason about.

**Proposed Changes**

- Update the shared URL-export filename helper so its default directory is `Path("output")` instead of the repository root.
- Ensure the target parent directory exists before writing a CSV file so first-run exports do not fail when `./output` has not been created yet.
- Leave the CSV update pipeline untouched so it continues to write back to the original file path.

**Scope**

In scope:

- default output location for `url -> csv`
- automatic creation of `./output` when needed
- regression coverage that `csv -> csv` still updates in place

Out of scope:

- Notion mode behavior
- user-specified `output_dir` overrides
- changing filename formats

**Testing**

- Add a focused regression test proving URL exports default to `output/`.
- Add a focused regression test proving the destination directory is created automatically.
- Keep existing `csv -> csv` tests as coverage for the unchanged in-place update path.
