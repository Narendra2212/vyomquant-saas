"""
tests/test_sec24_mfa_backup_codes.py — SEC-24: lost-2FA-device recovery via backup codes.

Exercises `backend_app.backend.mfa_backup_codes` against a fully mocked Supabase
client (the same style `tests/test_paper03_client_resolution_is_service_role.py`
uses), and the router wiring in `routers/security.py` through FastAPI's
`TestClient` with the app's dependencies overridden — no real Postgres required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend_app.backend.mfa_backup_codes import (
    BACKUP_CODE_COUNT,
    BackupCodeRateLimited,
    MAX_FAILED_ATTEMPTS,
    generate_and_store_backup_codes,
    generate_backup_codes,
    hash_backup_code,
    normalize_backup_code,
    recover_account_with_backup_code,
    store_backup_codes,
    verify_and_consume_backup_code,
)


# ──────────────────────────────────────────────────────────────────────────
# Generation and hashing
# ──────────────────────────────────────────────────────────────────────────


def test_generates_the_documented_count_of_unique_codes():
    codes = generate_backup_codes()
    assert len(codes) == BACKUP_CODE_COUNT
    assert len(set(codes)) == BACKUP_CODE_COUNT, "codes must not collide"


def test_codes_avoid_the_ambiguous_characters():
    codes = generate_backup_codes()
    for code in codes:
        for banned in "0O1IL":
            assert banned not in code, f"{code!r} contains ambiguous character {banned!r}"


def test_hash_is_deterministic_and_normalises_case_dashes_and_whitespace():
    code = "ABCDE-FGHJK"
    h1 = hash_backup_code(code)
    h2 = hash_backup_code(" abcde-fghjk \n")
    h3 = hash_backup_code("ABCDEFGHJK")
    assert h1 == h2 == h3
    assert len(h1) == 64, "sha256 hex digest is 64 chars"


def test_different_codes_hash_differently():
    assert hash_backup_code("AAAAA-AAAAA") != hash_backup_code("BBBBB-BBBBB")


def test_normalize_handles_non_string_input_without_raising():
    assert normalize_backup_code(None) == ""
    assert normalize_backup_code(123) == ""


def test_hash_never_equals_the_plaintext_or_contains_it():
    code = "QRSTU-VWXYZ"
    digest = hash_backup_code(code)
    assert code not in digest
    assert digest != code


# ──────────────────────────────────────────────────────────────────────────
# A minimal fake Supabase table/client, enough to drive the storage and
# verification paths without a real Postgres connection.
# ──────────────────────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    """In-memory stand-in for one Postgres table reached through
    `supabase.table(name)`. Supports exactly the chain this module calls:
    select/insert/update/upsert/delete, `.eq`, `.is_`, `.limit`, `.execute`.
    """

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self._pending_op = None
        self._filters = []
        self._payload = None

    # -- chain entry points --------------------------------------------------
    def select(self, *_args, **_kwargs):
        self._pending_op = "select"
        return self

    def insert(self, payload):
        self._pending_op = "insert"
        self._payload = payload if isinstance(payload, list) else [payload]
        return self

    def update(self, payload):
        self._pending_op = "update"
        self._payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._pending_op = "upsert"
        self._payload = payload
        return self

    def delete(self):
        self._pending_op = "delete"
        return self

    # -- filters --------------------------------------------------------------
    def eq(self, column, value):
        self._filters.append(("eq", column, value))
        return self

    def is_(self, column, value):
        self._filters.append(("is", column, value))
        return self

    def limit(self, _n):
        return self

    # -- matching ---------------------------------------------------------------
    def _matches(self, row):
        for kind, column, value in self._filters:
            if kind == "eq" and row.get(column) != value:
                return False
            if kind == "is" and value == "null" and row.get(column) is not None:
                return False
        return True

    def execute(self):
        if self._pending_op == "select":
            matched = [r for r in self.rows if self._matches(r)]
            return _FakeResult(matched)

        if self._pending_op == "insert":
            for row in self._payload:
                self.rows.append(dict(row))
            return _FakeResult(list(self._payload))

        if self._pending_op == "update":
            touched = []
            for row in self.rows:
                if self._matches(row):
                    row.update(self._payload)
                    touched.append(dict(row))
            return _FakeResult(touched)

        if self._pending_op == "upsert":
            key = self._payload.get("user_id")
            for row in self.rows:
                if row.get("user_id") == key:
                    row.update(self._payload)
                    return _FakeResult([dict(row)])
            self.rows.append(dict(self._payload))
            return _FakeResult([dict(self._payload)])

        if self._pending_op == "delete":
            remaining = [r for r in self.rows if not self._matches(r)]
            removed = [r for r in self.rows if self._matches(r)]
            self.rows[:] = remaining
            return _FakeResult(removed)

        raise AssertionError("no operation selected before execute()")


class FakeSupabase:
    """Stands in for `get_service_supabase()`'s return value.

    Real `supabase.table(name)` returns a FRESH query-builder object on every
    call — the table's ROWS persist, but the filter chain does not. This keeps
    the two separate: `_rows` is the persistent store, and `.table()` hands
    back a brand-new `_FakeTable` wrapping it each time, so one call's
    `.eq(...)` chain can never leak into the next call against the same
    table.
    """

    def __init__(self):
        self._rows = {"mfa_backup_codes": [], "mfa_backup_code_attempts": []}
        self.auth = MagicMock()

    def table(self, name):
        return _FakeTable(self._rows[name])


# ──────────────────────────────────────────────────────────────────────────
# Storage round-trip
# ──────────────────────────────────────────────────────────────────────────


def test_generate_and_store_returns_plaintext_and_stores_only_hashes():
    fake = FakeSupabase()
    codes = generate_and_store_backup_codes(fake, "user-1")

    assert len(codes) == BACKUP_CODE_COUNT
    stored = fake.table("mfa_backup_codes").rows
    assert len(stored) == BACKUP_CODE_COUNT
    stored_hashes = {row["code_hash"] for row in stored}
    for code in codes:
        assert code not in str(stored), "plaintext must never be persisted"
        assert hash_backup_code(code) in stored_hashes


def test_regenerating_invalidates_every_old_code():
    fake = FakeSupabase()
    first_batch = generate_and_store_backup_codes(fake, "user-1")
    second_batch = generate_and_store_backup_codes(fake, "user-1")

    # The old set is gone entirely, not merged with the new one.
    assert len(fake.table("mfa_backup_codes").rows) == BACKUP_CODE_COUNT
    # Checked against the per-account lockout budget: MAX_FAILED_ATTEMPTS - 1
    # old codes, so the account is not locked out before the final assertion.
    for old_code in first_batch[: MAX_FAILED_ATTEMPTS - 1]:
        assert verify_and_consume_backup_code(fake, "user-1", old_code) is False

    # The new set is live.
    assert verify_and_consume_backup_code(fake, "user-1", second_batch[0]) is True


def test_regenerating_clears_a_standing_lockout():
    fake = FakeSupabase()
    generate_and_store_backup_codes(fake, "user-1")
    for _ in range(MAX_FAILED_ATTEMPTS):
        assert verify_and_consume_backup_code(fake, "user-1", "WRONG-CODE1") is False

    with pytest.raises(BackupCodeRateLimited):
        verify_and_consume_backup_code(fake, "user-1", "WRONG-CODE1")

    new_codes = generate_and_store_backup_codes(fake, "user-1")
    # No longer locked, and the fresh code works.
    assert verify_and_consume_backup_code(fake, "user-1", new_codes[0]) is True


# ──────────────────────────────────────────────────────────────────────────
# Verification: valid, used, invalid
# ──────────────────────────────────────────────────────────────────────────


def test_a_valid_unused_code_succeeds_exactly_once():
    fake = FakeSupabase()
    codes = generate_and_store_backup_codes(fake, "user-1")
    code = codes[0]

    assert verify_and_consume_backup_code(fake, "user-1", code) is True
    # Second use of the SAME code must fail — one-time use.
    assert verify_and_consume_backup_code(fake, "user-1", code) is False


def test_an_invalid_code_is_rejected():
    fake = FakeSupabase()
    generate_and_store_backup_codes(fake, "user-1")
    assert verify_and_consume_backup_code(fake, "user-1", "NOTREAL-CODE") is False


def test_a_code_belonging_to_a_different_account_is_rejected():
    fake = FakeSupabase()
    codes = generate_and_store_backup_codes(fake, "user-1")
    assert verify_and_consume_backup_code(fake, "user-2", codes[0]) is False


def test_normalisation_means_case_and_dashes_do_not_matter_at_verify_time():
    fake = FakeSupabase()
    codes = generate_and_store_backup_codes(fake, "user-1")
    mangled = codes[0].lower().replace("-", "  ")
    assert verify_and_consume_backup_code(fake, "user-1", mangled) is True


# ──────────────────────────────────────────────────────────────────────────
# Rate limiting
# ──────────────────────────────────────────────────────────────────────────


def test_lockout_engages_after_the_documented_number_of_failures():
    fake = FakeSupabase()
    generate_and_store_backup_codes(fake, "user-1")

    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert verify_and_consume_backup_code(fake, "user-1", "BAD-CODE1") is False

    # One more failure crosses the threshold.
    assert verify_and_consume_backup_code(fake, "user-1", "BAD-CODE1") is False

    with pytest.raises(BackupCodeRateLimited) as exc_info:
        verify_and_consume_backup_code(fake, "user-1", "BAD-CODE1")
    assert exc_info.value.locked_until > datetime.now(timezone.utc)


def test_lockout_is_per_account_not_global():
    fake = FakeSupabase()
    generate_and_store_backup_codes(fake, "user-1")
    generate_and_store_backup_codes(fake, "user-2")

    for _ in range(MAX_FAILED_ATTEMPTS):
        verify_and_consume_backup_code(fake, "user-1", "BAD-CODE1")

    with pytest.raises(BackupCodeRateLimited):
        verify_and_consume_backup_code(fake, "user-1", "BAD-CODE1")

    # user-2 is untouched by user-1's lockout.
    codes2 = fake.table("mfa_backup_codes").rows
    user2_hash_rows = [r for r in codes2 if r["user_id"] == "user-2"]
    assert len(user2_hash_rows) == BACKUP_CODE_COUNT


def test_a_successful_verify_resets_the_failure_counter():
    fake = FakeSupabase()
    codes = generate_and_store_backup_codes(fake, "user-1")

    verify_and_consume_backup_code(fake, "user-1", "BAD-CODE1")
    verify_and_consume_backup_code(fake, "user-1", "BAD-CODE2")
    assert verify_and_consume_backup_code(fake, "user-1", codes[0]) is True

    # Failure count is cleared, so MAX_FAILED_ATTEMPTS fresh failures are needed
    # again before a lockout, not just MAX_FAILED_ATTEMPTS - 2 more.
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert verify_and_consume_backup_code(fake, "user-1", "BAD-CODE3") is False
    # still not locked after MAX-1 more failures post-reset
    assert verify_and_consume_backup_code(fake, "user-1", codes[1]) is True


# ──────────────────────────────────────────────────────────────────────────
# Recovery: the full lost-device path, including the admin MFA unenroll
# ──────────────────────────────────────────────────────────────────────────


def _mock_factor(factor_id, factor_type="totp"):
    f = MagicMock()
    f.id = factor_id
    f.factor_type = factor_type
    return f


def test_recovery_unenrolls_every_totp_factor_on_success():
    fake = FakeSupabase()
    codes = generate_and_store_backup_codes(fake, "user-1")

    listed = MagicMock()
    listed.factors = [_mock_factor("factor-a"), _mock_factor("factor-b"), _mock_factor("phone-1", "phone")]
    fake.auth.admin.mfa.list_factors.return_value = listed

    result = recover_account_with_backup_code(fake, "user-1", codes[0])

    assert result.success is True
    assert set(result.unenrolled_factor_ids) == {"factor-a", "factor-b"}
    assert fake.auth.admin.mfa.delete_factor.call_count == 2
    fake.auth.admin.mfa.delete_factor.assert_any_call({"id": "factor-a", "user_id": "user-1"})
    fake.auth.admin.mfa.delete_factor.assert_any_call({"id": "factor-b", "user_id": "user-1"})


def test_recovery_refuses_an_invalid_code_without_touching_any_factor():
    fake = FakeSupabase()
    generate_and_store_backup_codes(fake, "user-1")

    result = recover_account_with_backup_code(fake, "user-1", "NOT-A-REAL-CODE")

    assert result.success is False
    assert result.unenrolled_factor_ids == ()
    fake.auth.admin.mfa.delete_factor.assert_not_called()


def test_recovery_reports_success_even_if_factor_removal_itself_errors():
    # The code is already consumed by the time factor removal is attempted; a
    # transient admin-API failure must not be reported as "the code didn't work",
    # which would let the trader believe nothing happened and burn another code.
    fake = FakeSupabase()
    codes = generate_and_store_backup_codes(fake, "user-1")
    fake.auth.admin.mfa.list_factors.side_effect = RuntimeError("admin API unavailable")

    result = recover_account_with_backup_code(fake, "user-1", codes[0])

    assert result.success is True
    assert result.unenrolled_factor_ids == ()
    # And the code is indeed consumed — not left valid for a retry.
    assert verify_and_consume_backup_code(fake, "user-1", codes[0]) is False


# ──────────────────────────────────────────────────────────────────────────
# Router wiring
# ──────────────────────────────────────────────────────────────────────────


class TestBackupCodeRoutes:
    """Exercises `routers/security.py`'s three endpoints through the real app,
    with `get_current_user` / `require_aal2` / `get_service_supabase` overridden
    — the same override style FastAPI's own docs recommend and this repo's
    other router tests already use.
    """

    @pytest.fixture
    def fake_supabase(self):
        return FakeSupabase()

    @pytest.fixture
    def client(self, fake_supabase, monkeypatch):
        from backend_app.main import app
        from backend_app.core import dependencies
        import backend_app.routers.security as security_router
        from fastapi.testclient import TestClient

        aal2_user = {"id": "user-1", "sub": "user-1", "app_metadata": {"aal": "aal2"}}
        aal1_user = {"id": "user-1", "sub": "user-1", "app_metadata": {"aal": "aal1"}}

        monkeypatch.setattr(security_router, "get_service_supabase", lambda: fake_supabase)

        def _use_aal1():
            app.dependency_overrides[dependencies.get_current_user] = lambda: aal1_user
            app.dependency_overrides.pop(dependencies.require_aal2, None)

        def _use_aal2():
            app.dependency_overrides[dependencies.get_current_user] = lambda: aal2_user
            app.dependency_overrides[dependencies.require_aal2] = lambda: aal2_user

        _use_aal2()
        test_client = TestClient(app)
        test_client._use_aal1 = _use_aal1
        test_client._use_aal2 = _use_aal2
        yield test_client
        app.dependency_overrides.pop(dependencies.get_current_user, None)
        app.dependency_overrides.pop(dependencies.require_aal2, None)

    def test_generate_requires_aal2(self, client):
        client._use_aal1()
        resp = client.post("/api/security/mfa/backup-codes/generate")
        assert resp.status_code == 403

    def test_generate_returns_ten_codes_at_aal2(self, client, fake_supabase):
        client._use_aal2()
        resp = client.post("/api/security/mfa/backup-codes/generate")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["codes"]) == BACKUP_CODE_COUNT
        assert body["count"] == BACKUP_CODE_COUNT

    def test_verify_works_at_aal1_which_is_the_whole_point(self, client, fake_supabase):
        client._use_aal2()
        codes = client.post("/api/security/mfa/backup-codes/generate").json()["codes"]

        client._use_aal1()
        listed = MagicMock()
        listed.factors = [_mock_factor("factor-a")]
        fake_supabase.auth.admin.mfa.list_factors.return_value = listed

        resp = client.post("/api/security/mfa/backup-codes/verify", json={"code": codes[0]})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_verify_rejects_a_bad_code_with_401(self, client, fake_supabase):
        client._use_aal2()
        client.post("/api/security/mfa/backup-codes/generate")
        client._use_aal1()

        resp = client.post("/api/security/mfa/backup-codes/verify", json={"code": "WRONG-WRONG"})
        assert resp.status_code == 401

    def test_verify_rate_limits_after_repeated_failures(self, client, fake_supabase):
        client._use_aal2()
        client.post("/api/security/mfa/backup-codes/generate")
        client._use_aal1()

        for _ in range(MAX_FAILED_ATTEMPTS):
            client.post("/api/security/mfa/backup-codes/verify", json={"code": "WRONG-WRONG"})

        resp = client.post("/api/security/mfa/backup-codes/verify", json={"code": "WRONG-WRONG"})
        assert resp.status_code == 429
