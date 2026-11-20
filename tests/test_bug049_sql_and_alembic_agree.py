"""The BUG-049 conversion exists twice. These tests stop the two copies diverging.

WHY THERE ARE TWO
-----------------
    backend_app/alembic/versions/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py
    migrations/hardening/998_bug049_identity_types.sql

The alembic revision is the right mechanism for a database that already EXISTS -- it is
what will run against production. It cannot do the job on the from-empty provisioning
path, and that was measured rather than assumed: `scripts/provision_database.py` runs
`alembic upgrade head` at STEP 0c, BEFORE the numbered SQL set, and 9 of the 12 target
tables do not exist yet. The revision's `_table_exists` guard correctly skips them, and a
freshly provisioned database came out with 9 columns still `text` and 9 foreign keys
missing -- caught by `scripts/verify_production_schema.py` reporting

    drift/columns      9 missing, 9 unexpected
    drift/foreign_keys 0 missing, 9 unexpected

So the SQL file runs as an explicit provisioner step after the tables exist.

TWO IMPLEMENTATIONS OF ONE DECISION IS A LIABILITY
--------------------------------------------------
It is accepted because the two run in situations that cannot be collapsed, and it is
constrained rather than tolerated. These tests parse both files and fail if they
disagree about:

  * which tables are converted
  * the ON DELETE action chosen for each one
  * the foreign-key naming convention
  * the UUID pattern used by the refusal precheck

They need no database. A divergence is a source-level fact and should fail in the fast
lane, not wait for someone to provision PostgreSQL.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_ALEMBIC = (_ROOT / "backend_app" / "alembic" / "versions"
            / "d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py")
_SQL = _ROOT / "migrations" / "hardening" / "998_bug049_identity_types.sql"

#: The one place in the test suite that states the reviewed decision. Taken from
#: `BUG049_IDENTITY_MATRIX.json`, which is the artefact a human approved.
EXPECTED: dict[str, str] = {
    "exchange_connections": "CASCADE",
    "exchange_keys": "CASCADE",
    "notification_settings": "CASCADE",
    "referral_profiles": "CASCADE",
    "risk_settings": "CASCADE",
    "strategy_limits": "CASCADE",
    "billing_invoices": "RESTRICT",
    "invoices": "RESTRICT",
    "payment_methods": "RESTRICT",
    "subscriptions": "RESTRICT",
    "risk_settings_audit": "RESTRICT",
    "security_logs": "RESTRICT",
}


def test_both_files_exist():
    assert _ALEMBIC.exists(), f"missing {_ALEMBIC}"
    assert _SQL.exists(), f"missing {_SQL}"


def test_neither_file_has_a_byte_order_mark():
    """A BOM breaks `ast.parse` on the revision and is invisible in an editor.

    PowerShell's `Set-Content -Encoding utf8` writes one, and two files in this
    repository acquired one during this work. `staging/strip_bom.py --apply` removes it.
    """
    for path in (_ALEMBIC, _SQL):
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), (
            f"{path.name} begins with a UTF-8 BOM"
        )


def _alembic_targets() -> dict[str, str]:
    """Read `_TARGETS` out of the revision as data, not by regex.

    `ast.literal_eval` on the assignment's value, so the test reads exactly what Python
    will read and a reformatting of the tuple does not break it.
    """
    tree = ast.parse(_ALEMBIC.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "_TARGETS":
                value = ast.literal_eval(node.value)
                return {table: on_delete for table, _column, on_delete in value}
    pytest.fail("could not find _TARGETS in the alembic revision")


def _sql_targets() -> dict[str, str]:
    """Read the `specs` array out of the SQL file's foreign-key section.

    Matches `ARRAY['table', 'ACTION']` inside the `$bug049_fks$` block only, so the
    separate `targets text[]` lists used by the precheck and conversion sections are not
    confused with it.
    """
    source = _SQL.read_text(encoding="utf-8")
    start = source.index("$bug049_fks$")
    end = source.index("$bug049_fks$", start + 1)
    block = source[start:end]
    pairs = re.findall(r"ARRAY\[\s*'([a-z_]+)'\s*,\s*'([A-Z ]+)'\s*\]", block)
    assert pairs, "no ARRAY['table','ACTION'] pairs found in the SQL foreign-key block"
    return {table: action.strip() for table, action in pairs}


def _sql_section_tables(dollar_tag: str) -> set[str]:
    """The `targets text[] := ARRAY[...]` list inside one named DO block."""
    source = _SQL.read_text(encoding="utf-8")
    start = source.index(dollar_tag)
    end = source.index(dollar_tag, start + 1)
    block = source[start:end]
    match = re.search(r"targets\s+text\[\]\s*:=\s*ARRAY\[(.*?)\]", block, re.S)
    assert match, f"no targets array found in {dollar_tag}"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_alembic_revision_matches_the_reviewed_decision():
    assert _alembic_targets() == EXPECTED, (
        "the alembic revision's _TARGETS no longer matches the decision recorded in "
        "BUG049_IDENTITY_MATRIX.json and in this test"
    )


def test_the_sql_file_matches_the_reviewed_decision():
    assert _sql_targets() == EXPECTED, (
        "the SQL file's foreign-key specs no longer match the decision recorded in "
        "BUG049_IDENTITY_MATRIX.json and in this test"
    )


def test_the_two_implementations_agree():
    """The point of the file. Stated as its own test so the failure message is direct."""
    alembic = _alembic_targets()
    sql = _sql_targets()
    assert alembic == sql, (
        "the two BUG-049 implementations disagree.\n"
        f"  only in alembic: {sorted(set(alembic) - set(sql))}\n"
        f"  only in SQL    : {sorted(set(sql) - set(alembic))}\n"
        f"  differing action: "
        f"{{t: (alembic[t], sql[t]) for t in set(alembic) & set(sql) if alembic[t] != sql[t]}}"
    )


@pytest.mark.parametrize("tag", ["$bug049_precheck$", "$bug049_convert$"])
def test_every_sql_section_covers_the_same_tables(tag):
    """The SQL file lists its targets three times -- precheck, conversion, foreign keys.

    A table present in the conversion list but missing from the precheck would be
    converted WITHOUT its values being inventoried first, which is precisely the
    "silently cast a customer identity" outcome the precheck exists to prevent.
    """
    assert _sql_section_tables(tag) == set(EXPECTED), (
        f"the {tag} section's table list differs from the foreign-key section's:\n"
        f"  missing: {sorted(set(EXPECTED) - _sql_section_tables(tag))}\n"
        f"  extra  : {sorted(_sql_section_tables(tag) - set(EXPECTED))}"
    )


def test_credential_tables_cascade_in_both_implementations():
    """The security decision, asserted by name rather than left to the table above.

    An orphaned `exchange_keys` row is an encrypted exchange API key with no owner --
    it cannot be audited, rotated or revoked through the product. RESTRICT here would
    leave the credential in place and merely make deletion fail.
    """
    for table in ("exchange_keys", "exchange_connections"):
        assert _alembic_targets()[table] == "CASCADE", (
            f"{table} must CASCADE: a credential must not outlive its owner"
        )
        assert _sql_targets()[table] == "CASCADE", (
            f"{table} must CASCADE in the SQL path too"
        )


def test_financial_tables_do_not_cascade_in_either_implementation():
    """The retention decision. CASCADE here would silently destroy financial history."""
    for table in ("invoices", "billing_invoices", "subscriptions", "payment_methods"):
        for name, targets in (("alembic", _alembic_targets()), ("sql", _sql_targets())):
            assert targets[table] != "CASCADE", (
                f"{table} is ON DELETE CASCADE in the {name} path. Deleting a customer "
                f"would destroy their financial records."
            )


def test_both_use_the_same_foreign_key_naming_convention():
    """If the names differ, each implementation's "already present?" check misses the
    other's constraint and the second to run adds a duplicate.
    """
    alembic_source = _ALEMBIC.read_text(encoding="utf-8")
    sql_source = _SQL.read_text(encoding="utf-8")
    assert '_FK_PREFIX = "fk_bug049_"' in alembic_source, (
        "the alembic revision's foreign-key prefix changed"
    )
    assert "'fk_bug049_' || t || '_' || c" in sql_source, (
        "the SQL file's foreign-key naming no longer matches fk_bug049_<table>_<column>"
    )


def test_both_refuse_on_the_same_uuid_pattern():
    """A looser pattern in one path would let a value through that the other rejects."""
    alembic_source = _ALEMBIC.read_text(encoding="utf-8")
    sql_source = _SQL.read_text(encoding="utf-8")
    pattern = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    assert pattern in alembic_source, "the alembic UUID pattern changed"
    assert pattern in sql_source, "the SQL UUID pattern changed"


def test_both_raise_rather_than_cast_on_unconvertible_values():
    """The behaviour that makes this migration safe on a money table.

    Asserted as source-level facts because the condition cannot be reached on a clean
    database -- and a test that only runs when the data is already broken is a test that
    never runs.
    """
    alembic_source = _ALEMBIC.read_text(encoding="utf-8")
    sql_source = _SQL.read_text(encoding="utf-8")

    assert "NOTHING HAS BEEN ALTERED" in alembic_source
    assert "NOTHING HAS BEEN ALTERED" in sql_source
    assert "raise RuntimeError(" in alembic_source
    assert "RAISE EXCEPTION" in sql_source

    for source, name in ((alembic_source, "alembic"), (sql_source, "sql")):
        assert "orphan" in source.lower(), (
            f"the {name} path does not check for rows naming a customer that does not "
            f"exist before adding the foreign key"
        )


def test_the_sql_file_ends_with_a_whole_schema_backstop():
    """The named lists cannot cover a table added later. The backstop sweeps everything.

    Deliberately an EXCEPTION rather than a NOTICE: this is the defect class that let
    encrypted exchange credentials outlive the accounts that owned them, and a warning in
    a provisioning log is a warning nobody reads.
    """
    source = _SQL.read_text(encoding="utf-8")
    assert "$bug049_backstop$" in source
    backstop = source[source.index("$bug049_backstop$"):]
    assert "column_name IN ('user_id', 'tenant_id')" in backstop
    assert "data_type <> 'uuid'" in backstop
    assert "RAISE EXCEPTION" in backstop


def test_the_provisioner_runs_the_sql_file_as_an_explicit_step():
    """Not by filename ordering. Within `migrations/` the files sort
    numbered-then-alphabetical, so a new `zz_*.sql` would displace it -- the same
    fragility that was rejected for the privilege hardening.
    """
    provisioner = (_ROOT / "scripts" / "provision_database.py").read_text(
        encoding="utf-8")
    assert "BUG049_FILE" in provisioner
    assert "998_bug049_identity_types.sql" in provisioner
    # Anchored on the `print(...)` that emits the step, not on the string "STEP 3b" --
    # that also appears in the comment above `BUG049_FILE`, and matching the comment
    # would make this test pass while the step itself was absent.
    assert 'print("STEP 3b' in provisioner, (
        "the BUG-049 file is not applied as a named step"
    )
    # It must run AFTER the numbered set and BEFORE the hardening.
    index_step1 = provisioner.index('"step1"')
    index_bug049 = provisioner.index('print("STEP 3b')
    index_hardening = provisioner.index('print("STEP 4')
    assert index_step1 < index_bug049 < index_hardening, (
        "STEP 3b must run after the numbered SQL set (so the tables exist) and before "
        "the privilege hardening (so its policy work sees the final column types)"
    )


def test_the_provisioner_refuses_if_the_file_is_missing():
    """A missing conversion must stop provisioning, not be skipped quietly -- the same
    contract the privilege hardening has.
    """
    provisioner = (_ROOT / "scripts" / "provision_database.py").read_text(
        encoding="utf-8")
    block = provisioner[provisioner.index('print("STEP 3b'):]
    block = block[: block.index('print("STEP 4')]
    assert "REFUSING to report success" in block, (
        "a missing 998_bug049_identity_types.sql does not stop provisioning"
    )
