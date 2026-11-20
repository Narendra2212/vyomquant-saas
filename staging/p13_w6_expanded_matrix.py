"""W-6: reproduce Phase 12's eight cases and expand the matrix. Read-only.

Phase 12 measured 3 misclassifications in 8 cases against the live ladder
(``staging/p12_w6_probe.txt``). The brief for Phase 13 asks for reproduction plus coverage of
URL-bearing timeouts, venue paths, genuine symbol and auth errors, unrelated
``monkey``/``keyword``/``keyspace`` messages, rate limits and insufficient funds, and asks
that ordering and token-boundary behaviour be tested rather than inferred from reading the
source.

Nothing here instantiates a venue or performs I/O. The executor is built with
``object.__new__`` -- the pattern ``tests/test_trade04_live_order_gate_at_venue_boundary.py``
already uses -- so ``_handle_ccxt_error`` is exercised as the product would run it, with no
credentials and no network.

The redaction step at the head of the ladder is also verified, because the correction
proposal must not disturb it.
"""

from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend_app.backend.exchange_executor import (  # noqa: E402
    AuthenticationError,
    CCXTExchangeExecutor,
    ExchangeError,
    InsufficientFundsError,
    InvalidSymbolError,
    NetworkError,
    RateLimitError,
)

# (label, exception message, expected class, why)
CASES: list[tuple[str, str, type, str]] = [
    # ---- the eight Phase 12 cases, reproduced verbatim in intent ----
    ("P12-1 genuine invalid symbol",
     "binance does not have market symbol BTCUSDTX",
     InvalidSymbolError, "a real unknown-symbol rejection"),
    ("P12-2 TIMEOUT carrying the request URL",
     "RequestTimeout: GET https://api.binance.com/api/v3/ticker?symbol=BTCUSDT timed out",
     NetworkError, "transport failure; 'symbol' appears only in the URL"),
    ("P12-3 TIMEOUT, no symbol text",
     "RequestTimeout: request timed out after 10000ms",
     NetworkError, "unambiguous transport failure"),
    ("P12-4 network error carrying a venue market path",
     "NetworkError: connection reset on GET /api/v5/market/ticker",
     NetworkError, "transport failure; 'market' appears only in the path"),
    ("P12-5 genuine authentication failure",
     "AuthenticationError: Invalid API-key, IP, or permissions for action",
     AuthenticationError, "a real credential rejection"),
    ("P12-6 unrelated message containing 'key'",
     "monkey patch failed",
     ExchangeError, "'key' inside 'monkey'; nothing to do with credentials"),
    ("P12-7 insufficient funds",
     "InsufficientFunds: Account has insufficient balance for requested action",
     InsufficientFundsError, "a real balance rejection"),
    ("P12-8 rate limit",
     "RateLimitExceeded: too many requests, retry after 60s",
     RateLimitError, "a real throttle"),

    # ---- new cases this phase ----
    ("P13-1 timeout on a bybit market path",
     "RequestTimeout: GET https://api.bybit.com/v5/market/tickers?category=spot timed out",
     NetworkError, "venue path contains 'market'"),
    ("P13-2 DNS failure carrying a symbol query",
     "NetworkError: getaddrinfo ENOTFOUND api.okx.com /api/v5/market/books?instId=BTC-USDT",
     NetworkError, "DNS failure, both 'market' and a symbol-ish token present"),
    ("P13-3 connection reset, no shadowing token",
     "ConnectionResetError: connection reset by peer",
     NetworkError, "clean transport case, should already pass"),
    ("P13-4 'keyword' in an unrelated validation error",
     "TypeError: got an unexpected keyword argument 'recvWindow'",
     ExchangeError, "'key' inside 'keyword'; a client bug, not an auth failure"),
    ("P13-5 'keyspace' in an unrelated backend error",
     "redis keyspace notification misconfigured",
     ExchangeError, "'key' inside 'keyspace'"),
    ("P13-6 'monkeypatch' in a test-harness leak",
     "monkeypatch could not set attribute",
     ExchangeError, "'key' inside 'monkeypatch'"),
    ("P13-7 genuine apiKey phrasing",
     "AuthenticationError: apiKey required for this endpoint",
     AuthenticationError, "real auth failure, must keep working"),
    ("P13-8 genuine api-key phrasing with a hyphen",
     "Invalid api-key provided",
     AuthenticationError, "real auth failure, hyphenated"),
    ("P13-9 signature failure",
     "Signature for this request is not valid",
     AuthenticationError, "real auth failure via signature"),
    ("P13-10 insufficient funds mentioning a symbol",
     "InsufficientFunds: not enough USDT to buy BTC/USDT",
     InsufficientFundsError, "ordering: funds must beat the symbol test"),
    ("P13-11 rate limit mentioning a market path",
     "RateLimitExceeded on GET /api/v5/market/ticker, retry later",
     RateLimitError, "ordering: throttle must beat the symbol test"),
    ("P13-12 genuine symbol error that also says timeout",
     "market symbol BTCUSDTX not found; request timed out while resolving markets",
     None, "genuinely ambiguous -- recorded, not asserted"),
    ("P13-13 wholly unrelated failure",
     "ValueError: could not parse response body",
     ExchangeError, "falls through to the generic branch"),
]


def _bare_executor(api_key: str = "", api_secret: str = "", password: str = ""):
    """An executor whose ``__init__`` never ran, with only what redaction reads.

    ``_handle_ccxt_error`` calls ``_redact_credentials`` first (``exchange_executor.py:1236``
    -> ``:1140``), which reads ``self._api_key`` / ``self._api_secret`` / ``self._password``.
    Those three underscore-prefixed attributes are the entire requirement; setting anything
    else would be inventing state the measurement does not need.
    """
    ex = object.__new__(CCXTExchangeExecutor)
    ex._api_key = api_key
    ex._api_secret = api_secret
    ex._password = password
    return ex


def main() -> int:
    ex = _bare_executor()

    print("=" * 112)
    print("W-6 CLASSIFICATION MATRIX  (live ladder, no venue, no network)")
    print("=" * 112)
    print(f"{'case':<46} {'actual':<22} {'expected':<22} {'code':<18} {'retry':<6} verdict")
    print("-" * 112)

    mis = 0
    ambiguous = 0
    total_asserted = 0
    rows = []

    for label, msg, expected, _why in CASES:
        err = ex._handle_ccxt_error(Exception(msg))
        actual = type(err).__name__
        code = str(err.error_code)
        retry = str(err.retryable)

        if expected is None:
            verdict = "AMBIGUOUS (not asserted)"
            ambiguous += 1
        else:
            total_asserted += 1
            if type(err) is expected:
                verdict = "ok"
            else:
                verdict = f"MISCLASSIFIED (want {expected.__name__})"
                mis += 1

        exp_name = expected.__name__ if expected else "-"
        print(f"{label:<46} {actual:<22} {exp_name:<22} {code:<18} {retry:<6} {verdict}")
        rows.append((label, actual, exp_name, code, retry, verdict))

    print("-" * 112)
    print(f"cases={len(CASES)}  asserted={total_asserted}  ambiguous={ambiguous}  "
          f"MISCLASSIFIED={mis}")

    # ------------------------------------------------- Phase 12 reproduction check
    print("\n### Phase 12 reproduction (its 8 cases only)")
    p12 = [r for r in rows if r[0].startswith("P12-")]
    p12_mis = [r for r in p12 if r[5].startswith("MISCLASSIFIED")]
    print(f"  Phase 12 measured : 8 cases, 3 misclassified")
    print(f"  measured now      : {len(p12)} cases, {len(p12_mis)} misclassified")
    for r in p12_mis:
        print(f"    - {r[0]}  -> {r[1]} ({r[3]})")
    print(f"  reproduction      : {'IDENTICAL' if len(p12_mis) == 3 else 'DIVERGED -- investigate'}")

    # ------------------------------------------------------- token-boundary probe
    print("\n### Token-boundary behaviour of the 'key' test (ladder line ~1252)")
    print("  Each string contains 'key' as a SUBSTRING but not as a word.")
    for word in ("monkey", "monkeypatch", "keyword", "keyspace", "turkey", "keychain",
                 "donkey", "whiskey"):
        err = ex._handle_ccxt_error(Exception(f"failure involving {word}"))
        flag = "SHADOWED -> AUTH_ERROR" if isinstance(err, AuthenticationError) else "ok"
        print(f"    {word:<14} -> {type(err).__name__:<22} {flag}")

    print("\n### Token-boundary behaviour of the 'market'/'symbol' test (ladder line ~1246)")
    for path in ("/api/v5/market/ticker", "/v5/market/tickers", "/api/v3/ticker?symbol=BTCUSDT",
                 "/spot/v1/symbols", "/market-data/ohlc"):
        err = ex._handle_ccxt_error(Exception(f"RequestTimeout: GET {path} timed out"))
        flag = "SHADOWED -> INVALID_SYMBOL" if isinstance(err, InvalidSymbolError) else "ok"
        print(f"    {path:<34} -> {type(err).__name__:<22} {flag}")

    # ------------------------------------------------------------ ordering probe
    print("\n### Ordering: which test wins when two tokens are present")
    pairs = [
        ("insufficient + symbol", "insufficient balance for symbol BTC/USDT"),
        ("rate limit + symbol", "rate limit hit for symbol BTC/USDT"),
        ("symbol + timeout", "symbol lookup timed out"),
        ("network + key", "network failure, apikey rotation pending"),
        ("timeout + market", "timed out on /market/ticker"),
    ]
    for label, msg in pairs:
        err = ex._handle_ccxt_error(Exception(msg))
        print(f"    {label:<24} -> {type(err).__name__:<22} ({err.error_code})")

    # ------------------------------------------------------------ redaction check
    print("\n### Redaction still runs BEFORE classification (must not regress)")
    ex2 = _bare_executor(
        api_key="AKIAEXAMPLEKEY1234",
        api_secret="s3cr3t-value-should-not-appear",
        password="",
    )
    try:
        err = ex2._handle_ccxt_error(
            Exception("AuthenticationError: bad credentials AKIAEXAMPLEKEY1234 / s3cr3t-value-should-not-appear")
        )
        leaked_key = "AKIAEXAMPLEKEY1234" in err.message
        leaked_sec = "s3cr3t-value-should-not-appear" in err.message
        print(f"    classified as        : {type(err).__name__}")
        print(f"    api_key leaked       : {leaked_key}")
        print(f"    secret  leaked       : {leaked_sec}")
        print(f"    redaction verdict    : {'FAIL - SECRET PRESENT' if (leaked_key or leaked_sec) else 'ok, redacted'}")
    except Exception as exc:
        print(f"    could not evaluate redaction: {type(exc).__name__}: {exc}")

    # ------------------------------------------------- retryable consumer recheck
    print("\n### Is `retryable` still log-only? (re-measured this phase)")
    import re
    hits = []
    for p in ROOT.rglob("*.py"):
        if any(x in p.parts for x in (".venv", ".codex_verify_venv", "staging", ".git")):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, ln in enumerate(txt.splitlines(), 1):
            if re.search(r"\.retryable\b", ln):
                hits.append((p.relative_to(ROOT).as_posix(), i, ln.strip()))
    print(f"  occurrences of `.retryable` outside staging/: {len(hits)}")
    for rel, i, ln in hits:
        kind = "TEST" if rel.startswith("tests/") else "PRODUCTION"
        print(f"    [{kind}] {rel}:{i}  {ln[:88]}")
    prod = [h for h in hits if not h[0].startswith("tests/")]
    branching = [h for h in prod if re.search(r"\b(if|while|and|or|not|assert)\b", h[2])]
    print(f"\n  production occurrences : {len(prod)}")
    print(f"  of which look like control flow : {len(branching)}")
    if not branching:
        print("  => still LOG-ONLY. No production branch reads the flag.")
    else:
        print("  => CHANGED since Phase 12. A branch now reads it; re-assess W-6 severity.")
        for rel, i, ln in branching:
            print(f"       {rel}:{i}  {ln[:88]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
