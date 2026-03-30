# Docs Map

This directory mixes a small amount of current reference material with a larger historical record of design and implementation notes.

Use the docs in this order:

1. [`README.md`](../README.md)
   User-facing setup, supported inputs, environment variables, and CLI behavior.
2. [`ARCHITECTURE.md`](../ARCHITECTURE.md)
   Current runtime structure, shared rules, cache semantics, and extension guidance for maintainers.
3. Files under `docs/`
   Supporting material that is useful for background context, but is not the primary source of truth for current runtime behavior.

## Current Supporting References

- [`find_alphaxiv_github.sh`](find_alphaxiv_github.sh)
  One-off helper script for manual AlphaXiv inspection.
- [`huggingface.md`](huggingface.md)
  Research notes about Hugging Face papers endpoints and usage constraints.

## Historical Design Record

- `docs/plans/`
  Older plan and design notes from earlier closeout iterations.
- `docs/superpowers/plans/`
  Historical implementation plans written during earlier agent-guided work.
- `docs/superpowers/specs/`
  Historical design specs for features and refactors that may already be implemented, superseded, or only partially relevant now.

These historical docs are useful for understanding why a change happened, but they should not override the running code.

If a historical note disagrees with the current implementation:

1. trust the code first
2. align `README.md` and `ARCHITECTURE.md` to the real behavior
3. treat the historical doc as archived context, not current truth
