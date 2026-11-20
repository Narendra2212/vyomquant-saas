"""Every table a browser role can SELECT, measured by reading someone else's row.

WHY THIS FILE EXISTS SEPARATELY FROM test_tenant_isolation_rls_postgres.py
--------------------------------------------------------------------------
That file picks representative tables and exercises them in depth: six tenants, keyed
lookups, UPDATE/DELETE/INSERT attempts, the `app.current_tenant_id` family. Depth, on a
sample.

This file is the opposite shape -- one question, asked of EVERY table:

    if I seed a row owned by customer A, can customer B read it?

Breadth is what catches the defects that a sample misses, and it caught two:

  * `library_ratings` had `relrowsecurity = true` and a SELECT policy of `USING (true)`.
    Enabled-with-a-constant-TRUE-predicate filters nothing, so the table read as if RLS
    were off while every inventory query that checks `relrowsecurity` called it secured.
  * `referral_codes` carried TWO overlapping permissive SELECT policies.
    `referral_system_redesign.sql` added the correct `..._select_own`, and
    `006_reconcile_production_database.sql`'s older `referral_codes_select` --
    `USING (user_id = auth.uid() OR is_active = true)` -- was never dropped. Permissive
    policies are OR-ed, so the broad one still decided the outcome: any logged-in
    customer could enumerate every active referral code with the `user_id` owning it.

Neither is visible in a policy inventory that only asks whether RLS is on.

WHAT "NOT PROVEN" MEANS, AND WHY IT MUST BE ZERO
------------------------------------------------
The sweep seeds a real row before reading, because an empty table is silent rather than
safe -- a role reads zero rows from it either way. A table whose NOT NULL columns, CHECK
constraints or foreign keys could not be satisfied is reported NOT PROVEN, never as a
pass. The assertions below require that count to be zero, so a newly added table with an
unsatisfiable seed fails the run instead of quietly dropping out of coverage.

Everything happens inside a savepoint that is rolled back, so the sweep leaves no rows.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "needs a real PostgreSQL instance: RLS, roles and policies do not exist in "
        "SQLite, so this measurement is meaningless there. Start one with "
        "staging/bootstrap_pg.py and set AERORA_TEST_DATABASE_URL."
    ),
)

#: Run from the repository root so the script's relative paths resolve.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SWEEP = os.path.join(_REPO_ROOT, "staging", "anon_read_sweep.py")


def _sweep(role: str) -> str:
    """Run the sweep for `role` and return its output, failing loudly on a crash.

    Invoked as a subprocess rather than imported because `staging/` is not a package and
    the script is also the artefact the launch readiness report cites by name. One
    implementation, two callers -- a copy here would be free to drift.
    """
    proc = subprocess.run(
        [sys.executable, _SWEEP, role],
        cwd=_REPO_ROOT,
        env={**os.environ, "AERORA_TEST_DATABASE_URL": TEST_DATABASE_URL},
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode in (0, 1), (
        f"the sweep itself failed for role {role} (exit {proc.returncode}); it did not "
        f"measure anything:\n{proc.stdout}\n{proc.stderr}"
    )
    return proc.stdout


def _summary(output: str) -> dict[str, int]:
    line = next(l for l in output.splitlines() if l.startswith("SUMMARY:"))
    # "SUMMARY: 0 leaked, 69 proven blocked, 10 intentionally public, 0 not proven"
    parts = line.removeprefix("SUMMARY:").split(",")
    out: dict[str, int] = {}
    for part in parts:
        tokens = part.split()
        out[" ".join(tokens[1:])] = int(tokens[0])
    return out


@pytest.fixture(scope="module")
def anon_sweep():
    return _sweep("anon")


@pytest.fixture(scope="module")
def authenticated_sweep():
    return _sweep("authenticated")


def test_anon_cannot_read_any_customer_row(anon_sweep):
    """`anon` is the browser key with no login. It must see no customer data at all.

    62 policies in this schema are granted to role `public`, which INCLUDES `anon`, so a
    query filtering on `roles = '{anon}'` reports zero anon-reachable policies and looks
    reassuring. The predicates are `auth.uid() = user_id`; with no JWT `auth.uid()` is
    NULL and the comparison is NULL rather than true. This measures that rather than
    trusting it.
    """
    summary = _summary(anon_sweep)
    assert summary["leaked"] == 0, (
        "anon read rows belonging to a customer:\n"
        + "\n".join(
            l for l in anon_sweep.splitlines() if "anon saw" in l and "product" not in l
        )
    )


def test_authenticated_cannot_read_another_customers_row(authenticated_sweep):
    """The realistic attack: a registered customer reading a different customer's rows.

    Stronger than the `anon` case, because this reader has a valid JWT, so the policy
    predicates evaluate to true for their own rows instead of NULL for everything -- the
    policy has to actually discriminate.
    """
    summary = _summary(authenticated_sweep)
    assert summary["leaked"] == 0, (
        "a logged-in customer read another customer's rows:\n"
        + "\n".join(
            l
            for l in authenticated_sweep.splitlines()
            if "authenticated saw" in l and "product" not in l
        )
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_every_readable_table_was_actually_exercised(role, anon_sweep, authenticated_sweep):
    """No table may drop out of coverage by being unseedable.

    This is the assertion that keeps the two tests above honest. Without it, a table
    whose seed fails is reported NOT PROVEN, contributes nothing, and the leak count
    stays at zero -- a green run that measured less than the last one.
    """
    output = anon_sweep if role == "anon" else authenticated_sweep
    summary = _summary(output)
    unproven = summary["not proven"]
    assert unproven == 0, (
        f"{unproven} table(s) could not be seeded for role {role}, so isolation was "
        f"NOT measured for them. Add the missing CHECK value to CHECK_OVERRIDES in "
        f"staging/anon_read_sweep.py:\n"
        + "\n".join(
            l
            for l in output.splitlines()
            if "violates" in l or "does not exist" in l or "invalid input" in l
        )
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_the_sweep_covered_a_meaningful_number_of_tables(role, anon_sweep, authenticated_sweep):
    """A floor on coverage, so a broken GRANT query cannot silently empty the sweep.

    If `information_schema.role_table_grants` returned nothing -- a typo in the role
    name, say -- every assertion above would pass against zero tables. The floors are
    set below the counts measured on the provisioned schema (49 tables for `anon`,
    79 for `authenticated`) so that revoking a grant is allowed but gutting the
    measurement is not.
    """
    output = anon_sweep if role == "anon" else authenticated_sweep
    summary = _summary(output)
    covered = summary["proven blocked"] + summary["intentionally public"]
    floor = 30 if role == "anon" else 50
    assert covered >= floor, (
        f"the sweep only covered {covered} table(s) for role {role}, below the floor of "
        f"{floor}. Either a large number of grants were revoked -- which is fine, but "
        f"lower this floor deliberately -- or the sweep stopped finding tables."
    )
