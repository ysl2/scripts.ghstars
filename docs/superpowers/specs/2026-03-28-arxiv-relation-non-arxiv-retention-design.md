# ArXiv Relation Export Non-arXiv Retention Design

**Goal**

Extend the existing single-paper arXiv citation/reference export mode so that related works are no longer dropped just because they are not directly arXiv-backed. The mode should still prefer arXiv identity whenever possible, but must retain unresolved non-arXiv related works in the final CSVs.

**Scope**

In scope:

- keep the current single-paper arXiv CLI mode and output shape
- continue preferring direct arXiv-backed related works when OpenAlex exposes an arXiv identifier, DOI, or location that can be normalized to a canonical arXiv URL
- for non-arXiv related works, attempt to resolve them to arXiv by title search
- when a title search returns multiple arXiv matches, select the first most relevant result
- when a non-arXiv related work resolves to arXiv, reuse the existing arXiv-based GitHub and stars pipeline
- when a non-arXiv related work does not resolve to arXiv, retain it in the CSV instead of dropping it
- preserve the existing four-column CSV schema: `Name`, `Url`, `Github`, `Stars`
- add tests covering mapped and unresolved non-arXiv related works

Out of scope:

- support for non-arXiv input papers
- adding new CSV columns to show mapping provenance
- conservative disambiguation for non-arXiv title search hits
- introducing a second output file format for unresolved non-arXiv rows
- custom CLI flags for retention, mapping strategy, or URL selection

**Behavior Overview**

The input remains one single-paper arXiv URL. The target paper lookup does not change:

1. resolve the input paper title from arXiv metadata
2. search OpenAlex by that title
3. accept the first returned OpenAlex work by relevance
4. fetch references and citations from OpenAlex

What changes is the normalization path for each related work. Instead of "direct arXiv or drop", each related work now goes through a three-stage ladder:

1. direct arXiv normalization
2. arXiv title-search mapping
3. retained non-arXiv fallback row

The export still writes one references CSV and one citations CSV under `./output`.

**Normalization Ladder**

For each related OpenAlex work:

1. **Direct arXiv normalization**
   - inspect the OpenAlex work for an arXiv-backed identifier or location
   - if found, normalize to the canonical versionless arXiv `abs` URL
   - use that canonical arXiv URL as the row `Url`
   - treat the row exactly like current arXiv-backed rows

2. **ArXiv title-search mapping**
   - only if direct arXiv normalization fails
   - take the related work title from OpenAlex and run the existing arXiv title search flow
   - if arXiv returns multiple matches, take the first most relevant result
   - normalize the matched arXiv paper to the canonical versionless `abs` URL
   - use the matched arXiv title as the final row `Name`
   - use the matched canonical arXiv URL as the final row `Url`
   - reuse the existing arXiv-based GitHub and stars enrichment path

3. **Retained non-arXiv fallback row**
   - only if both direct arXiv normalization and arXiv title-search mapping fail
   - keep the row in the CSV
   - keep the original OpenAlex work title as `Name`
   - select `Url` with this priority:
     1. DOI URL
     2. landing page URL
     3. OpenAlex work URL
   - if GitHub discovery cannot find a repo, leave `Github` and `Stars` blank

This means "not arXiv" is no longer a drop condition by itself.

**Identity and Deduplication**

The mode should deduplicate after final normalization, not before:

- rows resolved directly or indirectly to arXiv deduplicate by canonical arXiv URL
- unresolved non-arXiv rows deduplicate by their retained final `Url`

This avoids exporting duplicate rows when one related work is directly arXiv-backed and another equivalent OpenAlex record only resolves through title search.

**CSV Semantics**

The CSV schema does not change:

- `Name`
- `Url`
- `Github`
- `Stars`

Field semantics:

- mapped-to-arXiv rows use the matched arXiv title and canonical arXiv URL
- unresolved non-arXiv rows use the original OpenAlex title and the fallback URL chosen by the priority rule above
- `Github` and `Stars` remain blank when discovery cannot identify a repository

This preserves compatibility with downstream CSV consumers.

**Enrichment Strategy**

The enrichment pipeline remains centered on the existing `PaperSeed -> PaperRecord` flow.

- direct arXiv rows and title-mapped arXiv rows should continue through the current arXiv-based GitHub/stars discovery path
- unresolved non-arXiv rows should still be converted into `PaperSeed` rows so they can be written through the shared CSV exporter
- if the current discovery layer cannot find a repository for unresolved non-arXiv rows, that is acceptable; the row must still be written

The important change is retention, not guaranteed GitHub discovery for non-arXiv inputs.

**OpenAlex Parsing Requirements**

The OpenAlex layer should expose enough information for the pipeline to decide between the three normalization stages above.

Minimum retained fields for a related work candidate:

- display title
- canonical arXiv URL if directly derivable
- DOI URL if present
- landing page URL if present
- OpenAlex work URL

This is broader than the current "return `PaperSeed` only for arXiv-backed works" behavior.

**Failure Behavior**

The run should still fail explicitly when:

- the input is not a single-paper arXiv URL
- the input paper title cannot be resolved
- OpenAlex returns no target work
- OpenAlex references fetch fails
- OpenAlex citations fetch fails

But it should **not** fail or silently drop rows simply because:

- a related work has no direct arXiv signal
- arXiv title search for a related work finds no match
- GitHub discovery finds no repository for a retained row

Those cases should produce retained rows with partial data.

**Code Boundaries**

Recommended changes:

- `src/shared/openalex.py`
  - stop collapsing non-arXiv related works to `None`
  - expose a richer related-work candidate shape or equivalent normalization helpers
- `src/arxiv_relations/pipeline.py`
  - implement the three-stage normalization ladder
  - choose final title, final URL, and deduplication key per row
- `src/shared/arxiv.py`
  - reuse the current title-search capability for mapping related works to arXiv
- shared CSV export modules should remain unchanged where possible

This keeps the new behavior localized to relation-export normalization rather than rewriting the export pipeline.

**Testing**

- direct arXiv related works still export as before
- non-arXiv related works that title-map to arXiv become canonical arXiv rows
- when multiple arXiv title-search matches exist, the first result is selected
- mapped rows use the matched arXiv title rather than the original OpenAlex title
- unresolved non-arXiv related works are retained in the CSV
- unresolved row `Url` selection follows:
  - DOI first
  - landing page second
  - OpenAlex work URL third
- mixed related-work sets deduplicate correctly after final normalization
- full `uv run pytest`
