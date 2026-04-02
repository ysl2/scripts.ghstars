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
