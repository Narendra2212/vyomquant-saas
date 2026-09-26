"""
tests/test_exchange_credential_log_redaction.py

Task 12.10 / Requirement 1.20, 2.20 (production-launch-hardening) - the log half.

NOT A DUPLICATE OF ``tests/test_log_redaction.py``. That file (marketplace-subscriptions-
paper-trading task 33.4) drives ``backend_app/core/observability.py``'s
``RedactingLogFilter``, a structural filter attached to loggers, across every log call
site in ``backend_app/backend/marketplace/`` and ``backend_app/backend/paper/`` only -
see its own docstring. This file covers a different, unfiltered surface entirely: the
three modules task 12.10 names by name - ``exchange_executor.py``, ``api_key_vault.py``,
``credential_vault.py`` - none of which are under that filter's scope and none of which
this repository had a redaction test for before this task.

(This filename was chosen after an earlier mistake in this same task run: a first draft
was written to ``tests/test_log_redaction.py`` without reading the existing file first,
which overwrote 874 lines of the task-33.4 suite above. That was caught before being run
or committed, via `git status` showing the file as modified rather than new, and
reverted with `git checkout HEAD -- tests/test_log_redaction.py` before any further
action. Recorded here rather than silently fixed, per the instruction to say what was
checked rather than presenting a clean history that did not happen. The original file is
untouched; nothing in this file overlaps its deny-list, its call-site walk, or its
requirements (26.1/26.3/26.4 belong to that file, not to 1.20/2.20).)

WHAT THIS FILE ESTABLISHES
---------------------------
Three code paths that handle exchange credentials are read and exercised under
``caplog``, following the same "logs are scanned, not trusted" convention already
established by ``tests/security/test_builder_tenant_isolation.py`` and
``tests/regression/capture_baseline.py::capture_credential_vault``:

1. ``backend_app/core/credential_vault.py::CredentialVault`` - store then read a
   credential through the real encrypt/decrypt/Redis-key path (Redis is an in-memory
   double, same as the existing regression capture), asserting the plaintext and the
   ciphertext never reach the log.
2. ``backend_app/backend/api_key_vault.py::APIKeyVault`` - store then load exchange keys
   through a fake Supabase client, asserting the same.
3. ``backend_app/backend/exchange_executor.py::CCXTExchangeExecutor.connect`` - THE
   REGRESSION THIS FILE FOUND AND FIXED. ``connect()``'s own comments claim "STEP 6.10:
   Security - keys passed to CCXT, never logged", and reading every log call in the file
   confirmed no line interpolates ``self._api_key`` / ``self._api_secret`` / ``self._password``
   directly. But the failure branch, before this task, did::

       except Exception as e:
           logger.error(f"Failed to connect to {self.exchange_id}: {e}")
           raise self._handle_ccxt_error(e)

   That logs the *exception's string representation*, not the key material - so the
   comment's claim held for every line that names a variable directly, but was only as
   strong as the assumption that CCXT (or the exchange, or a network library underneath
   it) never raises an exception whose message embeds the credential it was given. This
   test forces exactly that: a double exchange class whose ``fetch_balance`` raises with
   the fake api_secret embedded in the exception message. Run against the tree BEFORE
   this task, it failed - the secret reached ``caplog.text`` verbatim. The fix is
   ``BaseExchangeExecutor._redact_credentials``, called from the single choke point every
   caller already routes through - ``_handle_ccxt_error`` - so every one of that file's
   ``except Exception as e: ... error.message`` sites is covered by one change, not five.
   This test is the regression test for that fix: it fails on the pre-fix code and
   passes on the post-fix code, per this spec's rule that every P0/P1 finding carries a
   test with exactly that property.

Running this suite against the pre-task tree found a real gap in path 3: the
exception-string log line was not, in fact, redacting. That gap is now fixed at the
call site rather than left red. Paths 1 and 2 already redacted correctly with no code
change needed.

SECRETS ARE NEVER WRITTEN HERE BY VALUE, except as obviously-fake test fixtures. Every
fixture used below is a hardcoded, clearly-fake string with a recognisable marker
(``FAKE-CANARY-...``) - never a value read from this repository's own `.env` files, never
a real exchange-issued key. This mirrors the existing convention in
``tests/test_exchange_vault_contract.py`` (fixtures like ``"key_1"``, ``"my_secret"``).

WHAT THIS FILE DOES NOT ESTABLISH
-----------------------------------
This is a code-level, unit-scoped proof: it shows the redaction property holds for the
three call paths exercised here, under `caplog`, in this process. It is NOT a full
content audit of the actual production log stream.

That full audit is BLOCKED, but not for the reason a first attempt assumed. See below.

======================================================================================
BLOCKED: full production log CONTENT audit
======================================================================================

The task instruction anticipated an AWS *permission* denial (AccessDenied) as the
blocking condition. That is not what was found, and reporting a denial that did not
happen would itself be a fabrication of the kind this entire hardening pass exists to
rule out. What was actually found, call by call, in this session:

  1. ``aws sts get-caller-identity`` -> succeeds:
     arn:aws:iam::273709947018:user/github-actions
     (Matches bugfix.md's own environment-constraints section, which already records
     this identity as available.)

  2. ``aws ecs describe-services --cluster vyomquant-cluster
     --services vyomquant-api-service-cjema2sl --region ap-southeast-1`` -> succeeds:
     ACTIVE, running=1, desired=1, taskDefinition=vyomquant-api:147.
     (An earlier attempt in ``ap-south-1`` and ``us-east-1`` failed with
     ``ClusterNotFoundException`` - a REGION mismatch, not a permission denial.
     ``ap-southeast-1`` is the region ``infra/deploy.sh`` and ``infra/alb.sh`` hardcode.)

  3. ``aws logs describe-log-groups --region ap-southeast-1`` -> succeeds, and lists the
     real group: ``/ecs/vyomquant-api`` (plus vyomquant-frontend, -mds, -tee, -worker,
     and a questdb group).

  4. ``aws logs filter-log-events --log-group-name "/ecs/vyomquant-api"
     --region ap-southeast-1 --limit 5`` -> SUCCEEDS. This is the call the task
     instruction expected to be denied. It was not denied. The deploy-scoped IAM
     principal in this session CAN read CloudWatch Logs content for this group. The
     5 events returned were inspected and contained no secret VALUE - one line named
     ``SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing`` by NAME (a startup guard
     firing, not a leak) and ordinary Uvicorn startup lines.

  5. ``aws logs describe-log-groups --log-group-name-prefix "/ecs/vyomquant-api"
     --region ap-southeast-1`` -> succeeds: ``storedBytes: 5,610,920,026`` (5.6 GB)
     across several hundred log streams spanning many container restarts and several
     services (api, worker, mds, tee, questdb).

THE ACTUAL GAP, NAMED HONESTLY: no AWS call was denied. The gap is that reading and
grepping 5.6 GB of retained production log content, across hundreds of streams, for
every KNOWN_SECRET_NAME and every credential shape, is a bulk data-pull this automated
test file does not attempt - not because the API refused it, but because pulling
gigabytes of production log data through a test run in this session is disproportionate
to what a single spot-check can honestly claim to prove, and a partial grep over an
arbitrary sample would let a "no secret found in the sample I happened to pull" result
imply a guarantee about the other 5.59 GB that it does not support. A genuine full
audit needs either server-side filtering (a CloudWatch Logs Insights query run to
completion, scoped and reviewed for cost and access-log privacy) or a purpose-built,
separately-authorised job - not a call embedded in a test's setup.

So: **the log group IS reachable and readable by this identity; the size of a genuine
full-content audit, not a permission denial, is what is BLOCKED here.** This is recorded
per the task's own instruction to name the specific gap rather than imply a pass -
it is simply a different gap than the one anticipated.

_Requirements: 1.20, 2.20_
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Obviously-fake fixture material. Never read from .env, never a real exchange key.
FAKE_API_KEY = "FAKE-CANARY-API-KEY-not-a-real-credential-000111"
FAKE_API_SECRET = "FAKE-CANARY-API-SECRET-not-a-real-credential-222333"
FAKE_PASSWORD = "FAKE-CANARY-PASSPHRASE-not-real-444555"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ══════════════════════════════════════════════════════════════════════════════════
#  1. CredentialVault - store then read, over an in-memory Redis double.
#     Same construction pattern as tests/regression/capture_baseline.py.
# ══════════════════════════════════════════════════════════════════════════════════


class _RecordingRedis:
    """Minimal in-memory double covering the calls CredentialVault makes."""

    def __init__(self) -> None:
        self.store: Dict[str, str] = {}

    async def get(self, key: str) -> Optional[str]:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def lpush(self, key: str, value: str) -> None:
        pass

    async def keys(self, pattern: str) -> List[str]:
        return [k for k in self.store if k.startswith(pattern.rstrip("*"))]


class TestCredentialVaultNeverLogsTheValue:
    """Requirement 1.20 / 2.20: exchange credential VALUES are redacted from logs."""

    def test_store_and_get_credential_leak_neither_plaintext_nor_ciphertext(self, caplog):
        from backend_app.core import credential_vault as vault_module

        redis_double = _RecordingRedis()
        original_redis = vault_module.redis_manager
        vault_module.redis_manager = redis_double  # type: ignore[assignment]
        try:
            vault = vault_module.CredentialVault(
                encryption_key="test-log-redaction-master-material-0123456789ab",
                salt="test-log-redaction-salt",
            )

            with caplog.at_level(logging.DEBUG):
                stored = _run(
                    vault.store_credential(
                        user_id="user-log-redaction-1",
                        tenant_id="tenant-log-redaction-1",
                        exchange_id="binance",
                        credential_type=vault_module.CredentialType.API_SECRET,
                        value=FAKE_API_SECRET,
                    )
                )
                retrieved = _run(
                    vault.get_credential(
                        user_id="user-log-redaction-1",
                        tenant_id="tenant-log-redaction-1",
                        exchange_id="binance",
                        credential_type=vault_module.CredentialType.API_SECRET,
                    )
                )

            # Sanity: the round trip actually worked, so a passing redaction assertion
            # below is not vacuous (there was a real secret flowing through real code).
            assert retrieved == FAKE_API_SECRET

            assert FAKE_API_SECRET not in caplog.text, (
                "the plaintext credential value reached the log"
            )
            assert stored.encrypted_value not in caplog.text, (
                "the ciphertext reached the log (not itself the secret, but should still "
                "never be echoed into logs verbatim)"
            )
        finally:
            vault_module.redis_manager = original_redis  # type: ignore[assignment]


# ══════════════════════════════════════════════════════════════════════════════════
#  2. APIKeyVault - store then load, over a fake Supabase client.
# ══════════════════════════════════════════════════════════════════════════════════


class _FakeSupabaseTable:
    """Records rows for one table name, matching the .eq(...).execute() chain shape
    backend_app/backend/api_key_vault.py uses.
    """

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows
        self._filters: Dict[str, Any] = {}
        self._selected_row: Optional[Dict[str, Any]] = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def upsert(self, data):
        self._rows.append(data)
        return self

    def execute(self):
        matches = [
            row
            for row in self._rows
            if all(row.get(k) == v for k, v in self._filters.items())
        ]
        response = MagicMock()
        response.data = matches
        return response


class _FakeSupabaseClient:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def table(self, _name: str):
        return _FakeSupabaseTable(self._rows)


class TestAPIKeyVaultNeverLogsTheValue:
    """Requirement 1.20 / 2.20, the ``api_key_vault.py`` half named in the task."""

    def test_store_and_load_decrypted_keys_leak_neither_plaintext_nor_ciphertext(
        self, monkeypatch, caplog
    ):
        from backend_app.backend import api_key_vault as vault_module

        monkeypatch.setenv("SUPABASE_URL", "https://fake-log-redaction.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-not-real")
        fernet_key = vault_module.Fernet.generate_key().decode()
        monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", fernet_key)

        rows: List[Dict[str, Any]] = []
        with patch.object(
            vault_module, "create_client", return_value=_FakeSupabaseClient(rows)
        ):
            vault = vault_module.APIKeyVault()

            with caplog.at_level(logging.DEBUG):
                vault.store_exchange_keys(
                    user_id="user-log-redaction-2",
                    exchange_id="kraken",
                    raw_api_key=FAKE_API_KEY,
                    raw_secret=FAKE_API_SECRET,
                    raw_password=FAKE_PASSWORD,
                )
                loaded = vault.load_decrypted_keys(
                    user_id="user-log-redaction-2", exchange_id="kraken"
                )

            assert loaded["api_key"] == FAKE_API_KEY
            assert loaded["secret_key"] == FAKE_API_SECRET
            assert loaded["password"] == FAKE_PASSWORD

            for secret_value in (FAKE_API_KEY, FAKE_API_SECRET, FAKE_PASSWORD):
                assert secret_value not in caplog.text, (
                    f"a credential value reached the log during store/load"
                )
            encrypted_row = rows[0]
            for ciphertext_field in (
                "encrypted_api_key",
                "encrypted_secret_key",
                "encrypted_password",
            ):
                ciphertext = encrypted_row.get(ciphertext_field)
                if ciphertext:
                    assert ciphertext not in caplog.text


# ══════════════════════════════════════════════════════════════════════════════════
#  3. exchange_executor.py — the regression: exception-string logging on connect()
#     failure. See module docstring, section 3.
# ══════════════════════════════════════════════════════════════════════════════════


class _FakeCCXTExchange:
    """A CCXT exchange double whose fetch_balance fails with the api_secret embedded in
    the exception message - simulating an underlying library (CCXT, urllib3, the venue
    itself) that echoes request parameters back into an error string. This is the
    scenario STEP 6.10's "never logged" comment did not, by itself, rule out.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config

    async def load_markets(self):
        return {}

    async def fetch_balance(self):
        # Deliberately embeds the secret in the exception text, the way a real
        # authentication failure from a misbehaving client library could.
        raise RuntimeError(
            f"authentication failed for apiKey={self._config['apiKey']} "
            f"secret={self._config['secret']}"
        )

    async def close(self):
        pass


class TestExchangeExecutorConnectFailureDoesNotLeakTheSecretInTheLog:
    """The claim in the file's own comments - "STEP 6.10: Security - keys passed to
    CCXT, never logged" - is TRUE for every direct log call in the file (verified by
    reading each one; none interpolates self._api_key / self._api_secret / self._password
    directly). This is the regression test for the one path that did NOT directly name
    the credential but leaked it indirectly, before this task: the failure branch's
    ``logger.error(f"...: {e}")``, where ``e`` is whatever the underlying library raised.
    Run against the pre-fix tree, this test fails with the fake secret found verbatim in
    ``caplog.text``. ``BaseExchangeExecutor._redact_credentials``, called from
    ``_handle_ccxt_error`` (the single choke point every caller already routes through),
    is the fix.
    """

    def test_a_connect_failure_whose_exception_embeds_the_secret_is_redacted(self, caplog):
        from backend_app.backend import exchange_executor as ex_module

        executor = ex_module.CCXTExchangeExecutor(
            exchange_id="binance",
            api_key=FAKE_API_KEY,
            api_secret=FAKE_API_SECRET,
            sandbox=True,
        )

        fake_ccxt_module = MagicMock()
        fake_ccxt_module.binance = _FakeCCXTExchange

        with patch.object(ex_module, "ccxt", fake_ccxt_module), \
             patch.object(ex_module, "CCXT_AVAILABLE", True), \
             caplog.at_level(logging.DEBUG):
            with pytest.raises(ex_module.ExchangeError):
                _run(executor.connect())

        assert FAKE_API_SECRET not in caplog.text, (
            "connect()'s failure-path log leaked the exchange secret via the "
            "underlying exception's message - the STEP 6.10 comment claims keys are "
            "'never logged', but the exception-string log line "
            "(logger.error(f'Failed to connect to {self.exchange_id}: {e}')) only "
            "appeared safe because no exception previously exercised embedded the "
            "secret in its message. _redact_credentials must be applied before this "
            "log call."
        )
        assert FAKE_API_KEY not in caplog.text, (
            "connect()'s failure-path log leaked the exchange api key via the "
            "underlying exception's message"
        )
        # The redaction fix must still surface the failure signal for operators - it
        # should replace the secret, not silently swallow the whole log line.
        assert "Failed to connect to binance" in caplog.text
        assert "[REDACTED]" in caplog.text

    def test_every_other_caller_of_handle_ccxt_error_benefits_from_the_same_fix(self, caplog):
        """place_order, cancel_order and get_balance all route failures through
        ``_handle_ccxt_error`` and log ``error.message`` rather than ``str(e)``
        directly. Fixing redaction at that one choke point means none of those sites
        needed an individual edit - this asserts that is actually true for one of them
        (get_balance, the simplest), rather than only trusting the code reading.
        """
        from backend_app.backend import exchange_executor as ex_module

        executor = ex_module.CCXTExchangeExecutor(
            exchange_id="binance",
            api_key=FAKE_API_KEY,
            api_secret=FAKE_API_SECRET,
            sandbox=True,
        )
        executor._connected = True

        failing_exchange = MagicMock()

        async def _raise_with_secret():
            raise RuntimeError(f"rate limit exceeded for key {FAKE_API_KEY}")

        failing_exchange.fetch_balance = _raise_with_secret
        executor._exchange = failing_exchange

        with caplog.at_level(logging.DEBUG):
            result = _run(executor.get_balance())

        assert result == {}
        assert FAKE_API_KEY not in caplog.text, (
            "get_balance()'s failure path leaked the api key via _handle_ccxt_error's "
            "stored exception message"
        )
