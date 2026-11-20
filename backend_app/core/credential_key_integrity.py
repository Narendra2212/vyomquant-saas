"""VAULT-01: prove at boot that the configured master key can still read what it protects.

THE EVENT THIS EXISTS TO CATCH
==============================
A master encryption key was replaced without re-encrypting the credentials it
protected, and NOTHING detected it -- not a health check, not a test, not an alarm. It
surfaced only because ``scripts/rotate_master_encryption_keys.py --plan`` refuses to
rotate when ciphertext decrypts under no available key, and by then the key that had
encrypted the rows was gone.

How thoroughly gone, measured 2026-10-24: ``exchange_keys`` holds 2 rows with 4 genuine
Fernet tokens (urlsafe-base64, version byte 128, prefix ``gAAAAAB``), and none of them
open under

  * the current ``MASTER_ENCRYPTION_KEYS`` as a MultiFernet,
  * the PBKDF2 derivation in ``core/credential_vault.py``,
  * 265 Fernet-shaped candidates harvested from the entire git history,
  * every retained version of every related Secrets Manager secret, including the
    deprecated pre-rotation version AND a deleted secret from the older UPPERCASE naming
    generation restored from pending-deletion specifically to test it.

Secrets Manager has held exactly two distinct master keys in its life, and the rows
predate both: the earliest key material there was created 2026-07-22, the rows were
encrypted 2026-06-21. The key never lived in this account's secret store, so no amount
of version history could ever have recovered it. Those four values are unrecoverable.

With live trading hard-gated off and two rows affected, the cost was nil. The identical
event after launch, with funded exchange connections, silently breaks every one of them
-- and the first symptom would be a ``ValueError`` from ``_decrypt`` at order time.

WHY THIS BLOCKS THE BOOT RATHER THAN WARNING
--------------------------------------------
Because blocking the boot is what makes a bad key a FAILED DEPLOYMENT instead of a data
disaster. ECS starts the new task, the new task refuses to come up, the deployment
circuit breaker keeps the previous task definition serving -- which by definition still
has the working key. The rollout is rejected and nothing is lost. Booting instead, and
discovering the problem at first order, is the failure mode VAULT-01 was filed about.

This is the same posture ``main.py`` already takes for a missing required variable, an
unfit JWT secret, and a placeholder credential: refuse to serve rather than serve wrong.

AND WHY IT IS CAREFUL ABOUT *WHEN* IT BLOCKS
--------------------------------------------
A boot-blocking check that fires on bad evidence is worse than no check: it turns a
transient blip into a crash loop. So only ONE verdict blocks, and it is the unambiguous
one -- rows were read, at least one was in scope, and EVERY one of them failed. That is
a key swap and nothing else looks like it.

Specifically NOT blocking:

``UNDETERMINED``
    The sample could not be read, or no key is configured. A database that is briefly
    unreachable at boot is not evidence about the key, and refusing to start on it would
    convert a dependency blip into an outage of our own making.
``NO_SAMPLE``
    Nothing in scope to check -- a fresh environment, or every row already quarantined.
    There is no invariant to violate. This is production's state after
    ``scripts/quarantine_undecryptable_exchange_keys.py`` runs.
``DEGRADED``
    Some in-scope rows decrypt and some do not. Loud -- CRITICAL log, and DOWN on the
    dependency surface -- but a single poisoned row must not be able to block every
    future deploy.

QUARANTINE IS WHAT MAKES THE INVARIANT CHECKABLE
------------------------------------------------
The two stranded rows are real and permanently unreadable. Without somewhere to put
them, "every row must decrypt" would be false in production forever and the check would
have to be disabled to deploy -- a check that is always failing is a check nobody reads.
Marking them ``validation_status='unreadable'`` moves them out of scope, which is both
the UI state VAULT-01 asks for in part (1) and the baseline this invariant is measured
against. The two halves of the remediation are the same mechanism.

NO SECRETS. No key material, no plaintext, and no ciphertext is logged or returned --
only counts, and for a failure the identity columns of the affected row.

Regression: tests/test_vault01_master_key_decryptability_check.py
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("CredentialKeyIntegrity")

#: The table whose ciphertext the master key protects.
TABLE = "exchange_keys"

#: Columns holding ciphertext. Matches ``scripts/rotate_master_encryption_keys.py``'s
#: CIPHER_COLUMNS, because the rotation tool and this check must agree about what is
#: encrypted or one of them would pass while the other refused.
CIPHER_COLUMNS: Tuple[str, ...] = (
    "encrypted_api_key", "encrypted_secret_key", "encrypted_password",
)

#: Columns naming a row, for reporting a failure without disclosing its contents.
IDENTITY_COLUMNS: Tuple[str, ...] = ("user_id", "exchange_id")

#: ``validation_status`` for a row whose ciphertext opens under no configured key, so the
#: credential is gone and the user must re-enter it. Pinned here and imported by both the
#: writer (scripts/quarantine_undecryptable_exchange_keys.py) and the reader
#: (dashboard_aggregation_service._CREDENTIAL_INVALID_STATUSES) so the spelling cannot
#: drift -- migration 056 deliberately adds no CHECK constraint, and this is what takes
#: its place.
UNREADABLE_STATUS = "unreadable"

#: How many rows the boot check decrypts. A swapped key breaks EVERY row, so a bounded
#: sample detects it with certainty; decrypting an unbounded table would put a cost on
#: boot that grows with the customer base, for no additional evidence.
SAMPLE_LIMIT = 50

#: Set to a truthy value to start anyway after a ``KEY_MISMATCH``. Exists because a
#: boot-blocking check needs an operator override for the emergency it did not anticipate
#: -- including, specifically, a deliberate recovery where the old ciphertext is expected
#: to be unreadable and will be cleaned up after the service is back.
OVERRIDE_ENV_VAR = "ALLOW_UNREADABLE_EXCHANGE_CREDENTIALS"

# ── verdicts ──────────────────────────────────────────────────────────────────
#: Every in-scope row decrypted.
OK = "OK"
#: Nothing in scope. No invariant to violate.
NO_SAMPLE = "NO_SAMPLE"
#: Could not establish the answer. Never a reason to refuse to boot.
UNDETERMINED = "UNDETERMINED"
#: Some in-scope rows decrypt, some do not.
DEGRADED = "DEGRADED"
#: Rows were read, at least one was in scope, and every one of them failed.
KEY_MISMATCH = "KEY_MISMATCH"


@dataclass(frozen=True)
class MasterKeyIntegrityReport:
    """What the sample showed. Counts and row identities only, never contents."""

    verdict: str
    keys_configured: int = 0
    rows_sampled: int = 0
    values_checked: int = 0
    values_decrypted: int = 0
    values_undecryptable: int = 0
    quarantined_rows: int = 0
    failing_rows: Tuple[str, ...] = ()
    detail: str = ""

    @property
    def blocks_boot(self) -> bool:
        """Only an unambiguous key swap does."""
        return self.verdict == KEY_MISMATCH

    @property
    def healthy(self) -> bool:
        return self.verdict in (OK, NO_SAMPLE)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "keys_configured": self.keys_configured,
            "rows_sampled": self.rows_sampled,
            "values_checked": self.values_checked,
            "values_decrypted": self.values_decrypted,
            "values_undecryptable": self.values_undecryptable,
            "quarantined_rows": self.quarantined_rows,
            "failing_rows": list(self.failing_rows),
            "detail": self.detail[:200],
        }

    def summary(self) -> str:
        if self.verdict == NO_SAMPLE:
            return (f"no in-scope {TABLE} ciphertext to verify "
                    f"({self.quarantined_rows} row(s) quarantined as "
                    f"'{UNREADABLE_STATUS}')")
        if self.verdict == UNDETERMINED:
            return f"could not verify {TABLE} decryptability: {self.detail}"
        return (f"{self.values_decrypted}/{self.values_checked} in-scope ciphertext "
                f"value(s) across {self.rows_sampled} row(s) decrypt under the "
                f"{self.keys_configured} configured master key(s); "
                f"{self.quarantined_rows} row(s) quarantined")


def build_cipher() -> Optional[Any]:
    """A MultiFernet over ``MASTER_ENCRYPTION_KEYS``, or ``None`` if unusable.

    Built straight from the environment variable rather than taken from
    ``app_state.vault``. ``core/state._safe_init`` swallows a vault construction failure
    into a ``MockEngine``, and a check that silently ran against a mock would report
    success for every possible key -- the exact class of vacuous green this finding is
    about.
    """
    raw = os.environ.get("MASTER_ENCRYPTION_KEYS")
    if not raw:
        return None
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        return None
    try:
        from cryptography.fernet import Fernet, MultiFernet
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except Exception as exc:  # noqa: BLE001
        # Deliberately no key material in the message.
        logger.critical("MASTER_ENCRYPTION_KEYS is not valid Fernet key material: %s",
                        type(exc).__name__)
        return None


def configured_key_count() -> int:
    raw = os.environ.get("MASTER_ENCRYPTION_KEYS") or ""
    return len([k for k in (p.strip() for p in raw.split(",")) if k])


def _fetch_sample_via_supabase(limit: int) -> List[Dict[str, Any]]:
    """The sample, read with the same service-role client the vault itself uses.

    Service role is required: these are the ciphertext columns, and RLS would otherwise
    scope them to a request's user, of which a boot has none.
    """
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured")
    client = create_client(url, key)
    columns = ", ".join(IDENTITY_COLUMNS + CIPHER_COLUMNS + ("validation_status",))
    res = client.table(TABLE).select(columns).limit(limit).execute()
    rows = getattr(res, "data", None) or []
    return [r for r in rows if isinstance(r, dict)]


def _row_identity(row: Dict[str, Any]) -> str:
    return "/".join(str(row.get(c, "?"))[:8] for c in IDENTITY_COLUMNS)


def check_master_key_integrity(
    fetch_rows: Optional[Callable[[int], Sequence[Dict[str, Any]]]] = None,
    cipher: Optional[Any] = None,
    limit: int = SAMPLE_LIMIT,
) -> MasterKeyIntegrityReport:
    """Decrypt a bounded sample of stored credentials and report what happened.

    ``fetch_rows`` and ``cipher`` are injectable so the regression suite can drive every
    verdict without a database or a real key; production passes neither.
    """
    keys = configured_key_count()
    cipher = cipher if cipher is not None else build_cipher()
    if cipher is None:
        return MasterKeyIntegrityReport(
            verdict=UNDETERMINED, keys_configured=keys,
            detail="MASTER_ENCRYPTION_KEYS is absent or not valid Fernet key material",
        )

    fetch = fetch_rows or _fetch_sample_via_supabase
    try:
        rows = list(fetch(limit))
    except Exception as exc:  # noqa: BLE001
        return MasterKeyIntegrityReport(
            verdict=UNDETERMINED, keys_configured=keys,
            detail=f"could not read {TABLE}: {type(exc).__name__}",
        )

    quarantined = sum(
        1 for r in rows
        if str(r.get("validation_status") or "").strip().lower() == UNREADABLE_STATUS
    )

    checked = decrypted = undecryptable = 0
    sampled = 0
    failing: List[str] = []
    for row in rows:
        status = str(row.get("validation_status") or "").strip().lower()
        if status == UNREADABLE_STATUS:
            # Already recorded as unrecoverable. Counting it as a failure would make the
            # invariant permanently false and the check permanently ignored.
            continue
        values = [row.get(c) for c in CIPHER_COLUMNS]
        if not any(values):
            continue
        sampled += 1
        row_failed = False
        for value in values:
            if not value:
                continue
            checked += 1
            try:
                cipher.decrypt(value.encode() if isinstance(value, str) else value)
                decrypted += 1
            except Exception:  # noqa: BLE001
                undecryptable += 1
                row_failed = True
        if row_failed:
            failing.append(_row_identity(row))

    if checked == 0:
        return MasterKeyIntegrityReport(
            verdict=NO_SAMPLE, keys_configured=keys, rows_sampled=sampled,
            quarantined_rows=quarantined,
            detail=f"no in-scope ciphertext in {TABLE}",
        )

    if undecryptable == 0:
        verdict = OK
    elif decrypted == 0:
        verdict = KEY_MISMATCH
    else:
        verdict = DEGRADED

    return MasterKeyIntegrityReport(
        verdict=verdict, keys_configured=keys, rows_sampled=sampled,
        values_checked=checked, values_decrypted=decrypted,
        values_undecryptable=undecryptable, quarantined_rows=quarantined,
        failing_rows=tuple(failing),
    )


def override_is_set() -> bool:
    return (os.environ.get(OVERRIDE_ENV_VAR, "") or "").strip().lower() in (
        "true", "1", "yes")


def assert_master_key_can_read_stored_credentials(
    report: Optional[MasterKeyIntegrityReport] = None,
    deployed: Optional[bool] = None,
) -> MasterKeyIntegrityReport:
    """Log the verdict, and raise ``RuntimeError`` on an unambiguous key swap.

    Raises ONLY when all three hold: the verdict is ``KEY_MISMATCH``, this is a deployed
    environment, and the operator override is not set. A local or test run never fails
    the boot on this -- a developer with no production ciphertext must not be stopped by
    an invariant about production ciphertext.
    """
    report = report if report is not None else check_master_key_integrity()
    if deployed is None:
        from backend_app.core.config import settings
        deployed = settings._is_deployed_environment()

    if report.verdict == OK:
        logger.info("Credential vault key integrity: %s", report.summary())
        return report

    if report.verdict == NO_SAMPLE:
        logger.info("Credential vault key integrity: %s", report.summary())
        return report

    if report.verdict == UNDETERMINED:
        # Not evidence about the key. Loud enough to investigate, never fatal.
        logger.warning(
            "Credential vault key integrity UNDETERMINED: %s. Proceeding: an "
            "unreadable sample is not evidence that the master key is wrong, and "
            "refusing to boot on it would turn a dependency blip into an outage.",
            report.detail,
        )
        return report

    if report.verdict == DEGRADED:
        logger.critical(
            "CREDENTIAL VAULT PARTIAL KEY MISMATCH: %d of %d in-scope ciphertext "
            "value(s) do not decrypt under any configured master key. Affected row(s): "
            "%s. These credentials will raise at use time. Re-encrypt them with "
            "scripts/rotate_master_encryption_keys.py, or quarantine them with "
            "scripts/quarantine_undecryptable_exchange_keys.py once confirmed "
            "unrecoverable. NOT blocking the boot: other rows decrypt, so this is not a "
            "wholesale key swap and one poisoned row must not block every deploy.",
            report.values_undecryptable, report.values_checked,
            ", ".join(report.failing_rows) or "unknown",
        )
        return report

    # KEY_MISMATCH
    message = (
        f"FATAL: the configured MASTER_ENCRYPTION_KEYS cannot decrypt ANY of the "
        f"{report.values_checked} stored credential value(s) sampled from {TABLE}. "
        f"Every in-scope row failed, which means the master key was replaced without "
        f"re-encrypting what it protected -- every customer's exchange credential is "
        f"unreadable with this configuration. Refusing to start so this is a failed "
        f"deployment rather than a silent data loss: the previous task definition keeps "
        f"serving with the key that works. Restore the prior MASTER_ENCRYPTION_KEYS "
        f"value (prepend it, do not replace), redeploy, then re-encrypt with "
        f"scripts/rotate_master_encryption_keys.py --reencrypt. If these values are "
        f"genuinely unrecoverable and you intend to start anyway, set "
        f"{OVERRIDE_ENV_VAR}=true."
    )
    logger.critical(message)
    if not deployed:
        logger.warning(
            "Not a deployed environment, so the boot continues despite KEY_MISMATCH.")
        return report
    if override_is_set():
        logger.critical(
            "%s is set, so the boot continues despite KEY_MISMATCH. Every sampled "
            "credential is unreadable and will raise at use time.", OVERRIDE_ENV_VAR)
        return report
    raise RuntimeError(message)


__all__ = [
    "TABLE", "CIPHER_COLUMNS", "IDENTITY_COLUMNS", "UNREADABLE_STATUS",
    "SAMPLE_LIMIT", "OVERRIDE_ENV_VAR",
    "OK", "NO_SAMPLE", "UNDETERMINED", "DEGRADED", "KEY_MISMATCH",
    "MasterKeyIntegrityReport", "build_cipher", "configured_key_count",
    "check_master_key_integrity", "assert_master_key_can_read_stored_credentials",
    "override_is_set",
]
