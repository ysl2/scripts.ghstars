from unittest.mock import AsyncMock

import pytest

from src.core.record_model import PropertyState, Record, RecordFacts


@pytest.mark.anyio
async def test_sync_record_with_policy_warms_content_cache_and_applies_authoritative_normalized_url(
    monkeypatch,
):
    import src.core.record_sync_workflow as record_sync_workflow

    init_kwargs = {}
    sync_call = {}
    ordering_events = []
    warm_cache = AsyncMock(
        side_effect=lambda _url: ordering_events.append("content_warmed")
    )
    content_cache = type(
        "RecordingContentCache",
        (),
        {"ensure_local_content_cache": warm_cache},
    )()
    discovery_client = object()
    github_client = object()
    record = Record.from_source(
        name="Paper A",
        url="https://doi.org/10.1145/example",
        source="csv",
    ).with_supporting_state(
        facts=RecordFacts(
            normalized_url="https://arxiv.org/pdf/2603.05078v2.pdf",
            canonical_arxiv_url="https://arxiv.org/abs/2603.05078",
            url_resolution_authoritative=True,
        )
    )

    class FakeRecordSyncService:
        def __init__(self, **kwargs):
            init_kwargs.update(kwargs)

        async def sync(self, input_record, **kwargs):
            sync_call["record"] = input_record
            sync_call.update(kwargs)
            synced_record = input_record.with_supporting_state(
                facts=RecordFacts(
                    normalized_url="https://arxiv.org/abs/2603.05078",
                    canonical_arxiv_url="https://arxiv.org/abs/2603.05078",
                    url_resolution_authoritative=True,
                )
            )
            before_repo_metadata = kwargs.get("before_repo_metadata")
            if callable(before_repo_metadata):
                await before_repo_metadata(synced_record)
            ordering_events.append("repo_metadata_step")
            return synced_record

    monkeypatch.setattr(
        record_sync_workflow,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await record_sync_workflow.sync_record_with_policy(
        record,
        policy=record_sync_workflow.RecordSyncPolicy(
            allow_title_search=True,
            allow_github_discovery=True,
            apply_normalized_url=True,
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
    assert result.record.url.value == "https://arxiv.org/abs/2603.05078"
    assert result.record.url.source == "url_resolution"
    assert ordering_events == ["content_warmed", "repo_metadata_step"]


@pytest.mark.anyio
async def test_first_actionable_reason_falls_back_to_repo_metadata_error():
    import src.core.record_sync_workflow as record_sync_workflow

    original = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2603.05078",
        github="https://github.com/foo/bar",
        stars=42,
        created="2024-01-01T00:00:00Z",
        about="repo",
        source="csv",
    )
    synced = original.with_supporting_state(
        facts=RecordFacts(
            repo_metadata_error="repo metadata cache miss",
        )
    )

    reason = record_sync_workflow.first_actionable_reason(original, synced)

    assert reason == "repo metadata cache miss"


@pytest.mark.anyio
async def test_sync_record_with_policy_does_not_overwrite_url_when_normalization_is_non_authoritative(
    monkeypatch,
):
    import src.core.record_sync_workflow as record_sync_workflow

    record = Record.from_source(
        name="Paper A",
        url="https://example.com/paper",
        source="csv",
    )

    class FakeRecordSyncService:
        def __init__(self, **_kwargs):
            pass

        async def sync(self, input_record, **_kwargs):
            return input_record.with_property(
                "url",
                PropertyState.resolved("https://example.com/paper", source="service"),
            ).with_supporting_state(
                facts=RecordFacts(
                    normalized_url="https://arxiv.org/abs/2603.05078",
                    canonical_arxiv_url="https://arxiv.org/abs/2603.05078",
                    url_resolution_authoritative=False,
                )
            )

    monkeypatch.setattr(
        record_sync_workflow,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await record_sync_workflow.sync_record_with_policy(
        record,
        policy=record_sync_workflow.RecordSyncPolicy(
            allow_title_search=True,
            allow_github_discovery=True,
            apply_normalized_url=True,
        ),
        discovery_client=object(),
        github_client=object(),
    )

    assert result.record.url.value == "https://example.com/paper"
    assert result.record.url.source == "service"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "canonical_url",
    [
        "https://arxiv.org.evil.example/abs/2603.05078",
        "https://arxiv.org/help/contact",
    ],
)
async def test_build_content_warming_callback_skips_invalid_or_host_spoofed_arxiv_urls(
    canonical_url,
):
    import src.core.record_sync_workflow as record_sync_workflow

    warmer = AsyncMock()
    content_cache = type(
        "RecordingContentCache",
        (),
        {"ensure_local_content_cache": warmer},
    )()
    callback = record_sync_workflow.build_content_warming_callback(content_cache)
    record = Record.from_source(
        name="Paper A",
        url="https://example.com/paper",
        source="csv",
    ).with_supporting_state(
        facts=RecordFacts(canonical_arxiv_url=canonical_url),
    )

    await callback(record)

    warmer.assert_not_awaited()


@pytest.mark.anyio
async def test_build_content_warming_callback_swallows_warming_failures():
    import src.core.record_sync_workflow as record_sync_workflow

    warmer = AsyncMock(side_effect=RuntimeError("cache unavailable"))
    content_cache = type(
        "RecordingContentCache",
        (),
        {"ensure_local_content_cache": warmer},
    )()
    callback = record_sync_workflow.build_content_warming_callback(content_cache)
    record = Record.from_source(
        name="Paper A",
        url="https://example.com/paper",
        source="csv",
    ).with_supporting_state(
        facts=RecordFacts(canonical_arxiv_url="https://arxiv.org/abs/2603.05078"),
    )

    await callback(record)

    warmer.assert_awaited_once_with("https://arxiv.org/abs/2603.05078")


@pytest.mark.anyio
async def test_first_actionable_reason_reports_fresh_reason_even_when_source_matches():
    import src.core.record_sync_workflow as record_sync_workflow

    original = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2603.05078",
        github="https://github.com/foo/bar",
        source="csv",
    )
    synced = original.with_property(
        "github",
        PropertyState.failed("fresh repo failure", source=original.github.source),
    )

    reason = record_sync_workflow.first_actionable_reason(original, synced)

    assert reason == "fresh repo failure"
