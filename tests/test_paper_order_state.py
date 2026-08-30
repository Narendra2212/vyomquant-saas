"""
tests/test_paper_order_state.py - The Paper_Order_State machine.

Covers Requirements 16.1 (the six values), 16.2 (the nine permitted transitions,
including the single self-transition), 16.3 (terminality) and 17.12 (the retained
``PaperOrderStatus`` spellings stay reachable through ``LEGACY_STATUS_FOR_STATE``).
"""

from backend_app.backend.paper.paper_order_state import (
    LEGACY_STATUS_FOR_STATE,
    PAPER_ORDER_TRANSITIONS,
    REACHABLE_FROM_CREATED,
    TERMINAL,
    PaperOrderState,
    can_transition,
    is_terminal,
    legacy_status_for,
)
from backend_app.backend.paper_trading_service import PaperOrderStatus

S = PaperOrderState

#: The nine ordered pairs Requirement 16.2 permits, written out independently of the
#: module under test so a change to the mapping has to be a deliberate change here too.
PERMITTED_PAIRS = {
    (S.CREATED, S.ACCEPTED),
    (S.CREATED, S.REJECTED),
    (S.ACCEPTED, S.PARTIALLY_FILLED),
    (S.ACCEPTED, S.FILLED),
    (S.ACCEPTED, S.CANCELLED),
    (S.ACCEPTED, S.REJECTED),
    (S.PARTIALLY_FILLED, S.PARTIALLY_FILLED),
    (S.PARTIALLY_FILLED, S.FILLED),
    (S.PARTIALLY_FILLED, S.CANCELLED),
}


def test_state_value_set_is_exactly_the_six_values():
    assert {s.value for s in PaperOrderState} == {
        "CREATED",
        "ACCEPTED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELLED",
        "REJECTED",
    }


def test_every_state_is_a_key_of_the_transition_map():
    assert set(PAPER_ORDER_TRANSITIONS) == set(PaperOrderState)


def test_transition_map_is_exactly_the_permitted_pairs():
    actual = {
        (state, target)
        for state, targets in PAPER_ORDER_TRANSITIONS.items()
        for target in targets
    }
    assert actual == PERMITTED_PAIRS


def test_partially_filled_is_the_only_self_transition():
    self_loops = {
        state for state, targets in PAPER_ORDER_TRANSITIONS.items() if state in targets
    }
    assert self_loops == {S.PARTIALLY_FILLED}


def test_terminal_states_have_no_outgoing_transition():
    assert TERMINAL == frozenset({S.FILLED, S.CANCELLED, S.REJECTED})
    for state in TERMINAL:
        assert PAPER_ORDER_TRANSITIONS[state] == ()
        assert is_terminal(state)
        for target in PaperOrderState:
            assert can_transition(state, target) is False


def test_can_transition_agrees_with_the_permitted_pairs_over_the_whole_grid():
    for current in PaperOrderState:
        for target in PaperOrderState:
            assert can_transition(current, target) is ((current, target) in PERMITTED_PAIRS)


def test_every_state_is_reachable_from_created():
    assert REACHABLE_FROM_CREATED == frozenset(PaperOrderState)


def test_legacy_mapping_covers_every_state_with_a_retained_spelling():
    assert set(LEGACY_STATUS_FOR_STATE) == set(PaperOrderState)
    retained = {s.value for s in PaperOrderStatus}
    assert set(LEGACY_STATUS_FOR_STATE.values()) <= retained


def test_legacy_mapping_keeps_the_open_filter_meaning():
    assert legacy_status_for(S.CREATED) == PaperOrderStatus.NEW.value
    assert legacy_status_for(S.ACCEPTED) == PaperOrderStatus.OPEN.value
    assert legacy_status_for(S.PARTIALLY_FILLED) == PaperOrderStatus.OPEN.value
    assert legacy_status_for(S.FILLED) == PaperOrderStatus.FILLED.value
    assert legacy_status_for(S.CANCELLED) == PaperOrderStatus.CANCELLED.value
    assert legacy_status_for(S.REJECTED) == PaperOrderStatus.REJECTED.value
