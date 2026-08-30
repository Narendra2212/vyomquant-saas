"""Unit tests for ``marketplace/submission_state.py`` (task 5.1).

Requirements 4.1 (the 8 values), 4.2 (the eleven edges, no self-edge, nothing out of
``UNPUBLISHED``), 4.6/4.7 (``PUBLIC_STATES``), 2.7/4.5 (``OPEN_STATES``) and 4.12 (the one
shared ``moderation_status``/``is_active`` mapping, which never produces ``'featured'``).

The database side of Requirement 4.4 - a direct ``UPDATE`` refused by the trigger - and the
agreement between this table and the ``marketplace_submission_allowed_transitions`` seed are
covered by tasks 14.6 and 14.8, not here: this module is pure, so these tests stay pure too.
"""

import itertools

from backend_app.backend.marketplace.submission_state import (
    IS_ACTIVE_FOR_STATE,
    MODERATION_STATUS_FOR_STATE,
    OPEN_STATES,
    PUBLIC_STATES,
    SUBMISSION_STATE_VALUES,
    SUBMISSION_TRANSITIONS,
    SubmissionState,
    can_transition,
    is_active_for,
    is_open,
    is_public,
    legal_transitions,
    moderation_status_for,
    normalise_submission_state,
)

#: Requirement 4.2, transcribed independently of the module so the test is an oracle rather
#: than a restatement of the implementation.
PERMITTED_PAIRS = {
    ("DRAFT", "SUBMITTED"),
    ("SUBMITTED", "UNDER_REVIEW"),
    ("SUBMITTED", "REJECTED"),
    ("UNDER_REVIEW", "APPROVED"),
    ("UNDER_REVIEW", "REJECTED"),
    ("APPROVED", "PUBLISHED"),
    ("PUBLISHED", "SUSPENDED"),
    ("PUBLISHED", "UNPUBLISHED"),
    ("SUSPENDED", "PUBLISHED"),
    ("SUSPENDED", "UNPUBLISHED"),
    ("REJECTED", "DRAFT"),
}


class TestVocabulary:
    """Requirement 4.1: exactly 8 values, spelled as the column stores them."""

    def test_there_are_exactly_eight_states(self):
        assert len(SubmissionState) == 8

    def test_the_values_are_the_requirement_s_own_spellings(self):
        assert set(SUBMISSION_STATE_VALUES) == {
            "DRAFT",
            "SUBMITTED",
            "UNDER_REVIEW",
            "APPROVED",
            "PUBLISHED",
            "REJECTED",
            "SUSPENDED",
            "UNPUBLISHED",
        }

    def test_each_member_is_its_own_string_value(self):
        for state in SubmissionState:
            assert state == state.value
            assert str(state) == state.value


class TestTransitionTable:
    """Requirement 4.2: eleven edges, every state a key, no state listing itself."""

    def test_every_state_is_a_key_so_a_missing_key_cannot_read_as_terminal(self):
        assert set(SUBMISSION_TRANSITIONS) == set(SubmissionState)

    def test_the_table_holds_exactly_the_eleven_permitted_pairs(self):
        actual = {
            (current.value, target.value)
            for current, targets in SUBMISSION_TRANSITIONS.items()
            for target in targets
        }
        assert actual == PERMITTED_PAIRS
        assert len(actual) == 11

    def test_no_state_lists_itself_as_a_target(self):
        for current, targets in SUBMISSION_TRANSITIONS.items():
            assert current not in targets

    def test_unpublished_is_terminal(self):
        assert SUBMISSION_TRANSITIONS[SubmissionState.UNPUBLISHED] == ()
        assert legal_transitions(SubmissionState.UNPUBLISHED) == ()


class TestCanTransition:
    """The predicate every call site consults before a write."""

    def test_it_agrees_with_the_permitted_set_over_every_ordered_pair(self):
        for current, target in itertools.product(SubmissionState, repeat=2):
            expected = (current.value, target.value) in PERMITTED_PAIRS
            assert can_transition(current, target) is expected

    def test_a_same_value_transition_is_refused_for_every_state(self):
        for state in SubmissionState:
            assert can_transition(state, state) is False

    def test_nothing_leaves_unpublished(self):
        for target in SubmissionState:
            assert can_transition(SubmissionState.UNPUBLISHED, target) is False

    def test_column_strings_resolve_exactly_as_members_do(self):
        assert can_transition("DRAFT", "SUBMITTED") is True
        assert can_transition("under_review", "approved") is True
        assert can_transition("  PUBLISHED  ", "UNPUBLISHED") is True
        assert can_transition("DRAFT", "PUBLISHED") is False

    def test_an_unreadable_state_never_authorises_a_write(self):
        assert can_transition(None, SubmissionState.SUBMITTED) is False
        assert can_transition("ARCHIVED", SubmissionState.SUBMITTED) is False
        assert can_transition(SubmissionState.DRAFT, None) is False
        assert can_transition(SubmissionState.DRAFT, "featured") is False


class TestStateSets:
    """Requirements 4.6, 4.7 (public) and 2.7, 4.5 (open)."""

    def test_published_is_the_only_public_state(self):
        assert PUBLIC_STATES == {SubmissionState.PUBLISHED}

    def test_no_other_state_is_public(self):
        for state in SubmissionState:
            assert is_public(state) is (state is SubmissionState.PUBLISHED)

    def test_open_states_are_the_four_the_partial_unique_index_names(self):
        assert OPEN_STATES == {
            SubmissionState.SUBMITTED,
            SubmissionState.UNDER_REVIEW,
            SubmissionState.APPROVED,
            SubmissionState.PUBLISHED,
        }

    def test_draft_rejected_suspended_and_unpublished_do_not_occupy_the_slot(self):
        for state in (
            SubmissionState.DRAFT,
            SubmissionState.REJECTED,
            SubmissionState.SUSPENDED,
            SubmissionState.UNPUBLISHED,
        ):
            assert is_open(state) is False

    def test_an_unrecognised_state_is_neither_public_nor_open(self):
        assert is_public("ARCHIVED") is False
        assert is_open(None) is False


class TestModerationStatusMapping:
    """Requirement 4.12: one definition, total, and never ``'featured'``."""

    def test_the_mapping_is_total_over_the_eight_states(self):
        assert set(MODERATION_STATUS_FOR_STATE) == set(SubmissionState)
        assert set(IS_ACTIVE_FOR_STATE) == set(SubmissionState)

    def test_featured_is_never_produced(self):
        assert "featured" not in set(MODERATION_STATUS_FOR_STATE.values())

    def test_every_value_is_an_existing_moderation_status_value(self):
        assert set(MODERATION_STATUS_FOR_STATE.values()) <= {
            "pending",
            "approved",
            "rejected",
        }

    def test_only_published_maps_to_the_catalogue_visible_value(self):
        for state in SubmissionState:
            is_approved = MODERATION_STATUS_FOR_STATE[state] == "approved"
            assert is_approved is (state is SubmissionState.PUBLISHED)

    def test_approved_stays_pending_so_it_is_invisible_to_the_catalogue(self):
        assert MODERATION_STATUS_FOR_STATE[SubmissionState.APPROVED] == "pending"

    def test_suspended_and_unpublished_leave_the_catalogue(self):
        assert MODERATION_STATUS_FOR_STATE[SubmissionState.SUSPENDED] == "rejected"
        assert MODERATION_STATUS_FOR_STATE[SubmissionState.UNPUBLISHED] == "rejected"

    def test_is_active_is_true_for_published_alone(self):
        for state in SubmissionState:
            assert IS_ACTIVE_FOR_STATE[state] is (state is SubmissionState.PUBLISHED)
            assert is_active_for(state) is (state is SubmissionState.PUBLISHED)

    def test_an_unknown_state_is_not_projected_onto_a_visible_value(self):
        assert moderation_status_for("ARCHIVED") is None
        assert moderation_status_for(None) is None
        assert is_active_for("ARCHIVED") is False

    def test_lookup_helpers_accept_the_column_string(self):
        assert moderation_status_for("PUBLISHED") == "approved"
        assert moderation_status_for("under_review") == "pending"


class TestNormalisation:
    def test_a_member_passes_through(self):
        assert (
            normalise_submission_state(SubmissionState.DRAFT) is SubmissionState.DRAFT
        )

    def test_padded_hyphenated_and_lowercase_spellings_resolve(self):
        assert (
            normalise_submission_state(" under-review ") is SubmissionState.UNDER_REVIEW
        )
        assert (
            normalise_submission_state("under review") is SubmissionState.UNDER_REVIEW
        )

    def test_absent_and_foreign_values_return_none(self):
        assert normalise_submission_state(None) is None
        assert normalise_submission_state("") is None
        assert normalise_submission_state("   ") is None
        assert normalise_submission_state("PENDING") is None
        assert normalise_submission_state(7) is None
