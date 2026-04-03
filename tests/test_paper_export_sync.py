from unittest.mock import AsyncMock

import pytest

from src.core.record_model import PropertyState, Record, RecordFacts
from src.shared.papers import PaperSeed


@pytest.mark.anyio
async def test_sync_paper_seed_uses_precomputed_seed_facts_and_ignores_paper_seed_blocked_states(
    monkeypatch,
):
    import src.core.paper_export_sync as paper_export_sync

    init_kwargs = {}
    sync_call = {}
    content_cache = type(
        "RecordingContentCache",
        (),
        {"ensure_local_content_cache": AsyncMock()},
    )()
    discovery_client = object()
    github_client = object()

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            init_kwargs.update(kwargs)

        async def sync(self, record, **kwargs):
            sync_call["record"] = record
            sync_call.update(kwargs)
            synced_record = (
                record.with_property(
                    "github",
                    PropertyState.resolved(
                        "https://github.com/foo/bar",
                        source="discovered",
                    ),
                )
                .with_property(
                    "stars",
                    PropertyState.resolved(42, source="github_api"),
                )
                .with_supporting_state(
                    facts=RecordFacts(
                        normalized_url=record.facts.normalized_url,
                        canonical_arxiv_url=record.facts.canonical_arxiv_url,
                        github_source="discovered",
                        url_resolution_authoritative=record.facts.url_resolution_authoritative,
                    )
                )
            )
            before_repo_metadata = kwargs.get("before_repo_metadata")
            if callable(before_repo_metadata):
                await before_repo_metadata(synced_record)
            return synced_record

    monkeypatch.setattr(
        paper_export_sync,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await paper_export_sync.sync_paper_seed(
        PaperSeed(
            name="Paper A",
            url="https://arxiv.org/pdf/2603.05078v2.pdf",
            canonical_arxiv_url="https://arxiv.org/abs/2603.05078",
            url_resolution_authoritative=True,
        ),
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=content_cache,
    )

    assert init_kwargs["discovery_client"] is discovery_client
    assert init_kwargs["github_client"] is github_client
    assert sync_call["allow_title_search"] is True
    assert sync_call["allow_github_discovery"] is True
    assert sync_call["precomputed_normalized_url"] == "https://arxiv.org/pdf/2603.05078v2.pdf"
    assert sync_call["precomputed_canonical_arxiv_url"] == "https://arxiv.org/abs/2603.05078"
    assert sync_call["url_resolution_authoritative"] is True
    content_cache.ensure_local_content_cache.assert_awaited_once_with(
        "https://arxiv.org/abs/2603.05078"
    )
    assert result.record.url.value == "https://arxiv.org/pdf/2603.05078v2.pdf"
    assert result.record.url.source == "url_resolution"
    assert result.record.created.reason == "created missing from source"
    assert result.record.created.source == "paper_seed"
    assert result.record.about.reason == "about missing from source"
    assert result.record.about.source == "paper_seed"
    assert result.reason is None


@pytest.mark.anyio
async def test_sync_paper_seed_surfaces_fresh_error_even_when_source_label_matches_input(
    monkeypatch,
):
    import src.core.paper_export_sync as paper_export_sync

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            pass

        async def sync(self, record, **kwargs):
            return record.with_property(
                "github",
                PropertyState.failed(
                    "fresh github failure",
                    source="paper_seed",
                ),
            )

    monkeypatch.setattr(
        paper_export_sync,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await paper_export_sync.sync_paper_seed(
        PaperSeed(
            name="Paper A",
            url="https://arxiv.org/abs/2603.05078",
        ),
        discovery_client=object(),
        github_client=object(),
    )

    assert result.record.github.reason == "fresh github failure"
    assert result.record.github.source == "paper_seed"
    assert result.reason == "fresh github failure"


@pytest.mark.anyio
async def test_sync_paper_record_uses_repo_metadata_error_when_no_property_reason_exists(
    monkeypatch,
):
    import src.core.paper_export_sync as paper_export_sync

    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2603.05078",
        github="https://github.com/foo/bar",
        stars=42,
        created="2024-01-01T00:00:00Z",
        about="repo",
        source="csv",
    )

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            pass

        async def sync(self, record, **kwargs):
            return record.with_supporting_state(
                facts=RecordFacts(
                    normalized_url=record.facts.normalized_url,
                    canonical_arxiv_url=record.facts.canonical_arxiv_url,
                    github_source="existing",
                    repo_metadata_error="repo metadata cache miss",
                    url_resolution_authoritative=record.facts.url_resolution_authoritative,
                )
            )

    monkeypatch.setattr(
        paper_export_sync,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await paper_export_sync.sync_paper_record(
        record,
        allow_title_search=False,
        allow_github_discovery=False,
        discovery_client=object(),
        github_client=object(),
    )

    assert result.record.facts.repo_metadata_error == "repo metadata cache miss"
    assert result.reason == "repo metadata cache miss"


@pytest.mark.anyio
async def test_sync_paper_record_does_not_warm_content_cache_for_invalid_canonical_arxiv_url(
    monkeypatch,
):
    import src.core.paper_export_sync as paper_export_sync

    content_cache = type(
        "RecordingContentCache",
        (),
        {"ensure_local_content_cache": AsyncMock()},
    )()
    record = Record.from_source(
        name="Paper A",
        url="https://example.com/paper",
        source="paper_seed",
    )

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            pass

        async def sync(self, record, **kwargs):
            synced_record = record.with_supporting_state(
                facts=RecordFacts(canonical_arxiv_url="not-an-arxiv-url")
            )
            before_repo_metadata = kwargs.get("before_repo_metadata")
            if callable(before_repo_metadata):
                await before_repo_metadata(synced_record)
            return synced_record

    monkeypatch.setattr(
        paper_export_sync,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    await paper_export_sync.sync_paper_record(
        record,
        allow_title_search=True,
        allow_github_discovery=True,
        discovery_client=object(),
        github_client=object(),
        content_cache=content_cache,
    )

    content_cache.ensure_local_content_cache.assert_not_awaited()


@pytest.mark.anyio
async def test_sync_paper_record_does_not_warm_content_cache_for_host_spoofed_arxiv_url(
    monkeypatch,
):
    import src.core.paper_export_sync as paper_export_sync

    content_cache = type(
        "RecordingContentCache",
        (),
        {"ensure_local_content_cache": AsyncMock()},
    )()
    record = Record.from_source(
        name="Paper A",
        url="https://example.com/paper",
        source="paper_seed",
    )

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            pass

        async def sync(self, record, **kwargs):
            synced_record = record.with_supporting_state(
                facts=RecordFacts(
                    canonical_arxiv_url="https://example.com/archive/arxiv.org/abs/2603.05078"
                )
            )
            before_repo_metadata = kwargs.get("before_repo_metadata")
            if callable(before_repo_metadata):
                await before_repo_metadata(synced_record)
            return synced_record

    monkeypatch.setattr(
        paper_export_sync,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    await paper_export_sync.sync_paper_record(
        record,
        allow_title_search=True,
        allow_github_discovery=True,
        discovery_client=object(),
        github_client=object(),
        content_cache=content_cache,
    )

    content_cache.ensure_local_content_cache.assert_not_awaited()


@pytest.mark.anyio
async def test_sync_paper_record_does_not_overwrite_url_from_non_authoritative_normalized_fact(
    monkeypatch,
):
    import src.core.paper_export_sync as paper_export_sync

    record = Record.from_source(
        name="Paper A",
        url="https://doi.org/10.1145/example",
        source="csv",
    ).with_supporting_state(
        facts=RecordFacts(
            normalized_url="https://stale.example/should-not-promote",
            canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
            url_resolution_authoritative=False,
        )
    )

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            pass

        async def sync(self, record, **kwargs):
            return record

    monkeypatch.setattr(
        paper_export_sync,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await paper_export_sync.sync_paper_record(
        record,
        allow_title_search=False,
        allow_github_discovery=False,
        discovery_client=object(),
        github_client=object(),
    )

    assert result.record.url.value == "https://doi.org/10.1145/example"
    assert result.record.url.source == "csv"
