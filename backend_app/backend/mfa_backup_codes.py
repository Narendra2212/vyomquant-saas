"""
backend/mfa_backup_codes.py — SEC-24: self-service recovery from a lost 2FA device.

THE GAP THIS CLOSES
--------------------------------------------------------------------------------
`pages/TwoFA.jsx`'s `handleResetFactor` can unenroll and re-enroll a TOTP factor,
but it only renders once the trader is already past the MFA challenge — reached
from an authenticated session. A trader who has genuinely lost their authenticator
and holds no other logged-in session has no way back into their account short of
a manual support intervention.

Supabase Auth's own MFA API (`supabase.auth.mfa.*`) has no "recovery code" factor
type — only TOTP and phone. There is nothing native to call instead of building
this, so it is a small, deliberately narrow mechanism layered on top:

  * Ten single-use backup codes are generated (service-role only, gated on AAL2 —
    i.e. only once the trader has actually completed a real MFA challenge, which
    is also when `pages/TwoFA.jsx` offers to show them).
  * Only their SHA-256 hash is ever stored (`mfa_backup_codes.code_hash`,
    migration 062). The plaintext is returned exactly once, in the generate
    response, and never persisted anywhere, matching how this repository already
    treats every other one-way secret (`core/global_safety.py` and
    `core/live_execution_gate.py` hash an HMAC digest for their own tokens; here
    there is no shared secret to key an HMAC with, so a direct SHA-256 of a
    high-entropy, server-generated code is the equivalent — the code itself
    carries the entropy a password would need salting against, the same
    reasoning this repository's existing `hashlib.sha256(...).hexdigest()`
    identity hashes rely on, e.g. `core/credential_vault.py`,
    `backend/connection_engine.py`).
  * Verifying a code on the CHALLENGE screen (an AAL1 session — this is precisely
    the case where the trader cannot reach AAL2 any other way) consumes it
    atomically and, on success, deletes every TOTP factor on the account via
    Supabase's admin MFA API. `delete_factor` on a verified factor signs the
    account out of every session (gotrue's own documented behaviour), so the
    practical outcome is: the stale factor is gone, every session (including the
    one that just supplied the code) is revoked, and the trader signs back in
    with just their password and is walked through fresh TOTP enrolment —
    exactly `pages/TwoFA.jsx`'s existing "no verified factor" path. No bypass of
    MFA is created; a lost-device recovery removes the lost factor and forces a
    new one, which is the standard shape of this feature everywhere it exists.
  * Failed attempts are rate-limited per account (not just per IP — the router
    layer already applies a `slowapi` IP limit on top of this), so a stolen or
    guessed-at code cannot be brute-forced.

WHAT THIS DELIBERATELY DOES NOT DO
--------------------------------------------------------------------------------
It does not touch `supabase.auth.mfa.enroll` / `.challenge` / `.verify` /
`.unenroll` / `.listFactors` — the five calls `pages/TwoFA.jsx`'s own header
promises are carried across verbatim. Recovery acts entirely through the admin
MFA API (`auth.admin.mfa.list_factors` / `.delete_factor`), a different surface,
and only after this module's own verification has already succeeded.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("MFABackupCodes")

# ──────────────────────────────────────────────────────────────────────────
# Generation
# ──────────────────────────────────────────────────────────────────────────

#: How many codes a generate call produces. Ten matches the common convention
#: (GitHub, Google) and is enough that losing a handful still leaves a margin.
BACKUP_CODE_COUNT = 10

#: Five groups of five, dash-joined for readability when written down by hand.
_GROUP_LEN = 5
_GROUPS = 2

#: Excludes 0/O and 1/I/L — the same ambiguity `pages/TwoFA.jsx`'s TOTP secret
#: already has to be read and typed correctly, so the alphabet is held to the
#: same bar a trader transcribing a code onto paper needs.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate_one_code() -> str:
    """One code: ``XXXXX-XXXXX``, drawn from :data:`_ALPHABET` with
    :func:`secrets.choice` — a CSPRNG, not :mod:`random`."""
    groups = [
        "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LEN))
        for _ in range(_GROUPS)
    ]
    return "-".join(groups)


def normalize_backup_code(raw: str) -> str:
    """Case- and punctuation-insensitive on entry: a trader may type, paste or
    read the code back without the dash, in lowercase, or with surrounding
    whitespace from a copy-paste. Hashing happens on this normalised form on
    both the generate and the verify side, so the two never disagree."""
    if not isinstance(raw, str):
        return ""
    return "".join(ch for ch in raw.strip().upper() if ch != "-" and not ch.isspace())


def hash_backup_code(raw: str) -> str:
    """The one-way digest this module ever stores or compares. Plain SHA-256
    over the normalised code: the code is a 10-character draw from a
    32-symbol CSPRNG alphabet (log2(32**10) = 50 bits of entropy), which is
    what a password hash's per-guess slow-hash and salt exist to compensate
    for in a LOW-entropy secret. A salt would protect against a precomputed
    rainbow table of ever practical size here, and costs nothing, so one is
    still worth adding if this is ever revisited — but a raw SHA-256 lookup
    matches this codebase's own existing precedent for a high-entropy,
    server-generated token (`connection_engine.py`, `credential_vault.py`),
    and is not the weaker link in this mechanism: brute-forcing the hash is
    harder than brute-forcing the rate limiter below.
    """
    normalized = normalize_backup_code(raw)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_backup_codes() -> list[str]:
    """``BACKUP_CODE_COUNT`` fresh plaintext codes. Pure and side-effect free —
    storage is :func:`store_backup_codes`'s job, kept separate so generation
    can be unit-tested without a Supabase client."""
    return [_generate_one_code() for _ in range(BACKUP_CODE_COUNT)]


# ──────────────────────────────────────────────────────────────────────────
# Storage (service-role Supabase client)
# ──────────────────────────────────────────────────────────────────────────

BACKUP_CODES_TABLE = "mfa_backup_codes"
ATTEMPTS_TABLE = "mfa_backup_code_attempts"

#: Five failures locks the account's backup-code path for fifteen minutes.
#: Deliberately independent of the router's per-IP `slowapi` limit (10/minute):
#: this one survives an attacker who rotates source IPs, because it is keyed on
#: the account, not the request.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60


class BackupCodeRateLimited(Exception):
    """Raised by :func:`verify_and_consume_backup_code` while an account is
    locked out. Carries the UTC instant the lock clears."""

    def __init__(self, locked_until: datetime):
        self.locked_until = locked_until
        super().__init__(
            f"Too many failed backup-code attempts; try again after "
            f"{locked_until.isoformat()}."
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def store_backup_codes(supabase: Any, user_id: str, codes: list[str]) -> None:
    """Replace this account's whole set of backup codes.

    Regenerating is INVALIDATING: every previously issued code — used or not —
    stops working, which is the whole point of a regenerate action (a trader
    who suspects a written-down sheet was seen needs the old ten dead, not nine
    of them still live). Delete-then-insert rather than a diff, because the
    ten rows this call writes have no stable identity to diff against the ten
    they replace.
    """
    supabase.table(BACKUP_CODES_TABLE).delete().eq("user_id", user_id).execute()
    rows = [
        {"user_id": user_id, "code_hash": hash_backup_code(code)}
        for code in codes
    ]
    supabase.table(BACKUP_CODES_TABLE).insert(rows).execute()
    # A regenerate also clears any standing lockout: the trader just proved
    # control of the account at AAL2 to get here, so an old lock from a
    # different set of (now dead) codes should not linger.
    supabase.table(ATTEMPTS_TABLE).delete().eq("user_id", user_id).execute()


def generate_and_store_backup_codes(supabase: Any, user_id: str) -> list[str]:
    """Generate a fresh set and persist it, returning the plaintext — the ONLY
    moment the plaintext exists outside the trader's own clipboard. The caller
    (the router) must not log or cache the return value."""
    codes = generate_backup_codes()
    store_backup_codes(supabase, user_id, codes)
    return codes


# ──────────────────────────────────────────────────────────────────────────
# Rate limiting
# ──────────────────────────────────────────────────────────────────────────


def _load_attempt_row(supabase: Any, user_id: str) -> Optional[dict]:
    result = (
        supabase.table(ATTEMPTS_TABLE)
        .select("failed_attempts,locked_until")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(result, "data", None) or []
    return rows[0] if rows else None


def _parse_locked_until(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _check_not_locked(supabase: Any, user_id: str) -> None:
    row = _load_attempt_row(supabase, user_id)
    locked_until = _parse_locked_until(row.get("locked_until")) if row else None
    if locked_until is not None and locked_until > _now():
        raise BackupCodeRateLimited(locked_until)


def _record_failure(supabase: Any, user_id: str) -> None:
    row = _load_attempt_row(supabase, user_id)
    failed = (row.get("failed_attempts") if row else 0) or 0
    failed += 1
    payload = {"user_id": user_id, "failed_attempts": failed, "updated_at": _now().isoformat()}
    if failed >= MAX_FAILED_ATTEMPTS:
        payload["locked_until"] = (_now() + timedelta(seconds=LOCKOUT_SECONDS)).isoformat()
    supabase.table(ATTEMPTS_TABLE).upsert(payload, on_conflict="user_id").execute()


def _record_success(supabase: Any, user_id: str) -> None:
    supabase.table(ATTEMPTS_TABLE).delete().eq("user_id", user_id).execute()


# ──────────────────────────────────────────────────────────────────────────
# Verification
# ──────────────────────────────────────────────────────────────────────────


def verify_and_consume_backup_code(supabase: Any, user_id: str, code: str) -> bool:
    """Atomically consume one unused backup code for ``user_id``, or refuse.

    Rate-limited per account (see :data:`MAX_FAILED_ATTEMPTS`); raises
    :class:`BackupCodeRateLimited` while locked, so the caller returns 429
    rather than 401 and a lockout is distinguishable from a wrong code.

    The consuming UPDATE carries ``consumed_at IS NULL`` in its own filter, so
    two concurrent requests racing the same still-valid code can both match the
    SELECT half of a naive check-then-act, but only one UPDATE can flip a given
    row from NULL — the other's affected-row count is zero and it is reported
    as a failure, same as a wrong code. One code, one consumption, no race.
    """
    _check_not_locked(supabase, user_id)

    code_hash = hash_backup_code(code)
    if not code_hash:
        _record_failure(supabase, user_id)
        return False

    result = (
        supabase.table(BACKUP_CODES_TABLE)
        .update({"consumed_at": _now().isoformat()})
        .eq("user_id", user_id)
        .eq("code_hash", code_hash)
        .is_("consumed_at", "null")
        .execute()
    )
    consumed_rows = getattr(result, "data", None) or []

    if len(consumed_rows) == 0:
        _record_failure(supabase, user_id)
        return False

    _record_success(supabase, user_id)
    return True


# ──────────────────────────────────────────────────────────────────────────
# Recovery — the thing a lost-device trader actually calls
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackupCodeRecoveryResult:
    success: bool
    unenrolled_factor_ids: tuple[str, ...] = ()


def recover_account_with_backup_code(
    supabase: Any, user_id: str, code: str
) -> BackupCodeRecoveryResult:
    """Verify ``code``; on success, remove every TOTP factor on the account
    through Supabase's ADMIN MFA API — a different surface from the five calls
    `pages/TwoFA.jsx` carries verbatim (`enroll`/`challenge`/`verify`/
    `unenroll`/`listFactors`), reached only from a server holding the
    service-role key, never from the trader's own session.

    `delete_factor` on a VERIFIED factor signs the account out of every active
    session (gotrue's own documented behaviour) — including the one that just
    presented the code — so the caller must treat success as "go sign in
    again", not "now at AAL2".
    """
    if not verify_and_consume_backup_code(supabase, user_id, code):
        return BackupCodeRecoveryResult(success=False)

    unenrolled: list[str] = []
    try:
        listed = supabase.auth.admin.mfa.list_factors({"user_id": user_id})
        factors = getattr(listed, "factors", None) or []
        for factor in factors:
            factor_id = getattr(factor, "id", None) or (
                factor.get("id") if isinstance(factor, dict) else None
            )
            factor_type = getattr(factor, "factor_type", None) or (
                factor.get("factor_type") if isinstance(factor, dict) else None
            )
            if not factor_id or factor_type != "totp":
                continue
            supabase.auth.admin.mfa.delete_factor({"id": factor_id, "user_id": user_id})
            unenrolled.append(factor_id)
    except Exception:  # noqa: BLE001 — the code is already consumed; log and report partial
        logger.exception(
            "Backup code accepted for %s but factor removal failed; the account is left "
            "with its code consumed and its stale factor possibly still enrolled. Manual "
            "follow-up may be required.",
            user_id,
        )

    return BackupCodeRecoveryResult(success=True, unenrolled_factor_ids=tuple(unenrolled))
