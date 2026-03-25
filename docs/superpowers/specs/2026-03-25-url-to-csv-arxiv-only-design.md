# URL To CSV ArXiv-Only Design

**Goal**

Unify all `url -> csv` modes so the final CSV only contains papers with canonical arXiv URLs, using the arXiv URL as the key and sorting by arXiv URL time descending. Notion mode stays unchanged.

**Scope**

- Applies to:
  - `arxivxplorer -> csv`
  - `huggingface papers -> csv`
  - `semanticscholar -> csv`
- Does not apply to:
  - Notion sync mode

**Design**

Each URL source keeps its own collection logic and returns raw paper candidates. A shared normalization stage then converts candidates into an arXiv-only seed list before any Github or Stars enrichment runs.

Shared normalization rules:

1. If a candidate already has an arXiv URL, keep it.
2. If a candidate does not have an arXiv URL, try to resolve one.
3. If arXiv resolution fails, drop the candidate from the export pipeline.
4. Deduplicate by normalized arXiv URL.
5. Sort the final CSV by arXiv URL time descending.

**Semantic Scholar-specific behavior**

Semantic Scholar still starts from the search result list. Some results are not directly arXiv-backed. For those rows:

1. Try to resolve arXiv from shared title-based lookup.
2. If resolution fails, remove the row entirely.
3. Only after that, continue with the existing Github and Stars enrichment logic.

**Shared code boundaries**

- Source modules:
  - only fetch raw candidates
- Shared URL-to-CSV normalization:
  - detect arXiv-backed rows
  - resolve missing arXiv URLs
  - filter unresolved rows
  - dedupe by arXiv URL
- Shared export:
  - enrich Github/Stars only for normalized arXiv rows
  - write CSV sorted by arXiv URL descending

**Why this design**

- Keeps source-specific crawling isolated.
- Avoids running Github/Stars enrichment for rows that will be discarded.
- Reuses one arXiv-keyed pipeline for all URL modes.
- Matches the repository rule that non-Notion flows should key by arXiv URL.
