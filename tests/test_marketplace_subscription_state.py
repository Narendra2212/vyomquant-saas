"""Unit tests for ``marketplace/subscription_state.py`` (task 5.2, Requirements 11.1, 11.2, 11.6).

These pin the vocabulary size, the exact twelve pairs, the payment-gated target set and the
enum <-> ``library_subscriptions.status`` mapping - in particular that the four spellings
already stored today are reproduced character for character, so ``billing.py``'s
``.eq("status", "pending")`` and ``library.py``'s ``.eq("status", "active")`` keep matching.
The whole input space is covered separately by properties P-8 … P-10 and P-15 in
``tests/property/test_subscription_state_machine.py``.
"""

import pytest

from backend_app.backend.marketplace.subscription_state import (
    PAYMENT_REQUIRED_TARGETS,
    STATE_FOR_STATUS_TEXT,
    STATUS_TEXT_FOR_STATE,
    SUBSCRIPTION_STATUS_VALUES,
    SUBSCRIPTION_TRANSITIONS,
    SUBSCRIPTION_TRANSITION_PAIRS,
    SUBSCRIPTION_TRANSITION_TEXT_PAIRS,
    SubscriptionState,
    can_transition,
    is_terminal,
    legal_transitions,
    normalise_subscription_state,
    requires_confirmed_payment,
    status_text,
)

S = SubscriptionState

#: Requirement 11.2, transcribed independently of the module under test.
REQUIREMENT_11_2_PAIRS = {
    (S.PENDING, S.ACTIVE),
    (S.PENDING, S.PAYMENT_FAILED),
    (S.PENDING, S.CANCELLED),
    (S.ACTIVE, S.EXPIRED),
    (S.ACTIVE, S.CANCELLED),
    (S.ACTIVE, S.REFUNDED),
    (S.ACTIVE, S.SUSPENDED),
    (S.EXPIRED, S.ACTIVE),
    (S.CANCELLED, S.ACTIVE),
    (S.SUSPENDED, S.ACTIVE),
    (S.SUSPENDED, S.EXPIRED),
    (S.PAYMENT_FAILED, S.PENDING),
}


class TestVocabulary:
    """Requirement 11.1: exactly 7 values."""

    def test_exactly_seven_values(self):
        assert len(list(SubscriptionState)) == 7

    def test_the_seven_names(self):
        assert {state.name for state in SubscriptionState} == {
            "PENDING",
            "ACTIVE",
            "EXPIRED",
            "CANCELLED",
            "REFUNDED",
            "PAYMENT_FAILED",
            "SUSPENDED",
        }

    def test_each_member_is_its_own_wire_spelling(self):
        for state in SubscriptionState:
            assert state == state.value == state.name


class TestTransitionTable:
    """Requirement 11.2: the twelve pairs and nothing else."""

    def test_every_state_is_a_key(self):
        assert set(SUBSCRIPTION_TRANSITIONS) == set(SubscriptionState)

    def test_the_edge_set_is_exactly_the_twelve_pairs(self):
        assert set(SUBSCRIPTION_TRANSITION_PAIRS) == REQUIREMENT_11_2_PAIRS
        assert len(SUBSCRIPTION_TRANSITION_PAIRS) == 12

    def test_no_state_lists_itself(self):
        for state, targets in SUBSCRIPTION_TRANSITIONS.items():
            assert state not in targets

    def test_refunded_is_the_only_terminal_state(self):
        assert legal_transitions(S.REFUNDED) == ()
        assert is_terminal(S.REFUNDED)
        assert [s for s in SubscriptionState if is_terminal(s)] == [S.REFUNDED]

    @pytest.mark.parametrize("current,target", sorted(REQUIREMENT_11_2_PAIRS, key=str))
    def test_each_permitted_pair_is_allowed(self, current, target):
        assert can_transition(current, target) is True

    def test_every_pair_outside_the_twelve_is_refused(self):
        for current in SubscriptionState:
            for target in SubscriptionState:
                if (current, target) not in REQUIREMENT_11_2_PAIRS:
                    assert can_transition(current, target) is False

    def test_a_same_value_write_is_refused(self):
        for state in SubscriptionState:
            assert can_transition(state, state) is False

    def test_an_unrecognised_label_is_refused_rather_than_raising(self):
        assert can_transition("ACTIVE", "GHOST") is False
        assert can_transition(None, S.ACTIVE) is False
        assert can_transition("active ", "expired") is True  # stored text, whitespace


class TestPaymentGate:
    """Requirement 11.6: a Settlement_Record gates every transition into ACTIVE."""

    def test_active_is_the_only_payment_gated_target(self):
        assert PAYMENT_REQUIRED_TARGETS == {S.ACTIVE}

    @pytest.mark.parametrize("source", [S.PENDING, S.EXPIRED, S.CANCELLED, S.SUSPENDED])
    def test_every_source_reaching_active_is_gated(self, source):
        assert can_transition(source, S.ACTIVE) is True
        assert requires_confirmed_payment(S.ACTIVE) is True

    def test_payment_failed_reaches_active_only_by_way_of_pending(self):
        """Requirement 11.6 names ``PAYMENT_FAILED`` among the sources that reach ``ACTIVE``,
        but Requirement 11.2's twelve pairs carry no ``PAYMENT_FAILED -> ACTIVE`` edge: a
        failed payment goes back to ``PENDING`` and is paid from there. The gate is on the
        target, so that route is covered by ``PENDING -> ACTIVE`` above."""
        assert can_transition(S.PAYMENT_FAILED, S.ACTIVE) is False
        assert can_transition(S.PAYMENT_FAILED, S.PENDING) is True
        assert can_transition(S.PENDING, S.ACTIVE) is True

    def test_no_other_target_is_gated(self):
        for state in SubscriptionState:
            if state is not S.ACTIVE:
                assert requires_confirmed_payment(state) is False


class TestPersistedStatusMapping:
    """The one enum <-> ``library_subscriptions.status`` mapping."""

    def test_the_four_existing_spellings_are_unchanged(self):
        assert STATUS_TEXT_FOR_STATE[S.PENDING] == "pending"
        assert STATUS_TEXT_FOR_STATE[S.ACTIVE] == "active"
        assert STATUS_TEXT_FOR_STATE[S.EXPIRED] == "expired"
        assert STATUS_TEXT_FOR_STATE[S.CANCELLED] == "cancelled"

    def test_the_three_added_spellings(self):
        assert STATUS_TEXT_FOR_STATE[S.REFUNDED] == "refunded"
        assert STATUS_TEXT_FOR_STATE[S.PAYMENT_FAILED] == "payment_failed"
        assert STATUS_TEXT_FOR_STATE[S.SUSPENDED] == "suspended"

    def test_the_mapping_is_total_and_injective(self):
        assert set(STATUS_TEXT_FOR_STATE) == set(SubscriptionState)
        assert len(set(STATUS_TEXT_FOR_STATE.values())) == 7

    def test_every_value_is_lowercase(self):
        for text in SUBSCRIPTION_STATUS_VALUES:
            assert text == text.lower()

    def test_status_values_are_the_seven_spellings_in_requirement_order(self):
        assert SUBSCRIPTION_STATUS_VALUES == (
            "pending",
            "active",
            "expired",
            "cancelled",
            "refunded",
            "payment_failed",
            "suspended",
        )

    def test_round_trip_through_the_column_text(self):
        for state in SubscriptionState:
            assert STATE_FOR_STATUS_TEXT[status_text(state)] is state

    def test_a_stored_row_value_normalises_back_to_its_state(self):
        assert normalise_subscription_state("payment_failed") is S.PAYMENT_FAILED
        assert normalise_subscription_state("payment-failed") is S.PAYMENT_FAILED
        assert normalise_subscription_state(S.ACTIVE) is S.ACTIVE

    def test_an_unknown_label_normalises_to_none(self):
        assert normalise_subscription_state("ghost") is None
        assert normalise_subscription_state("") is None
        assert normalise_subscription_state(None) is None

    def test_status_text_refuses_an_unknown_label(self):
        with pytest.raises(ValueError):
            status_text("ghost")

    def test_text_pairs_mirror_the_enum_pairs(self):
        assert len(SUBSCRIPTION_TRANSITION_TEXT_PAIRS) == 12
        assert set(SUBSCRIPTION_TRANSITION_TEXT_PAIRS) == {
            (STATUS_TEXT_FOR_STATE[a], STATUS_TEXT_FOR_STATE[b])
            for a, b in REQUIREMENT_11_2_PAIRS
        }
