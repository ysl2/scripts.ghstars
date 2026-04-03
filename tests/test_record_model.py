import pytest

from src.core.record_model import (
    PropertyState,
    PropertyStatus,
    Record,
    RecordArtifacts,
    RecordContext,
    RecordFacts,
)


def test_record_with_property_returns_new_record_without_mutating_original():
    record = Record.from_source(
        name="Paper A",
        url="https://arxiv.org/abs/2501.12345",
        github="https://github.com/foo/bar",
        source="csv",
    )

    updated = record.with_property(
        "stars",
        PropertyState.resolved(42, source="github_api", trusted=True),
    )

    assert record.stars.value is None
    assert updated.stars.value == 42
    assert updated.github.value == "https://github.com/foo/bar"


def test_record_with_property_rejects_non_core_field_names():
    record = Record.from_source(name="Paper A", source="csv")

    with pytest.raises(ValueError, match="core property"):
        record.with_property("facts", PropertyState.present("oops", source="csv"))


def test_record_can_attach_facts_artifacts_and_context_without_promoting_them_to_core_properties():
    record = Record.from_source(name="Paper A", source="url_to_csv").with_supporting_state(
        facts=RecordFacts(canonical_arxiv_url="https://arxiv.org/abs/2501.12345"),
        artifacts=RecordArtifacts(overview_path="cache/overview/2501.12345.md"),
        context=RecordContext(csv_row_index=7),
    )

    assert record.facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert record.artifacts.overview_path.endswith("2501.12345.md")
    assert record.context.csv_row_index == 7


def test_record_facts_can_store_url_resolution_authoritativeness():
    facts = RecordFacts(
        canonical_arxiv_url="https://arxiv.org/abs/2501.12345",
        normalized_url="https://arxiv.org/pdf/2501.12345v2.pdf",
        url_resolution_authoritative=True,
    )

    assert facts.canonical_arxiv_url == "https://arxiv.org/abs/2501.12345"
    assert facts.normalized_url == "https://arxiv.org/pdf/2501.12345v2.pdf"
    assert facts.url_resolution_authoritative is True


def test_property_state_supports_explicit_empty_string_values_for_about_sync():
    state = PropertyState.resolved("", source="github_api", trusted=True)

    assert state.status is PropertyStatus.RESOLVED
    assert state.value == ""
    assert state.trusted is True
