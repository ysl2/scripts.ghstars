# ArXiv Relation Non-arXiv Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend single-paper arXiv relation export so citations and references no longer drop non-arXiv related works; instead, prefer direct arXiv normalization, then arXiv title mapping, and finally retain unresolved non-arXiv rows in the existing CSV schema.

**Architecture:** Keep the blast radius inside the existing single-paper relation path. `src/shared/openalex.py` should stop collapsing related works into arXiv-only `PaperSeed` rows and instead expose a richer candidate shape with enough metadata for the relation pipeline to decide among direct arXiv normalization, title-search mapping, and non-arXiv fallback retention. `src/arxiv_relations/pipeline.py` should own the normalization ladder, deterministic deduplication, and final conversion back into ordinary `PaperSeed` rows so shared enrichment/export code and all non-relation modes stay unchanged.

**Tech Stack:** Python 3.12, asyncio, aiohttp, pytest, OpenAlex API, arXiv metadata/search, uv

---

## File Map

- Modify: `src/shared/openalex.py`
  - Add a related-work candidate model that preserves display title, directly derivable canonical arXiv URL, DOI URL, landing page URL, and OpenAlex work URL.
  - Keep target-work search, references hydration, and citations pagination behavior unchanged.
- Modify: `src/arxiv_relations/pipeline.py`
  - Replace "direct arXiv or drop" normalization with the three-stage ladder plus deterministic deduplication.
  - Keep the public export entrypoint and shared CSV/export plumbing unchanged.
- Modify: `tests/test_openalex.py`
  - Cover candidate extraction for direct arXiv rows and unresolved non-arXiv rows.
- Modify: `tests/test_arxiv_relations.py`
  - Cover title-mapped arXiv rows, unresolved fallback retention, URL-priority rules, deterministic deduplication, and full export wiring.
- Modify: `README.md`
  - Update single-paper relation mode behavior to describe mapping-plus-retention instead of arXiv-only filtering.

## Guardrails

- Do not add a new CLI mode, new script, or new command family.
- Do not change URL mode, CSV update mode, or Notion mode semantics.
- Do not change the CSV schema; keep `Name, Url, Github, Stars`.
- Keep shared export code unchanged unless a focused regression test proves it is required.

### Task 1: Expose rich related-work candidates from OpenAlex

**Files:**
- Modify: `src/shared/openalex.py`
- Test: `tests/test_openalex.py`

- [ ] **Step 1: Write the failing OpenAlex candidate tests**

Add tests in `tests/test_openalex.py` for:

```python
def test_build_related_work_candidate_prefers_direct_arxiv_identity():
    work = {
        "display_name": "Direct Paper",
        "ids": {"arxiv": "2403.00001v2"},
        "doi": "https://doi.org/10.48550/arXiv.2403.00001",
        "locations": [{"landing_page_url": "https://example.com/direct"}],
        "id": "https://openalex.org/W1",
    }
    candidate = client.build_related_work_candidate(work)
    assert candidate.title == "Direct Paper"
    assert candidate.direct_arxiv_url == "https://arxiv.org/abs/2403.00001"
    assert candidate.doi_url == "https://doi.org/10.48550/arXiv.2403.00001"
    assert candidate.landing_page_url == "https://example.com/direct"
    assert candidate.openalex_url == "https://openalex.org/W1"


def test_build_related_work_candidate_retains_non_arxiv_fallback_fields():
    work = {
        "display_name": "Non Arxiv Paper",
        "doi": "https://doi.org/10.1145/example",
        "locations": [{"landing_page_url": "https://publisher.example/paper"}],
        "id": "https://openalex.org/W9",
    }
    candidate = client.build_related_work_candidate(work)
    assert candidate.direct_arxiv_url is None
    assert candidate.doi_url == "https://doi.org/10.1145/example"
    assert candidate.landing_page_url == "https://publisher.example/paper"
    assert candidate.openalex_url == "https://openalex.org/W9"
```

- [ ] **Step 2: Run the focused OpenAlex tests to verify they fail**

Run: `uv run pytest tests/test_openalex.py -q`
Expected: FAIL because `OpenAlexClient` does not yet expose a candidate builder and still only returns `PaperSeed | None`.

- [ ] **Step 3: Implement the minimal candidate model**

In `src/shared/openalex.py`, add a focused immutable model and one builder:

```python
@dataclass(frozen=True)
class RelatedWorkCandidate:
    title: str
    direct_arxiv_url: str | None
    doi_url: str | None
    landing_page_url: str | None
    openalex_url: str


def build_related_work_candidate(self, work: dict[str, Any]) -> RelatedWorkCandidate:
    return RelatedWorkCandidate(
        title=work.get("display_name") or work.get("title") or "",
        direct_arxiv_url=self._canonical_arxiv_url(work),
        doi_url=_normalize_doi_url(work.get("doi")),
        landing_page_url=self._extract_landing_page_url(work),
        openalex_url=work.get("id") or "",
    )
```

Keep `search_first_work()`, `fetch_referenced_works()`, and `fetch_citations()` behavior unchanged.

- [ ] **Step 4: Remove the arXiv-only collapse from the OpenAlex surface**

Either delete `normalize_related_work()` or reduce it to a thin compatibility wrapper that calls `build_related_work_candidate()` and is no longer used by the relation pipeline. The end state should make non-arXiv related works observable instead of turning them into `None`.

- [ ] **Step 5: Re-run the focused OpenAlex tests**

Run: `uv run pytest tests/test_openalex.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/shared/openalex.py tests/test_openalex.py
git commit -m "feat: expose openalex related work candidates"
```

### Task 2: Implement the relation normalization ladder in the pipeline

**Files:**
- Modify: `src/arxiv_relations/pipeline.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write the failing pipeline tests for mapping and retention**

Add focused tests in `tests/test_arxiv_relations.py` proving:

```python
@pytest.mark.anyio
async def test_normalize_related_works_maps_non_arxiv_title_hits_to_canonical_arxiv():
    related_works = [{"id": "R1"}, {"id": "R2"}]
    # R1: direct arXiv candidate
    # R2: non-arXiv candidate whose title search returns 2501.12345
    seeds = await normalize_related_works_to_seeds(
        related_works,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
    )
    assert seeds == [
        PaperSeed(name="Direct Paper", url="https://arxiv.org/abs/2403.00001"),
        PaperSeed(name="Mapped Arxiv Title", url="https://arxiv.org/abs/2501.12345"),
    ]


@pytest.mark.anyio
async def test_normalize_related_works_retains_unresolved_non_arxiv_rows_with_url_priority():
    related_works = [{"id": "R3"}, {"id": "R4"}, {"id": "R5"}]
    seeds = await normalize_related_works_to_seeds(
        related_works,
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
    )
    assert seeds == [
        PaperSeed(name="With DOI", url="https://doi.org/10.1145/example"),
        PaperSeed(name="With Landing", url="https://publisher.example/paper"),
        PaperSeed(name="OpenAlex Only", url="https://openalex.org/W5"),
    ]
```

- [ ] **Step 2: Run the focused relation tests to verify they fail**

Run: `uv run pytest tests/test_arxiv_relations.py -q`
Expected: FAIL because `normalize_related_works_to_seeds()` is synchronous, only consults `openalex_client.normalize_related_work()`, and still drops unresolved rows.

- [ ] **Step 3: Implement the three-stage normalization ladder**

In `src/arxiv_relations/pipeline.py`, add small helpers and make normalization async:

```python
async def normalize_related_works_to_seeds(
    related_works: list[dict],
    *,
    openalex_client,
    arxiv_client,
) -> list[PaperSeed]:
    candidates = [openalex_client.build_related_work_candidate(work) for work in related_works]
    normalized_rows = await _resolve_related_work_rows(candidates, arxiv_client=arxiv_client)
    deduped_rows = _dedupe_normalized_rows(normalized_rows)
    return [PaperSeed(name=row.title, url=row.url) for row in deduped_rows]
```

`_resolve_related_work_rows()` should:
- keep directly derivable arXiv rows immediately
- otherwise call `arxiv_client.get_arxiv_id_by_title(candidate.title)`
- when title search resolves, use the matched canonical arXiv URL plus the matched arXiv title from `arxiv_client.get_title()`
- otherwise retain the original OpenAlex title and choose fallback URL by `DOI > landing page > OpenAlex URL`

- [ ] **Step 4: Wire the async normalization path into the export entrypoint**

Update `export_arxiv_relations_to_csv()` to pass both clients:

```python
reference_seeds = await normalize_related_works_to_seeds(
    referenced_works,
    openalex_client=openalex_client,
    arxiv_client=arxiv_client,
)
```

Do the same for citations. Keep status reporting, CSV filenames, and exporter wiring unchanged.

- [ ] **Step 5: Re-run the focused relation tests**

Run: `uv run pytest tests/test_arxiv_relations.py -q`
Expected: PASS for the new mapping/retention cases, while existing invalid-input and hard-failure tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/arxiv_relations/pipeline.py tests/test_arxiv_relations.py
git commit -m "feat: retain non-arxiv relation rows"
```

### Task 3: Make deduplication deterministic after final normalization

**Files:**
- Modify: `src/arxiv_relations/pipeline.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write the failing deterministic-dedup tests**

Add tests covering the approved precedence and tie-break rules:

```python
def test_dedup_prefers_direct_arxiv_over_title_mapped_row():
    rows = [
        NormalizedRelatedRow(
            title="Mapped Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.TITLE_SEARCH,
        ),
        NormalizedRelatedRow(
            title="Direct Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.DIRECT_ARXIV,
        ),
    ]
    winner = _dedupe_normalized_rows(rows)
    assert winner == [
        NormalizedRelatedRow(
            title="Direct Title",
            url="https://arxiv.org/abs/2403.00001",
            strength=NormalizationStrength.DIRECT_ARXIV,
        )
    ]


def test_dedup_breaks_same_strength_ties_by_normalized_then_original_title():
    rows = [
        NormalizedRelatedRow(
            title="A  Study",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        ),
        NormalizedRelatedRow(
            title="A-study",
            url="https://publisher.example/paper",
            strength=NormalizationStrength.RETAINED_NON_ARXIV,
        ),
    ]
    winner = _dedupe_normalized_rows(rows)
    assert winner[0].title == "A  Study"
```

- [ ] **Step 2: Run the focused deterministic-dedup tests**

Run: `uv run pytest tests/test_arxiv_relations.py -q`
Expected: FAIL because the pipeline still deduplicates by "first seen URL" only.

- [ ] **Step 3: Implement normalized rows plus precedence ordering**

In `src/arxiv_relations/pipeline.py`, add explicit types:

```python
class NormalizationStrength(IntEnum):
    DIRECT_ARXIV = 0
    TITLE_SEARCH = 1
    RETAINED_NON_ARXIV = 2


@dataclass(frozen=True)
class NormalizedRelatedRow:
    title: str
    url: str
    strength: NormalizationStrength
```

Implement `_dedupe_normalized_rows()` so it:
- groups by final `url`
- chooses the lowest `strength` value first
- for same-strength collisions, compares `(normalize_title_for_matching(title), title)` lexicographically
- preserves the winning row’s original title text

- [ ] **Step 4: Re-run the focused relation tests**

Run: `uv run pytest tests/test_arxiv_relations.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/arxiv_relations/pipeline.py tests/test_arxiv_relations.py
git commit -m "fix: make relation dedup deterministic"
```

### Task 4: Update CLI-facing behavior docs and regression coverage

**Files:**
- Modify: `README.md`
- Modify: `tests/test_arxiv_relations.py`
- Modify: `tests/test_openalex.py`

- [ ] **Step 1: Add one end-to-end regression test for the exporter surface**

Extend `tests/test_arxiv_relations.py` so the existing export test covers a mixed set:

```python
assert reference_seeds == [
    PaperSeed(name="Direct Reference", url="https://arxiv.org/abs/2501.00001"),
    PaperSeed(name="Mapped Reference", url="https://arxiv.org/abs/2501.00002"),
    PaperSeed(name="Publisher Reference", url="https://doi.org/10.1145/example"),
]
```

This protects the public single-paper export path without touching other CLI modes.

- [ ] **Step 2: Update the README single-paper section**

Replace the old arXiv-only bullets with behavior that matches the spec:

```md
- keeps direct arXiv-backed related works as canonical arXiv rows
- otherwise tries arXiv title search and takes the first most relevant hit
- if still unresolved, keeps the non-arXiv row with `Url` priority `DOI > landing page > OpenAlex URL`
- mapped rows use the matched arXiv title and canonical arXiv `abs` URL
- unresolved rows remain in the CSV even when `Github` and `Stars` stay blank
```

- [ ] **Step 3: Run targeted regressions**

Run: `uv run pytest tests/test_openalex.py tests/test_arxiv_relations.py -q`
Expected: PASS

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 5: Run one real smoke command with the configured `.env`**

Run: `uv run main.py 'https://arxiv.org/abs/2312.03203'`
Expected:
- exit code `0`
- both `references` and `citations` CSV files appear under `./output`
- CSVs may contain a mix of canonical arXiv URLs and retained non-arXiv URLs

- [ ] **Step 6: Commit**

```bash
git add README.md tests/test_openalex.py tests/test_arxiv_relations.py src/shared/openalex.py src/arxiv_relations/pipeline.py
git commit -m "docs: describe retained non-arxiv relation rows"
```
