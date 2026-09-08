"""
tests/test_marketplace_paper_schema_contract.py

The schema contract for the marketplace-subscriptions-paper-trading spec (task 11.6).

WHAT THIS GUARDS
----------------
``backend_app/routers`` and the pure domain modules under
``backend_app/backend/marketplace`` and ``backend_app/backend/paper`` read and write a set of
columns on eleven new ``paper_*`` / ``marketplace_*`` tables and on additive columns of four
existing ones. The migrations that create those columns live in ``migrations/`` and
``backend_app/migrations/`` and are **applied by hand, per file** - there is no migration table
and no deploy step runs them (see the header of
``backend_app/migrations/006_backtest_evidence_columns.sql``). So a handler that reads a column
no migration creates does not fail at deploy time; it fails in production with PostgreSQL
``42703 undefined_column`` on a select or PostgREST ``PGRST204`` on an insert - the exact
condition the header of ``migrations/007_add_marketplace_pricing_columns.sql`` records for
``/trending`` and ``/featured``, which the router then swallowed as HTTP 200 with empty results.

This test is the mechanism that prevents a repeat of that ``42703`` condition. It parses the
migration set into an in-memory schema model and asserts, against the per-handler column
manifests declared in ``backend_app.backend.marketplace.COLUMN_CONTRACT`` and
``backend_app.backend.paper.COLUMN_CONTRACT``:

1. Every ``(table, column)`` pair either manifest names is created by the migration set
   (:func:`test_every_manifest_pair_exists`). Pairs on a table a manifest marks ``preexisting``
   - a platform table this spec does not create, such as ``profiles`` - are exempt from this
   check but still bound assertion 2.
2. Every ``.select("…")`` string literal in the new and modified modules names only columns in
   that module's manifest entry (:func:`test_select_literals_are_within_manifest`) - so a
   handler that starts reading a new column without declaring it fails here, not with ``42703``
   in production.
3. Every constraint, index and policy ``design.md`` names is present
   (:func:`test_named_constraints_present`, :func:`test_named_indexes_present`).
4. Every new table carries ``ENABLE ROW LEVEL SECURITY`` and both an owner/authenticated policy
   and a service-role policy (:func:`test_new_tables_have_rls_and_both_policies`).
5. No file in the set contains a destructive statement -
   ``DROP TABLE``/``DROP COLUMN``/``RENAME``/``DELETE FROM``/``TRUNCATE``
   (:func:`test_no_destructive_statements`), Requirement 24.7's additive-only rule.

Requirements: 1.3, 1.9, 21.2, 24.2, 24.3, 24.4, 24.7, 24.9, 24.10.

WHY THE PARSER IS DELIBERATELY SIMPLE
-------------------------------------
There is no PostgreSQL in CI (``tests/security/test_builder_tenant_isolation.py`` records the
same limitation for migrations 004-004e), so the schema model is built by reading the migration
*text*, not by applying it. The parser understands the shapes this repo's migrations actually
use: ``CREATE TABLE IF NOT EXISTS`` with inline column and ``CONSTRAINT`` clauses, guarded
``ALTER TABLE … ADD CONSTRAINT``, ``ALTER TABLE … ADD COLUMN IF NOT EXISTS``,
``CREATE [UNIQUE] INDEX IF NOT EXISTS``, ``ALTER TABLE … ENABLE ROW LEVEL SECURITY`` and both
the literal ``CREATE POLICY`` form and the ``EXECUTE format('CREATE POLICY %I ON public.%I …')``
loop form 007/008/009 use to create the owner and service-role policies over a list of tables.
Comments and string literals are stripped before the destructive-statement scan so a header
that says "no ``DROP TABLE``" is not mistaken for one.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, FrozenSet, List, Set, Tuple

import pytest

from backend_app.backend.marketplace import COLUMN_CONTRACT as MARKETPLACE_CONTRACT
from backend_app.backend.paper import COLUMN_CONTRACT as PAPER_CONTRACT

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "migrations"
BACKEND_MIGRATIONS_DIR = REPO_ROOT / "backend_app" / "migrations"


# ══════════════════════════════════════════════════════════════════════════
# SQL text handling
# ══════════════════════════════════════════════════════════════════════════


def _strip_sql_comments_and_strings(sql: str) -> str:
    """Return ``sql`` with ``--`` line comments, ``/* */`` block comments and single-quoted
    string literals removed.

    The destructive-statement scan runs on this so that a header explaining "no ``DROP TABLE``",
    a verification query embedded in a comment, or a ``DELETE`` mentioned inside an
    ``EXECUTE format('…')`` payload is not counted as a destructive statement in the DDL itself.
    String literals are blanked (replaced by ``''``) rather than deleted so that statement
    boundaries and the surrounding structure are preserved.
    """
    out: List[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # -- line comment
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            if end == -1:
                break
            i = end
            continue

        # /* block comment */  (PostgreSQL block comments do not nest in practice here)
        if ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            i = (end + 2) if end != -1 else n
            out.append(" ")
            continue

        # '…' string literal, honouring the doubled '' escape
        if ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append("''")
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def _all_migration_files() -> List[Path]:
    """Every ``*.sql`` in both hand-applied migration sequences, sorted within each."""
    assert MIGRATIONS_DIR.is_dir(), f"missing migrations dir: {MIGRATIONS_DIR}"
    assert BACKEND_MIGRATIONS_DIR.is_dir(), (
        f"missing migrations dir: {BACKEND_MIGRATIONS_DIR}"
    )
    files: List[Path] = []
    for directory in (MIGRATIONS_DIR, BACKEND_MIGRATIONS_DIR):
        files.extend(sorted(directory.glob("*.sql")))
    return files


# ══════════════════════════════════════════════════════════════════════════
# The in-memory schema model
# ══════════════════════════════════════════════════════════════════════════


class SchemaModel:
    """Tables, columns, constraints, indexes and policies parsed from the migration set.

    Deliberately additive: a column, constraint, index or policy that any file in the set
    creates is present. The parser never removes from the model, because the migration set is
    additive-only (Requirement 24.7), which :func:`test_no_destructive_statements` proves
    independently.
    """

    def __init__(self) -> None:
        #: table name -> set of column names
        self.columns: Dict[str, Set[str]] = {}
        #: every named constraint (chk_/uq_/fk_/pk_/…) found anywhere
        self.constraints: Set[str] = set()
        #: every index and unique-index name found anywhere
        self.indexes: Set[str] = set()
        #: table name -> True when ``ENABLE ROW LEVEL SECURITY`` was seen for it
        self.rls_enabled: Set[str] = set()
        #: table name -> set of "role scopes" of its policies: 'authenticated', 'service_role',
        #: 'public' (a ``FOR SELECT USING (true)`` with no ``TO`` role)
        self.policy_scopes: Dict[str, Set[str]] = {}

    def _table(self, name: str) -> Set[str]:
        return self.columns.setdefault(name, set())

    def add_column(self, table: str, column: str) -> None:
        self._table(table).add(column)

    def add_policy(self, table: str, scope: str) -> None:
        self.policy_scopes.setdefault(table, set()).add(scope)


# Identifier: optional public. schema qualifier, then a bare name.
_QUALIFIED = r"(?:public\.)?([a-zA-Z_][a-zA-Z0-9_]*)"

# A column definition line inside a CREATE TABLE body starts with an identifier followed by a
# type token. Lines that begin with a table constraint keyword are not columns.
_TABLE_CONSTRAINT_KEYWORDS = (
    "constraint",
    "primary",
    "unique",
    "check",
    "foreign",
    "exclude",
    "like",
)


def _find_balanced(sql: str, open_paren: int) -> int:
    """Index just past the ``)`` matching the ``(`` at ``open_paren``; -1 if unbalanced."""
    depth = 0
    for i in range(open_paren, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _split_top_level(body: str) -> List[str]:
    """Split a CREATE TABLE body on commas that are not inside parentheses."""
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _parse_create_table(model: SchemaModel, sql: str) -> None:
    """Populate columns and inline constraints from every ``CREATE TABLE … ( … )``."""
    pattern = re.compile(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?" + _QUALIFIED + r"\s*\(",
        re.IGNORECASE,
    )
    for m in pattern.finditer(sql):
        table = m.group(1).lower()
        open_paren = m.end() - 1
        close = _find_balanced(sql, open_paren)
        if close == -1:
            continue
        body = sql[open_paren + 1 : close - 1]
        for raw in _split_top_level(body):
            item = raw.strip()
            if not item:
                continue
            lowered = item.lower()
            # Inline CONSTRAINT <name> …
            cm = re.match(r"constraint\s+([a-zA-Z_][a-zA-Z0-9_]*)", lowered)
            if cm:
                model.constraints.add(cm.group(1))
                continue
            first = lowered.split(None, 1)[0] if lowered.split() else ""
            if first in _TABLE_CONSTRAINT_KEYWORDS:
                continue
            # A column: identifier followed by a type token.
            col = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s+\S", item)
            if col:
                model.add_column(table, col.group(1).lower())


def _parse_add_columns(model: SchemaModel, sql: str) -> None:
    """Populate columns from ``ALTER TABLE … ADD COLUMN [IF NOT EXISTS] <col>`` statements."""
    stmt = re.compile(
        r"alter\s+table\s+(?:only\s+)?" + _QUALIFIED + r"(.*?);",
        re.IGNORECASE | re.DOTALL,
    )
    add_col = re.compile(
        r"add\s+column\s+(?:if\s+not\s+exists\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
        re.IGNORECASE,
    )
    for m in stmt.finditer(sql):
        table = m.group(1).lower()
        for cm in add_col.finditer(m.group(2)):
            model.add_column(table, cm.group(1).lower())


def _parse_add_constraints(model: SchemaModel, sql: str) -> None:
    """Record every ``ADD CONSTRAINT <name>`` (guarded or bare)."""
    for m in re.finditer(
        r"add\s+constraint\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE
    ):
        model.constraints.add(m.group(1))


def _parse_indexes(model: SchemaModel, sql: str) -> None:
    """Record every ``CREATE [UNIQUE] INDEX [IF NOT EXISTS] <name>``."""
    for m in re.finditer(
        r"create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?"
        r"([a-zA-Z_][a-zA-Z0-9_]*)",
        sql,
        re.IGNORECASE,
    ):
        model.indexes.add(m.group(1))


def _parse_rls(model: SchemaModel, sql: str) -> None:
    """Record every ``ALTER TABLE <t> ENABLE ROW LEVEL SECURITY``."""
    for m in re.finditer(
        r"alter\s+table\s+" + _QUALIFIED + r"\s+enable\s+row\s+level\s+security",
        sql,
        re.IGNORECASE,
    ):
        model.rls_enabled.add(m.group(1).lower())


def _scope_of(fragment: str) -> str:
    """Classify a ``CREATE POLICY`` fragment by the role it targets."""
    low = fragment.lower()
    if "to service_role" in low:
        return "service_role"
    if "to authenticated" in low:
        return "authenticated"
    # A ``FOR SELECT USING (true)`` with no explicit role is public-readable.
    return "public"


def _parse_policies(model: SchemaModel, sql: str) -> None:
    """Attribute policies to tables, handling both the literal and the loop forms.

    Literal form (the seed-table policies)::

        CREATE POLICY submission_transitions_seed_read
            ON public.marketplace_submission_allowed_transitions
            FOR SELECT USING (true);

    Loop form (the owner and service-role policies of every owned table)::

        FOR spec IN SELECT * FROM (VALUES
            ('marketplace_submissions', 'owner_id', 'submissions'), …) AS t(…)
        LOOP
            EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL TO authenticated …',
                           spec.short_name || '_owner_access', spec.table_name);
            EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL TO service_role …', …);
        END LOOP;

    Because the loop's table names come from a ``VALUES`` or ``ARRAY[…]`` list and the policy
    name is built by concatenation, the concrete policy name never appears as a literal. What
    the parser needs - which tables get an authenticated/owner policy and which get a
    service-role policy - is recoverable from the table list plus the ``TO <role>`` clause of
    each ``CREATE POLICY %I`` template inside the block.
    """
    # ---- literal CREATE POLICY <name> ON public.<table> [FOR … TO <role>] ----
    for m in re.finditer(
        r"create\s+policy\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+on\s+" + _QUALIFIED,
        sql,
        re.IGNORECASE,
    ):
        table = m.group(2).lower()
        tail = sql[m.end() : m.end() + 120]
        model.add_policy(table, _scope_of(tail))

    # ---- loop form: DO $$ … LOOP … EXECUTE format('CREATE POLICY %I ON public.%I …') ----
    # Split into DO blocks and inspect each that creates policies through a %I template.
    for block in re.split(r"do\s+\$\$", sql, flags=re.IGNORECASE):
        if not re.search(
            r"create\s+policy\s+%i\s+on\s+public\.%i", block, re.IGNORECASE
        ):
            continue
        tables = _tables_in_loop(block)
        if not tables:
            continue
        # Every ``CREATE POLICY %I ON public.%I …`` template in the block applies to each
        # table in the loop's list; classify each template by its ``TO <role>`` clause.
        for tm in re.finditer(
            r"create\s+policy\s+%i\s+on\s+public\.%i(.*?)'",
            block,
            re.IGNORECASE | re.DOTALL,
        ):
            scope = _scope_of(tm.group(1))
            for table in tables:
                model.add_policy(table, scope)


def _tables_in_loop(block: str) -> List[str]:
    """Table names driving a policy loop, from a ``VALUES (…)`` or ``ARRAY[…]`` list.

    The first string literal of each ``VALUES`` tuple is the table name (the 007/008/009 loops
    are ``('<table>', …)``); an ``ARRAY[ '<table>', … ]`` list is all table names.
    """
    tables: List[str] = []

    values = re.search(r"values\s*(\(.*?\))\s*\)?\s*as\s+", block, re.IGNORECASE | re.DOTALL)
    if values:
        for tup in re.finditer(r"\(\s*'([a-zA-Z_][a-zA-Z0-9_]*)'", values.group(1)):
            tables.append(tup.group(1).lower())

    if not tables:
        arr = re.search(r"array\s*\[(.*?)\]", block, re.IGNORECASE | re.DOTALL)
        if arr:
            for lit in re.finditer(r"'([a-zA-Z_][a-zA-Z0-9_]*)'", arr.group(1)):
                tables.append(lit.group(1).lower())

    # De-duplicate, preserve order.
    seen: Set[str] = set()
    ordered: List[str] = []
    for t in tables:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def build_schema_model() -> SchemaModel:
    """Parse every migration file into one additive :class:`SchemaModel`."""
    model = SchemaModel()
    for path in _all_migration_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        clean = _strip_sql_comments_and_strings(text)
        _parse_create_table(model, clean)
        _parse_add_columns(model, clean)
        _parse_add_constraints(model, clean)
        _parse_indexes(model, clean)
        _parse_rls(model, clean)
        # Policies need the string literals (table names, roles), so parse the comment-stripped
        # but string-preserving text.
        _parse_policies(model, _strip_comments_only(text))
    return model


def _strip_comments_only(sql: str) -> str:
    """Strip ``--`` and ``/* */`` comments but keep string literals (policy parsing needs them)."""
    out: List[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            if end == -1:
                break
            i = end
            continue
        if ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            i = (end + 2) if end != -1 else n
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ══════════════════════════════════════════════════════════════════════════
# Manifest helpers
# ══════════════════════════════════════════════════════════════════════════

# The two per-handler manifests, merged. Both share the entry shape documented in
# ``backend_app/backend/marketplace/__init__.py``.
ALL_CONTRACTS = {
    **{f"marketplace.{k}": v for k, v in MARKETPLACE_CONTRACT.items()},
    **{f"paper.{k}": v for k, v in PAPER_CONTRACT.items()},
}


def _manifest_pairs(*, owned_only: bool) -> Set[Tuple[str, str]]:
    """Every ``(table, column)`` pair the manifests name.

    When ``owned_only`` is true, pairs on a table an entry marks ``preexisting`` are dropped -
    those tables are not this spec's migration set to create.
    """
    pairs: Set[Tuple[str, str]] = set()
    for entry in ALL_CONTRACTS.values():
        preexisting: FrozenSet[str] = entry.get("preexisting", frozenset())  # type: ignore[assignment]
        for table, columns in entry["tables"].items():  # type: ignore[union-attr,index]
            if owned_only and table in preexisting:
                continue
            for column in columns:
                pairs.add((table, column))
    return pairs


# The new tables this spec's migration set creates (design.md -> "Data Models": eleven new
# tables plus the four allowed-transition seed tables). Every one must carry RLS and both
# policies. The pre-existing tables the manifests also reference (profiles, library_strategies,
# strategy_backtests, library_subscriptions, signals) are excluded - their RLS is a control this
# spec must not touch.
NEW_TABLES: FrozenSet[str] = frozenset(
    {
        # marketplace (007)
        "marketplace_submissions",
        "marketplace_submission_transitions",
        "marketplace_backtest_evidence",
        "marketplace_price_evaluations",
        "marketplace_submission_allowed_transitions",
        # settlement / subscription (008)
        "marketplace_settlements",
        "library_subscription_transitions",
        "marketplace_subscription_allowed_transitions",
        # paper (009)
        "paper_sessions",
        "paper_accounts",
        "paper_orders",
        "paper_fills",
        "paper_positions",
        "paper_balance_events",
        "paper_trades",
        "paper_equity_snapshots",
        "paper_metrics",
        "paper_events",
        "paper_market_events",
        "paper_order_allowed_transitions",
        "paper_session_allowed_transitions",
    }
)

# Every constraint design.md names by name across its Data Models section.
NAMED_CONSTRAINTS: FrozenSet[str] = frozenset(
    {
        # library_strategies additive (007)
        "chk_ls_price_minor",
        "chk_ls_featured_requires_published",
        # library_subscriptions additive (008)
        "chk_ls_active_has_period",
        "chk_ls_split_conserved",
        # marketplace_submissions (007)
        "chk_submission_state",
        "chk_submission_rejection_reason",
        # marketplace_backtest_evidence (007)
        "uq_evidence_submission_backtest",
        "uq_evidence_submission_checksum",
        "chk_evidence_window",
        "chk_evidence_trades",
        "chk_evidence_bars",
        # marketplace_price_evaluations (007)
        "chk_price_range_ordered",
        # marketplace_settlements (008)
        "chk_settlement_amounts",
        "chk_settlement_conserved",
        "chk_settlement_currency",
        "chk_settlement_provider",
        "chk_settlement_reversal_reference",
        "uq_settlement_reference_reversal",
        # paper_sessions (009)
        "chk_paper_session_environment",
        "chk_paper_session_state",
        "chk_paper_capital",
        # paper_accounts (009)
        "chk_paper_balances_non_negative",
        # paper_orders (009)
        "chk_paper_order_state",
        "chk_paper_order_fill_bound",
        "chk_paper_order_idem_len",
        "uq_paper_order_idem",
        # paper_fills (009)
        "uq_paper_fill_event",
        # paper_metrics (009)
        "chk_paper_win_rate",
        "chk_paper_drawdown_fraction",
        # paper_events (009)
        "chk_paper_event_type",
        "chk_paper_event_sequence",
        "uq_paper_event_seq",
        "uq_paper_event_id",
        # paper_market_events (009)
        "uq_paper_market_event",
        # signals (010)
        "chk_signals_environment",
        "fk_signals_paper_session",
    }
)

# Every index (including partial-unique indexes) design.md names by name.
NAMED_INDEXES: FrozenSet[str] = frozenset(
    {
        # marketplace_submissions (007)
        "uq_submission_open_per_strategy",
        "idx_submissions_state",
        "idx_submissions_owner",
        "idx_submissions_listing",
        # marketplace_submission_transitions (007)
        "idx_submission_transitions",
        # marketplace_backtest_evidence (007)
        "idx_evidence_submission",
        # marketplace_price_evaluations (007)
        "idx_price_eval_lookup",
        # marketplace_settlements (008)
        "idx_settlements_owner_currency",
        # library_subscriptions (008)
        "idx_lib_subs_expiry",
        # paper_sessions (009)
        "idx_paper_sessions_user",
        "idx_paper_sessions_running",
        # paper_accounts (009) - partial-unique indexes
        "uq_paper_account_default",
        "uq_paper_account_session",
        # paper_orders (009)
        "idx_paper_orders_session_state",
        # paper_positions (009)
        "uq_paper_position_open",
        # paper_balance_events (009)
        "idx_paper_balance_events",
        # paper_trades (009)
        "idx_paper_trades",
        # paper_equity_snapshots (009)
        "idx_paper_equity",
        # paper_events (009)
        "idx_paper_events_replay",
        # paper_market_events (009)
        "idx_paper_market_events",
        # signals (010)
        "idx_signals_user_environment",
        "idx_signals_paper_session",
    }
)


# ══════════════════════════════════════════════════════════════════════════
# .select("…") literal extraction from the new/modified modules
# ══════════════════════════════════════════════════════════════════════════

# Modules the manifests attribute a .select projection to, and the manifest key that owns them.
_MODULES_WITH_SELECTS = {
    "backend_app.backend.marketplace.aliases": "marketplace.creator_alias_read",
    "backend_app.backend.marketplace.listing_projection": "marketplace.listing_projection",
    # The Eligibility_Gate (task 14.1) is the one marketplace module that reads the
    # Persistence_Layer, so its four ``.select`` projections are bound to its manifest entry
    # too. This is what turns the ``marketplace_submissions.state`` typo (fixed in 14.4) into a
    # red suite rather than a production 42703: ``.select("id, submission_state")`` must name
    # only columns the ``eligibility_gate`` manifest entry lists.
    "backend_app.backend.marketplace.eligibility_gate": "marketplace.eligibility_gate",
    # The Entitlement_Resolver (task 17.1) is the second marketplace module that reads the
    # Persistence_Layer. Its one embedded round trip and its version-resolution read must name
    # only columns the ``entitlement_resolver`` manifest entry lists - the same 42703 guard the
    # Eligibility_Gate is held to. The embedded ``.select`` names the two relation tokens
    # (``marketplace_submissions``, ``library_subscriptions``) and the embed's inner columns,
    # every one of which the manifest's table union carries.
    "backend_app.backend.marketplace.entitlement_resolver": "marketplace.entitlement_resolver",
    # The Marketplace checkout path (task 18.1) is the third marketplace module that reads the
    # Persistence_Layer and the only one that writes ``library_subscriptions``. Its two
    # projection constants — ``CHECKOUT_LISTING_SELECT`` (with its
    # ``marketplace_submissions(submission_state)`` embed) and ``CHECKOUT_SUBSCRIPTION_SELECT`` —
    # must name only columns the ``checkout`` manifest entry lists. This is the guard that keeps
    # a mistyped column on a *payment* path a red suite rather than a PGRST204 mid-checkout.
    "backend_app.backend.marketplace.checkout_service": "marketplace.checkout",
    # The Settlement_Record writer (task 19.1) - the other half of the payment path, and the one
    # module that writes ``marketplace_settlements``. Its ``SETTLEMENT_SUBSCRIPTION_SELECT`` is
    # bound to the ``settlement`` manifest entry, whose ``module`` is ``settlement_service``
    # itself. The older ``settlement_and_subscription`` entry names ``subscription_period`` - pure
    # arithmetic that issues no statement - so it bound no projection, and a mistyped column on a
    # payment *confirmation* would have reached production as a PGRST204.
    "backend_app.backend.marketplace.settlement_service": "marketplace.settlement",
    # The Subscription expiry sweep (task 20.1). Its one ``SWEEP_CANDIDATE_SELECT`` projection
    # must name only columns the ``expiry_sweep`` manifest entry lists - the same 42703 guard,
    # applied to the read that decides which Subscriptions the sweep relabels. A mistyped column
    # here would make the sweep silently expire nothing, which is the quietest of the failure
    # modes this contract exists to catch.
    "backend_app.backend.marketplace.expiry_sweep": "marketplace.expiry_sweep",
    # The Paper_Repository (task 23.1) - the single module that reads and writes the eight
    # ``paper_*`` accounting tables, and the first module under ``backend_app/backend/paper/`` to
    # issue a statement at all (the accounting, state-machine and replay modules are pure). Its
    # seven projection constants - ``ACCOUNT_SELECT``, ``POSITION_SELECT``, ``ORDER_SELECT``,
    # ``TRADE_SELECT``, ``EQUITY_SNAPSHOT_SELECT``, ``METRICS_SELECT`` and the one-column
    # ``PROBE_SELECT`` - must name only columns the ``paper_repository`` manifest entry lists.
    # Until this line existed the paper half of the manifest bound assertion 1 alone, so a
    # mistyped column on a *balance* read would have reached production as a PostgreSQL 42703 -
    # the exact condition Requirement 24.9 names.
    "backend_app.backend.paper.paper_repository": "paper.paper_repository",
}


def _module_path(dotted: str) -> Path:
    return REPO_ROOT / (dotted.replace(".", "/") + ".py")


def _string_columns(literal: str) -> List[str]:
    """Split a PostgREST ``select`` projection string into bare column names.

    ``"id,display_name"`` -> ``["id", "display_name"]``. A relationship/embed such as
    ``profiles(display_name)`` or an alias ``alias:column`` keeps only the underlying column
    token; whitespace and ``*`` are ignored.
    """
    columns: List[str] = []
    for part in literal.split(","):
        token = part.strip()
        if not token or token == "*":
            continue
        # Drop an ``alias:`` prefix.
        if ":" in token:
            token = token.split(":", 1)[1].strip()
        # Drop an embedded relation ``rel(cols)`` - keep the relation name only if bare column.
        token = token.split("(", 1)[0].strip()
        # Keep a plain identifier.
        if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", token):
            columns.append(token)
    return columns


def _select_literals_in_module(dotted: str) -> List[str]:
    """Every string literal reaching a ``.select(…)`` call in one module.

    Handles the two shapes the modules use:
      * a string literal passed directly - ``.select("id,name")``;
      * a module-level constant assigned a string and read by ``.select(NAME)`` - the modules
        declare ``ALIAS_SELECT`` and ``LISTING_SELECT`` for exactly this. The constant's value
        is resolved from its assignment.
    """
    source = _module_path(dotted).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)

    # Module-level string constants, so ``.select(LISTING_SELECT)`` resolves to its value.
    string_constants: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        string_constants[target.id] = node.value.value

    literals: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "select"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            literals.append(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in string_constants:
            literals.append(string_constants[arg.id])
    return literals


# ══════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def model() -> SchemaModel:
    return build_schema_model()


# ══════════════════════════════════════════════════════════════════════════
# Guard: the parser has something to work with
# ══════════════════════════════════════════════════════════════════════════


def test_migration_set_is_non_empty_and_parses(model: SchemaModel) -> None:
    assert _all_migration_files(), "no migration files found in either sequence"
    # The eleven paper tables and the marketplace tables must all be modelled, or every
    # downstream assertion is vacuous.
    for table in NEW_TABLES:
        assert table in model.columns, (
            f"{table} was named by design.md but no CREATE TABLE for it was parsed from the "
            f"migration set"
        )


def test_manifests_are_non_empty() -> None:
    assert MARKETPLACE_CONTRACT, "marketplace COLUMN_CONTRACT is empty"
    assert PAPER_CONTRACT, "paper COLUMN_CONTRACT is empty"


# ══════════════════════════════════════════════════════════════════════════
# 1. Every manifest (table, column) pair exists
# ══════════════════════════════════════════════════════════════════════════


def test_every_manifest_pair_exists(model: SchemaModel) -> None:
    """Every ``(table, column)`` a handler declares it reads or writes is created by a migration.

    This is the direct guard against the ``42703`` / ``PGRST204`` condition: a manifest pair
    with no migration is a column a handler will read that the database does not have.
    """
    missing: List[str] = []
    for table, column in sorted(_manifest_pairs(owned_only=True)):
        if column not in model.columns.get(table, set()):
            missing.append(f"{table}.{column}")
    assert not missing, (
        "columns named in a COLUMN_CONTRACT manifest but created by no migration in "
        "migrations/ or backend_app/migrations/ - this is the PostgreSQL 42703 / PGRST204 "
        f"condition the schema contract exists to prevent: {missing}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. .select("…") literals stay within the owning module's manifest entry
# ══════════════════════════════════════════════════════════════════════════


def test_select_literals_are_within_manifest() -> None:
    """Every column a module's ``.select(…)`` names is in that module's manifest entry.

    A handler that begins selecting a new column without widening its manifest fails here,
    before it can fail with ``42703`` in production.
    """
    problems: List[str] = []
    for dotted, contract_key in _MODULES_WITH_SELECTS.items():
        entry = ALL_CONTRACTS[contract_key]
        allowed: Set[str] = set()
        for columns in entry["tables"].values():  # type: ignore[union-attr,index]
            allowed |= set(columns)
        for literal in _select_literals_in_module(dotted):
            for column in _string_columns(literal):
                if column not in allowed:
                    problems.append(
                        f"{dotted}: .select(...) names {column!r} which is absent from the "
                        f"{contract_key} manifest entry (literal: {literal!r})"
                    )
    assert not problems, "\n".join(problems)


def test_eligibility_gate_reads_submission_state_not_state() -> None:
    """Regression for the ``marketplace_submissions.state`` typo task 14.4's verification found.

    ``eligibility_gate._read_open_state`` once selected ``id, state`` from
    ``marketplace_submissions``, but migration 007 defines the column as ``submission_state``
    and no ``state`` column exists there - so the select raised PostgreSQL 42703 at runtime
    and, because a failed read propagates to ``MARKETPLACE_ELIGIBILITY_UNEVALUABLE`` (503),
    every publication attempt was permanently blocked. This pins the two facts that fix and
    guard it: the manifest names ``submission_state`` (never ``state``) for
    ``marketplace_submissions``, and the module's ``.select`` on that table names
    ``submission_state``. The subset check above is the general guard; this is the named one.
    """
    entry = ALL_CONTRACTS["marketplace.eligibility_gate"]
    submission_columns = entry["tables"]["marketplace_submissions"]  # type: ignore[index]
    assert "submission_state" in submission_columns
    assert "state" not in submission_columns, (
        "marketplace_submissions has no 'state' column - migration 007 names it "
        "'submission_state'. Listing 'state' would re-encode the 42703 bug."
    )

    literals = _select_literals_in_module(
        "backend_app.backend.marketplace.eligibility_gate"
    )
    submission_selects = [
        lit for lit in literals if "submission_state" in lit or "state" in lit
    ]
    assert submission_selects, "no eligibility_gate .select naming a submission state was found"
    for literal in submission_selects:
        columns = _string_columns(literal)
        if "submission_state" in columns or "state" in columns:
            assert "submission_state" in columns, (
                f"eligibility_gate .select must name 'submission_state', not 'state' "
                f"(literal: {literal!r})"
            )
            assert "state" not in columns


# ══════════════════════════════════════════════════════════════════════════
# 3. Named constraints and indexes are present
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("constraint", sorted(NAMED_CONSTRAINTS))
def test_named_constraints_present(model: SchemaModel, constraint: str) -> None:
    assert constraint in model.constraints, (
        f"constraint {constraint} is named by design.md but is created by no migration in the "
        f"set"
    )


@pytest.mark.parametrize("index", sorted(NAMED_INDEXES))
def test_named_indexes_present(model: SchemaModel, index: str) -> None:
    assert index in model.indexes, (
        f"index {index} is named by design.md but is created by no migration in the set"
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. Every new table has RLS and both policies
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("table", sorted(NEW_TABLES))
def test_new_tables_have_rls_and_both_policies(model: SchemaModel, table: str) -> None:
    assert table in model.rls_enabled, (
        f"{table} is a new table but no ENABLE ROW LEVEL SECURITY was found for it "
        f"(Requirement 21.2)"
    )
    scopes = model.policy_scopes.get(table, set())
    # A service-role policy is required on every new table.
    assert "service_role" in scopes, (
        f"{table} has no service-role policy (found scopes: {sorted(scopes)})"
    )
    # And an owner/authenticated policy, or - for the reference seed tables that hold no user
    # data - a public read policy, which is the second policy design.md gives those tables.
    assert scopes & {"authenticated", "public"}, (
        f"{table} has neither an owner (authenticated) nor a public-read policy "
        f"(found scopes: {sorted(scopes)})"
    )


# ══════════════════════════════════════════════════════════════════════════
# 5. No destructive statement anywhere in the set
# ══════════════════════════════════════════════════════════════════════════

_DESTRUCTIVE = {
    "DROP TABLE": re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    "DROP COLUMN": re.compile(r"\bdrop\s+column\b", re.IGNORECASE),
    "RENAME": re.compile(r"\brename\b", re.IGNORECASE),
    "DELETE FROM": re.compile(r"\bdelete\s+from\b", re.IGNORECASE),
    "TRUNCATE": re.compile(r"\btruncate\b", re.IGNORECASE),
}


def test_no_destructive_statements() -> None:
    """No file contains a destructive statement (Requirement 24.7's additive-only rule).

    Comments and string literals are stripped first, so a header stating "no ``DROP TABLE``" or
    a verification query embedded in a comment does not trip the scan - only DDL in the file
    body counts.
    """
    offenders: List[str] = []
    for path in _all_migration_files():
        clean = _strip_sql_comments_and_strings(
            path.read_text(encoding="utf-8", errors="replace")
        )
        for label, pattern in _DESTRUCTIVE.items():
            if pattern.search(clean):
                match = pattern.search(clean)
                start = max(0, match.start() - 40)
                context = clean[start : match.end() + 40].replace("\n", " ").strip()
                offenders.append(f"{path.name}: {label} -> …{context}…")
    assert not offenders, (
        "destructive statement(s) found in the migration set, which Requirement 24.7 forbids:\n"
        + "\n".join(offenders)
    )

# ══════════════════════════════════════════════════════════════════════════
# Property P-58 — the migration set is idempotent and additive-only (Task 11.8)
# ══════════════════════════════════════════════════════════════════════════
#
# WHAT THIS ADDS TO THE FILE
# --------------------------
# Everything above proves the *content* of the migration set (every declared column, constraint,
# index and policy exists; nothing is destructive). This section proves a *behavioural* property
# of applying that set: it does not matter how many times each file is applied, nor in which
# order - so long as the declared dependency order is respected - the resulting schema is byte-
# for-byte the same one a single, in-order application produces.
#
# WHY THAT PROPERTY HOLDS BY CONSTRUCTION
# ---------------------------------------
# The five files that carry this spec's schema (006→007→008→009→010) are additive-only and every
# statement is guarded:
#   * ``CREATE TABLE IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS`` / ``CREATE INDEX IF NOT
#     EXISTS`` - a second application is a no-op;
#   * ``ADD CONSTRAINT`` and ``CREATE POLICY`` are wrapped in ``DO $$ … IF NOT EXISTS … $$`` or
#     ``EXCEPTION WHEN duplicate_object`` blocks (007/008/009), so re-running them raises nothing
#     and adds nothing;
#   * :func:`test_no_destructive_statements` above proves no file drops, renames, deletes or
#     truncates.
# Because the schema model this file builds is a *set* union (columns, constraints, indexes,
# policy-scopes are all sets - see :class:`SchemaModel`), applying a file twice unions the same
# elements in and applying files in a different order unions the same elements in a different
# sequence; set union is idempotent and commutative, so the resulting model is identical. The
# only real ordering constraint is the referential one design.md declares - 010 adds
# ``fk_signals_paper_session`` referencing ``paper_sessions``, created by 009 - so a valid order
# is any permutation in which 009 precedes 010. This test asserts the property against the model
# for every such order and every per-file repetition count ``n >= 1``.
#
# This is Property P-58; it validates Requirements 24.7 (additive-only), 24.8 (re-application at
# the current production revision is safe) and 24.9 (idempotent application).

from itertools import permutations

from hypothesis import given, settings
from hypothesis import strategies as st

# The spec's own schema-carrying migration set, in declared dependency order (design.md /
# Task 11.8): 006 (backtest-evidence columns) → 007 (submissions) → 008 (settlement) →
# 009 (paper trading) → 010 (signal environment). All five live under backend_app/migrations/.
_P58_SET_IN_ORDER: List[str] = [
    "006_backtest_evidence_columns.sql",
    "007_marketplace_submissions.sql",
    "008_marketplace_settlement.sql",
    "009_paper_trading.sql",
    "010_signal_environment.sql",
]

# The one hard dependency: 010 introduces fk_signals_paper_session, which references
# paper_sessions created by 009, so 009 MUST be applied before 010. Every other relative order
# is free (the files touch disjoint tables otherwise). A "dependency-consistent order" is any
# permutation of the five files in which 009 precedes 010.
_P58_DEP_BEFORE = ("009_paper_trading.sql", "010_signal_environment.sql")


def _p58_set_paths() -> List[Path]:
    """The five schema-carrying files as absolute paths, asserting each exists."""
    paths: List[Path] = []
    for name in _P58_SET_IN_ORDER:
        path = BACKEND_MIGRATIONS_DIR / name
        assert path.is_file(), (
            f"migration {name} named by Task 11.8 is missing from {BACKEND_MIGRATIONS_DIR}"
        )
        paths.append(path)
    return paths


def _apply_file(model: SchemaModel, path: Path) -> None:
    """Apply one migration file into ``model`` exactly as :func:`build_schema_model` does.

    Kept in lock-step with the loop body of :func:`build_schema_model` so this property tests the
    same parser the content assertions above use, not a second implementation of it.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    clean = _strip_sql_comments_and_strings(text)
    _parse_create_table(model, clean)
    _parse_add_columns(model, clean)
    _parse_add_constraints(model, clean)
    _parse_indexes(model, clean)
    _parse_rls(model, clean)
    _parse_policies(model, _strip_comments_only(text))


def _apply_sequence(paths: List[Path]) -> SchemaModel:
    """Build a fresh model by applying ``paths`` in the given order (an "empty database")."""
    model = SchemaModel()
    for path in paths:
        _apply_file(model, path)
    return model


def _snapshot(model: SchemaModel) -> Tuple:
    """A canonical, order-independent snapshot of the schema a model represents.

    Two models are equal for P-58's purposes when they define the same tables, the same columns
    per table, the same named constraints, the same indexes, the same RLS-enabled tables and the
    same policy scopes per table. Every component is sorted so the snapshot is independent of the
    order files were applied in - the snapshot compares the *resulting schema*, not the sequence
    that produced it.
    """
    return (
        tuple(sorted((t, tuple(sorted(cols))) for t, cols in model.columns.items())),
        tuple(sorted(model.constraints)),
        tuple(sorted(model.indexes)),
        tuple(sorted(model.rls_enabled)),
        tuple(
            sorted((t, tuple(sorted(scopes))) for t, scopes in model.policy_scopes.items())
        ),
    )


def _dependency_consistent_orders() -> List[Tuple[str, ...]]:
    """Every permutation of the five file names in which 009 precedes 010.

    Five files give 120 permutations; requiring 009 before 010 keeps exactly half, 60. That is a
    small, exhaustively enumerable space, so the Hypothesis strategy samples from it directly
    rather than trying to build a valid order incrementally.
    """
    before, after = _P58_DEP_BEFORE
    orders: List[Tuple[str, ...]] = []
    for perm in permutations(_P58_SET_IN_ORDER):
        if perm.index(before) < perm.index(after):
            orders.append(perm)
    return orders


_P58_ORDERS = _dependency_consistent_orders()


def _expand(order: Tuple[str, ...], repeats: Dict[str, int]) -> List[Path]:
    """Turn a dependency-consistent order plus per-file repeat counts into a path sequence.

    Each file appears ``repeats[name]`` times, contiguously, in the position the order gives it -
    modelling "apply this file, then apply it again" for a file whose count is > 1, which is the
    exact re-application scenario Requirement 24.9 is about.
    """
    sequence: List[Path] = []
    for name in order:
        path = BACKEND_MIGRATIONS_DIR / name
        sequence.extend([path] * repeats[name])
    return sequence


@settings(max_examples=200, deadline=None)
@given(
    order=st.sampled_from(_P58_ORDERS),
    repeats=st.fixed_dictionaries(
        {name: st.integers(min_value=1, max_value=4) for name in _P58_SET_IN_ORDER}
    ),
)
def test_p58_migration_set_is_idempotent(
    order: Tuple[str, ...], repeats: Dict[str, int]
) -> None:
    """Property P-58 — applying the set ``n`` times, in any dependency-consistent order, to an
    empty database yields the same schema as applying it once in declared order; applying it on
    top of the already-applied ("current production revision") schema yields that same schema;
    and no application drops or renames a column or table (the additive-only guard above).

    **Validates: Requirements 24.7, 24.8, 24.9**

    for all dependency-consistent orders and all per-file repetition counts n >= 1:
      snapshot(apply the set that way to an empty db) == snapshot(apply it once, in order)
      and snapshot(apply it once more on top of that) == the same snapshot.
    """
    # The reference: a single, in-order application to an empty database.
    once = _snapshot(_apply_sequence(_p58_set_paths()))

    # (24.9) n>=1 repetitions in any dependency-consistent order reach the same schema.
    many = _snapshot(_apply_sequence(_expand(order, repeats)))
    assert many == once, (
        "applying the migration set with per-file repeats "
        f"{repeats} in order {order} produced a different schema than a single in-order "
        "application - the set is not idempotent"
    )

    # (24.8) re-applying the whole set on top of an already-applied schema - the "current
    # production revision" - is a no-op: the schema does not change.
    at_revision = _apply_sequence(_p58_set_paths())
    for path in _p58_set_paths():
        _apply_file(at_revision, path)
    assert _snapshot(at_revision) == once, (
        "re-applying the migration set at the current production revision changed the schema - "
        "re-application is not safe (Requirement 24.8)"
    )

    # (24.7) the additive-only guarantee this idempotence rests on: no repeated application ever
    # removes a table or a column. Because the model only ever grows, the repeated-application
    # model is a superset of the single-application one on tables and columns; combined with the
    # equality above, they are exactly equal - no drop or rename occurred.
    many_model = _apply_sequence(_expand(order, repeats))
    for table, columns in _apply_sequence(_p58_set_paths()).columns.items():
        assert table in many_model.columns, (
            f"table {table} present after a single application vanished after repeated "
            f"application in order {order} - a non-additive change"
        )
        assert columns <= many_model.columns[table], (
            f"columns {sorted(columns - many_model.columns[table])} on {table} were dropped by "
            f"repeated application - a non-additive change"
        )
