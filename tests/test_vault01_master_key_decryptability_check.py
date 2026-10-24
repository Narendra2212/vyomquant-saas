"""VAULT-01: the boot refuses a master key that cannot read the vault, and only then.

WHAT THESE TESTS PIN
====================
A master encryption key was replaced without re-encrypting the credentials it protected
and nothing noticed. The remediation is a check that makes that event a failed
deployment. A boot-blocking check is dangerous in its own right, so the tests pin BOTH
directions with equal weight:

  * it DOES refuse to start on an unambiguous key swap, in a deployed environment;
  * it does NOT refuse on an unreadable sample, on a partial failure, on an empty table,
    on rows already known to be unrecoverable, or outside a deployed environment.

The second list is the one that keeps the check from becoming a self-inflicted outage,
so it gets more tests than the first.

Also pinned: the quarantine state is a KNOWN NEGATIVE the dashboard already understands,
and migration 056 supplies every column the dashboard's two projections name -- including
``updated_at``, whose absence made BOTH of them fail and emptied the venue list for every
account.

Nothing here needs AWS, a database or a real key: the check takes an injectable row
fetcher and an injectable cipher precisely so its own regression can drive every verdict.
"""

from __future__ import annotations

import os
import re
import sys

import pytest
from cryptography.fernet import Fernet, MultiFernet

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend_app.core import credential_key_integrity as cki  # noqa: E402

MIGRATION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "backend_app", "migrations", "056_vault01_credential_validity_state.sql",
)


# ── helpers ───────────────────────────────────────────────────────────────────

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


def cipher_for(*keys: str) -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in keys])


def row(api: str | None = None, secret: str | None = None,
        password: str | None = None, status: str | None = None,
        user: str = "11111111-2222-3333-4444-555555555555",
        exchange: str = "binance") -> dict:
    return {
        "user_id": user,
        "exchange_id": exchange,
        "encrypted_api_key": api,
        "encrypted_secret_key": secret,
        "encrypted_password": password,
        "validation_status": status,
    }


def encrypted_under(key: str, plaintext: str = "a-credential") -> str:
    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def fetcher(rows: list[dict]):
    def _fetch(limit: int):
        return rows[:limit]
    return _fetch


# ══════════════════════════════════════════════════════════════════════════════
#  THE VERDICTS
# ══════════════════════════════════════════════════════════════════════════════

def test_every_value_decrypting_is_ok():
    rows = [row(api=encrypted_under(KEY_A), secret=encrypted_under(KEY_A))]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_A))
    assert report.verdict == cki.OK
    assert report.values_checked == 2
    assert report.values_decrypted == 2
    assert report.values_undecryptable == 0
    assert report.blocks_boot is False
    assert report.healthy is True


def test_an_older_key_still_in_the_set_is_not_a_mismatch():
    """MultiFernet decrypts under ANY configured key. A correct rotation -- new key
    prepended, old key retained -- must read as OK, or the check would fire on exactly
    the procedure that is supposed to prevent this finding."""
    rows = [row(api=encrypted_under(KEY_B))]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_A, KEY_B))
    assert report.verdict == cki.OK


def test_nothing_decrypting_is_a_key_mismatch():
    rows = [row(api=encrypted_under(KEY_A), secret=encrypted_under(KEY_A))]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_B))
    assert report.verdict == cki.KEY_MISMATCH
    assert report.values_decrypted == 0
    assert report.values_undecryptable == 2
    assert report.blocks_boot is True


def test_some_decrypting_and_some_not_is_degraded_not_a_mismatch():
    """One poisoned row must not be able to block every future deployment."""
    rows = [
        row(api=encrypted_under(KEY_A), user="aaaaaaaa-0000-0000-0000-000000000001"),
        row(api=encrypted_under(KEY_B), user="bbbbbbbb-0000-0000-0000-000000000002"),
    ]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_A))
    assert report.verdict == cki.DEGRADED
    assert report.values_decrypted == 1
    assert report.values_undecryptable == 1
    assert report.blocks_boot is False


def test_an_empty_table_is_no_sample_not_a_failure():
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher([]), cipher=cipher_for(KEY_A))
    assert report.verdict == cki.NO_SAMPLE
    assert report.blocks_boot is False
    assert report.healthy is True


def test_a_row_with_no_ciphertext_is_not_counted():
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher([row()]), cipher=cipher_for(KEY_A))
    assert report.verdict == cki.NO_SAMPLE
    assert report.values_checked == 0


def test_an_unreadable_fetch_is_undetermined_and_never_blocks():
    def _explode(limit: int):
        raise RuntimeError("connection refused")

    report = cki.check_master_key_integrity(
        fetch_rows=_explode, cipher=cipher_for(KEY_A))
    assert report.verdict == cki.UNDETERMINED
    assert report.blocks_boot is False
    # The detail names the failure class without carrying a DSN.
    assert "RuntimeError" in report.detail
    assert "://" not in report.detail


def test_a_missing_master_key_is_undetermined_not_a_mismatch(monkeypatch):
    """No key configured is a configuration problem that `validate_required` and
    ENV-03 already fail the boot for. It must not masquerade as a key swap, which has a
    completely different remedy."""
    monkeypatch.delenv("MASTER_ENCRYPTION_KEYS", raising=False)
    report = cki.check_master_key_integrity(fetch_rows=fetcher([row(api="x")]))
    assert report.verdict == cki.UNDETERMINED
    assert report.blocks_boot is False


def test_garbage_key_material_is_undetermined_and_leaks_nothing(monkeypatch, caplog):
    monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", "not-a-fernet-key")
    with caplog.at_level("CRITICAL"):
        report = cki.check_master_key_integrity(fetch_rows=fetcher([row(api="x")]))
    assert report.verdict == cki.UNDETERMINED
    assert "not-a-fernet-key" not in caplog.text


def test_the_sample_is_bounded():
    """A swapped key breaks every row, so the sample is capped. An unbounded scan would
    put a cost on every boot that grows with the customer base."""
    rows = [row(api=encrypted_under(KEY_A), user=f"{i:08d}-0000-0000-0000-000000000000")
            for i in range(cki.SAMPLE_LIMIT + 25)]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_A))
    assert report.rows_sampled == cki.SAMPLE_LIMIT


# ══════════════════════════════════════════════════════════════════════════════
#  QUARANTINE IS WHAT MAKES THE INVARIANT CHECKABLE
# ══════════════════════════════════════════════════════════════════════════════

def test_a_quarantined_row_is_out_of_scope():
    """The two production rows are permanently unrecoverable. If they stayed in scope,
    "every row decrypts" would be false forever and the check would have to be switched
    off to deploy -- and a check that always fails is a check nobody reads."""
    rows = [row(api=encrypted_under(KEY_A), secret=encrypted_under(KEY_A),
                status=cki.UNREADABLE_STATUS)]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_B))
    assert report.verdict == cki.NO_SAMPLE
    assert report.quarantined_rows == 1
    assert report.values_checked == 0
    assert report.blocks_boot is False


def test_quarantine_is_recognised_case_insensitively():
    rows = [row(api=encrypted_under(KEY_A), status="UNREADABLE")]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_B))
    assert report.verdict == cki.NO_SAMPLE


def test_quarantining_the_dead_rows_does_not_hide_a_live_key_swap():
    """The decisive case for the whole design: production's own shape. Two quarantined
    unreadable rows plus a new row encrypted under a key that is no longer configured
    must still read as a mismatch -- quarantine must not become a blanket amnesty."""
    rows = [
        row(api=encrypted_under(KEY_A), status=cki.UNREADABLE_STATUS,
            user="dead0000-0000-0000-0000-000000000001"),
        row(api=encrypted_under(KEY_A), status=cki.UNREADABLE_STATUS,
            user="dead0000-0000-0000-0000-000000000002"),
        row(api=encrypted_under(KEY_B), user="live0000-0000-0000-0000-000000000003"),
    ]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_A))
    assert report.verdict == cki.KEY_MISMATCH
    assert report.quarantined_rows == 2
    assert report.blocks_boot is True


def test_a_failing_row_is_named_without_disclosing_its_contents():
    secret_value = encrypted_under(KEY_B)
    rows = [row(api=secret_value, user="deadbeef-1111-2222-3333-444444444444")]
    report = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_A))
    assert report.failing_rows == ("deadbeef/binance",)
    rendered = str(report.to_dict())
    assert secret_value not in rendered
    assert KEY_A not in rendered and KEY_B not in rendered


# ══════════════════════════════════════════════════════════════════════════════
#  THE BOOT GATE
# ══════════════════════════════════════════════════════════════════════════════

def test_a_key_mismatch_in_a_deployed_environment_refuses_the_boot(monkeypatch):
    monkeypatch.delenv(cki.OVERRIDE_ENV_VAR, raising=False)
    report = cki.MasterKeyIntegrityReport(
        verdict=cki.KEY_MISMATCH, values_checked=4, values_undecryptable=4)
    with pytest.raises(RuntimeError) as exc:
        cki.assert_master_key_can_read_stored_credentials(report, deployed=True)
    message = str(exc.value)
    # The message has to be actionable at 3am by somebody who has never read this module.
    assert "MASTER_ENCRYPTION_KEYS" in message
    assert "prepend" in message.lower()
    assert cki.OVERRIDE_ENV_VAR in message


@pytest.mark.parametrize("verdict", [cki.OK, cki.NO_SAMPLE, cki.UNDETERMINED,
                                     cki.DEGRADED])
def test_no_other_verdict_refuses_the_boot(verdict, monkeypatch):
    monkeypatch.delenv(cki.OVERRIDE_ENV_VAR, raising=False)
    report = cki.MasterKeyIntegrityReport(
        verdict=verdict, values_checked=4, values_decrypted=2,
        values_undecryptable=2, detail="whatever")
    # Must not raise.
    returned = cki.assert_master_key_can_read_stored_credentials(report, deployed=True)
    assert returned is report


def test_a_mismatch_outside_a_deployed_environment_does_not_refuse_the_boot(monkeypatch):
    """A developer with no production ciphertext must not be stopped by an invariant
    about production ciphertext."""
    monkeypatch.delenv(cki.OVERRIDE_ENV_VAR, raising=False)
    report = cki.MasterKeyIntegrityReport(verdict=cki.KEY_MISMATCH, values_checked=1,
                                          values_undecryptable=1)
    assert cki.assert_master_key_can_read_stored_credentials(
        report, deployed=False) is report


def test_the_operator_override_allows_the_boot(monkeypatch):
    monkeypatch.setenv(cki.OVERRIDE_ENV_VAR, "true")
    report = cki.MasterKeyIntegrityReport(verdict=cki.KEY_MISMATCH, values_checked=1,
                                          values_undecryptable=1)
    assert cki.assert_master_key_can_read_stored_credentials(
        report, deployed=True) is report


def test_the_override_is_not_triggered_by_an_arbitrary_value(monkeypatch):
    monkeypatch.setenv(cki.OVERRIDE_ENV_VAR, "no")
    report = cki.MasterKeyIntegrityReport(verdict=cki.KEY_MISMATCH, values_checked=1,
                                          values_undecryptable=1)
    with pytest.raises(RuntimeError):
        cki.assert_master_key_can_read_stored_credentials(report, deployed=True)


def test_a_degraded_verdict_is_logged_at_critical(caplog):
    report = cki.MasterKeyIntegrityReport(
        verdict=cki.DEGRADED, values_checked=4, values_decrypted=2,
        values_undecryptable=2, failing_rows=("abcdef12/binance",))
    with caplog.at_level("CRITICAL"):
        cki.assert_master_key_can_read_stored_credentials(report, deployed=True)
    assert "PARTIAL KEY MISMATCH" in caplog.text
    assert "abcdef12/binance" in caplog.text


def test_an_undetermined_verdict_is_a_warning_not_a_critical(caplog):
    """UNDETERMINED must not cry wolf: it is logged, but it is not evidence about the
    key, and treating it as one would train operators to ignore the real alarm."""
    report = cki.MasterKeyIntegrityReport(verdict=cki.UNDETERMINED,
                                          detail="could not read exchange_keys: OSError")
    with caplog.at_level("WARNING"):
        cki.assert_master_key_can_read_stored_credentials(report, deployed=True)
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert not any(r.levelname == "CRITICAL" for r in caplog.records)


def test_the_boot_actually_calls_the_gate():
    """A check nothing invokes is not a check. Pins the call site in main.py's lifespan
    rather than trusting that it is still there."""
    main_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "backend_app", "main.py")
    with open(main_py, encoding="utf-8") as fh:
        source = fh.read()
    assert "assert_master_key_can_read_stored_credentials()" in source
    # And before the port is bound / services come up.
    assert source.index("assert_master_key_can_read_stored_credentials()") < \
        source.index("ALGO22 server starting up")


# ══════════════════════════════════════════════════════════════════════════════
#  THE STATE THE UI CAN ACT ON
# ══════════════════════════════════════════════════════════════════════════════

def test_unreadable_is_a_known_negative_to_the_dashboard():
    from backend_app.backend.dashboard_aggregation_service import (
        VENUE_STATUS_INVALID_CREDENTIALS, credential_validity, venue_status,
    )
    record = {"validation_status": cki.UNREADABLE_STATUS}
    assert credential_validity(record) is False
    assert venue_status(None, False) == VENUE_STATUS_INVALID_CREDENTIALS


def test_unreadable_is_distinct_from_unknown():
    """`None` means nothing checked; `False` means we checked and it cannot be used.
    Collapsing them is the fabrication dashboard_aggregation_service exists to prevent."""
    from backend_app.backend.dashboard_aggregation_service import credential_validity
    assert credential_validity({"validation_status": None}) is None
    assert credential_validity({"validation_status": "pending"}) is None
    assert credential_validity({"validation_status": cki.UNREADABLE_STATUS}) is False


def test_the_writer_and_the_reader_share_one_spelling():
    """Migration 056 deliberately adds no CHECK constraint, so a shared constant is the
    only thing keeping the written value and the matched value identical."""
    from backend_app.backend import dashboard_aggregation_service as dash
    assert cki.UNREADABLE_STATUS in dash._CREDENTIAL_INVALID_STATUSES
    assert dash._UNREADABLE_CREDENTIAL_STATUS is cki.UNREADABLE_STATUS


def test_decryptability_is_never_reported_as_tradability():
    """Being able to decrypt a credential says nothing about whether the venue accepts
    it. The quarantine script must never write an affirmative."""
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "scripts", "quarantine_undecryptable_exchange_keys.py"),
              encoding="utf-8") as fh:
        source = fh.read()
    assert "can_trade = FALSE" in source
    assert "can_trade = true" not in source.lower()
    assert "validation_status = %s" in source  # the spelling comes from the constant


# ══════════════════════════════════════════════════════════════════════════════
#  MIGRATION 056 SUPPLIES WHAT THE DASHBOARD ASKS FOR
# ══════════════════════════════════════════════════════════════════════════════

def test_the_migration_adds_every_column_both_projections_name():
    """The widened projection asks for four validity columns and the NARROW FALLBACK
    asks for updated_at. The live table had none of the five, so both selects were
    rejected with 42703 and _fetch_exchange_credentials returned [] for every user --
    the venue list was empty for every account that had connected one."""
    from backend_app.backend.dashboard_aggregation_service import (
        DashboardAggregationService as D,
    )
    with open(MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    named = [c.strip() for c in
             (D._EXCHANGE_KEY_BASE_COLUMNS + ", " + D._EXCHANGE_KEY_VALIDITY_COLUMNS
              ).split(",")]
    assert "exchange_id" in named and "updated_at" in named
    for column in named:
        if column == "exchange_id":
            continue  # pre-existing
        assert re.search(rf"ADD COLUMN IF NOT EXISTS {column}\b", sql), \
            f"migration 056 does not add {column}, so the projection still fails"


def test_can_trade_is_nullable_with_no_default():
    """A DEFAULT false would convert "nobody has checked" into "we checked and it cannot
    trade", which is the exact fabrication credential_validity's tri-state prevents."""
    with open(MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    line = next(l for l in sql.splitlines()
                if "ADD COLUMN IF NOT EXISTS can_trade" in l)
    assert "DEFAULT" not in line.upper()
    assert "NOT NULL" not in line.upper()


def test_updated_at_is_backfilled_from_created_at_not_now():
    """Backfilling with now() would make every stale row claim it had just synced."""
    with open(MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    assert re.search(r"SET\s+updated_at\s*=\s*created_at", sql)


def test_the_migration_is_idempotent():
    with open(MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    adds = re.findall(r"ADD COLUMN(?! IF NOT EXISTS)", sql)
    assert not adds, "every ADD COLUMN must be IF NOT EXISTS to stay re-runnable"
    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "DROP TRIGGER IF EXISTS" in sql


# ══════════════════════════════════════════════════════════════════════════════
#  THE READINESS SURFACE
# ══════════════════════════════════════════════════════════════════════════════

def test_the_probe_is_registered_but_does_not_gate_readiness():
    """Gating would remove the only route to the fix: the remedy for an unreadable
    credential is for the user to re-enter it, through this API."""
    import inspect

    from backend_app.core import readiness as R

    assert "probe_credential_vault_keys" in inspect.getsource(R.evaluate_readiness)
    report = R.DependencyReport("credential_vault_keys", R.DOWN, False, "KEY_MISMATCH")
    assert report.blocks_readiness is False


@pytest.mark.asyncio
async def test_the_probe_reports_down_on_a_mismatch(monkeypatch):
    from backend_app.core import readiness as R

    monkeypatch.setattr(
        cki, "check_master_key_integrity",
        lambda *a, **k: cki.MasterKeyIntegrityReport(
            verdict=cki.KEY_MISMATCH, values_checked=2, values_undecryptable=2))
    report = await R.probe_credential_vault_keys()
    assert report.state == R.DOWN
    assert report.required is False
    assert "KEY_MISMATCH" in report.detail


@pytest.mark.asyncio
async def test_the_probe_reports_unknown_rather_than_down_when_undetermined(monkeypatch):
    from backend_app.core import readiness as R

    monkeypatch.setattr(
        cki, "check_master_key_integrity",
        lambda *a, **k: cki.MasterKeyIntegrityReport(
            verdict=cki.UNDETERMINED, detail="could not read exchange_keys: OSError"))
    report = await R.probe_credential_vault_keys()
    assert report.state == R.UNKNOWN


@pytest.mark.asyncio
async def test_the_probe_reports_up_when_the_key_reads_the_vault(monkeypatch):
    from backend_app.core import readiness as R

    monkeypatch.setattr(
        cki, "check_master_key_integrity",
        lambda *a, **k: cki.MasterKeyIntegrityReport(
            verdict=cki.OK, values_checked=2, values_decrypted=2))
    report = await R.probe_credential_vault_keys()
    assert report.state == R.UP


# ══════════════════════════════════════════════════════════════════════════════
#  NEGATIVE CONTROL
# ══════════════════════════════════════════════════════════════════════════════

def test_negative_control_the_check_is_not_vacuously_green():
    """Reconstructs the pre-fix world -- no check at all -- and proves the assertions
    above would not pass against it. Without this, every test here could be green
    because `check_master_key_integrity` always returns OK."""
    rows = [row(api=encrypted_under(KEY_A))]

    # A cipher that opens everything would make KEY_MISMATCH unreachable.
    class AlwaysOpens:
        def decrypt(self, token):
            return b"anything"

    always = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=AlwaysOpens())
    assert always.verdict == cki.OK

    # The same rows under a wrong key must NOT be OK. If this were equal to the line
    # above, the check would be measuring nothing.
    real = cki.check_master_key_integrity(
        fetch_rows=fetcher(rows), cipher=cipher_for(KEY_B))
    assert real.verdict == cki.KEY_MISMATCH
    assert real.verdict != always.verdict


def test_negative_control_the_vault_would_really_raise_on_these_rows():
    """Grounds the whole finding: the production failure mode is a ValueError from
    APIKeyVault._decrypt, not a tidy None. Reproduced here on a real MultiFernet."""
    token = encrypted_under(KEY_A)
    wrong = cipher_for(KEY_B)
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        wrong.decrypt(token.encode())
