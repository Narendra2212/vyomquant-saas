"""DB-ALM-02 -- every identity column the backstop judges must be converted by someone.

THE DEFECT THIS GUARDS, AND IT WAS LIVE
----------------------------------------
``migrations/hardening/998_bug049_identity_types.sql`` has two halves that can disagree:

*   SECTIONS 1-3 iterate a HARDCODED list of twelve table names with a HARDCODED column
    name of ``user_id``, and
*   SECTION 4 is a whole-schema backstop that RAISES if ANY ``public`` column named
    ``user_id`` or ``tenant_id`` is not ``uuid`` -- deliberately, so that "a table added
    later is not covered and nobody finds out" cannot happen.

Those two can drift apart, and on the real production database they had. Running 998
there converted all eleven of its remaining targets, restored 22 policies and added
eleven ``fk_bug049_*`` keys -- and then its own backstop aborted the whole transaction
naming five columns that SECTIONS 1-3 never touch: ``orders.tenant_id``,
``positions.tenant_id``, ``fills.tenant_id`` (BUG-033's targets, whose alembic revision
has never run against production) and ``strategy_backtests.user_id``,
``strategy_research_reports.user_id`` (owned by neither BUG-033 nor BUG-049). So 998
could not be applied to production at all, and nothing said so.

It was invisible in CI because a database provisioned purely from repository source
creates all five as ``uuid`` to begin with, so the backstop had nothing to catch. The
gap only appears against an older, drifted schema -- which is exactly the database that
matters.

WHY THIS TEST IS SHAPED THE WAY IT IS
--------------------------------------
It does NOT assert that 998's target list contains particular strings. A list-equality
check against a hardcoded expectation would have to be updated by the same person who
adds the uncovered table, which is the failure mode it is meant to catch.

Instead it asks the DATABASE which identity columns exist, and asserts every one of them
is claimed by one of the two migrations' target lists, parsed out of the files
themselves. Add a table with a ``user_id`` and convert it nowhere, and this fails by
name. That is a property of the real schema, not of a source string.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=("requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. A SKIP "
            "here is NOT a pass -- the coverage gap this file guards was itself invisible "
            "on a database where it could not bite."),
)

_ROOT = Path(__file__).resolve().parents[1]
_SQL_998 = _ROOT / "migrations" / "hardening" / "998_bug049_identity_types.sql"
_SQL_055 = (_ROOT / "backend_app" / "migrations"
            / "055_dbalm02_identity_types_uncovered.sql")

# Columns a migration is allowed to leave alone because they are not customer identities
# despite the name. Empty today, and kept here so that adding an exemption is a visible,
# reviewable edit rather than a silent widening of the rule.
_EXEMPT: set[tuple[str, str]] = set()


def _targets_998() -> set[tuple[str, str]]:
    """998's SECTIONS 1-3 convert `user_id` on the tables in its `targets` array."""
    text = _SQL_998.read_text(encoding="utf-8")
    block = re.search(r"targets\s+text\[\]\s*:=\s*ARRAY\[(.*?)\]", text, re.S)
    assert block, "could not find 998's targets array; the file's shape changed"
    names = re.findall(r"'([a-z_]+)'", block.group(1))
    assert names, "998's targets array parsed empty"
    return {(n, "user_id") for n in names}


def _targets_055() -> set[tuple[str, str]]:
    """055 carries (table, column) pairs, so both identity column names appear."""
    text = _SQL_055.read_text(encoding="utf-8")
    block = re.search(r"specs\s+text\[\]\[\]\s*:=\s*ARRAY\[(.*?)\n\s*\];", text, re.S)
    assert block, "could not find 055's specs array; the file's shape changed"
    pairs = re.findall(r"ARRAY\[\s*'([a-z_]+)'\s*,\s*'(user_id|tenant_id)'", block.group(1))
    assert pairs, "055's specs array parsed empty"
    return set(pairs)


def _identity_columns() -> dict[tuple[str, str], str]:
    conn = psycopg2.connect(_PG_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name, column_name, data_type
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND column_name IN ('user_id', 'tenant_id')
            """)
            return {(r[0], r[1]): r[2] for r in cur.fetchall()}
    finally:
        conn.close()


@pg_only
def test_any_identity_column_that_is_not_uuid_is_claimed_by_a_migration():
    """The invariant that was actually violated.

    Note what this does NOT require. Most identity columns in this schema are created
    ``uuid`` by their own ``CREATE TABLE`` and need no conversion at all -- measured on a
    database provisioned from repository source, 43 of them are unclaimed by either
    migration and already ``uuid``, and the backstop is perfectly happy with every one.
    Demanding that all of them appear in a conversion list would be noise, and would
    pressure the next person to add entries that convert nothing.

    What matters is the asymmetry: the backstop RAISES on a non-uuid identity column, so
    a non-uuid column with no migration claiming it is an abort with no owner. That is
    the production state this finding was about -- five such columns, 998 failing
    outright, and nothing reporting why.
    """
    claimed = _targets_998() | _targets_055() | _EXEMPT
    divergent = {k for k, v in _identity_columns().items() if v != "uuid"}
    orphaned = sorted(divergent - claimed)
    assert not orphaned, (
        "these public identity columns are NOT uuid and NO migration converts them: "
        f"{orphaned}.\n"
        "998's SECTION 4 backstop sweeps the whole schema and RAISES on any identity "
        "column that is not uuid, so leaving one unconverted makes 998 -- and therefore "
        "provisioning, and therefore any production apply -- fail outright, naming the "
        "column but offering nothing that fixes it. Either add it to 055's specs array "
        "(for a real customer/tenant identity) or to _EXEMPT in this file with a reason "
        "(for a column that merely shares the name)."
    )


@pg_only
def test_every_identity_column_is_actually_uuid_on_this_database():
    """The end state the backstop demands, asserted independently of the backstop so a
    change to 998 cannot quietly weaken it."""
    not_uuid = sorted(k for k, v in _identity_columns().items() if v != "uuid")
    assert not not_uuid, (
        f"identity columns that are not uuid: {not_uuid}. These cannot reference "
        "public.profiles(id), so nothing stops a row naming a customer who does not "
        "exist -- or keeps an encrypted exchange credential outliving its owner."
    )


@pg_only
def test_bug033s_three_tenant_foreign_keys_exist():
    """055's reason for existing. DB-ALM-02 recorded these three as blocked on a direct
    Postgres session; they are the keys orders/positions/fills could not declare while
    their tenant_id was still varchar."""
    conn = psycopg2.connect(_PG_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT conname FROM pg_constraint
                 WHERE conname IN ('fk_fills_tenant_id', 'fk_orders_tenant_id',
                                   'fk_positions_tenant_id')
            """)
            found = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    missing = sorted({"fk_fills_tenant_id", "fk_orders_tenant_id",
                      "fk_positions_tenant_id"} - found)
    assert not missing, f"BUG-033 tenant_id foreign key(s) absent: {missing}"


def test_055_and_998_do_not_both_claim_the_same_column():
    """No real database needed. Two migrations converting one column would make the
    second a no-op at best, and makes ownership ambiguous for the next reader."""
    overlap = sorted(_targets_998() & _targets_055())
    assert not overlap, (
        f"998 and 055 both claim {overlap}. 998 is BUG-049's from-empty twin and is "
        "held in lockstep with alembic d4e5f6a7b8c9 by "
        "tests/test_bug049_sql_and_alembic_agree.py; 055 owns only what 998 leaves."
    )


def test_055_declares_the_tenant_columns_998_cannot():
    """Pins the specific division of labour, so a future edit that moves tenant_id into
    998 has to confront test_bug049_sql_and_alembic_agree.py rather than drift past it."""
    t055 = _targets_055()
    for expected in (("orders", "tenant_id"), ("positions", "tenant_id"),
                     ("fills", "tenant_id")):
        assert expected in t055, (
            f"{expected} is BUG-033's target and must stay in 055, not 998: 998's "
            "target list is compared against alembic d4e5f6a7b8c9 (BUG-049) and would "
            "fail that agreement check if widened with BUG-033's columns."
        )
