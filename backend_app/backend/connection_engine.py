"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 2 — CONNECTION: connection_engine.py                              ║
║                                                                          ║
║  CCXT.pro exchange connector. Manages authenticated exchange sessions.   ║
║  Supports 100+ exchanges via a single unified interface.                 ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  CE-1  load_markets() had no retry — temporary outage killed startup     ║
║  CE-2  config always set apiKey/secret even when None — breaks Coinbase  ║
║  CE-3  disconnect() didn't null self.exchange — stale object on reconnect║
║  CE-4  No exchange instance pooling for multi-bot users                  ║
║  CE-5  validate_keys() called fetch_balance() — wastes rate limit budget ║
║  CE-6  get_supported_exchanges() was a module-level function, not method  ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import hashlib
import json
import logging
from typing import Optional

import ccxt as ccxt_base
import ccxt.pro as ccxt

logger = logging.getLogger("ConnectionEngine")


# ══════════════════════════════════════════════════════════════════════════
#  EXCHANGE INSTANCE POOL
#  FIX CE-4: One authenticated CCXT instance per (user_id, exchange_id).
#  If user has 3 bots all on Binance, they share one socket — not 3.
# ══════════════════════════════════════════════════════════════════════════

_exchange_pool: dict[str, ccxt.Exchange] = {}
_pool_lock = asyncio.Lock()

#: The CREDENTIAL IDENTITY of each pooled client, by pool key (CONN-01).
#:
#: The pool key is deliberately ``sha256([user_id, exchange_id])`` and omits the credential —
#: re-keying on the credential would be wrong, because it would let one (user, venue) pair hold
#: several simultaneous authenticated sockets and would defeat FIX CE-4's whole purpose. So the
#: credential is tracked ALONGSIDE the entry instead, and a mismatch rebuilds in place. See
#: :func:`_credential_identity` for what is stored and why it is not the credential itself.
_pool_credential_id: dict[str, Optional[str]] = {}

#: Whether each pooled client is a SANDBOX client, by pool key (EXCH-01).
#:
#: WHY THIS IS TRACKED SEPARATELY FROM THE CREDENTIAL. The pool key is
#: ``sha256([user_id, exchange_id])`` and a sandbox client and a live client for the same
#: pair used to be INTERCHANGEABLE under it: nothing recorded which environment the pooled
#: client addressed, so a caller asking for testnet could be handed the live client that
#: happened to be pooled first, and vice versa. `exchange_executor`'s own cache got this
#: right -- `_executor_cache_key` folds in ``"sandbox" if sandbox else "live"`` -- and this
#: pool did not.
#:
#: Recorded alongside the entry rather than folded into `_pool_key` on purpose. Changing the
#: key would require every caller of :func:`release_exchange` to know the environment too,
#: and those callers (``routers/exchange.py``'s store/delete paths) do not -- a one-sided
#: edit there would make eviction silently miss, which is CONN-01 again. This mirrors how
#: `_pool_credential_id` already solves the same shape of problem.
_pool_sandbox: dict[str, bool] = {}


def _pool_key(user_id: str, exchange_id: str) -> str:
    """The pool key for one (user, venue) pair.

    🔴 STEP 2 / FIX H1: SHA-256 rather than Python's ``hash()``, which is not cryptographically
    secure and carries collision risk — a collision here would hand one user's authenticated
    client to another, so this is the function that makes per-user isolation true.

    Extracted so that the create and release paths cannot drift apart: they previously inlined
    the same expression twice, and a one-sided edit would have made eviction silently miss.
    """
    return hashlib.sha256(
        json.dumps([user_id, exchange_id], sort_keys=True).encode()
    ).hexdigest()


def _credential_identity(
    api_key: Optional[str],
    secret_key: Optional[str],
    password: Optional[str],
) -> Optional[str]:
    """A non-reversible fingerprint of a credential triple, or ``None`` when there is none.

    WHY A FINGERPRINT AND NOT THE CREDENTIAL. The only question asked of this value is "is it
    the same credential as last time", which equality on a digest answers. Holding the digest
    rather than the key means the pool's own bookkeeping is not a second copy of the secret.
    It is no weaker than what is already in memory either way — the pooled ``ccxt`` client holds
    ``apiKey``/``secret`` in plaintext for the life of the entry — but it keeps the secret out of
    the one structure that gets iterated, counted and logged. This value is NEVER logged.

    ``None`` means "no credential was supplied", which is a distinct case from "a credential
    whose digest happens to be X" and is treated differently in
    :func:`get_or_create_exchange`. A client built with no credential can make public calls
    only, so it is not interchangeable with an authenticated one.
    """
    if not api_key and not secret_key:
        return None
    return hashlib.sha256(
        json.dumps([api_key or "", secret_key or "", password or ""]).encode()
    ).hexdigest()


async def get_or_create_exchange(
    user_id: str,
    exchange_id: str,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    password: Optional[str] = None,
    testnet: bool = False,
    proxies: Optional[dict] = None,
) -> ccxt.Exchange:
    """
    Returns a live exchange instance from the pool, creating one if needed.
    FIX CE-4: Multiple bots for the same user+exchange share one connection.
    FIX H1: Use SHA-256 hash to prevent key collision and API key mixing.

    CONN-01: A POOLED CLIENT IS ONLY REUSED FOR THE CREDENTIAL IT WAS BUILT WITH
    ---------------------------------------------------------------------------
    The pool key is the (user, venue) pair and does not include the credential, which is
    correct — one authenticated socket per pair is the point. What was NOT correct is that a
    later call for an already-pooled pair was served the existing client and its ``api_key``
    argument was silently discarded. Two real consequences, both measured in
    ``staging/p10c_probe_before.txt``:

    1. **A rotation was ineffective in-process.** New keys stored through
       ``POST /api/exchange/keys`` did not reach the venue until the connection was deleted or
       the process restarted. That is the finding as originally written. ``store_keys`` now
       evicts explicitly, and this function no longer depends on it having done so.

    2. **A credential-less caller decided the client for everyone.**
       ``backend/exchange_websocket_listener.py`` calls this function with NO credentials
       (``core/consistency_checker.py`` used to as well, until RT-16 moved it onto a real,
       vault-based per-tenant executor instead -- see that file's own `_get_exchange_positions`
       docstring). Whichever caller arrived first populated the key, so if the remaining
       credential-less caller won the race an UNAUTHENTICATED client was pooled and then handed
       to ``routers/orders.py`` and ``routers/market.py``, whose private calls
       (``fetch_open_orders``, ``fetch_balance``) cannot work on it. A restart was the only cure.

    The rule now applied, which is monotone in authentication and therefore cannot thrash:

    * **No credential supplied** → reuse whatever is pooled. Such a caller is asking for "a
      client for this venue" and must not downgrade an authenticated one it did not build.
    * **A credential matching the pooled client** → reuse. The ordinary multi-bot case; still
      one socket, still one handshake.
    * **A credential DIFFERING from the pooled client's, including the case where the pooled
      client has none** → close the stale client and rebuild. This is a rotation or an upgrade
      from unauthenticated, and in both the caller's credential is the correct one.

    Because the credential-less branch never rebuilds, alternating callers settle on the
    authenticated client rather than tearing it down in turn.

    WHAT THIS IS NOT. It is not a tenant-isolation control. Isolation is :func:`_pool_key`'s
    job and was never broken: one user's client has never been reachable by another's key. This
    is credential CURRENCY within one user's own pair.
    """
    pool_key = _pool_key(user_id, exchange_id)
    wanted = _credential_identity(api_key, secret_key, password)
    wanted_sandbox = bool(testnet)

    async with _pool_lock:
        pooled = _exchange_pool.get(pool_key)
        if pooled is not None:
            held = _pool_credential_id.get(pool_key)
            held_sandbox = _pool_sandbox.get(pool_key, False)

            # SANDBOX EQUALITY IS REQUIRED UNCONDITIONALLY, which is the one place this rule
            # is stricter than credential currency above. "No credential supplied → reuse
            # whatever is pooled" is right for authentication, because such a caller is
            # asking for "a client for this venue" and must not downgrade an authenticated
            # one. It is WRONG for the environment: a sandbox client and a live client are
            # not two states of one connection, they address different hosts with different
            # balances, and a credential-less caller asking for testnet being handed the
            # pooled LIVE client is the EXCH-01 defect arriving through the pool instead of
            # through set_sandbox_mode.
            if held_sandbox == wanted_sandbox and (wanted is None or wanted == held):
                return pooled

            # A different credential, a different environment, or both. Drop the stale client
            # before building its replacement, so the pool never holds two clients for one
            # pair and the superseded socket is closed rather than leaked. Closed while
            # holding `_pool_lock`, which is deliberate: the lock is already held across
            # `connect()`'s venue handshake below, and a local socket close is strictly
            # cheaper than that. Releasing it here instead would open a window for a
            # concurrent caller to pool a third client.
            _exchange_pool.pop(pool_key, None)
            _pool_credential_id.pop(pool_key, None)
            _pool_sandbox.pop(pool_key, None)
            try:
                await pooled.close()
            except Exception as e:
                logger.warning(
                    f"Exchange pool: close error on the superseded client for user {user_id} "
                    f"on {exchange_id}: {e}"
                )
            reason = (
                "the environment requested (sandbox="
                f"{wanted_sandbox}) is not the one the pooled client addresses "
                f"(sandbox={held_sandbox})"
                if held_sandbox != wanted_sandbox
                else "the credential supplied is not the one the pooled client was built with"
            )
            logger.info(
                f"Exchange pool: rebuilding for user {user_id} on {exchange_id} — {reason}."
            )

        engine = ConnectionEngine(
            exchange_id, api_key, secret_key, password, testnet, proxies
        )
        # A connect() that raises propagates with NOTHING written to either dict, so a refused
        # handshake leaves no entry to be served to the next caller. connect() closes the
        # client it built (FIX CE-3), so nothing leaks either.
        exchange = await engine.connect()
        _exchange_pool[pool_key] = exchange
        _pool_credential_id[pool_key] = wanted
        # Written in the same critical section as the client, for the same reason the
        # credential identity is: an entry whose environment is unrecorded defaults to
        # "live" on the next comparison, which would make a sandbox client reusable as a
        # live one exactly once.
        _pool_sandbox[pool_key] = wanted_sandbox
        # Log user/exchange for debugging (not the hashed key, and never the credential
        # fingerprint).
        logger.info(
            f"Exchange pool: created for user {user_id} on {exchange_id} "
            f"(sandbox={wanted_sandbox})"
        )
        return exchange


async def release_exchange(user_id: str, exchange_id: str):
    """Remove and close an exchange from the pool (called when all bots for this user stop).

    A no-op when nothing is pooled for the pair, which is what makes it safe to call
    unconditionally from the create-or-rotate path in ``routers/exchange.py::store_keys``.
    """
    pool_key = _pool_key(user_id, exchange_id)
    async with _pool_lock:
        exchange = _exchange_pool.pop(pool_key, None)
        # Dropped in the same critical section as the client itself: a credential identity or
        # an environment flag left behind for an evicted entry would make the next caller's
        # comparison meaningless.
        _pool_credential_id.pop(pool_key, None)
        _pool_sandbox.pop(pool_key, None)
    if exchange:
        try:
            await exchange.close()
            # Log user/exchange for debugging (not the hashed key)
            logger.info(f"Exchange pool: closed for user {user_id} on {exchange_id}")
        except Exception as e:
            logger.warning(f"Exchange pool: close error for user {user_id} on {exchange_id}: {e}")


def exchange_pool_size() -> int:
    """How many venue clients the pool currently holds.

    Read without the lock on purpose: this is an observability figure for shutdown logging and
    tests, and ``len()`` on a dict is atomic under the GIL. Taking ``_pool_lock`` here would let
    a diagnostic read block behind a 30-second venue handshake.

    The pool is bounded by the number of DISTINCT (user, venue) pairs in flight, not by the
    number of callers: credential rotations replace an entry rather than adding one, and a
    refused handshake adds nothing. There is no eviction by age or idleness — see
    ``staging/PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md`` for why that is recorded as a residual risk
    rather than given an invented ceiling here.
    """
    return len(_exchange_pool)


async def close_all_exchanges() -> int:
    """Close and drop every pooled venue client. Returns how many were closed.

    CONN-01: nothing used to do this. ``main.py``'s lifespan shutdown stopped every background
    service that had OPENED a connection but never closed the connections themselves, so every
    pooled ``ccxt`` client — each holding an open ``aiohttp`` session and a plaintext credential
    — survived until the process died. That is the "Unclosed client session" class of noise this
    module's CE-3 comment describes, arriving at shutdown instead of on a failed connect.

    Every client is attempted even if an earlier one raises: one venue refusing to close must
    not strand the rest. The pool is emptied under the lock FIRST, so a caller arriving mid
    shutdown builds a fresh client rather than being handed one that is in the middle of being
    closed.
    """
    async with _pool_lock:
        draining = list(_exchange_pool.items())
        _exchange_pool.clear()
        _pool_credential_id.clear()
        _pool_sandbox.clear()

    closed = 0
    for pool_key, exchange in draining:
        try:
            await exchange.close()
            closed += 1
        except Exception as e:
            # The pool key is a digest of (user, venue) and names neither, which is why it is
            # the only identifier available here; it is safe to log for exactly that reason.
            logger.warning(f"Exchange pool: close error during drain for {pool_key[:12]}…: {e}")
    if draining:
        logger.info(
            f"Exchange pool: drained {closed} of {len(draining)} pooled client(s) at shutdown."
        )
    return closed


# ══════════════════════════════════════════════════════════════════════════
#  CONNECTION ENGINE
# ══════════════════════════════════════════════════════════════════════════


class ConnectionEngine:
    """
    Manages a single authenticated CCXT.pro exchange connection.
    Use the module-level get_or_create_exchange() pool for multi-bot setups.
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        password: Optional[str] = None,
        testnet: bool = False,
        proxies: Optional[dict] = None,
        uid: Optional[str] = None,
    ):
        self.exchange_id = exchange_id.lower()
        self.api_key = api_key if api_key != "dummy_api_key" else None
        self.secret_key = secret_key if secret_key != "dummy_secret_key" else None
        self.password = password
        self.uid = uid
        self.testnet = testnet
        self.proxies = proxies
        self.exchange: Optional[ccxt.Exchange] = None

    async def connect(self, max_retries: int = 3, *, allow_mock: bool = True) -> ccxt.Exchange:
        """
        Creates and configures the CCXT exchange object, loads markets.

        ``allow_mock`` (RT-01): in ``DEV_MODE`` a failed market load used to be answered with
        ``_apply_mock_interface`` -- a two-market map and a CONSTANT 50,000 price stream. That is
        acceptable for a developer's laptop and wrong for anything that reports results to a user,
        so a caller that feeds a deployed bot passes ``allow_mock=False`` and gets the real error.
        FIX CE-1: load_markets() now retries with exponential backoff.
        FIX CE-2: apiKey/secret only added to config when they are not None.
        """
        if not hasattr(ccxt, self.exchange_id):
            raise ValueError(
                f"Exchange '{self.exchange_id}' is not supported by CCXT Pro. "
                f"Call ConnectionEngine.list_supported_exchanges() for valid IDs."
            )

        exchange_class = getattr(ccxt, self.exchange_id)

        # FIX CE-2: Only include auth keys when provided — null keys break some exchanges
        # 🔴 STEP 4: Increased timeout to 30s to prevent premature failures during exchange lag
        config: dict = {
            "enableRateLimit": True,
            "timeout": 30_000,  # 30 seconds - CRITICAL for order submission safety
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
            },
        }
        if self.api_key:
            config["apiKey"] = self.api_key
        if self.secret_key:
            config["secret"] = self.secret_key
        if self.password:
            config["password"] = self.password
        if self.uid:
            config["uid"] = self.uid
        if self.proxies:
            config["proxies"] = self.proxies

        if self.testnet:
            # EXCH-01: refuse a sandbox the venue cannot provide BEFORE a client exists,
            # so nothing is allocated for a connection that cannot be made. Reached only
            # when testnet is REQUESTED, so the 100+ venues this factory serves for market
            # data are untouched; a sandbox request for a venue nobody has measured is
            # refused rather than attempted, which is the same fail-closed rule
            # `CCXTExchangeExecutor.connect` applies.
            from backend_app.backend.exchange_executor import \
                assert_sandbox_supported
            assert_sandbox_supported(self.exchange_id)

        self.exchange = exchange_class(config)

        # ═══════════════════════════════════════════════════════════════════════════════
        # EXCH-01, SECOND FACTORY: PROVE THE SANDBOX, DO NOT ANNOUNCE IT
        # ═══════════════════════════════════════════════════════════════════════════════
        # WHAT THIS REPLACED, verbatim:
        #
        #     if self.testnet:
        #         try:
        #             self.exchange.set_sandbox_mode(True)
        #             logger.info(f"[{...}] Testnet/Sandbox ENABLED.")
        #         except Exception:
        #             logger.warning(f"[{...}] Testnet requested but not supported ...")
        #
        # That is the EXCH-01 defect, which was closed on `CCXTExchangeExecutor.connect`
        # and left open here -- the same mistake in the OTHER venue-client factory, and
        # this is the one paper trading and live market data actually use. Two ways it
        # lied. On bitget `set_sandbox_mode` does not raise and does not move the
        # endpoint, so "Testnet/Sandbox ENABLED" was logged for a client addressing
        # PRODUCTION. On a venue with no sandbox it raised, the bare `except` swallowed
        # it, and execution continued with `self.testnet` still True and the client still
        # on production -- a warning, not a refusal. ABSENCE OF AN EXCEPTION IS NOT
        # EVIDENCE OF ROUTING, and neither is a log line.
        #
        # It now uses the control that already exists. `verify_sandbox_endpoint` RAISES
        # rather than returning a verdict, precisely so a caller cannot drop it, and this
        # one does not.
        if self.testnet:
            # Local import: `exchange_executor` is a large module and this file sits
            # BELOW it in the layering (it is the connection layer), so importing it at
            # module scope would invert that and risk a cycle. `connect()` runs once per
            # pooled client, not per request, so the import cost is not on a hot path.
            from backend_app.backend.exchange_executor import (
                SandboxVerificationFailed, verify_sandbox_endpoint)

            # THE PRODUCTION ENDPOINT MUST COME FROM A PRISTINE **ccxt.pro** CLIENT, and
            # it is passed explicitly rather than left to the verifier's default for a
            # measured reason. That default resolves the class from
            # `ccxt.async_support`, and MEASURED on ccxt 4.5.90 a pro client's
            # `urls['api']` carries an extra 'ws' key that the REST client's does not --
            # for binance, bybit and okx alike. So a pro client's endpoint can never
            # equal a REST client's, and the "did it leave production" comparison would
            # come out TRUE for a reason that has nothing to do with sandbox routing. It
            # would pass vacuously, which is worse than failing, because it would look
            # like proof.
            #
            # Constructing the pristine client costs nothing and leaks nothing, measured
            # not assumed: after `exchange_class({})` on ccxt.pro 4.5.90, `session` and
            # `tcp_connector` are both None and `clients` is empty, no network call is
            # made, and the config is empty so no credential is involved.
            try:
                pristine_production = (
                    getattr(exchange_class({}), "urls", None) or {}
                ).get("api")
            except Exception:  # noqa: BLE001 - an unanswerable question is a refusal
                pristine_production = None
            if pristine_production is None:
                await self.disconnect()
                raise SandboxVerificationFailed(
                    self.exchange_id,
                    "the venue's production endpoint could not be read from a pristine "
                    "ccxt.pro client, so it is impossible to prove this one was swapped "
                    "away from it.",
                )

            try:
                self.exchange.set_sandbox_mode(True)
            except Exception as exc:  # noqa: BLE001
                # Classified and RAISED, where it used to be swallowed into a warning.
                # On ccxt 4.5.90 a venue with no sandbox raises
                # TypeError("'NoneType' object is not iterable") here rather than ccxt's
                # NotSupported, which is exactly why this catches broadly and converts
                # instead of matching on a type.
                await self.disconnect()
                raise SandboxVerificationFailed(
                    self.exchange_id,
                    f"arming sandbox mode on this venue failed "
                    f"({type(exc).__name__}: {exc}), so the client is still addressing "
                    f"the production API.",
                ) from exc

            try:
                verification = verify_sandbox_endpoint(
                    self.exchange,
                    self.exchange_id,
                    production_endpoint=pristine_production,
                )
            except SandboxVerificationFailed:
                # Closed BEFORE the raise, via the same `disconnect()` the
                # AuthenticationError branch below uses. A client that is built and then
                # abandoned leaks its aiohttp session and keeps a decrypted credential
                # reachable -- this file's own CE-3 note documents that leak, and
                # `disconnect()` is what closes the socket and nulls the reference.
                await self.disconnect()
                logger.critical(
                    "[%s] EXCH-01: refused a sandbox connection that could not be proven "
                    "to have left production.", self.exchange_id.upper(),
                )
                raise
            logger.info(
                "[%s] VERIFIED SANDBOX via %s — %s",
                self.exchange_id.upper(), verification.mechanism, verification.detail,
            )

        # FIX CE-1: load_markets with exponential backoff retry
        for attempt in range(max_retries):
            try:
                logger.info(
                    f"[{self.exchange_id.upper()}] Loading markets (attempt {attempt + 1})..."
                )
                await self.exchange.load_markets()
                logger.info(
                    f"[{self.exchange_id.upper()}] Connected — "
                    f"{len(self.exchange.symbols)} markets loaded."
                )
                return self.exchange
            except ccxt_base.AuthenticationError as e:
                # Auth errors will not resolve on retry — fail immediately
                logger.error(f"[{self.exchange_id.upper()}] Invalid API keys.")
                # A FAILED connect() never hands the caller a reference, so the caller
                # cannot close what it never received. get_or_create_exchange does
                #     engine = ConnectionEngine(...); exchange = await engine.connect()
                # and pools the result on the NEXT line, so when connect() raises the
                # engine goes out of scope still holding a live ccxt client with an open
                # aiohttp session, and release_exchange cannot reach it because it was
                # never pooled. That is the ERROR:asyncio:Unclosed client session and
                # the ccxt okx requires to release all resources warning in production.
                # disconnect() closes the session and nulls the reference (FIX CE-3).
                await self.disconnect()
                raise e
            except (TypeError, AttributeError, KeyError, IndexError) as e:
                # A DETERMINISTIC error will not resolve on retry either, for the same
                # reason the AuthenticationError above does not: nothing about waiting
                # two seconds changes it. These are parsing faults raised INSIDE the
                # client library while it reads the venue response, not transient
                # network conditions.
                #
                # The case that motivated this: ccxt 4.3.92 okx.parse_market computes
                # symbol = base + / + quote, and OKX lists a pre-open SPOT instrument
                # (CT-USDT) whose baseCcy, quoteCcy, settleCcy and uly are all empty
                # strings. safe_currency_code() maps an empty string to None, so that
                # concatenation raises TypeError: unsupported operand type(s) for +:
                # NoneType and str. Every OKX startup burned three attempts and six
                # seconds of backoff on an error identical each time, then failed
                # anyway. Upstream ccxt fixed it later by deriving base and quote from
                # instId; 4.4.1 and 4.4.50 do not carry that fix.
                #
                # THE MOTIVATING CASE IS NOW FIXED UPSTREAM, and this paragraph used to
                # say the opposite: "Failing fast does NOT make the venue load - OKX
                # markets still do not load on this ccxt". That was true on 4.3.92 and is
                # FALSE on 4.5.90, which WS-03's upgrade brought in. Measured on 4.5.90:
                # okx.py carries the `weird preopen markets` guard, and
                # `okx.load_markets()` returns 4705 markets including CT/EUR -- the very
                # CT-shaped pre-open instrument described above. So OKX is a venue whose
                # markets load now, which it was not before.
                #
                # THIS BRANCH STAYS ANYWAY, and the reason is the first paragraph rather
                # than the OKX anecdote: a TypeError raised inside the client library
                # while parsing a venue response is deterministic whatever venue produced
                # it, and retrying it three times with backoff charges every container
                # start for attempts that cannot succeed. Keeping it costs nothing when no
                # such defect exists and keeps the diagnosis honest when the next one
                # appears. What changed is that it is now a general guard with no known
                # live instance, not a workaround for a specific broken venue.
                #
                # It stops reporting a deterministic library defect as a flaky connection,
                # and the raised type is unchanged, so callers (AssetUniverse among them)
                # see exactly what they saw before.
                from backend_app.core.dependencies import DEV_MODE

                logger.error(
                    f"[{self.exchange_id.upper()}] load_markets raised a deterministic "
                    f"{type(e).__name__} while parsing the venue response, so it was "
                    f"not retried: {e}",
                    exc_info=True,
                )
                if DEV_MODE and allow_mock:
                    logger.warning(
                        f"[{self.exchange_id.upper()}] DEV_MODE is active, injecting "
                        f"mock interface."
                    )
                    self._apply_mock_interface()
                    return self.exchange
                await self.disconnect()
                raise ConnectionError(f"Exchange connection failed: {e}")

            except Exception as e:
                if attempt == max_retries - 1:
                    from backend_app.core.dependencies import DEV_MODE
                    if DEV_MODE and allow_mock:
                        logger.warning(
                            f"[{self.exchange_id.upper()}] Connection failed: {e}. DEV_MODE is active, injecting mock interface."
                        )
                        self._apply_mock_interface()
                        return self.exchange
                    else:
                        # Diagnostic: previously this log omitted exc_info, so a production
                        # failure (e.g. "[OKX] Failed to load markets after 3 attempts:
                        # unsupported operand type(s) for +: NoneType and str") gave no
                        # traceback to locate the failing line inside the CCXT call chain.
                        # exc_info=True makes the next occurrence self-diagnosing via the
                        # existing CloudWatch/awslogs pipeline. No change to control flow,
                        # the raised exception, or any return value.
                        logger.error(
                            f"[{self.exchange_id.upper()}] Failed to load markets after "
                            f"{max_retries} attempts: {type(e).__name__}: {e}",
                            exc_info=True,
                        )
                        await self.disconnect()
                        raise ConnectionError(f"Exchange connection failed: {e}")
                wait = 2**attempt
                logger.warning(
                    f"[{self.exchange_id.upper()}] load_markets failed: {e}. "
                    f"Retrying in {wait}s..."
                )
                await asyncio.sleep(wait)

    def _apply_mock_interface(self):
        """Injects mock methods and properties into the ccxt Exchange instance for offline/dev testing."""
        if not self.exchange:
            return
            
        import asyncio
        import time
        from datetime import datetime
        
        self.exchange.symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        self.exchange.markets = {
            "BTC/USDT": {
                "id": "BTCUSDT",
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "precision": {"amount": 6, "price": 2},
                "limits": {
                    "amount": {"min": 0.0001, "max": 100.0},
                    "cost": {"min": 10.0, "max": 1000000.0}
                }
            },
            "ETH/USDT": {
                "id": "ETHUSDT",
                "symbol": "ETH/USDT",
                "base": "ETH",
                "quote": "USDT",
                "precision": {"amount": 5, "price": 2},
                "limits": {
                    "amount": {"min": 0.001, "max": 1000.0},
                    "cost": {"min": 10.0, "max": 1000000.0}
                }
            }
        }
        
        # Mock methods
        async def mock_load_markets(*args, **kwargs):
            return self.exchange.markets
            
        async def mock_fetch_ohlcv(symbol, timeframe="1m", since=None, limit=100, *args, **kwargs):
            now = int(time.time() * 1000)
            interval_ms = 60000
            if timeframe == "1h":
                interval_ms = 3600000
            elif timeframe == "5m":
                interval_ms = 300000
                
            return [
                [now - i * interval_ms, 50000.0 + i * 10, 50050.0 + i * 10, 49950.0 + i * 10, 50000.0 + i * 10, 1.5]
                for i in reversed(range(limit))
            ]
            
        async def mock_fetch_balance(*args, **kwargs):
            return {
                "free": {"BTC": 10.0, "ETH": 50.0, "USDT": 100000.0},
                "used": {"BTC": 0.0, "ETH": 0.0, "USDT": 0.0},
                "total": {"BTC": 10.0, "ETH": 50.0, "USDT": 100000.0},
                "BTC": {"free": 10.0, "used": 0.0, "total": 10.0},
                "USDT": {"free": 100000.0, "used": 0.0, "total": 100000.0}
            }

        async def mock_fetch_positions(symbols=None, *args, **kwargs):
            return []
            
        async def mock_create_order(symbol, type, side, amount, price=None, params=None, *args, **kwargs):
            order_id = f"mock_order_{int(time.time())}"
            return {
                "id": order_id,
                "clientOrderId": order_id,
                "timestamp": int(time.time() * 1000),
                "datetime": datetime.utcnow().isoformat(),
                "lastTradeTimestamp": int(time.time() * 1000),
                "status": "closed",
                "symbol": symbol,
                "type": type,
                "side": side,
                "price": price or 50000.0,
                "amount": float(amount),
                "filled": float(amount),
                "remaining": 0.0,
                "cost": float(amount) * (price or 50000.0),
                "trades": [],
                "fee": {"currency": "USDT", "cost": float(amount) * (price or 50000.0) * 0.001}
            }
            
        async def mock_cancel_order(id, symbol, *args, **kwargs):
            return {"id": id, "symbol": symbol, "status": "canceled"}
            
        async def mock_fetch_status(*args, **kwargs):
            return {"status": "ok", "updated": int(time.time() * 1000)}

        async def mock_fetch_accounts(*args, **kwargs):
            return [{"id": "mock_account", "type": "spot"}]

        # Websocket streams mocks (returning async generators)
        async def mock_watch_trades(symbol, *args, **kwargs):
            await asyncio.sleep(2)
            return [{
                "symbol": symbol,
                "price": 50000.0,
                "amount": 0.01,
                "side": "buy",
                "timestamp": int(time.time() * 1000),
                "id": f"mock_trade_{int(time.time())}"
            }]
            
        async def mock_watch_ohlcv(symbol, timeframe="1m", *args, **kwargs):
            await asyncio.sleep(2)
            now = int(time.time() * 1000)
            return [now, 50000.0, 50050.0, 49950.0, 50010.0, 1.2]
            
        async def mock_watch_order_book(symbol, *args, **kwargs):
            await asyncio.sleep(2)
            return {
                "symbol": symbol,
                "bids": [[49990.0, 1.5], [49980.0, 2.0]],
                "asks": [[50010.0, 1.1], [50020.0, 2.5]],
                "timestamp": int(time.time() * 1000),
                "nonce": 123456
            }
            
        async def mock_watch_ticker(symbol, *args, **kwargs):
            await asyncio.sleep(2)
            return {
                "symbol": symbol,
                "last": 50000.0,
                "percentage": 0.5,
                "timestamp": int(time.time() * 1000)
            }
            
        async def mock_watch_balance(*args, **kwargs):
            await asyncio.sleep(2)
            return await mock_fetch_balance()
            
        async def mock_watch_orders(symbol=None, *args, **kwargs):
            await asyncio.sleep(2)
            return []
            
        async def mock_watch_my_trades(symbol=None, *args, **kwargs):
            await asyncio.sleep(2)
            return []

        # Bind mock methods to the exchange instance
        self.exchange.load_markets = mock_load_markets
        self.exchange.fetch_ohlcv = mock_fetch_ohlcv
        self.exchange.fetch_balance = mock_fetch_balance
        self.exchange.fetch_positions = mock_fetch_positions
        self.exchange.create_order = mock_create_order
        self.exchange.cancel_order = mock_cancel_order
        self.exchange.fetch_status = mock_fetch_status
        self.exchange.fetch_accounts = mock_fetch_accounts
        
        self.exchange.watch_trades = mock_watch_trades
        self.exchange.watch_ohlcv = mock_watch_ohlcv
        self.exchange.watch_order_book = mock_watch_order_book
        self.exchange.watch_ticker = mock_watch_ticker
        self.exchange.watch_balance = mock_watch_balance
        self.exchange.watch_orders = mock_watch_orders
        self.exchange.watch_my_trades = mock_watch_my_trades

        # Set capabilities in `has`
        self.exchange.has = {
            "fetchOHLCV": True,
            "fetchBalance": True,
            "createOrder": True,
            "cancelOrder": True,
            "fetchStatus": True,
            "fetchAccounts": True,
            "watchTrades": True,
            "watchOHLCV": True,
            "watchOrderBook": True,
            "watchTicker": True,
            "watchBalance": True,
            "watchOrders": True,
            "watchMyTrades": True,
        }

    async def validate_keys(self) -> bool:
        """
        Validates API keys are live and have the required permissions.
        FIX CE-5: Uses fetch_currencies() (lightweight) instead of fetch_balance()
                  to avoid consuming the user's main rate limit budget.
        Falls back to fetch_balance() if fetch_currencies not supported.
        """
        if not self.exchange:
            raise RuntimeError("Call connect() before validate_keys().")
        if not self.api_key:
            logger.warning(f"[{self.exchange_id.upper()}] Public mode — no API keys.")
            return False

        try:
            # Lightweight validation: fetch_account() or fetch_currencies()
            if self.exchange.has.get("fetchAccounts"):
                await self.exchange.fetch_accounts()
            elif self.exchange.has.get("fetchBalance"):
                await self.exchange.fetch_balance()
            else:
                logger.warning(
                    f"[{self.exchange_id.upper()}] Cannot validate keys — no suitable endpoint."
                )
                return False

            logger.info(f"[{self.exchange_id.upper()}] API keys VALID.")
            return True

        except ccxt_base.AuthenticationError:
            logger.error(f"[{self.exchange_id.upper()}] API keys INVALID or expired.")
            return False
        except ccxt_base.PermissionDenied:
            logger.error(
                f"[{self.exchange_id.upper()}] Keys valid but lack required permissions."
            )
            return False
        except Exception as e:
            logger.error(
                f"[{self.exchange_id.upper()}] Key validation network error: {e}"
            )
            return False

    async def check_exchange_status(self) -> str:
        """Returns 'ok', 'maintenance', or 'assumed_ok' if status not fetchable."""
        if not self.exchange:
            return "offline"
        if self.exchange.has.get("fetchStatus"):
            try:
                status = await self.exchange.fetch_status()
                return status.get("status", "unknown")
            except Exception as e:
                logger.warning(f"[{self.exchange_id.upper()}] fetch_status error: {e}")
                return "assumed_ok"
        return "assumed_ok"

    async def disconnect(self):
        """
        Closes the WebSocket and REST session.
        FIX CE-3: Sets self.exchange = None so reconnect() creates a fresh object.
        """
        if self.exchange is not None:
            try:
                await self.exchange.close()
                logger.info(f"[{self.exchange_id.upper()}] Session closed.")
            except Exception as e:
                logger.warning(f"[{self.exchange_id.upper()}] Close error: {e}")
            finally:
                self.exchange = None  # FIX CE-3: null the reference

    @staticmethod
    def list_supported_exchanges() -> list[str]:
        """
        FIX CE-6: Now a proper static method on the class instead of a
        module-level function. Call as ConnectionEngine.list_supported_exchanges().
        """
        return list(ccxt.exchanges)

    def get_exchange(self) -> ccxt.Exchange:
        """Returns the live exchange instance. Raises if not connected."""
        if self.exchange is None:
            raise RuntimeError("Exchange not connected. Call await connect() first.")
        return self.exchange
