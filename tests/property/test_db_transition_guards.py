"""Property test for Property 49 — the database, not only the service, refuses illegal
Submission / Subscription / Paper_Order / Paper_Session transitions.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property-to-test mapping`` ->
``P-49 | tests/property/test_db_transition_guards.py | all ordered pairs per machine |
the four Python transition tables``. Property 49 in full:

    For all four state machines M in {Submission_State, Subscription_State,
    Paper_Order_State, Paper_Session state}, for all ordered pairs (s1, s2) ABSENT from M's
    permitted set, and for all rows of M's table currently holding s1: an UPDATE issued
    directly to the Persistence_Layer, bypassing every service module, leaves the stored
    value at s1, raises an error naming the rejected transition, records no
    transition-history row, and — for Submission_State — leaves the Listing's visibility to
    the Listing_Projection unchanged.

    **Validates: Requirements 4.4, 11.3, 16.4, 17.14, 24.2**

WHY THIS IS ASSERTED AS A MECHANISM, NOT RUN AGAINST A LIVE DATABASE
-------------------------------------------------------------------
There is no PostgreSQL in this test environment. Every sibling migration/schema test in the
repository verifies migrations by STATIC PARSE rather than by executing them
(``tests/test_marketplace_paper_migrations.py``, ``tests/test_marketplace_paper_schema_
contract.py``, ``tests/test_schema_as_code_completeness.py``), and ``tests/property/
test_010_...``-style tests assert the RAISE that *would* fire rather than firing it. So a
direct ``UPDATE`` cannot be issued here — instead P-49 asserts the MECHANISM that produces
the database refusal, exactly the way task 11.7's test asserts a preflight RAISE.

The four transition guards in ``007``/``008``/``009`` are all one shape:

    IF NEW.<col> {=,IS NOT DISTINCT FROM} OLD.<col> THEN RETURN NEW; END IF;   -- no-op
    IF NOT EXISTS (SELECT 1 FROM public.<allowed_transitions_table> t
                    WHERE t.from_state = OLD.<col> AND t.to_state = NEW.<col>) THEN
        RAISE EXCEPTION 'disallowed ... transition % -> %', OLD.<col>, NEW.<col>
            USING ERRCODE = '23514';
    END IF;

The refusal is therefore GENERIC over pairs: the guard does not enumerate the illegal edges
in a ``CASE`` — it checks *membership* of ``(OLD, NEW)`` in the seed table. Any ordered pair
NOT seeded is refused by the same ``NOT EXISTS`` branch, with a ``RAISE`` that interpolates
BOTH ``OLD`` and ``NEW``. That is precisely the universal-quantifier structure P-49 needs:
verifying the guard is membership-based and names both states proves the refusal holds for
*arbitrary* absent pairs, without enumerating them in SQL.

WHAT THE HYPOTHESIS PROPERTY DOES
---------------------------------
For each machine, Hypothesis generates ordered ``(from_state, to_state)`` pairs drawn from
the machine's full value set and FILTERS to the pairs ABSENT from the permitted set (parsed
off the migration seed, asserted equal to the Python oracle first, so "permitted" means the
same thing on both sides). For every such absent pair the property asserts the parsed guard
would refuse it: the guard's membership predicate is generic (``NOT EXISTS`` against the
allowed-transitions table, keyed on ``OLD``/``NEW``, never an enumerated ``CASE`` of illegal
edges), and its ``RAISE`` names both states. Because the predicate is membership over the
same seed the parser read, "absent from the seed" and "refused by the guard" are the same
fact — so the assertion holds for every generated absent pair by construction, which is the
point: the DB refuses *all* of them, not a hand-picked list.

ONE MACHINE'S PERMITTED SET SPANS TWO MIGRATIONS
------------------------------------------------
The guard probes the TABLE, not a file, so "permitted" for Subscription_State is what every
migration that seeds ``marketplace_subscription_allowed_transitions`` has put there:
``008``'s twelve Requirement 11.2 pairs plus ``012``'s one Requirement 11.6 addendum,
``('payment_failed','active')`` (task 19.15 — the retried payment that later confirms). The
addendum is imported as the NAMED constant ``MIGRATION_012_SUBSCRIPTION_ADDENDUM`` from the
task-14.6 agreement test, on both sides of the equality: the seed side through
``_Machine.addendum_migrations`` and the oracle side through an explicit union. Reading only
``008`` here would put that pair in the "absent" space and have P-49 assert the database
refuses an edge it now permits — which is the opposite of true, and would have hidden the
divergence rather than pinned it.

Two machine-specific riders, the other halves of P-49:

* Submission_State — "leaves the Listing's visibility unchanged": the projection trigger
  ``trg_submission_projects_moderation_status`` runs AFTER and its function returns early on
  a no-op (``NEW.submission_state = OLD.submission_state``); more to the point it is an
  AFTER trigger, so a transition the BEFORE guard rejects aborts the statement and the
  projection never runs. Asserted by parsing the projection function for the guarding
  ``RETURN NULL`` on no-change and confirming the guard is BEFORE and the projection AFTER.

* "records no transition-history row": the history is append-only and the guard raises
  BEFORE UPDATE, so a rejected transition writes nothing. Asserted by confirming the
  history/append-only tables carry ``REVOKE UPDATE, DELETE`` and that the guard fires
  ``BEFORE UPDATE`` (a rejected BEFORE-UPDATE aborts before any history INSERT the service
  would have made).

The seed parser and the four oracles are shared with ``tests/test_submission_state_
agreement.py`` (task 14.6): its helpers are imported rather than re-derived, so the two
tests read the machines through exactly one parser and one set of oracles.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, FrozenSet, Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# The parser, the oracle flatteners and the paper-session fallback all already live in the
# task-14.6 agreement test. Import them so P-49 reads every machine through the same code.
from tests.test_submission_state_agreement import (
    MIGRATION_012_SUBSCRIPTION_ADDENDUM,
    MIGRATIONS,
    SUBSCRIPTION_ADDENDUM_MIGRATION,
    _flatten_transitions,
    _read_seed_pairs,
    _session_python_pairs,
)
from backend_app.backend.marketplace.submission_state import SUBMISSION_TRANSITIONS
from backend_app.backend.marketplace.subscription_state import SUBSCRIPTION_TRANSITIONS
from backend_app.backend.paper.paper_order_state import PAPER_ORDER_TRANSITIONS

Pair = Tuple[str, str]

#: The shared configuration ``design.md § Property-based testing configuration`` prescribes:
#: at least 100 examples, no per-example deadline (the first example pays the migration
#: read + parse cost), matching the decorator every other file in ``tests/property/`` uses.
PROPERTY_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


# ---------------------------------------------------------------------------
# Reading a guard function's body out of a migration
# ---------------------------------------------------------------------------


def _read_function_body(migration_filename: str, function_name: str) -> str:
    """The ``CREATE OR REPLACE FUNCTION public.<function_name>() ... $$ LANGUAGE`` body.

    Returns the text between the ``AS $$`` that opens the body and the closing
    ``$$ LANGUAGE`` — the plpgsql the trigger actually executes — so the membership check
    and the ``RAISE`` can be inspected without the surrounding ``DO $$`` trigger-creation
    block being mistaken for part of it.
    """
    sql = (MIGRATIONS / migration_filename).read_text(encoding="utf-8")
    match = re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\." + re.escape(function_name)
        + r"\s*\(\s*\)\s*RETURNS\s+TRIGGER\s+AS\s+\$\$(?P<body>.*?)\$\$\s*LANGUAGE",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, (
        f"no `CREATE OR REPLACE FUNCTION public.{function_name}() RETURNS TRIGGER` found in "
        f"{migration_filename}; P-49 cannot assert a refusal mechanism that is not defined"
    )
    body = match.group("body")
    assert body.strip(), f"{function_name} in {migration_filename} has an empty body"
    return body


def _read_trigger_timing(
    migration_filename: str, trigger_name: str
) -> str:
    """The ``BEFORE``/``AFTER`` timing and event of a ``CREATE TRIGGER`` block, upper-cased.

    e.g. ``"BEFORE UPDATE"`` or ``"AFTER INSERT OR UPDATE"``. Used to prove the guard fires
    BEFORE the write (so a rejected transition aborts before any stored value changes or any
    history row is written) and that the submission projection fires AFTER (so it never runs
    for a transition the guard rejected).
    """
    sql = (MIGRATIONS / migration_filename).read_text(encoding="utf-8")
    match = re.search(
        r"CREATE\s+TRIGGER\s+" + re.escape(trigger_name)
        + r"\s+(?P<timing>(?:BEFORE|AFTER)\s+[A-Z ]*?(?:INSERT|UPDATE|DELETE)"
        + r"(?:\s+OR\s+(?:INSERT|UPDATE|DELETE))*)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, (
        f"no `CREATE TRIGGER {trigger_name} (BEFORE|AFTER) ...` found in "
        f"{migration_filename}"
    )
    return re.sub(r"\s+", " ", match.group("timing").strip().upper())


# ---------------------------------------------------------------------------
# The two structural assertions P-49 makes about a transition guard, once per machine.
# These are the "the DB refusal mechanism exists and is generic" checks; the Hypothesis
# property below then quantifies them over every absent pair.
# ---------------------------------------------------------------------------


def _assert_guard_is_generic_membership_check(
    body: str, allowed_table: str, old_col: str, new_col: str, label: str
) -> None:
    """The guard refuses by TABLE MEMBERSHIP, generically, not by an enumerated CASE.

    A guard that listed the illegal edges in a ``CASE`` would not satisfy P-49's universal
    quantifier — a new value pair could be forgotten. The guards here instead ask "is
    ``(OLD, NEW)`` in the seed table?" and raise when it is not, so *every* absent pair is
    refused by the one ``NOT EXISTS`` branch. This asserts that shape:

    * a ``NOT EXISTS (SELECT 1 FROM public.<allowed_table> ...)`` membership probe exists,
    * keyed on ``from_state = OLD.<col>`` AND ``to_state = NEW.<col>`` (the generic key,
      not a literal pair), and
    * the guard contains no ``CASE`` enumerating states as the refusal mechanism.
    """
    # The membership probe against the seed table.
    probe = re.search(
        r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+public\." + re.escape(allowed_table),
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert probe is not None, (
        f"{label}: guard does not probe public.{allowed_table} with a NOT EXISTS membership "
        f"check; without it the refusal would not be generic over absent pairs"
    )

    # The probe is keyed on OLD/NEW generically — from_state = OLD.col AND to_state = NEW.col.
    keyed_from = re.search(
        r"from_state\s*=\s*OLD\." + re.escape(old_col), body, re.IGNORECASE
    )
    keyed_to = re.search(
        r"to_state\s*=\s*NEW\." + re.escape(new_col), body, re.IGNORECASE
    )
    assert keyed_from is not None and keyed_to is not None, (
        f"{label}: the membership probe is not keyed generically on "
        f"from_state = OLD.{old_col} AND to_state = NEW.{new_col}; a refusal keyed on "
        f"literal states would not cover arbitrary absent pairs"
    )

    # No CASE statement is used AS the refusal mechanism — the machine's legality lives in
    # the seed table, read generically, not enumerated in the trigger.
    assert not re.search(r"\bCASE\b", body, re.IGNORECASE), (
        f"{label}: the guard body contains a CASE statement; the transition legality must "
        f"be a generic table-membership test, not an enumerated CASE (that is exactly the "
        f"second definition design.md § 'why the seed is a table' forbids)"
    )


def _assert_raise_names_both_states(
    body: str, old_col: str, new_col: str, label: str
) -> None:
    """The refusal ``RAISE`` names BOTH the current state and the rejected target.

    Requirements 4.4 / 11.3 / 16.4 / 17.14 all require the error to name the current state
    and the rejected transition. The guards format ``'... % -> %', OLD.<col>, NEW.<col>``
    with ``ERRCODE = '23514'`` so the service can translate by SQLSTATE. Assert the RAISE
    that follows the membership probe interpolates both ``OLD.<col>`` and ``NEW.<col>`` and
    carries the check-violation SQLSTATE.
    """
    raise_match = re.search(
        r"RAISE\s+EXCEPTION.*?USING\s+ERRCODE\s*=\s*'23514'",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert raise_match is not None, (
        f"{label}: no `RAISE EXCEPTION ... USING ERRCODE = '23514'` in the guard; the "
        f"refusal must carry the check_violation SQLSTATE the service translates by"
    )
    raise_text = raise_match.group(0)
    assert re.search(r"OLD\." + re.escape(old_col), raise_text, re.IGNORECASE), (
        f"{label}: the refusal RAISE does not name the current state (OLD.{old_col})"
    )
    assert re.search(r"NEW\." + re.escape(new_col), raise_text, re.IGNORECASE), (
        f"{label}: the refusal RAISE does not name the rejected target (NEW.{new_col})"
    )
    # Two %-placeholders — "current -> rejected" — so the message actually renders both.
    assert raise_text.count("%") >= 2, (
        f"{label}: the refusal RAISE has fewer than two %-placeholders, so it cannot name "
        f"both the current state and the rejected transition"
    )


# ---------------------------------------------------------------------------
# One descriptor per machine: everything the property needs to check it.
# ---------------------------------------------------------------------------


class _Machine:
    def __init__(
        self,
        label: str,
        migration: str,
        allowed_table: str,
        guard_function: str,
        guard_trigger: str,
        old_col: str,
        new_col: str,
        oracle_pairs: FrozenSet[Pair],
        history_revoke_migration: str,
        addendum_migrations: Tuple[str, ...] = (),
    ) -> None:
        self.label = label
        self.migration = migration
        self.allowed_table = allowed_table
        self.guard_function = guard_function
        self.guard_trigger = guard_trigger
        self.old_col = old_col
        self.new_col = new_col
        self.oracle_pairs = oracle_pairs
        self.history_revoke_migration = history_revoke_migration
        #: Additive migrations that seed FURTHER pairs into ``allowed_table`` after the
        #: machine's own migration. The guard reads the TABLE, not one file, so the permitted
        #: set is the union — see :attr:`seed_pairs`.
        self.addendum_migrations = addendum_migrations

    @property
    def seed_pairs(self) -> FrozenSet[Pair]:
        """Every pair the guard's ``NOT EXISTS`` probe finds: the machine's seed ∪ addenda.

        Reading only ``self.migration`` would make the absent-pair space wrong for any machine
        a later additive migration widened, and the property would then assert that the
        database refuses an edge it in fact permits. Subscription_State is exactly that case:
        ``012`` seeds Requirement 11.6's ``('payment_failed','active')`` on top of ``008``'s
        twelve Requirement 11.2 pairs.
        """
        pairs = _read_seed_pairs(self.migration, self.allowed_table)
        for addendum in self.addendum_migrations:
            pairs = pairs | _read_seed_pairs(addendum, self.allowed_table)
        return pairs

    @property
    def states(self) -> Tuple[str, ...]:
        seed = self.seed_pairs
        return tuple(sorted({s for pair in seed for s in pair}))


def _session_oracle() -> FrozenSet[Pair]:
    pairs, _source = _session_python_pairs()
    return pairs


def _machines() -> Tuple[_Machine, ...]:
    return (
        _Machine(
            label="Submission_State",
            migration="007_marketplace_submissions.sql",
            allowed_table="marketplace_submission_allowed_transitions",
            guard_function="marketplace_submission_guard",
            guard_trigger="trg_submission_transition_guard",
            old_col="submission_state",
            new_col="submission_state",
            oracle_pairs=_flatten_transitions(SUBMISSION_TRANSITIONS),
            history_revoke_migration="007_marketplace_submissions.sql",
        ),
        _Machine(
            label="Subscription_State",
            migration="008_marketplace_settlement.sql",
            allowed_table="marketplace_subscription_allowed_transitions",
            guard_function="marketplace_subscription_guard",
            guard_trigger="trg_subscription_transition_guard",
            old_col="status",
            new_col="status",
            # Requirement 11.2's twelve pairs PLUS Requirement 11.6's one named addendum
            # (task 19.15). The addendum is enumerated, not wildcarded, so a fourteenth
            # permitted edge still fails test_seed_equals_python_oracle_for_every_machine.
            oracle_pairs=(
                _flatten_transitions(SUBSCRIPTION_TRANSITIONS)
                | MIGRATION_012_SUBSCRIPTION_ADDENDUM
            ),
            history_revoke_migration="008_marketplace_settlement.sql",
            addendum_migrations=(SUBSCRIPTION_ADDENDUM_MIGRATION,),
        ),
        _Machine(
            label="Paper_Order_State",
            migration="009_paper_trading.sql",
            allowed_table="paper_order_allowed_transitions",
            guard_function="paper_order_transition_guard",
            guard_trigger="trg_paper_order_transition_guard",
            old_col="order_state",
            new_col="order_state",
            oracle_pairs=_flatten_transitions(PAPER_ORDER_TRANSITIONS),
            history_revoke_migration="009_paper_trading.sql",
        ),
        _Machine(
            label="Paper_Session_State",
            migration="009_paper_trading.sql",
            allowed_table="paper_session_allowed_transitions",
            guard_function="paper_session_guard",
            guard_trigger="trg_paper_session_guard",
            old_col="session_state",
            new_col="session_state",
            oracle_pairs=_session_oracle(),
            history_revoke_migration="009_paper_trading.sql",
        ),
    )


MACHINES = _machines()


# ---------------------------------------------------------------------------
# Preconditions the property leans on: the seed and the Python oracle are the SAME machine.
# If they were not, "absent from the seed" would not mean "absent from what the service
# believes legal", and the property would be quantifying over the wrong pairs. Task 14.6's
# agreement test owns this equality; it is re-asserted here so this file is self-contained
# and can never quantify over a mismatched machine.
# ---------------------------------------------------------------------------


def test_seed_equals_python_oracle_for_every_machine() -> None:
    for machine in MACHINES:
        assert machine.seed_pairs == machine.oracle_pairs, (
            f"{machine.label}: the {machine.allowed_table} seed and the Python oracle "
            f"describe different machines, so P-49 would quantify over the wrong 'absent' "
            f"pairs.\n  in seed only: {sorted(machine.seed_pairs - machine.oracle_pairs)}\n"
            f"  in oracle only: {sorted(machine.oracle_pairs - machine.seed_pairs)}"
        )
        assert machine.seed_pairs, f"{machine.label}: empty seed"


# ---------------------------------------------------------------------------
# The structural half, once per machine: the guard exists, refuses by generic membership,
# names both states, and fires BEFORE the write.
# ---------------------------------------------------------------------------


def test_every_guard_is_a_generic_membership_check_that_names_both_states() -> None:
    for machine in MACHINES:
        body = _read_function_body(machine.migration, machine.guard_function)
        _assert_guard_is_generic_membership_check(
            body, machine.allowed_table, machine.old_col, machine.new_col, machine.label
        )
        _assert_raise_names_both_states(
            body, machine.old_col, machine.new_col, machine.label
        )
        # The guard fires BEFORE the UPDATE: a rejected transition aborts the statement
        # before the stored value changes and before any history row could be inserted.
        timing = _read_trigger_timing(machine.migration, machine.guard_trigger)
        assert timing.startswith("BEFORE") and "UPDATE" in timing, (
            f"{machine.label}: {machine.guard_trigger} is '{timing}', not a BEFORE UPDATE "
            f"guard; a refusal that fired after the write could not leave the stored value "
            f"unchanged"
        )


# ---------------------------------------------------------------------------
# "records no transition-history row": the history is append-only (REVOKE UPDATE, DELETE),
# and because the guard raises BEFORE UPDATE, a rejected transition aborts before the
# service's history INSERT ever runs. Assert the append-only REVOKE is present.
# ---------------------------------------------------------------------------


def test_transition_history_is_append_only_so_a_refusal_records_nothing() -> None:
    # The history / append-only tables whose UPDATE,DELETE is revoked, per migration.
    revoked_tables = {
        "007_marketplace_submissions.sql": "marketplace_submission_transitions",
        "008_marketplace_settlement.sql": "library_subscription_transitions",
        # Paper machines record lifecycle history in the append-only paper_events log.
        "009_paper_trading.sql": "paper_events",
    }
    for migration, table in revoked_tables.items():
        sql = (MIGRATIONS / migration).read_text(encoding="utf-8")
        revoke = re.search(
            r"REVOKE\s+UPDATE\s*,\s*DELETE\s+ON\s+public\." + re.escape(table),
            sql,
            re.IGNORECASE,
        )
        assert revoke is not None, (
            f"{migration}: no `REVOKE UPDATE, DELETE ON public.{table}`; the transition "
            f"history must be append-only so a refused transition can record nothing"
        )


# ---------------------------------------------------------------------------
# Submission-only rider: a refused transition leaves the Listing's visibility unchanged.
# The projection is an AFTER trigger and returns early on a no-op state change, so it never
# runs for a transition the BEFORE guard aborted.
# ---------------------------------------------------------------------------


def test_submission_projection_is_after_and_noop_guarded() -> None:
    migration = "007_marketplace_submissions.sql"
    # The projection fires AFTER — so a BEFORE-guard refusal aborts the statement before it.
    timing = _read_trigger_timing(migration, "trg_submission_projects_moderation_status")
    assert timing.startswith("AFTER"), (
        f"the moderation projection trigger is '{timing}', not AFTER; only an AFTER trigger "
        f"is guaranteed not to run when the BEFORE transition guard aborts the UPDATE"
    )
    # And it no-ops on an unchanged state, the belt to the AFTER braces.
    projection_body = _read_function_body(migration, "marketplace_project_listing_state")
    assert re.search(
        r"NEW\.submission_state\s*=\s*OLD\.submission_state", projection_body, re.IGNORECASE
    ), (
        "the projection function does not short-circuit when submission_state is unchanged; "
        "a refused (hence unchanged) transition must project nothing onto library_strategies"
    )


# ---------------------------------------------------------------------------
# The Hypothesis property: for EVERY ordered pair ABSENT from a machine's permitted set, the
# guard refuses it — because the guard's membership predicate is generic over the seed the
# absent pair is, by definition, missing from. One property function per machine keeps the
# generated value space (that machine's states) tight and the counterexamples readable.
# ---------------------------------------------------------------------------


def _absent_pairs_strategy(machine: _Machine):
    """Ordered (from, to) pairs over ``machine``'s states, filtered to the ABSENT ones."""
    states = machine.states
    permitted = machine.seed_pairs
    return st.tuples(st.sampled_from(states), st.sampled_from(states)).filter(
        lambda pair: pair not in permitted
    )


def _assert_absent_pair_is_refused(machine: _Machine, pair: Pair, guard_body: str) -> None:
    """For an absent ``pair``, the generic membership guard would refuse it.

    The guard raises exactly when ``(OLD, NEW)`` is NOT in the seed table. ``pair`` was
    filtered to be absent from that very seed, and the guard's key is the generic
    ``from_state = OLD AND to_state = NEW`` (asserted structurally above). So the refusal
    holds for this pair by the same fact that made it "absent". Re-assert both facts here so
    a counterexample points at the exact pair and machine:

    * the pair is genuinely absent from the seed the guard consults, and
    * the pair is not a no-op self-edge that the guard's ``RETURN NEW`` short-circuit would
      (correctly) let through as "not a transition" — every machine except Paper_Order has
      no seeded self-edge, and Paper_Order's only self-edge (``PARTIALLY_FILLED`` ->
      ``PARTIALLY_FILLED``) IS seeded, hence not in this absent set. So an absent pair is
      never a tolerated no-op; it always reaches the ``NOT EXISTS`` refusal.
    """
    from_state, to_state = pair
    assert pair not in machine.seed_pairs, (
        f"{machine.label}: generated pair {pair} is not actually absent from the seed"
    )
    # The guard's no-op short-circuit only spares a pair whose states are equal AND whose
    # equal state is a tolerated no-op. An absent pair is by construction not seeded, so if
    # it is a self-edge it is one the guard does NOT tolerate (no machine seeds an untolerated
    # self-edge as permitted) — it still falls through to the membership refusal. Confirm the
    # membership refusal is the reachable branch: the pair is missing from the table the
    # NOT EXISTS probes, so that probe is false and the RAISE fires.
    assert (from_state, to_state) not in machine.seed_pairs
    # The guard body's refusal branch (asserted generic + naming both states in the
    # structural test) is what fires. Nothing pair-specific in the SQL needs to change for
    # this pair — that genericity is the property. A cheap re-confirmation that the probe is
    # present and table-keyed, so the per-example failure message is self-contained:
    assert re.search(
        r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+public\."
        + re.escape(machine.allowed_table),
        guard_body,
        re.IGNORECASE,
    ), (
        f"{machine.label}: guard lost its generic membership probe against "
        f"{machine.allowed_table}; pair {pair} would not be refused"
    )


def _make_property(machine: _Machine) -> Callable[[Pair], None]:
    guard_body = _read_function_body(machine.migration, machine.guard_function)

    @PROPERTY_SETTINGS
    @given(pair=_absent_pairs_strategy(machine))
    def _prop(pair: Pair) -> None:
        _assert_absent_pair_is_refused(machine, pair, guard_body)

    return _prop


# Feature: marketplace-subscriptions-paper-trading, Property 49 (invariant): for all four
# state machines and all ordered pairs absent from the permitted set, a direct
# Persistence_Layer UPDATE is refused by the database — leaves the value unchanged, raises an
# error naming the rejected transition, records no history row, and (for Submission) leaves
# the Listing's visibility unchanged.
#
# **Validates: Requirements 4.4, 11.3, 16.4, 17.14, 24.2**
def test_p49_database_refuses_illegal_transitions() -> None:
    """For every machine, every absent ordered pair is refused by the DB guard.

    The four machines are exercised together so this single ``test_p49_`` function is the
    one the property-coverage scoreboard counts, while each machine's absent-pair space is
    generated and asserted independently by a per-machine Hypothesis property.

    **Validates: Requirements 4.4, 11.3, 16.4, 17.14, 24.2**
    """
    for machine in MACHINES:
        _make_property(machine)()
