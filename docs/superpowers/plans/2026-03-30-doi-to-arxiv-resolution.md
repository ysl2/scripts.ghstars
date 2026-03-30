# DOI To arXiv Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current mixed DOI-to-arXiv normalization chain with a cache-first short-circuit pipeline of `OpenAlex exact -> arXiv title API -> Crossref -> DataCite`, while removing Hugging Face and OpenAlex sibling-search behavior from this shared DOI path.

**Architecture:** Keep the existing shared normalization entrypoint in `src/shared/arxiv_url_resolution.py`, but narrow OpenAlex to an exact-only helper and add two new peer metadata clients for Crossref and DataCite. Thread the new clients through the existing shared enrichment/export call graph so all non-Notion and Notion flows reuse the same DOI normalization policy without rewriting unrelated repo-discovery logic.

**Tech Stack:** Python, `aiohttp`, existing shared client pattern in `src/shared/*`, `pytest`/`pytest-anyio`, SQLite-backed relation-resolution cache.

---

## File Structure

**Create:**
- `src/shared/crossref.py`
- `src/shared/datacite.py`
- `tests/test_crossref.py`
- `tests/test_datacite.py`

**Modify:**
- `src/shared/openalex.py`
- `src/shared/arxiv_url_resolution.py`
- `src/shared/paper_enrichment.py`
- `src/shared/paper_export.py`
- `src/csv_update/runner.py`
- `src/url_to_csv/pipeline.py`
- `src/url_to_csv/runner.py`
- `src/notion_sync/pipeline.py`
- `src/notion_sync/runner.py`
- `src/arxiv_relations/pipeline.py`
- `src/arxiv_relations/runner.py`
- `tests/test_openalex.py`
- `tests/test_arxiv_url_resolution.py`
- `tests/test_paper_enrichment.py`
- `tests/test_url_to_csv.py`
- `tests/test_csv_update.py`
- `tests/test_notion_mode.py`
- `tests/test_arxiv_relations.py`

**Keep unchanged on purpose:**
- `src/shared/discovery.py`
- `src/arxiv_relations/title_resolution.py`
- GitHub repo discovery ordering
- Hugging Face papers URL ingestion

### Task 1: Add OpenAlex Exact-Only Lookup

**Files:**
- Modify: `src/shared/openalex.py`
- Test: `tests/test_openalex.py`

- [ ] **Step 1: Write the failing OpenAlex exact-only tests**

Add these tests to `tests/test_openalex.py` near the existing `find_preprint_match_by_identifier` coverage:

```python
@pytest.mark.anyio
async def test_find_exact_arxiv_match_by_identifier_returns_direct_arxiv_without_followup_search():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "id": "https://openalex.org/W1",
                    "display_name": "Published Paper",
                    "ids": {"arxiv": "2501.12345v2"},
                }
            )
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_exact_arxiv_match_by_identifier(
        "https://doi.org/10.48550/arXiv.2501.12345",
        title="Published Paper",
    )

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"
    assert len(session.calls) == 1


@pytest.mark.anyio
async def test_find_exact_arxiv_match_by_identifier_does_not_run_same_title_sibling_search():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "id": "https://openalex.org/W-published",
                    "display_name": "Example Published Paper",
                    "doi": "https://doi.org/10.1145/example",
                }
            )
        ]
    )
    client = OpenAlexClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_exact_arxiv_match_by_identifier(
        "https://openalex.org/W-published",
        title="Example Published Paper",
    )

    assert arxiv_url is None
    assert resolved_title == "Example Published Paper"
    assert len(session.calls) == 1
```

- [ ] **Step 2: Run the focused OpenAlex tests and verify failure**

Run: `uv run pytest tests/test_openalex.py -k 'find_exact_arxiv_match_by_identifier' -q`

Expected: `AttributeError` or failing assertions because `find_exact_arxiv_match_by_identifier` does not exist yet.

- [ ] **Step 3: Implement the exact-only OpenAlex entrypoint**

Add an exact-only helper to `src/shared/openalex.py` and keep the existing sibling-search methods intact for relation-mode code:

```python
async def find_exact_arxiv_match_by_identifier(
    self,
    identifier: str,
    *,
    title: str | None = None,
) -> tuple[str | None, str | None]:
    work = await self.fetch_work_by_identifier(identifier)
    if not isinstance(work, dict):
        return None, None

    direct_arxiv_url = self._canonical_arxiv_url(work)
    resolved_title = " ".join(str(work.get("display_name") or work.get("title") or title or "").split()).strip()
    return direct_arxiv_url, resolved_title or title


async def find_preprint_match_by_identifier(
    self,
    identifier: str,
    *,
    title: str | None = None,
) -> tuple[str | None, str | None]:
    direct_arxiv_url, resolved_title = await self.find_exact_arxiv_match_by_identifier(identifier, title=title)
    if direct_arxiv_url:
        return direct_arxiv_url, resolved_title

    search_title = " ".join(str(title or resolved_title or "").split()).strip()
    if not search_title:
        return None, resolved_title or None

    work = await self.fetch_work_by_identifier(identifier)
    if not isinstance(work, dict):
        return None, resolved_title or None
    return await self.find_related_work_preprint_match(work, title=search_title)
```

- [ ] **Step 4: Run the OpenAlex tests and verify pass**

Run: `uv run pytest tests/test_openalex.py -k 'find_exact_arxiv_match_by_identifier or find_preprint_match_by_identifier' -q`

Expected: targeted OpenAlex exact-only tests pass, and existing sibling-search tests still pass.

- [ ] **Step 5: Commit the OpenAlex exact-only change**

```bash
git add tests/test_openalex.py src/shared/openalex.py
git commit -m "refactor: add exact-only openalex arxiv lookup"
```

### Task 2: Add Crossref and DataCite Metadata Clients

**Files:**
- Create: `src/shared/crossref.py`
- Create: `src/shared/datacite.py`
- Test: `tests/test_crossref.py`
- Test: `tests/test_datacite.py`

- [ ] **Step 1: Write failing Crossref tests**

Create `tests/test_crossref.py` with focused extraction coverage:

```python
import pytest

from src.shared.crossref import CrossrefClient


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers=None, params=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "params": dict(params or {})})
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_crossref_client_returns_arxiv_url_from_message_relation():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "message": {
                        "title": ["Published Paper"],
                        "relation": {
                            "is-preprint-of": [{"id": "https://arxiv.org/abs/2501.12345v2"}]
                        },
                    }
                }
            )
        ]
    )
    client = CrossrefClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_arxiv_match_by_doi("https://doi.org/10.1145/example")

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"


@pytest.mark.anyio
async def test_crossref_client_returns_none_when_no_arxiv_relation_exists():
    session = FakeSession([FakeResponse({"message": {"title": ["Published Paper"], "relation": {}}})])
    client = CrossrefClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_arxiv_match_by_doi("https://doi.org/10.1145/example")

    assert arxiv_url is None
    assert resolved_title == "Published Paper"
```

- [ ] **Step 2: Write failing DataCite tests**

Create `tests/test_datacite.py` with focused related-identifier coverage:

```python
import pytest

from src.shared.datacite import DataCiteClient


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers=None, params=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "params": dict(params or {})})
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_datacite_client_returns_arxiv_url_from_related_identifiers():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": {
                        "attributes": {
                            "titles": [{"title": "Published Paper"}],
                            "relatedIdentifiers": [
                                {
                                    "relatedIdentifierType": "arXiv",
                                    "relatedIdentifier": "2501.12345v2",
                                    "relationType": "IsVersionOf",
                                }
                            ],
                        }
                    }
                }
            )
        ]
    )
    client = DataCiteClient(session, min_interval=0, max_concurrent=1)

    arxiv_url, resolved_title = await client.find_arxiv_match_by_doi("https://doi.org/10.5555/example")

    assert arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert resolved_title == "Published Paper"
```

- [ ] **Step 3: Run the new tests and verify failure**

Run: `uv run pytest tests/test_crossref.py tests/test_datacite.py -q`

Expected: import failures because the new clients do not exist yet.

- [ ] **Step 4: Implement the Crossref and DataCite clients**

Create `src/shared/crossref.py`:

```python
import asyncio
from urllib.parse import quote, urlparse

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import normalize_arxiv_url, normalize_doi_url


class CrossrefClient:
    def __init__(self, session: aiohttp.ClientSession, max_concurrent: int = 4, min_interval: float = 0.2):
        self.session = session
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def find_arxiv_match_by_doi(self, doi_url: str) -> tuple[str | None, str | None]:
        normalized_doi = normalize_doi_url(doi_url)
        if not normalized_doi:
            return None, None

        doi_path = urlparse(normalized_doi).path.lstrip("/")
        payload = await self._get_json(f"https://api.crossref.org/works/{quote(doi_path, safe='')}")
        message = payload.get("message") or {}
        resolved_title = self._extract_title(message)
        return self._extract_arxiv_url(message), resolved_title
```

Create `src/shared/datacite.py`:

```python
import asyncio
from urllib.parse import quote, urlparse

import aiohttp

from src.shared.http import MAX_RETRIES, RateLimiter
from src.shared.paper_identity import build_arxiv_abs_url, normalize_arxiv_url, normalize_doi_url


class DataCiteClient:
    def __init__(self, session: aiohttp.ClientSession, max_concurrent: int = 4, min_interval: float = 0.2):
        self.session = session
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)

    async def find_arxiv_match_by_doi(self, doi_url: str) -> tuple[str | None, str | None]:
        normalized_doi = normalize_doi_url(doi_url)
        if not normalized_doi:
            return None, None

        doi_path = urlparse(normalized_doi).path.lstrip("/")
        payload = await self._get_json(f"https://api.datacite.org/dois/{quote(doi_path, safe='')}")
        attributes = ((payload.get("data") or {}).get("attributes") or {})
        resolved_title = self._extract_title(attributes)
        return self._extract_arxiv_url(attributes), resolved_title
```

- [ ] **Step 5: Run the new metadata-client tests and verify pass**

Run: `uv run pytest tests/test_crossref.py tests/test_datacite.py -q`

Expected: both client suites pass.

- [ ] **Step 6: Commit the new clients**

```bash
git add src/shared/crossref.py src/shared/datacite.py tests/test_crossref.py tests/test_datacite.py
git commit -m "feat: add crossref and datacite arxiv lookup clients"
```

### Task 3: Refactor the Shared DOI Resolver Order

**Files:**
- Modify: `src/shared/arxiv_url_resolution.py`
- Test: `tests/test_arxiv_url_resolution.py`

- [ ] **Step 1: Write failing resolver-order tests**

Extend `tests/test_arxiv_url_resolution.py` with pipeline-order coverage:

```python
@pytest.mark.anyio
async def test_resolve_arxiv_url_uses_openalex_exact_before_all_fallbacks():
    openalex_client = SimpleNamespace(
        find_exact_arxiv_match_by_identifier=AsyncMock(return_value=("https://arxiv.org/abs/2501.12345", "Mapped"))
    )
    arxiv_client = SimpleNamespace(get_arxiv_match_by_title_from_api=AsyncMock())
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        allow_title_search=True,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    arxiv_client.get_arxiv_match_by_title_from_api.assert_not_awaited()
    crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
    datacite_client.find_arxiv_match_by_doi.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arxiv_url_runs_crossref_then_datacite_after_title_api_fails():
    openalex_client = SimpleNamespace(find_exact_arxiv_match_by_identifier=AsyncMock(return_value=(None, "Published Paper")))
    arxiv_client = SimpleNamespace(get_arxiv_match_by_title_from_api=AsyncMock(return_value=(None, None, None, "No arXiv ID found from title search")))
    crossref_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(return_value=(None, "Published Paper")))
    datacite_client = SimpleNamespace(find_arxiv_match_by_doi=AsyncMock(return_value=("https://arxiv.org/abs/2501.12345", "Published Paper")))

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1145/example",
        openalex_client=openalex_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        allow_title_search=True,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    crossref_client.find_arxiv_match_by_doi.assert_awaited_once()
    datacite_client.find_arxiv_match_by_doi.assert_awaited_once()
```

- [ ] **Step 2: Run the resolver tests and verify failure**

Run: `uv run pytest tests/test_arxiv_url_resolution.py -q`

Expected: failures because `resolve_arxiv_url` does not accept `crossref_client`/`datacite_client` and still calls the old OpenAlex/HF path.

- [ ] **Step 3: Implement the new resolver order**

Refactor `src/shared/arxiv_url_resolution.py` to:

- accept `crossref_client` and `datacite_client`
- call `openalex_client.find_exact_arxiv_match_by_identifier(...)`
- remove Hugging Face fallback from this shared DOI path
- keep cache-first behavior
- write negative cache only after all stages fail

Core flow:

```python
exact_openalex_lookup = getattr(openalex_client, "find_exact_arxiv_match_by_identifier", None)
if callable(exact_openalex_lookup):
    arxiv_url, resolved_title = await exact_openalex_lookup(key_value, title=normalized_title or None)
    if arxiv_url:
        ...

title_resolution = await _resolve_by_title(
    normalized_title,
    arxiv_client=arxiv_client,
    discovery_client=None,
)
if title_resolution.canonical_arxiv_url:
    ...

crossref_lookup = getattr(crossref_client, "find_arxiv_match_by_doi", None)
if callable(crossref_lookup) and normalized_doi_key:
    arxiv_url, resolved_title = await crossref_lookup(normalized_doi_key)
    if arxiv_url:
        ...

datacite_lookup = getattr(datacite_client, "find_arxiv_match_by_doi", None)
if callable(datacite_lookup) and normalized_doi_key:
    arxiv_url, resolved_title = await datacite_lookup(normalized_doi_key)
    if arxiv_url:
        ...
```

- [ ] **Step 4: Run the resolver tests and verify pass**

Run: `uv run pytest tests/test_arxiv_url_resolution.py -q`

Expected: all resolver tests pass, including cache short-circuit coverage.

- [ ] **Step 5: Commit the resolver refactor**

```bash
git add src/shared/arxiv_url_resolution.py tests/test_arxiv_url_resolution.py
git commit -m "refactor: reorder shared doi to arxiv resolution"
```

### Task 4: Thread the New Clients Through Shared Enrichment and Export Paths

**Files:**
- Modify: `src/shared/paper_enrichment.py`
- Modify: `src/shared/paper_export.py`
- Modify: `src/url_to_csv/pipeline.py`
- Modify: `src/notion_sync/pipeline.py`
- Modify: `src/arxiv_relations/pipeline.py`
- Test: `tests/test_paper_enrichment.py`
- Test: `tests/test_url_to_csv.py`
- Test: `tests/test_csv_update.py`
- Test: `tests/test_notion_mode.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write failing shared-flow tests**

Update existing tests to target the new OpenAlex exact-only method and new optional client parameters. For example:

```python
openalex_client = types.SimpleNamespace(
    find_exact_arxiv_match_by_identifier=AsyncMock(
        return_value=("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title")
    )
)
crossref_client = types.SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())
datacite_client = types.SimpleNamespace(find_arxiv_match_by_doi=AsyncMock())

result = await process_single_paper(
    PaperEnrichmentRequest(...),
    discovery_client=discovery_client,
    github_client=github_client,
    openalex_client=openalex_client,
    crossref_client=crossref_client,
    datacite_client=datacite_client,
    content_cache=content_cache,
)

openalex_client.find_exact_arxiv_match_by_identifier.assert_awaited_once()
crossref_client.find_arxiv_match_by_doi.assert_not_awaited()
datacite_client.find_arxiv_match_by_doi.assert_not_awaited()
```

- [ ] **Step 2: Run the shared-flow tests and verify failure**

Run:

```bash
uv run pytest \
  tests/test_paper_enrichment.py \
  tests/test_url_to_csv.py \
  tests/test_csv_update.py \
  tests/test_notion_mode.py \
  tests/test_arxiv_relations.py \
  -k 'doi or arxiv or content_cache or build' -q
```

Expected: failures because the new clients are not yet threaded through these paths.

- [ ] **Step 3: Implement the shared parameter threading**

Update signatures and call sites to pass the new clients through without disturbing unrelated behavior:

```python
async def process_single_paper(
    request: PaperEnrichmentRequest,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    openalex_client=None,
    crossref_client=None,
    datacite_client=None,
    ...
):
    url_resolution = await resolve_arxiv_url(
        title,
        raw_url,
        arxiv_client=arxiv_client,
        openalex_client=openalex_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        ...
    )
```

Apply the same parameter threading to:

- `src/shared/paper_export.py`
- `src/url_to_csv/pipeline.py`
- `src/notion_sync/pipeline.py`
- `src/arxiv_relations/pipeline.py`

The goal is that every flow already using shared URL normalization can opt into the same new DOI chain.

- [ ] **Step 4: Run the shared-flow tests and verify pass**

Run the same focused command from Step 2.

Expected: focused flow tests pass after the signatures and call sites are aligned.

- [ ] **Step 5: Commit the shared-flow wiring**

```bash
git add \
  src/shared/paper_enrichment.py \
  src/shared/paper_export.py \
  src/url_to_csv/pipeline.py \
  src/notion_sync/pipeline.py \
  src/arxiv_relations/pipeline.py \
  tests/test_paper_enrichment.py \
  tests/test_url_to_csv.py \
  tests/test_csv_update.py \
  tests/test_notion_mode.py \
  tests/test_arxiv_relations.py
git commit -m "refactor: thread doi resolution clients through shared flows"
```

### Task 5: Build the New Clients in Runners and Verify End-to-End Wiring

**Files:**
- Modify: `src/csv_update/runner.py`
- Modify: `src/url_to_csv/runner.py`
- Modify: `src/notion_sync/runner.py`
- Modify: `src/arxiv_relations/runner.py`
- Test: `tests/test_url_to_csv.py`
- Test: `tests/test_csv_update.py`
- Test: `tests/test_notion_mode.py`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write failing runner wiring tests**

Add or update runner tests so fake pipeline functions receive `crossref_client` and `datacite_client` objects in addition to the existing `openalex_client`. Example pattern:

```python
class FakeCrossrefClient:
    def __init__(self, session, *, max_concurrent=0, min_interval=0):
        self.session = session


class FakeDataCiteClient:
    def __init__(self, session, *, max_concurrent=0, min_interval=0):
        self.session = session


async def fake_export_url_to_csv(*args, **kwargs):
    assert kwargs["crossref_client"].__class__.__name__ == "FakeCrossrefClient"
    assert kwargs["datacite_client"].__class__.__name__ == "FakeDataCiteClient"
    return expected_result
```

- [ ] **Step 2: Run runner wiring tests and verify failure**

Run:

```bash
uv run pytest \
  tests/test_url_to_csv.py \
  tests/test_csv_update.py \
  tests/test_notion_mode.py \
  tests/test_arxiv_relations.py \
  -k 'run_url_mode or run_csv_mode or run_notion_mode or content_cache or wires' -q
```

Expected: failures because the runners do not yet build or pass the new clients.

- [ ] **Step 3: Implement runner-level client construction**

Instantiate `CrossrefClient` and `DataCiteClient` with the existing `build_client(...)` pattern and thread them into the pipelines:

```python
crossref_client = build_client(
    crossref_client_cls,
    runtime.session,
    max_concurrent=CONCURRENT_LIMIT,
    min_interval=REQUEST_DELAY,
)
datacite_client = build_client(
    datacite_client_cls,
    runtime.session,
    max_concurrent=CONCURRENT_LIMIT,
    min_interval=REQUEST_DELAY,
)
```

Apply to:

- `src/csv_update/runner.py`
- `src/url_to_csv/runner.py`
- `src/notion_sync/runner.py`
- `src/arxiv_relations/runner.py`

- [ ] **Step 4: Run runner wiring tests and verify pass**

Run the same focused command from Step 2.

Expected: runner tests pass and fake pipeline assertions confirm the new clients are wired through.

- [ ] **Step 5: Commit the runner wiring**

```bash
git add \
  src/csv_update/runner.py \
  src/url_to_csv/runner.py \
  src/notion_sync/runner.py \
  src/arxiv_relations/runner.py \
  tests/test_url_to_csv.py \
  tests/test_csv_update.py \
  tests/test_notion_mode.py \
  tests/test_arxiv_relations.py
git commit -m "feat: wire crossref and datacite into doi resolution runners"
```

### Task 6: Full Verification

**Files:**
- Verify only

- [ ] **Step 1: Run the focused DOI-resolution regression suite**

Run:

```bash
uv run pytest \
  tests/test_openalex.py \
  tests/test_crossref.py \
  tests/test_datacite.py \
  tests/test_arxiv_url_resolution.py \
  tests/test_paper_enrichment.py \
  tests/test_url_to_csv.py \
  tests/test_csv_update.py \
  tests/test_notion_mode.py \
  tests/test_arxiv_relations.py \
  -q
```

Expected: focused suites pass.

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest -q`

Expected: full suite passes with no regressions.

- [ ] **Step 3: Run one real DOI normalization smoke test**

Use a copied real CSV, not the user’s source file:

```bash
TMP_DIR=$(mktemp -d)
cp /Users/songliyu/Documents/scripts.ghstars/output/arxiv-2312.03203-citations-20260330090307.csv "$TMP_DIR/input.csv"
uv run main.py "$TMP_DIR/input.csv"
```

Expected: the command exits `0`, and at least the DOI normalization path executes without errors. If recall still needs improvement, capture before/after rows explicitly instead of guessing from console summaries.

- [ ] **Step 4: Summarize outcomes before handing off**

Include:

- which files changed
- which old behaviors were intentionally removed from the DOI path
- focused test command and result
- full `pytest` command and result
- smoke-test command and result
