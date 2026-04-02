import pytest

from src.core.record_model import PropertyState as CorePropertyState
from src.shared.property_model import PropertyState, PropertyStatus, RecordState


def test_record_state_seeds_peer_level_properties_from_source_values():
    state = RecordState.from_source(
        name="Paper A",
        url="https://doi.org/10.1145/example",
        github="https://github.com/foo/bar",
        stars="7",
    )

    assert state.name.value == "Paper A"
    assert state.name.status is PropertyStatus.PRESENT
    assert state.url.status is PropertyStatus.PRESENT
    assert state.github.status is PropertyStatus.PRESENT
    assert state.stars.value == "7"
    assert state.stars.status is PropertyStatus.PRESENT
    assert state.created.status is PropertyStatus.BLOCKED
    assert state.about.status is PropertyStatus.BLOCKED


def test_property_state_helpers_support_resolved_failed_and_skipped_states():
    assert (
        PropertyState.resolved("https://github.com/foo/bar", source="url").status
        is PropertyStatus.RESOLVED
    )
    assert PropertyState.failed("metadata failed").reason == "metadata failed"
    assert PropertyState.skipped("preserve existing value").status is PropertyStatus.SKIPPED


def test_shared_property_state_supports_legacy_positional_source_argument():
    state = PropertyState.resolved("https://github.com/foo/bar", "url")

    assert state.status is PropertyStatus.RESOLVED
    assert state.source == "url"


def test_shared_property_state_supports_legacy_positional_reason_argument():
    state = PropertyState(
        None,
        PropertyStatus.SKIPPED,
        "csv",
        "preserve existing value",
    )

    assert state.status is PropertyStatus.SKIPPED
    assert state.source == "csv"
    assert state.reason == "preserve existing value"
    assert state.trusted is False


def test_shared_and_core_property_state_values_compare_equal():
    shared_state = PropertyState.resolved("x", "url")
    core_state = CorePropertyState.resolved("x", source="url")

    assert shared_state == core_state
    assert core_state == shared_state


def test_property_state_validation_enforces_consistent_states():
    with pytest.raises(ValueError):
        PropertyState.present(None, source="url")

    with pytest.raises(ValueError):
        PropertyState("value", PropertyStatus.RESOLVED, reason="should not have reason")

    with pytest.raises(ValueError):
        PropertyState.skipped("", source="url")


def test_record_state_blocks_missing_fields_with_reason():
    state = RecordState.from_source(
        name="Paper A",
        url="",
        github=None,
        stars="7",
        created="",
        about=None,
    )

    assert state.url.status is PropertyStatus.BLOCKED
    assert state.url.reason is not None
    assert "url" in state.url.reason
    assert state.created.reason is not None
    assert state.about.reason is not None


def test_record_state_rejects_unknown_fields():
    with pytest.raises(TypeError):
        RecordState.from_source(name="Paper A", invalid="value")


def test_record_state_tracks_provenance_via_source_field():
    state = RecordState.from_source(
        name="Paper A",
        url=None,
        github="https://github.com/foo/bar",
        stars="7",
        created="2020-01-01",
        about="desc",
    )

    assert state.name.source == "source"
    assert state.url.source == "source"
    assert state.github.source == "source"


def test_record_state_whitespace_only_strings_are_blocked():
    state = RecordState.from_source(
        name="Paper A",
        url="   ",
        github="https://github.com/foo/bar",
        stars="7",
        created="2020-01-01",
        about="desc",
    )

    assert state.url.status is PropertyStatus.BLOCKED
    assert state.url.reason == "url missing from source"
    assert state.url.value is None
