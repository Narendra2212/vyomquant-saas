"""
tests/test_submission_state_agreement.py

One state machine, two spellings, one answer — and a test that fails the moment the
database seed stops describing the Python constant it is meant to mirror.

Spec: marketplace-subscriptions-paper-trading task 14.6. ``design.md`` ->
"``marketplace_submission_allowed_transitions`` is a two-column seed table holding the same
eleven pairs as ``SUBMISSION_TRANSITIONS`` ... a table rather than an inline ``CASE``
because ``tests/test_submission_state_agreement.py`` reads both the table's seed statements
and the Python constant and asserts they are the same set — the same technique
``tests/test_version_consumer_agreement.py`` already uses". Requirements 4.1, 4.2, 4.12,
11.1, 11.2, 16.1, 16.2, 17.7.

WHAT IS UNDER TEST
------------------
Four state machines are each defined **twice** in this codebase:

============================================= ====================================== ===========================
seed table (migration)                        Python constant (module)               pairs
============================================= ====================================== ===========================
``marketplace_submission_allowed_transitions`` ``submission_state.SUBMISSION_TRANSITIONS`` 11 (uppercase)
(``007_marketplace_submissions.sql``)
``marketplace_subscription_allowed_transitions`` ``subscription_state.SUBSCRIPTION_TRANSITIONS`` 12 (lowercase column)
(``008_marketplace_settlement.sql``)                                                             + 1 addendum from ``012``
``paper_order_allowed_transitions``           ``paper_order_state.PAPER_ORDER_TRANSITIONS`` 9 (uppercase)
(``009_paper_trading.sql``)
``paper_session_allowed_transitions``         Requirement 17.7 session machine        6 (uppercase)
(``009_paper_trading.sql``)
============================================= ====================================== ===========================

A trigger reads the seed table; the service reads the Python constant. If the two ever
disagree, a transition the service believes legal is refused by the database (or the
reverse), and Requirements 4.4 / 11.3 / 16.4 / 17.14 quietly stop meaning what they say. So
this file reads the ``INSERT ... VALUES`` block of each seed table straight off disk, reads
the matching Python constant, normalises **both** to a set of ``(from_state, to_state)``
tuples in one case, and asserts the two sets are equal — and non-empty, so the assertion can
never pass by comparing two empty sets.

THE ONE DOCUMENTED DIVERGENCE: ``('payment_failed','active')``
--------------------------------------------------------------
``008``'s seed is Requirement 11.2's twelve pairs and is still pinned to them here, pair for
pair. Requirement 11.6 additionally names ``PAYMENT_FAILED`` as a source of a transition into
``ACTIVE`` — the two clauses contradict each other, and task 19.2 resolved the contradiction
in favour of 11.6 (a retried payment that later confirms must activate the Subscription it
paid for). ``settlement_service.ELIGIBLE_FOR_ACTIVATION`` implements that decision, and
``012_subscription_payment_failed_activation.sql`` (task 19.15) seeds the matching edge so
``trg_subscription_transition_guard`` admits it instead of refusing a payment whose ledger
row has already been written.

So the set the DATABASE permits is *not* the twelve pairs — it is
``SUBSCRIPTION_TRANSITIONS ∪ MIGRATION_012_SUBSCRIPTION_ADDENDUM``, thirteen pairs. That
addendum is a **named constant**, asserted equal to what ``012`` actually seeds, rather than
a wildcard: ``008``'s seed still has to be exactly twelve, ``012``'s still has to be exactly
that one pair, and a fourteenth pair appearing in either file — or anywhere else — still
fails. :func:`subscription_permitted_pairs_in_db` is the one function that answers "what does
the guard admit today", and ``tests/property/test_db_transition_guards.py`` reads the
Subscription machine through it so P-49 never claims the guard refuses an edge ``012``
permits.

A CASE NOTE ON SUBSCRIPTIONS
----------------------------
``SubscriptionState`` members are UPPERCASE (Requirement 11.1 spells them so); the
``library_subscriptions.status`` column — and therefore the ``008`` seed — is lowercase.
``subscription_state.STATUS_TEXT_FOR_STATE`` is the one place that mapping lives. Rather than
lean on either spelling, every comparison here upper-cases both sides, so the seed and the
constant are compared as the *same* machine regardless of which column casing each was
written in.

THE PAPER SESSION MACHINE
-------------------------
``paper_session_allowed_transitions`` is seeded in ``009`` (six pairs) and its Python
constant landed with task 27.3 as
``backend_app.backend.paper.paper_session_service.PAPER_SESSION_TRANSITIONS`` — which is what
``009``'s own seed comment asks for ("Task 27.3 defines the matching Python constant; keep the
two in step"). :func:`_session_python_pairs` finds it there and the seed is compared against
it. :data:`_REQ_17_7_SESSION_PAIRS` — Requirement 17.7's six operations transcribed verbatim —
remains as the fallback oracle for the case where no such constant exists at all, so this test
was non-vacuous before 27.3 landed and did not have to be rewritten when it did.

THE moderation_status RULES
---------------------------
Requirement 4.12 / ``design.md`` -> "no handler writes ``moderation_status`` directly": the
Listing's ``moderation_status`` is *projected* from the Submission_State by the trigger
``trg_submission_projects_moderation_status``, never written by a call site. Two assertions
pin that:

* :data:`MODERATION_STATUS_FOR_STATE` never yields ``'featured'`` — featuring stays
  ``library_strategies.is_featured``, orthogonal to the review lifecycle (module docstring).
* The **marketplace submission and settlement handler modules**
  (``submission_service`` and ``subscription_period``) never write ``moderation_status``.
  Scope is deliberate: ``design.md`` § "Listing state" names *these* handlers as the ones
  that must not write it. ``library.py::admin_moderate_strategy`` is the retained legacy
  featured-setter (task 14.5 narrows it, 14.11 tests it) and ``library.py::publish_strategy``
  is replaced by task 14.10 — asserting against ``library.py`` here would be a false positive
  against behaviour those tasks own, so the check targets the new handlers, where the rule is
  real and testable.
"""

import ast
import re
from pathlib import Path

import pytest

from backend_app.backend.marketplace import subscription_period as _subscription_period
from backend_app.backend.marketplace import submission_service as _submission_service
from backend_app.backend.marketplace.submission_state import (
    MODERATION_STATUS_FOR_STATE,
    SUBMISSION_TRANSITIONS,
)
from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SUBSCRIPTION_TRANSITIONS,
    SubscriptionState,
)
from backend_app.backend.paper.paper_order_state import PAPER_ORDER_TRANSITIONS

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO_ROOT / "backend_app" / "migrations"

#: The migration that seeds Requirement 11.2's twelve Subscription_State pairs.
SUBSCRIPTION_SEED_MIGRATION = "008_marketplace_settlement.sql"

#: The additive migration that seeds Requirement 11.6's one addendum pair (task 19.15).
SUBSCRIPTION_ADDENDUM_MIGRATION = "012_subscription_payment_failed_activation.sql"

SUBSCRIPTION_ALLOWED_TABLE = "marketplace_subscription_allowed_transitions"

#: **The one documented divergence from Requirement 11.2's twelve pairs**, upper-cased like
#: every other pair in this module.
#:
#: Requirement 11.2 gives ``PAYMENT_FAILED`` a single successor (``PENDING``); Requirement 11.6
#: names ``PAYMENT_FAILED`` among the sources of a transition into ``ACTIVE``. Task 19.2 resolved
#: the contradiction in favour of 11.6 and widened
#: ``settlement_service.ELIGIBLE_FOR_ACTIVATION``; task 19.15's
#: ``012_subscription_payment_failed_activation.sql`` seeds the matching edge so the database
#: agrees. Named here as a distinct constant, and asserted equal to what ``012`` actually
#: seeds, so the divergence stays VISIBLE: it is one enumerated pair, not a relaxed assertion,
#: and any further drift in either file still fails
#: :meth:`TestSubscriptionStateAgreement.test_the_database_permits_the_twelve_pairs_plus_the_addendum`.
MIGRATION_012_SUBSCRIPTION_ADDENDUM = frozenset({("PAYMENT_FAILED", "ACTIVE")})


# ---------------------------------------------------------------------------
# The seed parser — the technique design.md points at test_version_consumer_agreement.py
# for: read the artifact off disk, do not trust a transcription of it.
# ---------------------------------------------------------------------------


def _read_seed_pairs(migration_filename: str, table: str) -> frozenset:
    """The ``(from_state, to_state)`` pairs the named table's ``INSERT ... VALUES`` seeds.

    Reads the SQL text, finds the ``INSERT INTO public.<table> (from_state, to_state)
    VALUES ( ... )`` statement, and parses every ``('a', 'b')`` row out of the ``VALUES``
    body up to its terminating ``ON CONFLICT`` / ``;``. States are upper-cased so a
    lowercase column seed and an uppercase enum compare as the same machine.

    A parse that finds no rows is a failure, not an empty set: the whole point is that the
    seed exists and is read, so a silent miss must not masquerade as agreement downstream.
    """
    sql = (MIGRATIONS / migration_filename).read_text(encoding="utf-8")

    # The seed's INSERT for exactly this table, non-greedy up to the first terminator.
    insert = re.search(
        r"INSERT\s+INTO\s+public\." + re.escape(table)
        + r"\s*\(\s*from_state\s*,\s*to_state\s*\)\s*VALUES(?P<body>.*?)"
        + r"(?:ON\s+CONFLICT|;)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert insert is not None, (
        f"no `INSERT INTO public.{table} (from_state, to_state) VALUES` seed found in "
        f"{migration_filename}; the parity test cannot read a machine that is not seeded"
    )

    body = insert.group("body")
    # Each row is a ('from', 'to') tuple. Single-quoted SQL string literals, comma-separated.
    rows = re.findall(
        r"\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)",
        body,
    )
    pairs = frozenset((frm.strip().upper(), to.strip().upper()) for frm, to in rows)
    assert pairs, (
        f"the {table} seed in {migration_filename} parsed to zero pairs; the VALUES block "
        f"was found but no ('from','to') rows were read from it"
    )
    # A duplicated seed row would collapse in the set and hide a real double-insert.
    assert len(rows) == len(pairs), (
        f"the {table} seed in {migration_filename} contains a duplicated pair: {rows}"
    )
    return pairs


def subscription_permitted_pairs_in_db() -> frozenset:
    """Every ``(from, to)`` pair ``trg_subscription_transition_guard`` admits today.

    ``008``'s twelve-pair seed UNION ``012``'s one-pair addendum — both parsed off disk by the
    same parser, never transcribed. This is the *effective* permitted set: the guard's
    ``NOT EXISTS`` probe reads the table, and the table is what both migrations seeded, so any
    consumer asking "would the database refuse this edge?" must ask this function and not the
    ``008`` seed alone.
    """
    return _read_seed_pairs(
        SUBSCRIPTION_SEED_MIGRATION, SUBSCRIPTION_ALLOWED_TABLE
    ) | _read_seed_pairs(SUBSCRIPTION_ADDENDUM_MIGRATION, SUBSCRIPTION_ALLOWED_TABLE)


def _state_text(state) -> str:
    """The wire/column value of a state, upper-cased.

    ``.value`` rather than ``str(...)``: every state here is a ``str``-valued enum whose
    value is its own column spelling, but not all of them override ``__str__`` — a plain
    ``str`` ``Enum`` renders as ``ClassName.MEMBER`` under ``str()`` (``PaperOrderState``
    does exactly this). Reading ``.value`` takes the column spelling directly and never the
    qualified member name; upper-casing then folds the uppercase enums and the lowercase
    subscription column seed onto one casing.
    """
    return str(getattr(state, "value", state)).upper()


def _flatten_transitions(table) -> frozenset:
    """A ``{state: (targets,)}`` adjacency map as a set of upper-cased ``(from, to)`` tuples."""
    return frozenset(
        (_state_text(state), _state_text(target))
        for state, targets in table.items()
        for target in targets
    )


def _assert_same_machine(python_pairs: frozenset, sql_pairs: frozenset, label: str) -> None:
    """The one comparison every sibling test funnels through: equal, and non-vacuous."""
    assert python_pairs, f"{label}: the Python transition set is empty"
    assert sql_pairs, f"{label}: the SQL seed set is empty"
    missing_in_sql = python_pairs - sql_pairs
    extra_in_sql = sql_pairs - python_pairs
    assert python_pairs == sql_pairs, (
        f"{label}: the seed table and the Python constant describe different machines.\n"
        f"  edges in Python but not seeded in SQL: {sorted(missing_in_sql)}\n"
        f"  edges seeded in SQL but absent from Python: {sorted(extra_in_sql)}"
    )


# ---------------------------------------------------------------------------
# Submission (Requirements 4.1, 4.2) — eleven pairs, uppercase both sides
# ---------------------------------------------------------------------------


class TestSubmissionStateAgreement:
    def test_seed_equals_python_constant(self):
        sql_pairs = _read_seed_pairs(
            "007_marketplace_submissions.sql",
            "marketplace_submission_allowed_transitions",
        )
        python_pairs = _flatten_transitions(SUBMISSION_TRANSITIONS)
        _assert_same_machine(python_pairs, sql_pairs, "Submission_State")
        # Requirement 4.2's eleven pairs, pinned as a count so a twelfth edge is caught even
        # if it is added to both sides at once.
        assert len(python_pairs) == 11


# ---------------------------------------------------------------------------
# Subscription (Requirements 11.1, 11.2) — twelve pairs, UPPERCASE enum vs lowercase seed
# ---------------------------------------------------------------------------


class TestSubscriptionStateAgreement:
    def test_seed_equals_python_constant(self):
        """``008``'s seed is Requirement 11.2's twelve pairs, still pinned pair for pair.

        Unweakened by task 19.15: the addendum lives in its own migration and its own named
        constant, so this assertion keeps meaning exactly what it meant before — ``008``
        describes ``SUBSCRIPTION_TRANSITIONS`` and nothing else.
        """
        sql_pairs = _read_seed_pairs(
            SUBSCRIPTION_SEED_MIGRATION,
            SUBSCRIPTION_ALLOWED_TABLE,
        )
        python_pairs = _flatten_transitions(SUBSCRIPTION_TRANSITIONS)
        _assert_same_machine(python_pairs, sql_pairs, "Subscription_State")
        assert len(python_pairs) == 12
        assert MIGRATION_012_SUBSCRIPTION_ADDENDUM.isdisjoint(sql_pairs), (
            "the Requirement 11.6 addendum has been seeded into "
            f"{SUBSCRIPTION_SEED_MIGRATION} as well as "
            f"{SUBSCRIPTION_ADDENDUM_MIGRATION}; 008 is Requirement 11.2's twelve pairs and "
            "the addendum belongs in the additive migration alone, or the divergence stops "
            "being traceable to the requirement that authorised it"
        )

    def test_migration_012_seeds_exactly_the_named_addendum(self):
        """``012`` seeds the one Requirement 11.6 pair, idempotently, and no other.

        The addendum is enumerated, not wildcarded: if ``012`` ever grows a second pair, or
        seeds a different one, this fails rather than silently widening what the database
        permits. The ``ON CONFLICT DO NOTHING`` is asserted too — a re-run of a hand-applied
        migration must insert nothing and raise nothing (Requirements 24.8, 24.9).
        """
        addendum = _read_seed_pairs(
            SUBSCRIPTION_ADDENDUM_MIGRATION,
            SUBSCRIPTION_ALLOWED_TABLE,
        )
        assert addendum == MIGRATION_012_SUBSCRIPTION_ADDENDUM, (
            f"{SUBSCRIPTION_ADDENDUM_MIGRATION} seeds {sorted(addendum)}, but the named "
            f"addendum constant is {sorted(MIGRATION_012_SUBSCRIPTION_ADDENDUM)}"
        )
        assert len(addendum) == 1

        # The lowercase spelling in the file is the column spelling, resolved through the one
        # enum -> column mapping rather than trusted as a literal.
        sql = (MIGRATIONS / SUBSCRIPTION_ADDENDUM_MIGRATION).read_text(encoding="utf-8")
        expected_lower = (
            STATUS_TEXT_FOR_STATE[SubscriptionState.PAYMENT_FAILED],
            STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE],
        )
        assert (
            f"('{expected_lower[0]}', '{expected_lower[1]}')" in sql
            or f"('{expected_lower[0]}','{expected_lower[1]}')" in sql
        ), (
            f"{SUBSCRIPTION_ADDENDUM_MIGRATION} does not seed the pair in the "
            f"library_subscriptions.status casing {expected_lower}; a mis-cased seed row is "
            f"a row the guard's text comparison never matches"
        )
        assert re.search(
            r"ON\s+CONFLICT\s*\(\s*from_state\s*,\s*to_state\s*\)\s*DO\s+NOTHING",
            sql,
            re.IGNORECASE,
        ), (
            f"{SUBSCRIPTION_ADDENDUM_MIGRATION}'s seed is not idempotent: no "
            f"`ON CONFLICT (from_state, to_state) DO NOTHING`, so a re-run of a hand-applied "
            f"migration would raise a unique violation"
        )

    def test_the_database_permits_the_twelve_pairs_plus_the_addendum(self):
        """The effective DB set is ``SUBSCRIPTION_TRANSITIONS ∪ the named addendum``.

        This is the assertion that keeps the application and the database in agreement after
        task 19.15. Before ``012``, ``settlement_service.ELIGIBLE_FOR_ACTIVATION`` admitted
        ``payment_failed -> active`` while the seed table did not, so a retried payment wrote
        its ``marketplace_settlements`` row and was then refused the activation by the guard —
        the purchaser paid and got nothing. Thirteen pairs, enumerated on both sides.
        """
        permitted = subscription_permitted_pairs_in_db()
        expected = (
            _flatten_transitions(SUBSCRIPTION_TRANSITIONS)
            | MIGRATION_012_SUBSCRIPTION_ADDENDUM
        )
        assert permitted == expected, (
            "the permitted-edge set the database holds is not Requirement 11.2's twelve "
            "pairs plus Requirement 11.6's one named addendum.\n"
            f"  permitted in SQL but not expected: {sorted(permitted - expected)}\n"
            f"  expected but not permitted in SQL: {sorted(expected - permitted)}"
        )
        assert len(permitted) == 13

    def test_the_application_activation_sources_match_the_permitted_edges(self):
        """``ELIGIBLE_FOR_ACTIVATION`` is exactly the DB's set of sources of ``-> ACTIVE``.

        The divergence task 19.2 opened between the service's five activation sources and the
        seed table's four is what task 19.15 closes; asserted here from BOTH artifacts so the
        two halves cannot drift apart again in either direction.
        """
        from backend_app.backend.marketplace.settlement_service import (
            ELIGIBLE_FOR_ACTIVATION,
        )

        db_sources = frozenset(
            frm.lower()
            for frm, to in subscription_permitted_pairs_in_db()
            if to == "ACTIVE"
        )
        assert db_sources == ELIGIBLE_FOR_ACTIVATION, (
            "settlement_service.ELIGIBLE_FOR_ACTIVATION and the permitted edges into "
            "'active' disagree; a status the service will activate but the guard refuses is "
            "a purchaser charged with no access.\n"
            f"  service admits but DB refuses: {sorted(ELIGIBLE_FOR_ACTIVATION - db_sources)}\n"
            f"  DB permits but service refuses: {sorted(db_sources - ELIGIBLE_FOR_ACTIVATION)}"
        )

    def test_the_column_spelling_is_the_one_the_seed_uses(self):
        """The seed is lowercase; ``STATUS_TEXT_FOR_STATE`` is the sanctioned lowercasing.

        This guards the normalisation itself: if the column mapping ever diverged from the
        seed's casing, upper-casing both sides in the parity test would paper over it, so
        the lowercase pairs are compared directly here too.
        """
        sql_pairs_raw = re.findall(
            r"\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)",
            re.search(
                r"INSERT\s+INTO\s+public\.marketplace_subscription_allowed_transitions"
                r".*?VALUES(?P<body>.*?)ON\s+CONFLICT",
                (MIGRATIONS / "008_marketplace_settlement.sql").read_text(encoding="utf-8"),
                re.IGNORECASE | re.DOTALL,
            ).group("body"),
        )
        sql_lower = frozenset((frm, to) for frm, to in sql_pairs_raw)
        python_lower = frozenset(
            (STATUS_TEXT_FOR_STATE[state], STATUS_TEXT_FOR_STATE[target])
            for state, targets in SUBSCRIPTION_TRANSITIONS.items()
            for target in targets
        )
        assert python_lower == sql_lower, (
            "the lowercase column pairs and the lowercase seed pairs disagree; the "
            "STATUS_TEXT_FOR_STATE mapping has drifted from the 008 seed"
        )


# ---------------------------------------------------------------------------
# Requirements 11.6, 11.14 — widening the edge set must not open the payment gate
#
# ``012`` adds ONE permitted edge. The invariant it must not disturb is the one the guard's
# third branch enforces: no transition into ``'active'`` without a qualifying non-reversal
# ``marketplace_settlements`` row. These assertions read ``008``'s guard body and ``012``'s
# text off disk — the same static-parse technique every migration test in this repository
# uses, since there is no PostgreSQL in this environment.
# ---------------------------------------------------------------------------


#: The regex that finds one ``CREATE OR REPLACE FUNCTION
#: public.marketplace_subscription_guard()`` definition in a migration's text. Written once
#: because two readers want it: ``008``'s own definition and the EFFECTIVE one.
_SUBSCRIPTION_GUARD_DEFINITION = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.marketplace_subscription_guard"
    r"\s*\(\s*\)\s*RETURNS\s+TRIGGER\s+AS\s+\$\$(?P<body>.*?)\$\$\s*LANGUAGE",
    re.IGNORECASE | re.DOTALL,
)


def _subscription_guard_body() -> str:
    """The plpgsql body of ``public.marketplace_subscription_guard()`` as ``008`` defines it.

    ``008``'s own text, deliberately: the assertions in :class:`TestTheAddendumDoesNotOpenTheGate`
    are about the file that authors the guard. For "what does the guard do on a database with
    every migration applied", ask :func:`subscription_guard_body_in_db`.
    """
    sql = (MIGRATIONS / SUBSCRIPTION_SEED_MIGRATION).read_text(encoding="utf-8")
    match = _SUBSCRIPTION_GUARD_DEFINITION.search(sql)
    assert match is not None, (
        "marketplace_subscription_guard() is not defined in "
        f"{SUBSCRIPTION_SEED_MIGRATION}; without it nothing enforces Requirement 11.3's "
        "permitted-edge check or Requirement 11.14's payment gate"
    )
    return match.group("body")


def subscription_guard_body_in_db() -> str:
    """The EFFECTIVE body of ``public.marketplace_subscription_guard()``.

    ``CREATE OR REPLACE FUNCTION`` means the guard a database actually runs is the definition in
    the **last** migration that authors one, not ``008``'s. ``008`` writes the original;
    ``014_subscription_admin_reinstatement.sql`` replaces it to admit the one administrative
    ``suspended -> active`` reinstatement (Requirement 11.17). Migrations are applied in filename
    order, so the last matching file by name is the effective definition.

    THE ONE helper for that question, for the same reason
    :func:`subscription_permitted_pairs_in_db` is the one helper for the permitted-pair set: a
    consumer asking "would the database refuse this write?" that read only ``008`` would assert
    against a definition no environment runs.
    """
    body: str = ""
    source: str = ""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        match = _SUBSCRIPTION_GUARD_DEFINITION.search(path.read_text(encoding="utf-8"))
        if match is not None:
            body = match.group("body")
            source = path.name
    assert body, (
        "no migration under backend_app/migrations defines "
        "public.marketplace_subscription_guard(), so nothing enforces Requirement 11.3's "
        "permitted-edge check or Requirement 11.14's payment gate"
    )
    assert source, source
    return body


class TestTheAddendumDoesNotOpenTheGate:
    def test_the_guard_still_refuses_an_activation_with_no_settlement_row(self):
        """Requirement 11.14: ``-> active`` needs a non-reversal ``marketplace_settlements`` row.

        This is the branch that makes ``('payment_failed','active')`` safe to permit. After
        ``012`` the missing-edge branch no longer refuses a retried payment — so the ONLY thing
        standing between an arbitrary caller and a free activation is this settlement probe. It
        is asserted here, in the same file that asserts the edge is permitted, so the two facts
        can never be separated.
        """
        body = _subscription_guard_body()

        activation_gate = re.search(
            r"IF\s+NEW\.status\s*=\s*'active'\s+THEN.*?NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+"
            r"public\.marketplace_settlements.*?END\s+IF\s*;",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        assert activation_gate is not None, (
            "the guard has no `IF NEW.status = 'active' THEN ... NOT EXISTS (SELECT 1 FROM "
            "public.marketplace_settlements ...)` branch; a transition into 'active' would be "
            "admitted with no confirmed payment behind it (Requirements 11.6, 11.14)"
        )
        gate = activation_gate.group(0)

        # Scoped to THIS subscription, and a refund never funds an activation.
        assert re.search(r"s\.subscription_id\s*=\s*NEW\.id", gate, re.IGNORECASE), (
            "the settlement probe is not scoped to NEW.id; any subscription's payment would "
            "fund any other subscription's activation"
        )
        assert re.search(r"s\.is_reversal\s*=\s*FALSE", gate, re.IGNORECASE), (
            "the settlement probe does not exclude reversals; a refund could fund an "
            "activation (Requirement 11.14)"
        )
        # The non-circular anchor: a payment older than the period it is funding does not count.
        assert re.search(
            r"s\.settled_at\s*>=\s*OLD\.period_expiry", gate, re.IGNORECASE
        ), (
            "the settlement probe no longer compares settled_at against OLD.period_expiry, so "
            "the payment that bought the period now ending could fund the next one too"
        )
        # And the refusal is the translatable check violation.
        assert re.search(
            r"RAISE\s+EXCEPTION.*?USING\s+ERRCODE\s*=\s*'23514'",
            gate,
            re.IGNORECASE | re.DOTALL,
        ), "the activation refusal does not carry the 23514 SQLSTATE the service translates by"

    def test_every_pair_outside_the_permitted_set_is_still_absent(self):
        """Thirteen permitted, thirty-six refused: the gate widened by exactly one edge.

        Quantified over all 7 x 7 ordered pairs of Requirement 11.1's statuses, so "one more
        edge" is proven rather than asserted about a count. The guard refuses by membership in
        this table (``tests/property/test_db_transition_guards.py`` P-49 proves the mechanism
        is generic), so an absent pair is a refused pair.
        """
        permitted = subscription_permitted_pairs_in_db()
        statuses = [state.name for state in SubscriptionState]
        assert len(statuses) == 7

        expected_permitted = (
            _flatten_transitions(SUBSCRIPTION_TRANSITIONS)
            | MIGRATION_012_SUBSCRIPTION_ADDENDUM
        )
        all_pairs = {(frm, to) for frm in statuses for to in statuses}
        assert len(all_pairs) == 49

        refused = all_pairs - permitted
        assert len(permitted) == 13
        assert len(refused) == 36
        assert permitted <= all_pairs, (
            f"the seed permits pairs over states Requirement 11.1 does not define: "
            f"{sorted(permitted - all_pairs)}"
        )
        for pair in sorted(all_pairs):
            if pair in expected_permitted:
                assert pair in permitted, f"{pair} should be permitted and is absent"
            else:
                assert pair in refused, (
                    f"{pair} is permitted by the seed tables but is neither one of "
                    f"Requirement 11.2's twelve pairs nor the one Requirement 11.6 addendum"
                )

        # No self-edge is permitted: a same-value write is the guard's first branch's business
        # (it returns early), never a seeded transition.
        assert not [pair for pair in permitted if pair[0] == pair[1]]

        # 'active' is reachable from exactly five sources — the four of Requirement 11.2 plus
        # 'payment_failed'. Nothing else gained a route in.
        assert {frm for frm, to in permitted if to == "ACTIVE"} == {
            "PENDING",
            "EXPIRED",
            "CANCELLED",
            "SUSPENDED",
            "PAYMENT_FAILED",
        }

    def test_the_addendum_migration_touches_nothing_but_the_seed_table(self):
        """``012`` is one INSERT. No function, no policy, no grant, no destructive statement.

        A file that widened the edge set *and* redefined the guard, or granted a role something
        new, would be doing the dangerous half of this change out of sight of the assertions
        above. Asserted over the executable SQL with comments stripped, so the header's prose —
        which discusses policies, grants and the guard at length — cannot false-positive.
        """
        sql = (MIGRATIONS / SUBSCRIPTION_ADDENDUM_MIGRATION).read_text(encoding="utf-8")
        executable = "\n".join(
            re.sub(r"--.*$", "", line) for line in sql.splitlines()
        )
        # String literals carry the RAISE messages, which name policies/grants/the guard.
        executable = re.sub(r"'(?:[^']|'')*'", "''", executable)

        forbidden = {
            "CREATE OR REPLACE FUNCTION": r"\bcreate\s+or\s+replace\s+function\b",
            "CREATE TRIGGER": r"\bcreate\s+trigger\b",
            "CREATE POLICY": r"\bcreate\s+policy\b",
            "ALTER POLICY": r"\balter\s+policy\b",
            "ROW LEVEL SECURITY": r"\brow\s+level\s+security\b",
            "GRANT": r"\bgrant\b",
            "REVOKE": r"\brevoke\b",
            "ALTER TABLE": r"\balter\s+table\b",
            "CREATE TABLE": r"\bcreate\s+table\b",
            "DROP": r"\bdrop\b",
            "DELETE FROM": r"\bdelete\s+from\b",
            "TRUNCATE": r"\btruncate\b",
            "RENAME": r"\brename\b",
            "UPDATE": r"\bupdate\s+public\.",
        }
        offenders = sorted(
            name
            for name, pattern in forbidden.items()
            if re.search(pattern, executable, re.IGNORECASE)
        )
        assert not offenders, (
            f"{SUBSCRIPTION_ADDENDUM_MIGRATION} contains {offenders}; it must seed one "
            f"reference row and do nothing else (Requirement 24.7)"
        )

        # Exactly one INSERT in the whole file, and it targets the seed table.
        inserts = re.findall(r"\bINSERT\s+INTO\s+([a-zA-Z_.]+)", executable, re.IGNORECASE)
        assert inserts == [f"public.{SUBSCRIPTION_ALLOWED_TABLE}"], (
            f"{SUBSCRIPTION_ADDENDUM_MIGRATION} issues INSERTs into {inserts}; the only write "
            f"it may make is the one addendum pair"
        )
        # Dormant tables stay dormant (Requirement 1.2). Checked over the executable SQL, not
        # the raw text: the header's scope note *names* both tables to say it does not touch
        # them, and a promise is not a reference.
        for dormant in ("marketplace_listings", "strategy_subscriptions"):
            assert dormant not in executable, (
                f"{SUBSCRIPTION_ADDENDUM_MIGRATION} references the dormant table {dormant} in "
                f"an executable statement"
            )


# ---------------------------------------------------------------------------
# Paper order (Requirements 16.1, 16.2) — nine pairs, uppercase both sides
# ---------------------------------------------------------------------------


class TestPaperOrderStateAgreement:
    def test_seed_equals_python_constant(self):
        sql_pairs = _read_seed_pairs(
            "009_paper_trading.sql",
            "paper_order_allowed_transitions",
        )
        python_pairs = _flatten_transitions(PAPER_ORDER_TRANSITIONS)
        _assert_same_machine(python_pairs, sql_pairs, "Paper_Order_State")
        assert len(python_pairs) == 9


# ---------------------------------------------------------------------------
# Paper session (Requirement 17.7) — six pairs, uppercase both sides
#
# The Python constant is task 27.3 and not in the tree yet; the oracle is Requirement 17.7
# until it lands, and _session_python_pairs() switches to the constant automatically the
# moment it appears — so this test is non-vacuous today and correct tomorrow.
# ---------------------------------------------------------------------------


#: Requirement 17.7's six operations as edges: start CREATED->RUNNING; pause RUNNING->PAUSED;
#: resume PAUSED->RUNNING; stop RUNNING->STOPPED and PAUSED->STOPPED; reset STOPPED->CREATED.
_REQ_17_7_SESSION_PAIRS = frozenset(
    {
        ("CREATED", "RUNNING"),
        ("RUNNING", "PAUSED"),
        ("PAUSED", "RUNNING"),
        ("RUNNING", "STOPPED"),
        ("PAUSED", "STOPPED"),
        ("STOPPED", "CREATED"),
    }
)


def _session_python_pairs():
    """The paper-session transition set from a Python constant if one exists, else 17.7.

    Task 27.3 added the session state machine to
    ``backend_app.backend.paper.paper_session_service`` as
    ``PAPER_SESSION_TRANSITIONS`` — the adjacency map 009 section 2's seed comment asks for. This
    picks it up from there (or from any of the other conventional names and module locations below,
    as an adjacency map or a flat pair iterable), and the requirement-derived fallback is used only
    while no such constant exists at all. The returned tuple records which source was consulted so
    the test can say so.
    """
    from backend_app.backend import paper as paper_pkg

    candidate_names = (
        "PAPER_SESSION_TRANSITIONS",
        "SESSION_TRANSITIONS",
        "PAPER_SESSION_ALLOWED_TRANSITIONS",
    )
    for name in candidate_names:
        table = getattr(paper_pkg, name, None)
        if table is None:
            # Also look for a dedicated module, e.g. paper.paper_session_state.
            for module_name in (
                "paper_session_service",
                "paper_session_state",
                "session_state",
            ):
                try:
                    module = __import__(
                        f"backend_app.backend.paper.{module_name}",
                        fromlist=[name],
                    )
                except Exception:
                    continue
                table = getattr(module, name, None)
                if table is not None:
                    break
        if table is None:
            continue
        if isinstance(table, dict):
            return _flatten_transitions(table), f"python:{name}"
        # A flat iterable of (from, to) pairs.
        flat = frozenset(
            (_state_text(frm), _state_text(to)) for frm, to in table
        )
        if flat:
            return flat, f"python:{name}"
    return _REQ_17_7_SESSION_PAIRS, "requirement:17.7"


class TestPaperSessionStateAgreement:
    def test_seed_equals_python_constant(self):
        sql_pairs = _read_seed_pairs(
            "009_paper_trading.sql",
            "paper_session_allowed_transitions",
        )
        python_pairs, source = _session_python_pairs()
        _assert_same_machine(python_pairs, sql_pairs, f"Paper_Session_State ({source})")
        assert len(python_pairs) == 6


# ---------------------------------------------------------------------------
# Requirement 4.12 — moderation_status is projected, never freely written
# ---------------------------------------------------------------------------


class TestModerationStatusIsProjectedNotWritten:
    def test_the_mapping_never_yields_featured(self):
        """``'featured'`` is ``library_strategies.is_featured``, orthogonal to the review
        lifecycle (submission_state module docstring). The projection must not mint it."""
        assert "featured" not in set(MODERATION_STATUS_FOR_STATE.values()), (
            "MODERATION_STATUS_FOR_STATE yields 'featured'; featuring is is_featured, not a "
            "Submission_State projection"
        )
        # Every value is one of the retained non-featured moderation_status spellings.
        assert set(MODERATION_STATUS_FOR_STATE.values()) <= {"pending", "approved", "rejected"}

    def test_the_marketplace_handlers_never_write_moderation_status(self):
        """The submission and settlement handlers project ``moderation_status`` through the
        trigger; they must not write it. Scope per design.md § "Listing state": these two
        handler modules, NOT library.py's retained legacy featured-setter (tasks 14.5/14.11)
        or its publish_strategy (task 14.10).

        Asserted structurally: no dict literal anywhere in either module carries a
        ``moderation_status`` key. That is exactly what a ``.update({...})`` /
        ``.insert({...})`` payload is — whether written inline or assembled into a variable
        first and passed by name (``.update(patch)``), which a call-site-only check would
        miss (``library.py::admin_moderate_strategy`` does precisely this). Reads are
        untouched: ``.select("... moderation_status ...")`` and ``.eq("moderation_status",
        ...)`` pass the column name as a *string argument*, never as a dict key, and a
        comment or docstring mention (both modules explain the projection) is not a node.
        Neither handler has any legitimate reason to place ``moderation_status`` as a dict
        key, so flagging every such key is both safe and complete here.
        """
        for module in (_submission_service, _subscription_period):
            source_path = Path(module.__file__)
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            offenders = sorted(set(_moderation_status_write_sites(tree)))
            assert not offenders, (
                f"{source_path.name} places moderation_status in a payload dict at line(s) "
                f"{offenders}; it must be projected by trg_submission_projects_moderation_"
                f"status, never written by the handler (Requirement 4.12)"
            )


def _moderation_status_write_sites(tree: ast.AST):
    """Yield the line of every dict literal that carries a ``moderation_status`` key.

    A dict literal with a ``moderation_status`` key is a write payload — the only shape in
    which a Supabase ``.update``/``.insert``/``.upsert`` assigns a column — regardless of
    whether it is passed to the write call inline or through an intervening variable. String
    occurrences (``.select``/``.eq`` arguments, comments, docstrings) are not dict keys and
    are never reached by this walk.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and key.value == "moderation_status":
                yield node.lineno


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
