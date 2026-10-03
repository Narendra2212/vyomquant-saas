"""
tests/test_no_credential_in_api_response_body.py — clause 1.20's THIRD surface.

Clause 1.20 names three places a credential may not appear: the built frontend bundle, the
production logs, and **an API response body**. The bundle half is held by
``tests/test_no_secrets_in_bundle.py``. The log half was discharged by task 13.26 (audited
against the live log group) and tightened by 13.27. The response-body half was the named
remaining gap, and this file closes it.

WHY THE GAP SURVIVED THIS LONG: TWO TESTS THAT LOOK LIKE COVER AND ARE VACUOUS
------------------------------------------------------------------------------
``tests/test_exchange_connection_security.py::test_connection_metadata_redaction`` and
``tests/test_exchange_connection_e2e.py::test_e2e_schema_to_preflight_flow`` both assert
that ``api_key`` / ``secret_key`` / ``password`` are absent **from a dict literal the test
itself wrote three lines earlier**. The author picks the keys, then asserts those keys are
missing. No route is called. The first file's module docstring claims "Stored secrets never
leak in GET /api/exchanges/connections" while never once naming that path.
``tests/test_exchange_vault_contract.py`` has no response-body assertion at all and asserts
in the *opposite* direction, that ``ExchangeKeysRequest`` retains ``api_key`` /
``secret_key`` — correct for a request model, silent about responses. Real response-body
scans do exist elsewhere in this suite (``test_deterministic_sandbox.py``,
``test_asset_discovery.py``, ``test_support_feature_e2e.py``,
``test_task_13_2_signal_trace_detail.py``, ``test_notifications_feature_e2e.py``,
``test_model_versioning.py``) but not one of them drives a credential-bearing route.

THE DESIGN RULE THIS FILE FOLLOWS: ASSERT ON A VALUE YOU PLANTED, NOT A KEY YOU CHOSE
-------------------------------------------------------------------------------------
Every credential this file puts into the system is a high-entropy **sentinel** (see
:data:`ALL_SENTINELS`). The sentinel goes into the store, the vault or the request body;
the assertion is that the sentinel appears **nowhere in the serialised response**. That is
what the two broken tests cannot do: a key allowlist only ever finds the names its author
thought of, so a leak through a nested object, an ``details`` member, an error ``message``
or an echoed request body walks straight past it.

Both halves are asserted, because they fail differently:

  * the **key-name** scan catches a field that is renamed but still carries the secret
    (``apiKey``, ``credential``, ``encrypted_api_key``);
  * the **sentinel** scan catches a value that escapes through a field nobody enumerated.

Scanning is over ``response.text`` — the whole serialised body as text, not a field list —
for the same reason. And the scan terms are **exact literals**, never patterns: this spec
has now burned three times on a probe that matched something adjacent to its target
(``re_`` matching ``total_exposure_usdt``, an unscoped ``token=`` matching a QuestDB view
name, a ``"136-to-2" not in text`` guard firing on its own correction).

EVERY SCAN IS PAIRED WITH A POSITIVE CONTROL, WHICH IS THE OTHER HALF OF NON-VACUITY
------------------------------------------------------------------------------------
A "no sentinel in the body" assertion passes trivially against an empty list, a 500, or a
route that was never reached — which is exactly how the two tests above ended up proving
nothing. So each route under test also asserts that a **planted non-secret** came back:
:data:`CONNECTED_AT` for the connections read (it can only have come from the seeded row),
:data:`PLANTED_BALANCE` for the verification routes (it can only have come from the double
the credential was handed to), and the vault's own record that it received the sentinel for
the storage route. If the positive control fails, the negative assertions mean nothing and
the test says so in its message.

THE STORE IS SCHEMA-FAITHFUL, REUSED RATHER THAN REWRITTEN
----------------------------------------------------------
:class:`tests.test_mounted_endpoint_projections._SchemaFaithfulTable` (task 13.18/13.19) is
the double: its ``.select()`` raises a ``42703`` for any column ``public.exchange_keys``
does not have and narrows returned rows to the projection, as PostgreSQL does. That matters
here for one specific reason — a permissive double that ignores its projection would hand
``encrypted_api_key`` back to the handler on a ``select("exchange_id, created_at")``, and a
clean response would then be the double's doing rather than the handler's. The column
oracle is the drift suite's own ``_allowed_columns``, not a list written beside this file.

**Validates: Requirements 1.20**
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import ccxt
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.redis_manager import get_redis_manager  # noqa: E402
from backend_app.core.dependencies import (  # noqa: E402
    get_current_user,
    get_request_supabase,
    get_vault,
)
from backend_app.main import app  # noqa: E402
from tests.test_mounted_endpoint_projections import (  # noqa: E402
    _SchemaFaithfulSupabase,
    _SchemaFaithfulTable,
    _columns_of,
)

# ══════════════════════════════════════════════════════════════════════════
#  THE SENTINELS
# ══════════════════════════════════════════════════════════════════════════
#
# High-entropy on purpose. Each of these can only be in a response body because the
# system put it there: none of them is a word, a path, a column name, a status or a
# number, so none can arise by coincidence the way `re_` or `token=` did. They are also
# distinct from one another, so a failure names WHICH credential escaped and through
# which route rather than reporting "a secret leaked".

#: The plaintext api key a caller submits to ``POST /keys`` and ``POST /test``, and the one
#: the vault hands back to ``POST /test-stored``.
SENTINEL_API_KEY = "SENTINEL_API_KEY_9f3c1d4a7e20b815"

#: The plaintext secret, same three routes.
SENTINEL_API_SECRET = "SENTINEL_API_SECRET_7b2e8450cf1396ad"

#: The plaintext passphrase (``password`` in this router's vocabulary).
SENTINEL_PASSPHRASE = "SENTINEL_PASSPHRASE_3d9a61fe08b742cc"

#: What sits in ``exchange_keys.encrypted_api_key`` at rest. Ciphertext is still stored
#: credential material and still may not be served, so it gets its own sentinel rather
#: than being folded into the plaintext one — a leak of the row is a different defect
#: from a leak of the request, and they must be distinguishable in a failure.
SENTINEL_CIPHERTEXT_API_KEY = "SENTINEL_CIPHERTEXT_APIKEY_5c08e7b2914fda63"

#: ``exchange_keys.encrypted_secret_key`` at rest.
SENTINEL_CIPHERTEXT_SECRET = "SENTINEL_CIPHERTEXT_SECRET_a41d6f09e3b85c72"

#: ``exchange_keys.encrypted_password`` at rest.
SENTINEL_CIPHERTEXT_PASSWORD = "SENTINEL_CIPHERTEXT_PASSWORD_8e25c7a0d6f1b394"

ALL_SENTINELS: Tuple[str, ...] = (
    SENTINEL_API_KEY,
    SENTINEL_API_SECRET,
    SENTINEL_PASSPHRASE,
    SENTINEL_CIPHERTEXT_API_KEY,
    SENTINEL_CIPHERTEXT_SECRET,
    SENTINEL_CIPHERTEXT_PASSWORD,
)

#: Field names that would carry a credential if they appeared in one of these bodies. The
#: key-name half of the scan. Deliberately NOT applied to the connection-schema routes,
#: which legitimately enumerate these very strings as form field identifiers — see
#: :class:`TestTheExcludedRoutesAreExcludedForAStatedReason`, which proves that is so
#: rather than asserting it.
CREDENTIAL_KEY_NAMES: Tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret_key",
    "secretkey",
    "passphrase",
    "password",
    "encrypted_api_key",
    "encrypted_secret_key",
    "encrypted_password",
)

# ══════════════════════════════════════════════════════════════════════════
#  PLANTED NON-SECRETS — the positive controls
# ══════════════════════════════════════════════════════════════════════════

OWNER_ID = "usr_credential_body_owner"
OWNER: Dict[str, Any] = {
    "id": OWNER_ID,
    "email": "trader@test.vyomquant.io",
    "access_token": "token_credential_body_owner",
    "role": "authenticated",
}

EXCHANGE_ID = "binance"

#: ``exchange_keys.created_at`` on the seeded row. Odd to the microsecond so that its
#: presence in a body can only mean the handler read the row this test planted.
CONNECTED_AT = "2026-03-04T05:06:07.890123+00:00"

#: The USDT total the balance double reports. ``round(x, 2)`` leaves it unchanged, so the
#: handler's own arithmetic cannot disguise it, and no default or fallback in this router
#: produces it.
PLANTED_BALANCE = 4242.42

EXCHANGES_PATH = "/api/exchanges"
EXCHANGES_SLASH_PATH = "/api/exchanges/"
CONNECTIONS_PATH = "/api/exchanges/connections"
KEYS_PATH = "/api/exchanges/keys"
TEST_PATH = "/api/exchanges/test"
TEST_STORED_PATH = "/api/exchanges/test-stored"
CONNECTION_TEST_PATH = f"/api/exchanges/connections/{EXCHANGE_ID}/test"
RECONNECT_PATH = f"/api/exchanges/connections/{EXCHANGE_ID}/reconnect"

client = TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════
#  THE SCAN
# ══════════════════════════════════════════════════════════════════════════


def _assert_route_answered(response, control: str, where: str) -> None:
    """The positive control. Without this every scan below passes on an empty body.

    ``control`` is a value this test planted and nothing else in the system produces, so
    its presence proves the handler reached the data it is being scanned for.
    """
    assert control in response.text, (
        "POSITIVE CONTROL FAILED for %s: the planted value %r is absent from the "
        "response, so this route did not return the data it was seeded with and the "
        "credential assertions below would pass vacuously. status=%s body=%.400s"
        % (where, control, response.status_code, response.text)
    )


def _assert_no_sentinel(response, where: str, sentinels=ALL_SENTINELS) -> None:
    """No planted credential value appears anywhere in the serialised body.

    Over ``response.text`` rather than a field list, so a nested object, a ``details``
    member, an error ``message`` or an echoed request body is in scope. Exact literal
    containment, never a pattern.
    """
    for sentinel in sentinels:
        assert sentinel not in response.text, (
            "CREDENTIAL LEAK at %s: the planted credential %r is present in the "
            "response body. status=%s body=%.600s"
            % (where, sentinel, response.status_code, response.text)
        )


def _assert_no_credential_key_name(response, where: str) -> None:
    """No credential-bearing field name appears in the body.

    The complement of the sentinel scan: this catches a field that still carries the
    secret under a different name, which a value scan would miss if the value were
    transformed on the way out.
    """
    lowered = response.text.lower()
    for name in CREDENTIAL_KEY_NAMES:
        assert name not in lowered, (
            "CREDENTIAL FIELD at %s: the response body names %r. status=%s body=%.600s"
            % (where, name, response.status_code, response.text)
        )


# ══════════════════════════════════════════════════════════════════════════
#  THE STORE — 13.18/13.19's schema-faithful double, seeded with sentinels
# ══════════════════════════════════════════════════════════════════════════


def _seeded_row() -> Dict[str, Any]:
    """One ``public.exchange_keys`` row, every credential column carrying a sentinel."""
    return {
        "user_id": OWNER_ID,
        "exchange_id": EXCHANGE_ID,
        "created_at": CONNECTED_AT,
        "encrypted_api_key": SENTINEL_CIPHERTEXT_API_KEY,
        "encrypted_secret_key": SENTINEL_CIPHERTEXT_SECRET,
        "encrypted_password": SENTINEL_CIPHERTEXT_PASSWORD,
    }


def _seeded_db() -> _SchemaFaithfulSupabase:
    return _SchemaFaithfulSupabase(
        {
            "exchange_keys": [_seeded_row()],
            "strategies": [
                {
                    "id": "strat-credential-body",
                    "user_id": OWNER_ID,
                    "exchange_id": EXCHANGE_ID,
                    "status": "running",
                }
            ],
        }
    )


class _CredentialEchoingDriverError(Exception):
    """A driver error whose text carries the stored ciphertext.

    This is the shape that makes an error path a leak path, and it is not hypothetical:
    ``store_exchange_keys`` upserts a dict whose values ARE the ciphertext, and PostgreSQL
    renders the offending tuple into ``DETAIL: Failing row contains (...)`` for a
    constraint or width violation on exactly that kind of write. Task 13.19 found the same
    shape from the other direction — a ``42703`` whose driver text reached the client in a
    ``status: 500`` body. A handler that stringifies a caught exception into its response
    therefore leaks, and a happy-path-only test cannot see it.
    """

    def __init__(self, payload: str) -> None:
        self.code = "22001"
        self.message = (
            'value too long for type character varying(512)\n'
            'DETAIL:  Failing row contains (%s, %s, %s).' % (OWNER_ID, EXCHANGE_ID, payload)
        )
        self.hint = "the exchange_keys upsert carried %s" % payload
        super().__init__(self.message)


class _CredentialEchoingReadTable(_SchemaFaithfulTable):
    """``exchange_keys`` whose every statement fails with a credential-bearing error.

    ``delete`` is accepted and then fails on ``execute`` for the same reason the read
    does: ``DELETE /{exchange_id}`` is the other statement against this table, and a
    failure there must not echo the driver text either.
    """

    def delete(self, *args: Any, **kwargs: Any):
        return self

    async def execute(self):
        raise _CredentialEchoingDriverError(SENTINEL_CIPHERTEXT_API_KEY)


class _ReadFailingSupabase(_SchemaFaithfulSupabase):
    def table(self, name: str):
        if name == "exchange_keys":
            return _CredentialEchoingReadTable(
                name,
                self.stores.setdefault(name, []),
                _columns_of(name),
                self.journal,
            )
        return super().table(name)


# ══════════════════════════════════════════════════════════════════════════
#  THE VAULT — faithful to APIKeyVault's surface, holding sentinels
# ══════════════════════════════════════════════════════════════════════════


class _SentinelVault:
    """``APIKeyVault``'s three methods this router uses, holding sentinel credentials.

    ``load_decrypted_keys`` returns the same ``{api_key, secret_key, password}`` shape the
    real vault returns from ``_decrypt``, which is what makes ``POST /test-stored`` and
    the two ``/connections/{id}/...`` routes carry a live credential into the handler.
    """

    def __init__(
        self,
        store_error: Optional[BaseException] = None,
        load_error: Optional[BaseException] = None,
    ) -> None:
        self.store_error = store_error
        self.load_error = load_error
        self.stored: List[Dict[str, Any]] = []
        self.loaded: List[Tuple[str, str]] = []

    def get_user_tier(self, user_id: str) -> Dict[str, Any]:
        return {"subscription_tier": "pro_quant", "max_api_slots": 5}

    def store_exchange_keys(self, **kwargs: Any) -> bool:
        self.stored.append(dict(kwargs))
        if self.store_error is not None:
            raise self.store_error
        return True

    def load_decrypted_keys(self, user_id: str, exchange_id: str) -> Dict[str, Any]:
        self.loaded.append((user_id, exchange_id))
        if self.load_error is not None:
            raise self.load_error
        return {
            "api_key": SENTINEL_API_KEY,
            "secret_key": SENTINEL_API_SECRET,
            "password": SENTINEL_PASSPHRASE,
        }


# ══════════════════════════════════════════════════════════════════════════
#  THE VENUE — ccxt-shaped, and it records the credential it was handed
# ══════════════════════════════════════════════════════════════════════════


class _FakeExchange:
    def __init__(self) -> None:
        self.has: Dict[str, Any] = {"createFuturesOrder": False, "futures": False}

    async def load_time_difference(self) -> int:
        return 0


class _BalanceEngine:
    """``DataEngine``'s one method these handlers call."""

    def __init__(self, exchange: Any) -> None:
        self.exchange = exchange

    async def fetch_wallet_balance_snapshot(self) -> Dict[str, Any]:
        return {"USDT": {"total": PLANTED_BALANCE, "free": PLANTED_BALANCE}}


def _engine(connect_error: Optional[BaseException] = None):
    """A ``ConnectionEngine`` double plus the list of credentials it was constructed with.

    The recorded credentials are the second positive control for the verification routes:
    they prove the sentinel really did reach the venue-dialling code, so "the sentinel is
    not in the body" is a statement about the handler and not about a credential that
    never got that far.
    """
    seen: List[Dict[str, Any]] = []

    class _Engine:
        def __init__(
            self,
            exchange_id: str,
            api_key: Any = None,
            secret_key: Any = None,
            password: Any = None,
            uid: Any = None,
        ) -> None:
            seen.append(
                {
                    "exchange_id": exchange_id,
                    "api_key": api_key,
                    "secret_key": secret_key,
                    "password": password,
                    "uid": uid,
                }
            )

        async def connect(self) -> _FakeExchange:
            if connect_error is not None:
                raise connect_error
            return _FakeExchange()

        async def validate_keys(self) -> bool:
            return True

        async def check_exchange_status(self) -> str:
            return "ok"

        async def disconnect(self) -> None:
            return None

    return _Engine, seen


def _ccxt_auth_error_carrying(*payloads: str) -> BaseException:
    """The ccxt error SC-27 exists for: a venue message built from the submitted key.

    A real ``ccxt.AuthenticationError`` routinely carries the venue's own response body,
    which can echo the parameters that were signed. Using ccxt's own class rather than a
    bare ``Exception`` means ``_verification_refusal``'s classifier is genuinely exercised
    — the test is of the refusal this repository builds, not of a fallback branch.
    """
    return ccxt.AuthenticationError(
        "binance {\"code\":-2015,\"msg\":\"Invalid API-key, IP, or permissions for "
        "action, request: apiKey=%s&signature=%s\"}" % payloads
    )


async def _noop_slot(*args: Any, **kwargs: Any) -> None:
    """The exchange-connection quota gate, neutralised.

    Not the subject: it resolves a plan context and a usage ledger across tables this
    file does not seed, and a refusal from it would short-circuit ``store_keys`` before
    any credential moved — which would make every assertion after it vacuous. Its own
    behaviour is covered by ``tests/test_subscriber_restricted_operations.py``.
    """
    return None


def _submitted_keys_body() -> Dict[str, Any]:
    return {
        "exchange_id": EXCHANGE_ID,
        "api_key": SENTINEL_API_KEY,
        "secret_key": SENTINEL_API_SECRET,
        "password": SENTINEL_PASSPHRASE,
        "label": "Main Account",
    }


@pytest.fixture
def overrides():
    """Auth, store, vault and cache, torn down whatever the test does."""

    def _install(db: Any = None, vault: Any = None):
        app.dependency_overrides[get_current_user] = lambda: dict(OWNER)
        app.dependency_overrides[get_request_supabase] = lambda: db
        app.dependency_overrides[get_vault] = lambda: vault
        app.dependency_overrides[get_redis_manager] = lambda: None
        return db, vault

    yield _install
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════
#  GET /api/exchanges  and  GET /api/exchanges/connections
# ══════════════════════════════════════════════════════════════════════════


class TestTheConnectionsReadServesNoStoredCredential:
    """The two read routes that touch ``exchange_keys``.

    ``GET /connections`` is not a separate implementation — it delegates straight to
    ``list_exchanges`` — but it is a separately mounted path with its own docstring
    promising "without exposing secrets", and the vacuous test in
    ``test_exchange_connection_security.py`` named this path specifically. So it is driven
    rather than argued about.
    """

    @pytest.mark.parametrize(
        "path", [EXCHANGES_PATH, EXCHANGES_SLASH_PATH, CONNECTIONS_PATH]
    )
    def test_the_read_returns_the_row_that_was_seeded(self, path, overrides):
        overrides(_seeded_db(), _SentinelVault())
        res = client.get(path)
        assert res.status_code == 200, res.text
        _assert_route_answered(res, CONNECTED_AT, path)
        body = res.json()
        assert isinstance(body, list) and len(body) == 1, res.text
        assert body[0]["exchange_id"] == EXCHANGE_ID
        assert body[0]["connected_at"] == CONNECTED_AT

    @pytest.mark.parametrize(
        "path", [EXCHANGES_PATH, EXCHANGES_SLASH_PATH, CONNECTIONS_PATH]
    )
    def test_no_stored_credential_appears_in_the_read_body(self, path, overrides):
        overrides(_seeded_db(), _SentinelVault())
        res = client.get(path)
        _assert_route_answered(res, CONNECTED_AT, path)
        _assert_no_sentinel(res, path)

    @pytest.mark.parametrize(
        "path", [EXCHANGES_PATH, EXCHANGES_SLASH_PATH, CONNECTIONS_PATH]
    )
    def test_no_credential_field_name_appears_in_the_read_body(self, path, overrides):
        overrides(_seeded_db(), _SentinelVault())
        res = client.get(path)
        _assert_route_answered(res, CONNECTED_AT, path)
        _assert_no_credential_key_name(res, path)

    def test_the_masked_key_is_not_built_from_the_stored_key(self, overrides):
        """``masked_key`` is the one field that LOOKS like it could carry key material.

        13.26 read the source and concluded it is built from the exchange id. This drives
        it: the seeded ciphertext is present in the store, and no prefix or suffix of it
        reaches the mask.
        """
        overrides(_seeded_db(), _SentinelVault())
        res = client.get(EXCHANGES_PATH)
        mask = res.json()[0]["masked_key"]
        assert mask.startswith(EXCHANGE_ID[:3].upper()), mask
        for sentinel in ALL_SENTINELS:
            for width in (4, 6, 8):
                assert sentinel[:width] not in mask, (
                    "masked_key %r carries the leading %d characters of %r"
                    % (mask, width, sentinel)
                )

    def test_a_failed_read_does_not_echo_the_drivers_credential_text(self, overrides):
        """The error path, which a happy-path-only test cannot see.

        The read fails with a driver error whose text carries the stored ciphertext —
        13.19's shape. The refusal must carry its stable code and none of that text.
        """
        overrides(_ReadFailingSupabase({"exchange_keys": [_seeded_row()]}), _SentinelVault())
        res = client.get(EXCHANGES_PATH)
        assert res.status_code == 503, res.text
        _assert_route_answered(res, "EXCHANGE_CONNECTIONS_READ_FAILED", "read failure")
        _assert_no_sentinel(res, "%s (read failure)" % EXCHANGES_PATH)
        assert "22001" not in res.text, res.text
        assert "Failing row" not in res.text, res.text

    def test_the_absent_client_refusal_carries_no_credential(self, overrides):
        """``supabase=None`` is the other 503 branch and has its own body."""
        overrides(None, _SentinelVault())
        res = client.get(EXCHANGES_PATH)
        assert res.status_code == 503, res.text
        _assert_route_answered(res, "EXCHANGE_CONNECTIONS_READ_FAILED", "no client")
        _assert_no_sentinel(res, "%s (no client)" % EXCHANGES_PATH)
        _assert_no_credential_key_name(res, "%s (no client)" % EXCHANGES_PATH)


# ══════════════════════════════════════════════════════════════════════════
#  POST /api/exchanges/keys
# ══════════════════════════════════════════════════════════════════════════


class TestStoreKeysDoesNotEchoTheSubmittedCredential:
    """The one route whose REQUEST body is a credential.

    An echoed request is the leak a key allowlist is worst at: the names are the ones the
    client sent, so they look expected. The quota gate is stubbed to a no-op because it
    reads the plan ledger across half a dozen tables and is not what is under test here;
    everything downstream of it — the vault write, the venue dial, both error paths — is
    the real handler.
    """

    def test_the_vault_received_the_sentinel_and_the_route_answered(self, overrides):
        _, vault = overrides(_seeded_db(), _SentinelVault())
        engine, seen = _engine()
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ), patch(
            "backend_app.routers.exchange.check_exchange_connection_slot",
            new=_noop_slot,
        ):
            res = client.post(KEYS_PATH, json=_submitted_keys_body())
        assert res.status_code == 200, res.text
        assert seen and seen[0]["api_key"] == SENTINEL_API_KEY, seen
        assert vault.stored and vault.stored[0]["raw_api_key"] == SENTINEL_API_KEY, (
            "POSITIVE CONTROL FAILED: the vault never received the submitted credential, "
            "so this request did not exercise the storage path at all. stored=%r"
            % (vault.stored,)
        )
        _assert_route_answered(res, "securely encrypted", KEYS_PATH)

    def test_the_success_body_does_not_echo_the_submitted_credential(self, overrides):
        _, vault = overrides(_seeded_db(), _SentinelVault())
        engine, _seen = _engine()
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ), patch(
            "backend_app.routers.exchange.check_exchange_connection_slot",
            new=_noop_slot,
        ):
            res = client.post(KEYS_PATH, json=_submitted_keys_body())
        assert vault.stored, "the storage path was not reached"
        _assert_no_sentinel(res, KEYS_PATH)
        _assert_no_credential_key_name(res, KEYS_PATH)

    def test_a_failed_storage_write_does_not_echo_the_driver_text(self, overrides):
        """The ``except Exception`` branch: a PostgREST error carrying the ciphertext."""
        vault = _SentinelVault(
            store_error=_CredentialEchoingDriverError(SENTINEL_CIPHERTEXT_API_KEY)
        )
        overrides(_seeded_db(), vault)
        engine, _seen = _engine()
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ), patch(
            "backend_app.routers.exchange.check_exchange_connection_slot",
            new=_noop_slot,
        ):
            res = client.post(KEYS_PATH, json=_submitted_keys_body())
        assert res.status_code == 500, res.text
        assert vault.stored, "the storage path was not reached"
        _assert_route_answered(res, "EXCHANGE_KEY_STORAGE_FAILED", "storage failure")
        _assert_no_sentinel(res, "%s (storage failure)" % KEYS_PATH)
        assert "Failing row" not in res.text, res.text

    def test_a_vault_argument_refusal_does_not_echo_the_credential(self, overrides):
        """The ``except ValueError`` branch, which DOES return ``str(e)``.

        That branch is the one place in this router where a caught exception's text
        reaches the client, so it is the one that has to be driven rather than read.
        ``APIKeyVault`` raises ValueError from ``_validate_id`` (which interpolates the
        user id or the exchange id, both caller-chosen and non-secret) and from
        "api_key and secret_key are required." — neither interpolates key material. Both
        real messages are replayed here; the assertion is that the sentinel the same call
        was holding does not come back with them.
        """
        for message in (
            "exchange_id '%s!' contains invalid characters. Only alphanumeric, hyphens, "
            "and underscores are allowed." % EXCHANGE_ID,
            "api_key and secret_key are required.",
        ):
            vault = _SentinelVault(store_error=ValueError(message))
            overrides(_seeded_db(), vault)
            engine, _seen = _engine()
            with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
                "backend_app.routers.exchange.DataEngine", _BalanceEngine
            ), patch(
                "backend_app.routers.exchange.check_exchange_connection_slot",
                new=_noop_slot,
            ):
                res = client.post(KEYS_PATH, json=_submitted_keys_body())
            assert res.status_code == 400, res.text
            assert vault.stored, "the storage path was not reached"
            _assert_no_sentinel(res, "%s (vault ValueError)" % KEYS_PATH)

    def test_a_rejected_verification_does_not_echo_the_venue_text(self, overrides):
        """SC-27 at the body level: ccxt's message is built from the submitted key."""
        overrides(_seeded_db(), _SentinelVault())
        engine, seen = _engine(
            connect_error=_ccxt_auth_error_carrying(
                SENTINEL_API_KEY, SENTINEL_API_SECRET
            )
        )
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ), patch(
            "backend_app.routers.exchange.check_exchange_connection_slot",
            new=_noop_slot,
        ):
            res = client.post(KEYS_PATH, json=_submitted_keys_body())
        assert res.status_code == 400, res.text
        assert seen and seen[0]["api_key"] == SENTINEL_API_KEY, (
            "POSITIVE CONTROL FAILED: the venue was never dialled with the sentinel, so "
            "the refusal under test was not built from a credential-bearing failure."
        )
        _assert_route_answered(res, "EXCHANGE_VERIFICATION_REJECTED", "verification")
        _assert_no_sentinel(res, "%s (verification refused)" % KEYS_PATH)
        assert "-2015" not in res.text, res.text


# ══════════════════════════════════════════════════════════════════════════
#  POST /api/exchanges/test
# ══════════════════════════════════════════════════════════════════════════


class TestTheProvidedCredentialTestDoesNotEchoIt:
    """``POST /test`` takes the credential in its body and never stores it."""

    def test_the_verified_body_reports_the_planted_balance(self, overrides):
        overrides(_seeded_db(), _SentinelVault())
        engine, seen = _engine()
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ):
            res = client.post(TEST_PATH, json=_submitted_keys_body())
        assert res.status_code == 200, res.text
        assert seen and seen[0]["secret_key"] == SENTINEL_API_SECRET, seen
        _assert_route_answered(res, str(PLANTED_BALANCE), TEST_PATH)
        assert res.json()["status"] == "CONNECTED"

    def test_the_verified_body_does_not_echo_the_submitted_credential(self, overrides):
        overrides(_seeded_db(), _SentinelVault())
        engine, seen = _engine()
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ):
            res = client.post(TEST_PATH, json=_submitted_keys_body())
        _assert_route_answered(res, str(PLANTED_BALANCE), TEST_PATH)
        assert seen, "the venue was never dialled"
        _assert_no_sentinel(res, TEST_PATH)
        _assert_no_credential_key_name(res, TEST_PATH)

    @pytest.mark.parametrize(
        "error,expected_status",
        [
            ("auth", 400),
            ("permission", 403),
            ("network", 503),
            ("unclassified", 502),
        ],
    )
    def test_no_failure_class_echoes_the_venue_text(
        self, error, expected_status, overrides
    ):
        """Every arm of ``_verification_refusal``, each with the sentinel in the cause.

        One failure class per arm rather than one failure overall, because the arms build
        four different bodies and a leak in any one of them is a leak.
        """
        causes = {
            "auth": _ccxt_auth_error_carrying(SENTINEL_API_KEY, SENTINEL_API_SECRET),
            "permission": ccxt.PermissionDenied(
                "binance permissions denied for apiKey=%s" % SENTINEL_API_KEY
            ),
            "network": ccxt.NetworkError(
                "connection reset while signing with %s" % SENTINEL_API_SECRET
            ),
            "unclassified": RuntimeError(
                "unexpected failure holding %s / %s"
                % (SENTINEL_API_KEY, SENTINEL_PASSPHRASE)
            ),
        }
        overrides(_seeded_db(), _SentinelVault())
        engine, seen = _engine(connect_error=causes[error])
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ):
            res = client.post(TEST_PATH, json=_submitted_keys_body())
        assert res.status_code == expected_status, res.text
        assert seen and seen[0]["api_key"] == SENTINEL_API_KEY, (
            "POSITIVE CONTROL FAILED: the %s failure was raised before the credential "
            "reached the engine." % error
        )
        _assert_route_answered(res, "EXCHANGE_", "%s failure" % error)
        _assert_no_sentinel(res, "%s (%s failure)" % (TEST_PATH, error))


# ══════════════════════════════════════════════════════════════════════════
#  POST /test-stored  and the two /connections/{id}/... routes
# ══════════════════════════════════════════════════════════════════════════


STORED_CREDENTIAL_ROUTES = (
    (TEST_STORED_PATH, {"exchange_id": EXCHANGE_ID}),
    (CONNECTION_TEST_PATH, None),
    (RECONNECT_PATH, None),
)


class TestTheStoredCredentialRoutesDoNotReturnWhatTheyDecrypted:
    """The three routes that pull a DECRYPTED credential out of the vault.

    These are the highest-risk bodies in the router: unlike ``POST /test``, the plaintext
    here was not supplied by the caller, so a leak hands over a credential the caller may
    no longer possess. ``/connections/{id}/reconnect`` is the specific reason this file
    scans ``response.text`` rather than a field list — it nests the entire
    ``test_stored_connection`` result under a ``details`` member, which is precisely the
    shape a key allowlist written against the top level walks past.
    """

    @pytest.mark.parametrize("path,body", STORED_CREDENTIAL_ROUTES)
    def test_the_route_used_the_vaulted_credential_and_answered(
        self, path, body, overrides
    ):
        _, vault = overrides(_seeded_db(), _SentinelVault())
        engine, seen = _engine()
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ):
            res = client.post(path, json=body)
        assert res.status_code == 200, res.text
        assert vault.loaded == [(OWNER_ID, EXCHANGE_ID)], vault.loaded
        assert seen and seen[0]["api_key"] == SENTINEL_API_KEY, (
            "POSITIVE CONTROL FAILED for %s: the decrypted sentinel never reached the "
            "engine, so nothing credential-bearing was in flight." % path
        )
        _assert_route_answered(res, str(PLANTED_BALANCE), path)

    @pytest.mark.parametrize("path,body", STORED_CREDENTIAL_ROUTES)
    def test_no_decrypted_credential_appears_in_the_body(self, path, body, overrides):
        overrides(_seeded_db(), _SentinelVault())
        engine, seen = _engine()
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ):
            res = client.post(path, json=body)
        _assert_route_answered(res, str(PLANTED_BALANCE), path)
        assert seen, "the venue was never dialled"
        _assert_no_sentinel(res, path)
        _assert_no_credential_key_name(res, path)

    def test_the_reconnect_envelope_nests_the_result_and_still_carries_nothing(
        self, overrides
    ):
        """The nesting is asserted, not assumed — otherwise the scan above proves less."""
        overrides(_seeded_db(), _SentinelVault())
        engine, _seen = _engine()
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ):
            res = client.post(RECONNECT_PATH)
        body = res.json()
        assert body["status"] == "RECONNECTED", res.text
        assert body["details"]["usdt_balance"] == PLANTED_BALANCE, (
            "POSITIVE CONTROL FAILED: reconnect did not nest the verification result "
            "under `details`, so the nested-leak case is not being exercised. body=%r"
            % (body,)
        )
        _assert_no_sentinel(res, "%s (nested details)" % RECONNECT_PATH)

    @pytest.mark.parametrize("path,body", STORED_CREDENTIAL_ROUTES)
    def test_a_rejected_stored_credential_does_not_come_back_in_the_refusal(
        self, path, body, overrides
    ):
        overrides(_seeded_db(), _SentinelVault())
        engine, seen = _engine(
            connect_error=_ccxt_auth_error_carrying(
                SENTINEL_API_KEY, SENTINEL_API_SECRET
            )
        )
        with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
            "backend_app.routers.exchange.DataEngine", _BalanceEngine
        ):
            res = client.post(path, json=body)
        assert res.status_code == 400, res.text
        assert seen and seen[0]["api_key"] == SENTINEL_API_KEY, (
            "POSITIVE CONTROL FAILED for %s: no credential was in flight." % path
        )
        _assert_route_answered(res, "EXCHANGE_VERIFICATION_REJECTED", path)
        _assert_no_sentinel(res, "%s (stored credential rejected)" % path)

    @pytest.mark.parametrize("path,body", STORED_CREDENTIAL_ROUTES)
    def test_a_decryption_failure_does_not_come_back_in_the_404(
        self, path, body, overrides
    ):
        """``load_decrypted_keys``' own ValueErrors, replayed verbatim.

        ``_decrypt`` raises "Decryption failed. Key rotation issue or corrupted data." and
        the lookup raises ``f"No keys found for {user_id}/{exchange_id}."`` — the second
        interpolates the caller's own identifiers. This handler answers 404 with a message
        it writes itself, and neither real text nor the sentinel may appear.
        """
        for cause in (
            ValueError("No keys found for %s/%s." % (OWNER_ID, EXCHANGE_ID)),
            ValueError("Decryption failed. Key rotation issue or corrupted data."),
        ):
            _, vault = overrides(_seeded_db(), _SentinelVault(load_error=cause))
            engine, _seen = _engine()
            with patch("backend_app.routers.exchange.ConnectionEngine", engine), patch(
                "backend_app.routers.exchange.DataEngine", _BalanceEngine
            ):
                res = client.post(path, json=body)
            assert res.status_code == 404, res.text
            assert vault.loaded, "the vault was never consulted"
            _assert_route_answered(res, "EXCHANGE_NO_STORED_CREDENTIALS", path)
            _assert_no_sentinel(res, "%s (no stored credential)" % path)
            assert "Decryption failed" not in res.text, res.text


# ══════════════════════════════════════════════════════════════════════════
#  THE EXCLUSIONS, DRIVEN RATHER THAN ASSERTED
# ══════════════════════════════════════════════════════════════════════════


class TestTheDisconnectRouteReturnsNothingItDeleted:
    """``DELETE /{exchange_id}`` — the fourth route that touches ``exchange_keys``.

    It deletes rather than projects, so it was the easiest of the four to leave out on a
    reading. It is driven anyway: a delete that reported the row it removed would be a
    leak of exactly the same shape as a read, and a reading is not a measurement.
    """

    class _DeletableTable(_SchemaFaithfulTable):
        def delete(self, *args: Any, **kwargs: Any):
            self._deleting = True
            return self

        async def execute(self):
            if getattr(self, "_deleting", False):
                self._journal.append((self._name, tuple(self._filters)))
                removed = self._matched()
                for row in removed:
                    self._rows.remove(row)
                return type("_R", (), {"data": removed})()
            return await super().execute()

    class _DeletableSupabase(_SchemaFaithfulSupabase):
        def table(self, name: str):
            if name == "exchange_keys":
                return TestTheDisconnectRouteReturnsNothingItDeleted._DeletableTable(
                    name,
                    self.stores.setdefault(name, []),
                    _columns_of(name),
                    self.journal,
                )
            return super().table(name)

    def test_the_disconnect_removes_the_row_and_reports_none_of_it(self, overrides):
        db = self._DeletableSupabase(
            {"exchange_keys": [_seeded_row()], "strategies": []}
        )
        overrides(db, _SentinelVault())
        with patch(
            "backend_app.routers.exchange.release_exchange", new=_noop_release
        ):
            res = client.delete("/api/exchanges/%s" % EXCHANGE_ID)
        assert res.status_code == 200, res.text
        _assert_route_answered(res, "disconnected successfully", "delete")
        assert db.stores["exchange_keys"] == [], (
            "POSITIVE CONTROL FAILED: the seeded row is still in the store, so this "
            "request did not reach the delete. store=%r" % (db.stores,)
        )
        _assert_no_sentinel(res, "DELETE /api/exchanges/{id}")
        _assert_no_credential_key_name(res, "DELETE /api/exchanges/{id}")

    def test_a_failed_disconnect_does_not_echo_the_driver_text(self, overrides):
        overrides(
            _ReadFailingSupabase({"exchange_keys": [_seeded_row()], "strategies": []}),
            _SentinelVault(),
        )
        with patch(
            "backend_app.routers.exchange.release_exchange", new=_noop_release
        ):
            res = client.delete("/api/exchanges/%s" % EXCHANGE_ID)
        assert res.status_code == 500, res.text
        _assert_route_answered(res, "EXCHANGE_DISCONNECT_FAILED", "delete failure")
        _assert_no_sentinel(res, "DELETE /api/exchanges/{id} (failure)")
        assert "Failing row" not in res.text, res.text


async def _noop_release(*args: Any, **kwargs: Any) -> None:
    """``release_exchange`` evicts a live socket from the connection pool. Out of scope."""
    return None


class TestTheScanItselfFires:
    """The scan is proven to catch a leak, because a scan that cannot is not a gate.

    Every assertion in this file is of the form "the sentinel is absent". A harness bug
    — a sentinel that is never planted, a helper whose loop body is unreachable, a
    ``response.text`` that is always empty — makes all of them pass while measuring
    nothing, which is the failure mode the two tests this file replaces actually had. So
    the helpers are run against a body that DOES leak, and are required to fail.
    """

    class _LeakyResponse:
        status_code = 200

        def __init__(self, text: str) -> None:
            self.text = text

    def test_a_nested_leak_is_caught(self):
        leaky = self._LeakyResponse(
            '{"status":"RECONNECTED","details":{"echo":{"api_key":"%s"}}}'
            % SENTINEL_API_KEY
        )
        with pytest.raises(AssertionError, match="CREDENTIAL LEAK"):
            _assert_no_sentinel(leaky, "harness self-check")

    def test_a_leak_through_an_error_message_is_caught(self):
        leaky = self._LeakyResponse(
            '{"detail":{"error":"X","message":"ccxt said apiKey=%s"}}'
            % SENTINEL_CIPHERTEXT_SECRET
        )
        with pytest.raises(AssertionError, match="CREDENTIAL LEAK"):
            _assert_no_sentinel(leaky, "harness self-check")

    def test_a_renamed_credential_field_is_caught(self):
        leaky = self._LeakyResponse('[{"apiKey":"redacted-but-named"}]')
        with pytest.raises(AssertionError, match="CREDENTIAL FIELD"):
            _assert_no_credential_key_name(leaky, "harness self-check")

    def test_an_empty_body_fails_the_positive_control(self):
        with pytest.raises(AssertionError, match="POSITIVE CONTROL FAILED"):
            _assert_route_answered(self._LeakyResponse("[]"), CONNECTED_AT, "self-check")

    def test_the_seeded_row_really_holds_the_sentinels(self):
        """The planted side of the plant/scan pair, asserted rather than assumed."""
        row = _seeded_row()
        assert row["encrypted_api_key"] == SENTINEL_CIPHERTEXT_API_KEY
        assert row["encrypted_secret_key"] == SENTINEL_CIPHERTEXT_SECRET
        assert row["encrypted_password"] == SENTINEL_CIPHERTEXT_PASSWORD


class TestTheCleanBodyIsTheHandlersDoingNotTheDoubles:
    """The schema-faithful double narrows rows to the projection; so would Postgres.

    That raises a fair objection to every scan above: if the store never hands the
    ciphertext to the handler, a clean response proves nothing about the handler. So this
    drives the same route against a store that returns the WHOLE row — every
    ``encrypted_*`` column, as ``select("*")`` would — and requires the body to stay
    clean. That isolates the property under test to the one thing that matters: the
    handler assembles its response field by field and never splats the row it read.
    """

    class _PermissiveTable(_SchemaFaithfulTable):
        """Columns are still checked; the projection is simply not applied to the rows."""

        async def execute(self):
            self._journal.append((self._name, tuple(self._filters)))
            return type("_R", (), {"data": self._matched()})()

    class _PermissiveSupabase(_SchemaFaithfulSupabase):
        def table(self, name: str):
            if name == "exchange_keys":
                return TestTheCleanBodyIsTheHandlersDoingNotTheDoubles._PermissiveTable(
                    name,
                    self.stores.setdefault(name, []),
                    _columns_of(name),
                    self.journal,
                )
            return super().table(name)

    def test_the_handler_does_not_splat_a_row_it_was_handed_whole(self, overrides):
        db = self._PermissiveSupabase({"exchange_keys": [_seeded_row()], "strategies": []})
        overrides(db, _SentinelVault())
        res = client.get(EXCHANGES_PATH)
        assert res.status_code == 200, res.text
        _assert_route_answered(res, CONNECTED_AT, "permissive store")
        _assert_no_sentinel(res, "%s (permissive store)" % EXCHANGES_PATH)
        _assert_no_credential_key_name(res, "%s (permissive store)" % EXCHANGES_PATH)

    def test_the_permissive_store_really_does_hand_back_the_ciphertext(self):
        """Without this the test above is just the narrowing test again."""
        db = self._PermissiveSupabase({"exchange_keys": [_seeded_row()]})
        table = db.table("exchange_keys").select("exchange_id, created_at").eq(
            "user_id", OWNER_ID
        )
        rows = _run(table.execute()).data
        assert rows and rows[0]["encrypted_api_key"] == SENTINEL_CIPHERTEXT_API_KEY, rows


def _run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


class TestTheExcludedRoutesAreExcludedForAStatedReason:
    """Why the remaining ``routers/exchange.py`` routes are not in the scan above.

    Narrowing a scan silently is how a guard becomes vacuous, so each exclusion is
    recorded here with the fact that justifies it, and the one that could mislead is
    driven.

      * ``GET /certification``, ``GET /{id}/capabilities``, ``GET /{id}/health`` and
        ``POST /{id}/preflight`` — each resolves
        ``get_exchange_certification_registry()`` and returns its answer. No store read,
        no vault call, no request credential; nothing credential-bearing can reach them.
      * ``GET /supported`` — builds its list from ``ccxt.exchanges`` and each venue's
        ``has`` map. Same reason: no store, no vault, no credential in the request. It
        belongs with the schema routes below for the key-name half, because it reports
        ``required_fields`` and those field identifiers ARE ``api_key``, ``secret_key``
        and ``password``.
      * ``GET /schema/{id}`` and ``GET /{id}/connection-schema`` — these describe the
        shape of the connection FORM, so their bodies legitimately contain the strings
        ``api_key``, ``secret_key`` and ``password`` as field identifiers. They are
        therefore out of scope for the key-name scan BY CONSTRUCTION, and the test below
        proves that is the reason rather than a convenient narrowing: the field names are
        present, and no credential value is.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "/api/exchanges/schema/%s" % EXCHANGE_ID,
            "/api/exchanges/%s/connection-schema" % EXCHANGE_ID,
        ],
    )
    def test_the_schema_routes_name_the_fields_without_carrying_a_value(
        self, path, overrides
    ):
        overrides(_seeded_db(), _SentinelVault())
        res = client.get(path)
        assert res.status_code == 200, res.text
        lowered = res.text.lower()
        assert "api_key" in lowered, (
            "POSITIVE CONTROL FAILED: %s does not name `api_key`, so it is not the "
            "form-describing route this exclusion is written for. body=%.300s"
            % (path, res.text)
        )
        _assert_no_sentinel(res, path)
        for field in res.json()["fields"]:
            assert not field.get("value"), field
