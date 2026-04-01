# OpenAlex Removal And Semantic Scholar Core Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace OpenAlex completely with Semantic Scholar Graph API / ai4scholar across shared arXiv normalization, relation export, and Semantic Scholar search URL ingestion, while removing all OpenAlex code, tests, config, docs, and database traces.

**Architecture:** Keep one shared metadata core built on `Semantic Scholar Graph API`, with one logical `Semantic Scholar resolver` stage internally ordered as `identifier exact -> title exact fallback`. Replace OpenAlex in the shared normalization path first, then hard-cut relation export to Semantic Scholar only, then remove OpenAlex and Semantic Scholar HTML-search leftovers from runtime, tests, docs, and cache/database behavior.

**Tech Stack:** Python 3, `aiohttp`, SQLite (`cache.db`), `pytest`, `uv`, Semantic Scholar Graph API, ai4scholar relay.

---

### Task 1: Replace The Shared arXiv Resolver Contract

**Files:**
- Modify: `src/shared/arxiv_url_resolution.py:37-374`
- Modify: `src/shared/semantic_scholar_graph.py:44-247`
- Modify: `src/shared/relation_resolution_cache.py:1-129`
- Modify: `src/shared/paper_identity.py:5-120`
- Modify: `src/shared/relation_candidates.py:4-14`
- Modify: `src/shared/progress.py:83-116`
- Delete: `src/shared/openalex.py`
- Test: `tests/test_arxiv_url_resolution.py`
- Test: `tests/test_relation_resolution_cache.py`
- Test: `tests/test_paper_identity.py`
- Test: `tests/test_semantic_scholar_graph.py`
- Delete: `tests/test_openalex.py`

- [ ] **Step 1: Write the failing tests for the new Semantic Scholar-only resolver contract**

```python
@pytest.mark.anyio
async def test_resolve_arxiv_url_resolves_doi_via_semantic_scholar_and_records_doi_cache():
    semanticscholar_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            return_value=("https://arxiv.org/abs/2501.12345", "Mapped Arxiv Title", "semantic_scholar_exact_doi")
        )
    )
    cache = RecordingRelationResolutionCache()

    result = await resolve_arxiv_url(
        title="Published Paper",
        raw_url="https://doi.org/10.1007/978-3-031-72933-1_9",
        semanticscholar_graph_client=semanticscholar_client,
        relation_resolution_cache=cache,
        allow_title_search=False,
    )

    assert result.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert result.source == "semantic_scholar_exact_doi"
    assert cache.record_calls == [
        {
            "key_type": "doi",
            "key_value": "https://doi.org/10.1007/978-3-031-72933-1_9",
            "arxiv_url": "https://arxiv.org/abs/2501.12345",
            "resolved_title": "Mapped Arxiv Title",
        }
    ]


@pytest.mark.anyio
async def test_resolve_arxiv_url_uses_source_url_cache_key_for_semantic_scholar_inputs():
    semanticscholar_client = SimpleNamespace(
        find_arxiv_match_by_identifier=AsyncMock(
            side_effect=lambda identifier, title=None: (
                ("https://arxiv.org/abs/2507.01125", "Mapped Title", "semantic_scholar_exact_source_url")
                if identifier == "https://www.semanticscholar.org/paper/Foo/abc123"
                else (None, None, None)
            )
        )
    )
    cache = RecordingRelationResolutionCache()

    result = await resolve_arxiv_url(
        title="Foo",
        raw_url="https://doi.org/10.1145/example",
        semanticscholar_graph_client=semanticscholar_client,
        relation_resolution_cache=cache,
        extra_identifiers=["https://www.semanticscholar.org/paper/Foo/abc123"],
        allow_title_search=False,
    )

    assert result.source == "semantic_scholar_exact_source_url"
    assert ("source_url", "https://www.semanticscholar.org/paper/Foo/abc123") in cache.get_calls


def test_relation_resolution_cache_store_deletes_legacy_openalex_rows_on_init(tmp_path):
    db_path = tmp_path / "cache.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        '''
        CREATE TABLE relation_resolution_cache (
            key_type TEXT NOT NULL,
            key_value TEXT NOT NULL,
            arxiv_url TEXT,
            resolved_title TEXT,
            checked_at TEXT NOT NULL,
            PRIMARY KEY (key_type, key_value)
        )
        '''
    )
    connection.execute(
        "INSERT INTO relation_resolution_cache VALUES (?, ?, ?, ?, ?)",
        ("openalex_work", "https://openalex.org/W123", "https://arxiv.org/abs/2501.12345", "Old", datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()
    connection.close()

    store = RelationResolutionCacheStore(db_path)

    assert store.get("openalex_work", "https://openalex.org/W123") is None
```

- [ ] **Step 2: Run the targeted tests to verify the current OpenAlex-based code fails them**

Run:

```bash
uv run python -m pytest \
  tests/test_arxiv_url_resolution.py \
  tests/test_relation_resolution_cache.py \
  tests/test_paper_identity.py \
  tests/test_semantic_scholar_graph.py -q
```

Expected:

```text
FAIL current code still references openalex_work, lacks Semantic Scholar resolver helpers, or keeps legacy cache rows
```

- [ ] **Step 3: Implement Semantic Scholar-only exact lookup, exact-title fallback, and generic cache keys**

```python
# src/shared/arxiv_url_resolution.py
def _build_cache_keys(identifiers: list[str]) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw_identifier in identifiers:
        normalized_source_url = normalize_semanticscholar_paper_url(raw_identifier) or " ".join(str(raw_identifier or "").split()).strip()
        if normalized_source_url and normalized_source_url.startswith("http"):
            key = ("source_url", normalized_source_url)
            if key not in seen:
                keys.append(key)
                seen.add(key)

    for raw_identifier in identifiers:
        normalized_doi = normalize_doi_url(raw_identifier)
        if normalized_doi:
            key = ("doi", normalized_doi)
            if key not in seen:
                keys.append(key)
                seen.add(key)

    return keys


semantic_lookup = getattr(semanticscholar_graph_client, "find_arxiv_match_by_identifier", None)
if callable(semantic_lookup):
    for key_type, key_value in cache_keys:
        try:
            arxiv_url, resolved_title, source = await semantic_lookup(key_value, title=normalized_title or None)
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
            metadata_transient_failure = True
            continue
        if arxiv_url:
            _record_positive_resolution(
                relation_resolution_cache,
                cache_keys,
                arxiv_url=arxiv_url,
                resolved_title=resolved_title or normalized_title or None,
            )
            return ArxivUrlResolutionResult(
                resolved_url=arxiv_url,
                canonical_arxiv_url=arxiv_url,
                resolved_title=resolved_title or normalized_title or arxiv_url,
                source=source or f"semantic_scholar_exact_{key_type}",
                script_derived=True,
            )
```

```python
# src/shared/semantic_scholar_graph.py
async def find_arxiv_match_by_identifier(
    self,
    identifier: str,
    *,
    title: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    normalized_doi = normalize_doi_url(identifier)
    normalized_source_url = self._normalize_source_url(identifier)
    lookup_identifiers: list[tuple[str, str, str]] = []
    if normalized_doi:
        lookup_identifiers.append((f"DOI:{normalized_doi.removeprefix('https://doi.org/')}", "doi", "semantic_scholar_exact_doi"))
    if normalized_source_url:
        lookup_identifiers.append((f"URL:{normalized_source_url}", "source_url", "semantic_scholar_exact_source_url"))

    for paper_identifier, _kind, source in lookup_identifiers:
        paper = await self.fetch_paper_by_identifier(paper_identifier)
        arxiv_url = self._build_arxiv_url((paper or {}).get("externalIds", {}).get("ArXiv"))
        if arxiv_url:
            resolved_title = " ".join(str((paper or {}).get("title") or title or "").split()).strip()
            return arxiv_url, resolved_title or title, source

    return await self.find_arxiv_match_by_title(title or "", source_url=normalized_source_url)


async def find_arxiv_match_by_title(
    self,
    title: str,
    *,
    source_url: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    normalized_title = normalize_title_for_matching(title)
    if not normalized_title:
        return None, None, None

    matches = await self.search_papers_by_title(title)
    for paper in matches:
        candidate_title = " ".join(str(paper.get("title") or "").split()).strip()
        if normalize_title_for_matching(candidate_title) != normalized_title:
            continue
        arxiv_url = self._build_arxiv_url((paper.get("externalIds") or {}).get("ArXiv"))
        if arxiv_url:
            return arxiv_url, candidate_title or title, "semantic_scholar_title_exact"
    return None, None, None
```

```python
# src/shared/relation_resolution_cache.py
def _initialize_schema(self) -> None:
    self.connection.execute(
        """
        CREATE TABLE IF NOT EXISTS relation_resolution_cache (
            key_type TEXT NOT NULL,
            key_value TEXT NOT NULL,
            arxiv_url TEXT,
            resolved_title TEXT,
            checked_at TEXT NOT NULL,
            PRIMARY KEY (key_type, key_value)
        )
        """
    )
    columns = {
        row["name"]
        for row in self.connection.execute(
            "PRAGMA table_info(relation_resolution_cache)"
        ).fetchall()
    }
    if "resolved_title" not in columns:
        self.connection.execute(
            "ALTER TABLE relation_resolution_cache ADD COLUMN resolved_title TEXT"
        )
    self.connection.execute(
        """
        DELETE FROM relation_resolution_cache
        WHERE key_type NOT IN ('doi', 'source_url')
        """
    )
    self.connection.commit()
```

```python
# src/shared/relation_candidates.py
@dataclass(frozen=True)
class RelatedWorkCandidate:
    title: str
    direct_arxiv_url: str | None
    doi_url: str | None
    landing_page_url: str | None
    source_url: str
```

```python
# src/shared/progress.py
mapping = {
    "doi": "DOI",
    "source_url": "Source URL",
}
if source.startswith("semantic_scholar_exact_"):
    kind = source.removeprefix("semantic_scholar_exact_")
    return f"Semantic Scholar exact ({_humanize_resolution_source_kind(kind)})"
if source == "semantic_scholar_title_exact":
    return "Semantic Scholar title exact"
```

- [ ] **Step 4: Remove the old OpenAlex helper and its tests**

```python
# src/shared/paper_identity.py
SEMANTIC_SCHOLAR_HOSTS = {"semanticscholar.org", "www.semanticscholar.org"}
DOI_TEXT_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)

def normalize_semanticscholar_paper_url(url: str) -> str | None:
    if not url or not isinstance(url, str):
        return None

    parsed = urlparse(url.strip())
    host = (parsed.netloc or parsed.hostname or "").lower()
    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/")
    if parsed.scheme not in {"http", "https"} or host not in SEMANTIC_SCHOLAR_HOSTS or not path.startswith("/paper/"):
        return None

    return urlunparse(("https", "www.semanticscholar.org", path, "", "", ""))
```

Delete the OpenAlex-specific helper block above and replace the identity tests with Semantic Scholar-only expectations:

```python
def test_normalize_semanticscholar_paper_url_accepts_paper_pages():
    assert normalize_semanticscholar_paper_url("https://www.semanticscholar.org/paper/Foo/abc123") == (
        "https://www.semanticscholar.org/paper/Foo/abc123"
    )
```

- [ ] **Step 5: Run the focused suite to verify the resolver hard-cut passes**

Run:

```bash
uv run python -m pytest \
  tests/test_arxiv_url_resolution.py \
  tests/test_relation_resolution_cache.py \
  tests/test_paper_identity.py \
  tests/test_semantic_scholar_graph.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 6: Commit the resolver-contract replacement**

```bash
git add \
  src/shared/arxiv_url_resolution.py \
  src/shared/semantic_scholar_graph.py \
  src/shared/relation_resolution_cache.py \
  src/shared/paper_identity.py \
  src/shared/relation_candidates.py \
  src/shared/progress.py \
  tests/test_arxiv_url_resolution.py \
  tests/test_relation_resolution_cache.py \
  tests/test_paper_identity.py \
  tests/test_semantic_scholar_graph.py
git rm src/shared/openalex.py tests/test_openalex.py
git commit -m "Replace shared arXiv resolver with Semantic Scholar"
```

### Task 2: Remove OpenAlex From Runtime Wiring Across All Modes

**Files:**
- Modify: `src/shared/runtime.py:25-73`
- Modify: `src/shared/paper_enrichment.py:34-63`
- Modify: `src/shared/paper_export.py:10-92`
- Modify: `src/csv_update/runner.py:26-107`
- Modify: `src/url_to_csv/runner.py:31-146`
- Modify: `src/notion_sync/runner.py:32-137`
- Modify: `src/arxiv_relations/runner.py:31-125`
- Test: `tests/test_main.py`
- Test: `tests/test_paper_export.py`
- Test: `tests/test_csv_update.py`
- Test: `tests/test_url_to_csv.py`
- Test: `tests/test_notion_mode.py`

- [ ] **Step 1: Write failing tests asserting runtime/config no longer expose OpenAlex**

```python
def test_load_runtime_config_does_not_read_openalex_api_key():
    config = load_runtime_config(
        {
            "OPENALEX_API_KEY": "oa_key",
            "SEMANTIC_SCHOLAR_API_KEY": "ss_key",
            "AIFORSCHOLAR_TOKEN": "relay_key",
        }
    )

    assert "openalex_api_key" not in config
    assert config["semantic_scholar_api_key"] == "ss_key"
    assert config["aiforscholar_token"] == "relay_key"


@pytest.mark.anyio
async def test_run_arxiv_relations_mode_builds_only_semantic_scholar_graph_client(monkeypatch):
    captured = {}

    async def fake_export(*args, **kwargs):
        captured["export_kwargs"] = kwargs
        return SimpleNamespace(
            references=SimpleNamespace(resolved=0, skipped=[], csv_path=Path("references.csv")),
            citations=SimpleNamespace(resolved=0, skipped=[], csv_path=Path("citations.csv")),
        )

    class FakeRuntimeContext:
        async def __aenter__(self):
            return SimpleNamespace(
                session=object(),
                discovery_client=object(),
                github_client=object(),
                relation_resolution_cache=None,
            )

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("src.arxiv_relations.runner.load_runtime_config", lambda _env: {
        "github_token": "",
        "huggingface_token": "",
        "alphaxiv_token": "",
        "aiforscholar_token": "",
        "semantic_scholar_api_key": "ss_key",
        "arxiv_relation_no_arxiv_recheck_days": 30,
        "repo_discovery_no_repo_recheck_days": 7,
    })
    monkeypatch.setattr("src.arxiv_relations.runner.open_runtime_clients", lambda *args, **kwargs: FakeRuntimeContext())
    monkeypatch.setattr("src.arxiv_relations.runner.export_arxiv_relations_to_csv", fake_export)

    class FakeSemanticScholarGraphClient:
        def __init__(self, _session, **kwargs):
            captured["semantic_kwargs"] = kwargs

    exit_code = await run_arxiv_relations_mode(
        "https://arxiv.org/abs/2510.22706",
        arxiv_client_cls=lambda *_args, **_kwargs: object(),
        crossref_client_cls=lambda *_args, **_kwargs: object(),
        datacite_client_cls=lambda *_args, **_kwargs: object(),
        discovery_client_cls=lambda *_args, **_kwargs: object(),
        github_client_cls=lambda *_args, **_kwargs: object(),
        content_client_cls=lambda *_args, **_kwargs: object(),
        semanticscholar_graph_client_cls=FakeSemanticScholarGraphClient,
    )

    assert exit_code == 0
    assert captured["semantic_kwargs"]["semantic_scholar_api_key"] == "ss_key"
    assert "openalex_client" not in captured["export_kwargs"]


@pytest.mark.anyio
async def test_export_paper_seeds_to_csv_threads_semantic_scholar_graph_client_to_enrichment(monkeypatch, tmp_path):
    received = {}

    async def fake_process_single_paper(*args, **kwargs):
        received["semanticscholar_graph_client"] = kwargs.get("semanticscholar_graph_client")
        return SimpleNamespace(
            title="Paper",
            raw_url="https://doi.org/10.1000/example",
            normalized_url="https://arxiv.org/abs/2501.12345",
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            github_url="https://github.com/foo/bar",
            github_source="discovered",
            stars=10,
            reason=None,
        )

    monkeypatch.setattr("src.shared.paper_export.process_single_paper", fake_process_single_paper)

    semanticscholar_graph_client = object()
    await export_paper_seeds_to_csv(
        [PaperSeed(name="Paper", url="https://doi.org/10.1000/example")],
        tmp_path / "papers.csv",
        discovery_client=object(),
        github_client=object(),
        semanticscholar_graph_client=semanticscholar_graph_client,
    )

    assert received["semanticscholar_graph_client"] is semanticscholar_graph_client
```

- [ ] **Step 2: Run the runtime-focused tests to prove the current wiring still depends on OpenAlex**

Run:

```bash
uv run python -m pytest \
  tests/test_main.py \
  tests/test_paper_export.py \
  tests/test_csv_update.py \
  tests/test_url_to_csv.py \
  tests/test_notion_mode.py -q
```

Expected:

```text
FAIL current wiring still exposes openalex_api_key or constructs/threads openalex_client
```

- [ ] **Step 3: Remove OpenAlex from config loading and client construction**

```python
# src/shared/runtime.py
return {
    "github_token": (env.get("GITHUB_TOKEN") or "").strip(),
    "huggingface_token": (env.get("HUGGINGFACE_TOKEN") or "").strip(),
    "alphaxiv_token": (env.get("ALPHAXIV_TOKEN") or "").strip(),
    "aiforscholar_token": (env.get("AIFORSCHOLAR_TOKEN") or "").strip(),
    "semantic_scholar_api_key": (env.get("SEMANTIC_SCHOLAR_API_KEY") or "").strip(),
    "arxiv_relation_no_arxiv_recheck_days": _parse_positive_int(
        env.get("ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS"),
        default=ARXIV_RELATION_NO_ARXIV_RECHECK_DAYS,
    ),
    "repo_discovery_no_repo_recheck_days": _parse_positive_int(
        repo_discovery_recheck_days_raw,
        default=REPO_DISCOVERY_NO_REPO_RECHECK_DAYS,
    ),
}
```

```python
# src/csv_update/runner.py
async def run_csv_mode(
    csv_path: Path | str,
    *,
    session_factory=aiohttp.ClientSession,
    arxiv_client_cls=ArxivClient,
    discovery_client_cls=DiscoveryClient,
    github_client_cls=GitHubClient,
    semanticscholar_graph_client_cls=SemanticScholarGraphClient,
    crossref_client_cls=CrossrefClient,
    datacite_client_cls=DataCiteClient,
    content_client_cls=AlphaXivContentClient,
    content_cache_root: Path | str | None = None,
):
    semanticscholar_graph_client = build_client(
        semanticscholar_graph_client_cls,
        runtime.session,
        semantic_scholar_api_key=config["semantic_scholar_api_key"],
        aiforscholar_token=config["aiforscholar_token"],
        max_concurrent=CONCURRENT_LIMIT,
        min_interval=resolve_semantic_scholar_min_interval(
            config["semantic_scholar_api_key"],
            config["aiforscholar_token"],
            REQUEST_DELAY,
        ),
    )
```

Apply the same construction pattern in:

- `src/url_to_csv/runner.py`
- `src/notion_sync/runner.py`
- `src/arxiv_relations/runner.py`

- [ ] **Step 4: Rename enrichment/export call signatures to Semantic Scholar-only metadata injection**

```python
# src/shared/paper_enrichment.py
async def process_single_paper(
    request: PaperEnrichmentRequest,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
):
    url_resolution = await resolve_arxiv_url(
        title,
        raw_url,
        arxiv_client=arxiv_client,
        semanticscholar_graph_client=semanticscholar_graph_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        discovery_client=discovery_client,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
```

```python
# src/shared/paper_export.py
async def build_paper_outcome(
    index: int,
    seed: PaperSeed,
    *,
    discovery_client,
    github_client,
    arxiv_client=None,
    semanticscholar_graph_client=None,
    crossref_client=None,
    datacite_client=None,
    content_cache=None,
    relation_resolution_cache=None,
    arxiv_relation_no_arxiv_recheck_days: int = 30,
):
    enrichment = await process_single_paper(
        PaperEnrichmentRequest(
            title=seed.name,
            raw_url=seed.url,
            existing_github_url=None,
            allow_title_search=True,
            allow_github_discovery=True,
            precomputed_normalized_url=seed.url if seed.url_resolution_authoritative else None,
            precomputed_canonical_arxiv_url=seed.canonical_arxiv_url,
            url_resolution_authoritative=seed.url_resolution_authoritative,
        ),
        semanticscholar_graph_client=semanticscholar_graph_client,
        discovery_client=discovery_client,
        github_client=github_client,
        arxiv_client=arxiv_client,
        crossref_client=crossref_client,
        datacite_client=datacite_client,
        content_cache=content_cache,
        relation_resolution_cache=relation_resolution_cache,
        arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    )
```

- [ ] **Step 5: Run the runtime suite again**

Run:

```bash
uv run python -m pytest \
  tests/test_main.py \
  tests/test_paper_export.py \
  tests/test_csv_update.py \
  tests/test_url_to_csv.py \
  tests/test_notion_mode.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 6: Commit the runtime-wiring cleanup**

```bash
git add \
  src/shared/runtime.py \
  src/shared/paper_enrichment.py \
  src/shared/paper_export.py \
  src/csv_update/runner.py \
  src/url_to_csv/runner.py \
  src/notion_sync/runner.py \
  src/arxiv_relations/runner.py \
  tests/test_main.py \
  tests/test_paper_export.py \
  tests/test_csv_update.py \
  tests/test_url_to_csv.py \
  tests/test_notion_mode.py
git commit -m "Remove OpenAlex runtime wiring"
```

### Task 3: Hard-Cut Single-Paper Relation Export To Semantic Scholar Only

**Files:**
- Modify: `src/arxiv_relations/pipeline.py:82-648`
- Test: `tests/test_arxiv_relations.py`

- [ ] **Step 1: Write failing relation tests for the Semantic Scholar-only path**

```python
@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_fails_when_semantic_scholar_target_cannot_be_resolved():
    semanticscholar_graph_client = SimpleNamespace(
        fetch_paper_by_identifier=AsyncMock(return_value=None),
        search_papers_by_title=AsyncMock(return_value=[]),
    )

    with pytest.raises(ValueError, match="No Semantic Scholar relation target found"):
        await export_arxiv_relations_to_csv(
            "https://arxiv.org/abs/2510.22706",
            arxiv_client=FakeArxivClient(),
            semanticscholar_graph_client=semanticscholar_graph_client,
            discovery_client=FakeDiscoveryClient(),
            github_client=FakeGitHubClient(),
        )


@pytest.mark.anyio
async def test_export_arxiv_relations_to_csv_keeps_empty_semantic_side_without_openalex_fallback():
    statuses = []
    semanticscholar_graph_client = SimpleNamespace(
        fetch_paper_by_identifier=AsyncMock(
            return_value={"paperId": "paper-1", "title": "Target", "externalIds": {"ArXiv": "2510.22706"}}
        ),
        search_papers_by_title=AsyncMock(return_value=[]),
        fetch_references=AsyncMock(return_value=[]),
        fetch_citations=AsyncMock(return_value=[]),
        build_related_work_candidate=lambda row: row,
    )

    result = await export_arxiv_relations_to_csv(
        "https://arxiv.org/abs/2510.22706",
        arxiv_client=FakeArxivClient(),
        semanticscholar_graph_client=semanticscholar_graph_client,
        discovery_client=FakeDiscoveryClient(),
        github_client=FakeGitHubClient(),
        status_callback=statuses.append,
    )
    assert "falling back to OpenAlex" not in statuses
    assert result.references.resolved == 0
    assert result.citations.resolved == 0
```

- [ ] **Step 2: Run the relation tests to confirm current code still contains OpenAlex fallback behavior**

Run:

```bash
uv run python -m pytest tests/test_arxiv_relations.py -q
```

Expected:

```text
FAIL current relation pipeline still references openalex target lookup or fallback behavior
```

- [ ] **Step 3: Delete OpenAlex target resolution and fallback branches**

```python
# src/arxiv_relations/pipeline.py
async def _fetch_primary_relation_candidates(
    *,
    relation_label: str,
    semanticscholar_graph_client,
    semantic_scholar_target_paper: dict,
    status_callback=None,
) -> list[RelatedWorkCandidate]:
    semantic_fetcher = (
        semanticscholar_graph_client.fetch_references
        if relation_label == "references"
        else semanticscholar_graph_client.fetch_citations
    )
    if callable(status_callback):
        status_callback(f"🔎 Fetching Semantic Scholar {relation_label}")
    semantic_rows = await semantic_fetcher(semantic_scholar_target_paper)
    if callable(status_callback):
        status_callback(f"📚 Semantic Scholar returned {len(semantic_rows)} {relation_label}")
    return [semanticscholar_graph_client.build_related_work_candidate(row) for row in semantic_rows]
```

```python
# src/arxiv_relations/pipeline.py
def _fallback_related_work_url(candidate) -> str:
    return candidate.doi_url or candidate.landing_page_url or candidate.source_url
```

```python
# src/arxiv_relations/pipeline.py
semantic_scholar_target_paper = await _resolve_target_semantic_scholar_paper(
    arxiv_url,
    title,
    semanticscholar_graph_client,
)
if semantic_scholar_target_paper is None:
    raise ValueError(f"No Semantic Scholar relation target found for title: {title}")
```

- [ ] **Step 4: Remove OpenAlex-specific arguments from relation normalization helpers**

```python
# src/arxiv_relations/pipeline.py
resolution = await resolve_arxiv_url_fn(
    title=candidate.title,
    raw_url=fallback_url,
    arxiv_client=arxiv_client,
    semanticscholar_graph_client=semanticscholar_graph_client,
    crossref_client=crossref_client,
    datacite_client=datacite_client,
    discovery_client=discovery_client,
    relation_resolution_cache=relation_resolution_cache,
    arxiv_relation_no_arxiv_recheck_days=arxiv_relation_no_arxiv_recheck_days,
    extra_identifiers=[candidate.source_url, candidate.doi_url],
)
```

- [ ] **Step 5: Re-run the relation test suite**

Run:

```bash
uv run python -m pytest tests/test_arxiv_relations.py -q
```

Expected:

```text
all relation tests passed
```

- [ ] **Step 6: Commit the relation hard cut**

```bash
git add src/arxiv_relations/pipeline.py tests/test_arxiv_relations.py
git commit -m "Remove OpenAlex fallback from relation export"
```

### Task 4: Rebuild Semantic Scholar Search URL Ingestion On Graph API Only

**Files:**
- Modify: `src/url_to_csv/semanticscholar.py:1-269`
- Modify: `src/url_to_csv/pipeline.py:24-220`
- Test: `tests/test_semanticscholar.py`
- Test: `tests/test_url_to_csv.py`

- [ ] **Step 1: Write failing tests for API-only search ingestion**

```python
@pytest.mark.anyio
async def test_fetch_paper_seeds_from_semanticscholar_url_uses_bulk_api_filters_and_token_pagination(tmp_path):
    class FakeSemanticScholarClient:
        async def search_papers_bulk(self, *, query, year, fields_of_study, venue, sort, token=None):
            if token is None:
                return {
                    "data": [
                        {
                            "paperId": "abc123",
                            "title": "Paper A",
                            "externalIds": {"ArXiv": "2501.00001"},
                            "url": "https://www.semanticscholar.org/paper/Paper-A/abc123",
                        }
                    ],
                    "token": "next-token",
                }
            return {
                "data": [
                    {
                        "paperId": "def456",
                        "title": "Paper B",
                        "externalIds": {},
                        "url": "https://www.semanticscholar.org/paper/Paper-B/def456",
                    }
                ]
            }

    result = await fetch_paper_seeds_from_semanticscholar_url(
        "https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&year%5B0%5D=2025",
        semanticscholar_client=FakeSemanticScholarClient(),
        output_dir=tmp_path,
    )

    assert [(seed.name, seed.url) for seed in result.seeds] == [
        ("Paper A", "https://arxiv.org/abs/2501.00001"),
        ("Paper B", "https://www.semanticscholar.org/paper/Paper-B/def456"),
    ]
```

- [ ] **Step 2: Run the search-ingestion tests to verify the current HTML/browser path fails them**

Run:

```bash
uv run python -m pytest tests/test_semanticscholar.py tests/test_url_to_csv.py -q
```

Expected:

```text
FAIL current implementation still requires fetch_search_page_html or HTML pager parsing
```

- [ ] **Step 3: Replace the HTML client with Graph API bulk search**

```python
# src/url_to_csv/semanticscholar.py
class SemanticScholarSearchClient:
    def __init__(
        self,
        session,
        *,
        semantic_scholar_api_key: str = "",
        aiforscholar_token: str = "",
        max_concurrent: int = 5,
        min_interval: float = 0.2,
    ):
        self.graph_client = SemanticScholarGraphClient(
            session,
            semantic_scholar_api_key=semantic_scholar_api_key,
            aiforscholar_token=aiforscholar_token,
            max_concurrent=max_concurrent,
            min_interval=min_interval,
        )

    async def search_papers_bulk(
        self,
        *,
        query: str,
        year: str | None,
        fields_of_study: tuple[str, ...],
        venue: tuple[str, ...],
        sort: str,
        token: str | None = None,
    ) -> dict:
        params = {
            "query": query,
            "fields": "paperId,title,externalIds,url",
        }
        if year:
            params["year"] = year
        if fields_of_study:
            params["fieldsOfStudy"] = ",".join(fields_of_study)
        if venue:
            params["venue"] = ",".join(venue)
        if sort:
            params["sort"] = _map_sort(sort)
        if token:
            params["token"] = token
        return await self.graph_client._get_json(f"{self.graph_client.graph_url}/paper/search/bulk", params=params)
```

```python
# src/url_to_csv/semanticscholar.py
def _build_seed(row: dict) -> PaperSeed | None:
    external_ids = row.get("externalIds") or {}
    arxiv_id = external_ids.get("ArXiv")
    if isinstance(arxiv_id, str) and arxiv_id.strip():
        return PaperSeed(name=row["title"], url=f"https://arxiv.org/abs/{arxiv_id.strip()}")

    paper_url = normalize_semanticscholar_paper_url(row.get("url") or "")
    if paper_url:
        return PaperSeed(name=row["title"], url=paper_url)
    return None
```

- [ ] **Step 4: Delete the browser/HTML-only helpers and adapt the pipeline signature**

Delete these symbols from `src/url_to_csv/semanticscholar.py` entirely:

```python
TOTAL_PAGES_PATTERN
TITLE_LINK_PATTERN
extract_total_pages_from_semanticscholar_html
extract_paper_seeds_from_semanticscholar_html
build_semanticscholar_search_page_url
_fetch_search_page
dump_rendered_html
```

Replace the page-crawl loop with token-based pagination:

```python
# src/url_to_csv/semanticscholar.py
async def fetch_paper_seeds_from_semanticscholar_url(
    input_url: str,
    *,
    semanticscholar_client,
    output_dir: Path | None = None,
    status_callback=None,
) -> FetchedSeedsResult:
    spec = parse_semanticscholar_url(input_url)
    csv_path = output_csv_path_for_semanticscholar_url(input_url, output_dir=output_dir)

    seeds: list[PaperSeed] = []
    seen_urls: set[str] = set()
    next_token: str | None = None

    while True:
        payload = await semanticscholar_client.search_papers_bulk(
            query=spec.search_text,
            year=",".join(spec.years) or None,
            fields_of_study=spec.fields_of_study,
            venue=spec.venues,
            sort=spec.sort,
            token=next_token,
        )
        page_rows = payload.get("data") or []
        for row in page_rows:
            seed = _build_seed(row)
            if seed is None or seed.url in seen_urls:
                continue
            seeds.append(seed)
            seen_urls.add(seed.url)

        next_token = payload.get("token")
        if not next_token:
            break

    return FetchedSeedsResult(seeds=seeds, csv_path=csv_path)
```

Update the runner construction to pass API credentials:

```python
semanticscholar_client = build_client(
    semanticscholar_client_cls,
    runtime.session,
    semantic_scholar_api_key=config["semantic_scholar_api_key"],
    aiforscholar_token=config["aiforscholar_token"],
    max_concurrent=CONCURRENT_LIMIT,
    min_interval=resolve_semantic_scholar_min_interval(
        config["semantic_scholar_api_key"],
        config["aiforscholar_token"],
        REQUEST_DELAY,
    ),
)
```

- [ ] **Step 5: Re-run the Semantic Scholar URL tests**

Run:

```bash
uv run python -m pytest tests/test_semanticscholar.py tests/test_url_to_csv.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 6: Commit the API-only search ingestion rewrite**

```bash
git add src/url_to_csv/semanticscholar.py src/url_to_csv/pipeline.py src/url_to_csv/runner.py tests/test_semanticscholar.py tests/test_url_to_csv.py
git commit -m "Replace Semantic Scholar HTML search with Graph API"
```

### Task 5: Final Cleanup Of Docs, Env, CLI, And Full Regression Coverage

**Files:**
- Modify: `.env.example:1-24`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `cache.py:10-82`
- Modify: `src/app.py:82-118`
- Test: `tests/test_cache_cli.py`
- Test: `tests/test_main.py`
- Full suite: `tests/test_semantic_scholar_graph.py`, `tests/test_semanticscholar.py`, `tests/test_arxiv_url_resolution.py`, `tests/test_arxiv_relations.py`, `tests/test_main.py`, `tests/test_url_to_csv.py`, `tests/test_csv_update.py`, `tests/test_notion_mode.py`, `tests/test_paper_export.py`, `tests/test_relation_resolution_cache.py`

- [ ] **Step 1: Write failing cleanup tests for env/docs/cache behavior**

```python
def test_env_example_does_not_include_openalex_api_key():
    assert "OPENALEX_API_KEY=" not in Path(".env.example").read_text()


def test_cache_main_preserves_positive_source_url_relation_entries(tmp_path, capsys):
    db_path = tmp_path / "cache.db"
    store = RelationResolutionCacheStore(db_path)
    store.record_resolution(
        key_type="source_url",
        key_value="https://www.semanticscholar.org/paper/Foo/abc123",
        arxiv_url="https://arxiv.org/abs/2501.12345",
        resolved_title="Mapped Title",
    )
    store.close()

    exit_code = cache.main(["--db-path", str(db_path), "--apply"])
    assert exit_code == 0
```

- [ ] **Step 2: Update docs and env example to the final API-only, OpenAlex-free story**

```env
# Optional for Semantic Scholar-backed metadata resolution across relation export, CSV update, URL export, and Notion sync.
SEMANTIC_SCHOLAR_API_KEY=
AIFORSCHOLAR_TOKEN=
```

Update README and ARCHITECTURE to describe:

- one shared `Semantic Scholar resolver`
- no OpenAlex stage
- no Semantic Scholar HTML search
- relation retained URL priority `DOI > landing page > source URL`
- official API preferred over ai4scholar relay

- [ ] **Step 3: Make the cache CLI and runtime cleanup OpenAlex-free**

```python
# cache.py
description="Inspect or clear negative repo-discovery and relation-resolution cache entries."
print(f"Relation negative entries: {relation_negative_entry_count}")
print(
    "Dry run: found "
    f"{relation_negative_entry_count} negative relation resolution cache entries in {db_path}. "
    "Re-run with --apply to delete them."
)
print(f"Deleted {deleted_relation} negative relation resolution cache entries from {db_path}.")
```

Keep the CLI generic. Do not mention OpenAlex anywhere in output or seeded tests. Seed relation positives with `source_url` instead of `openalex_work`.

- [ ] **Step 4: Run the full regression suite**

Run:

```bash
uv run python -m pytest \
  tests/test_semantic_scholar_graph.py \
  tests/test_semanticscholar.py \
  tests/test_arxiv_url_resolution.py \
  tests/test_arxiv_relations.py \
  tests/test_main.py \
  tests/test_url_to_csv.py \
  tests/test_csv_update.py \
  tests/test_notion_mode.py \
  tests/test_paper_export.py \
  tests/test_relation_resolution_cache.py \
  tests/test_cache_cli.py
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 5: Run grep-based cleanup verification and live export smoke tests**

Run:

```bash
rg -n "OpenAlex|openalex|OPENALEX" src tests README.md ARCHITECTURE.md .env.example
```

Expected:

```text
no matches
```

Run:

```bash
uv run main.py 'https://arxiv.org/abs/2510.22706'
```

Expected:

```text
exit code 0
Semantic Scholar returned non-empty references/citations
CSV files written under ./output
```

Run:

```bash
uv run main.py 'https://www.semanticscholar.org/search?q=semantic%203d%20reconstruction&year%5B0%5D=2025'
```

Expected:

```text
exit code 0
Semantic Scholar search URL handled through Graph API only
CSV file written under ./output
```

- [ ] **Step 6: Commit the final cleanup**

```bash
git add \
  .env.example \
  README.md \
  ARCHITECTURE.md \
  cache.py \
  src/app.py \
  tests/test_cache_cli.py \
  tests/test_main.py
git commit -m "Finalize OpenAlex removal and API-only docs"
```
