import pytest

from src.core.record_model import Record, RecordFacts
from src.shared.papers import PaperSeed


@pytest.mark.anyio
async def test_sync_paper_record_delegates_to_shared_workflow_with_matching_policy(
    monkeypatch,
):
    import src.core.paper_export_sync as paper_export_sync

    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2603.05078",
        source="csv",
    )
    call = {}
    expected = paper_export_sync.PaperSyncResult(record=record, reason="ok")
    discovery_client = object()
    github_client = object()

    async def fake_sync_record_with_policy(input_record, *, policy, **kwargs):
        call["record"] = input_record
        call["policy"] = policy
        call["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(
        paper_export_sync,
        "sync_record_with_policy",
        fake_sync_record_with_policy,
    )

    result = await paper_export_sync.sync_paper_record(
        record,
        allow_title_search=False,
        allow_github_discovery=True,
        trust_existing_github=True,
        discovery_client=discovery_client,
        github_client=github_client,
        content_cache=object(),
    )

    assert result is expected
    assert call["record"] is record
    assert call["policy"] == paper_export_sync.RecordSyncPolicy(
        allow_title_search=False,
        allow_github_discovery=True,
        trust_existing_github=True,
        apply_normalized_url=True,
    )
    assert call["kwargs"]["discovery_client"] is discovery_client
    assert call["kwargs"]["github_client"] is github_client


@pytest.mark.anyio
async def test_sync_paper_seed_converts_seed_and_delegates_to_shared_workflow(monkeypatch):
    import src.core.paper_export_sync as paper_export_sync

    seed = PaperSeed(
        name="Paper A",
        url="https://arxiv.org/pdf/2603.05078v2.pdf",
        canonical_arxiv_url="https://arxiv.org/abs/2603.05078",
        url_resolution_authoritative=True,
    )
    call = {}

    async def fake_sync_record_with_policy(input_record, *, policy, **kwargs):
        call["record"] = input_record
        call["policy"] = policy
        call["kwargs"] = kwargs
        return paper_export_sync.PaperSyncResult(record=input_record, reason=None)

    monkeypatch.setattr(
        paper_export_sync,
        "sync_record_with_policy",
        fake_sync_record_with_policy,
    )

    result = await paper_export_sync.sync_paper_seed(
        seed,
        discovery_client=object(),
        github_client=object(),
    )

    assert call["record"].name.value == "Paper A"
    assert call["record"].url.value == "https://arxiv.org/pdf/2603.05078v2.pdf"
    assert call["record"].facts.normalized_url == "https://arxiv.org/pdf/2603.05078v2.pdf"
    assert call["record"].facts.canonical_arxiv_url == "https://arxiv.org/abs/2603.05078"
    assert call["record"].facts.url_resolution_authoritative is True
    assert call["policy"] == paper_export_sync.RecordSyncPolicy(
        allow_title_search=True,
        allow_github_discovery=True,
        apply_normalized_url=True,
    )
    assert result.record == call["record"]


@pytest.mark.anyio
async def test_sync_paper_seed_uses_shared_workflow_with_record_sync_service_stub(monkeypatch):
    import src.core.paper_export_sync as paper_export_sync
    import src.core.record_sync_workflow as record_sync_workflow

    seed = PaperSeed(
        name="Paper A",
        url="https://arxiv.org/pdf/2603.05078v2.pdf",
        canonical_arxiv_url="https://arxiv.org/abs/2603.05078",
        url_resolution_authoritative=True,
    )
    sync_call = {}

    class FakeRecordSyncService:
        def __init__(self, **_kwargs):
            pass

        async def sync(self, input_record, **kwargs):
            sync_call["record"] = input_record
            sync_call.update(kwargs)
            return input_record.with_supporting_state(
                facts=RecordFacts(
                    normalized_url="https://arxiv.org/abs/2603.05078",
                    canonical_arxiv_url="https://arxiv.org/abs/2603.05078",
                    url_resolution_authoritative=True,
                )
            )

    monkeypatch.setattr(
        record_sync_workflow,
        "RecordSyncService",
        FakeRecordSyncService,
    )

    result = await paper_export_sync.sync_paper_seed(
        seed,
        discovery_client=object(),
        github_client=object(),
    )

    assert sync_call["record"].name.value == "Paper A"
    assert sync_call["allow_title_search"] is True
    assert sync_call["allow_github_discovery"] is True
    assert sync_call["precomputed_normalized_url"] == "https://arxiv.org/pdf/2603.05078v2.pdf"
    assert sync_call["precomputed_canonical_arxiv_url"] == "https://arxiv.org/abs/2603.05078"
    assert sync_call["url_resolution_authoritative"] is True
    assert result.record.url.value == "https://arxiv.org/abs/2603.05078"
    assert result.record.url.source == "url_resolution"
