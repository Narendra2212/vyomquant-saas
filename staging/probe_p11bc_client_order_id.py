"""Phase 11B-C Stage 3 — does ccxt map unified `clientOrderId` to each venue's native field?

The brief forbids assuming it does. This settles it by reading ccxt 4.3.92's own
per-venue source — offline, no venue contact, no credential, no order.

WHY IT MATTERS. `exchange_executor.place_order` forwards the platform's deterministic
`execution_id` as:

    extra_params["clientOrderId"] = self._consumed_execution_id

and that is the platform's ONLY protection against a retry becoming a second real order.
It works only if ccxt translates `clientOrderId` into the parameter the venue actually
reads:

    binance -> newClientOrderId
    bybit   -> orderLinkId
    okx     -> clOrdId
    kucoin  -> clientOid
    kraken  -> userref        (numeric — a 32-bit signed int, not a string)

If a venue's implementation does NOT translate it, the key is passed through verbatim,
the venue ignores an unrecognised field, and duplicate submissions are NOT refused —
silently.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import re

import ccxt

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: venue -> the native parameter its REST API reads for a client-supplied order id.
NATIVE_FIELD = {
    "binance": "newClientOrderId",
    "bybit": "orderLinkId",
    "okx": "clOrdId",
    "kucoin": "clientOid",
    "kraken": "userref",
    "coinbase": "client_order_id",
}

#: Methods worth reading: `create_order` plus the helpers venues delegate request
#: construction to.
CANDIDATE_METHODS = (
    "create_order", "create_order_request", "createOrderRequest",
    "create_market_order", "create_limit_order", "handle_request_network_errors",
)

out: dict = {"ccxt_version": ccxt.__version__, "venues": {}}

print("=" * 92)
print("DOES ccxt TRANSLATE `clientOrderId` TO EACH VENUE'S NATIVE FIELD?")
print(f"ccxt {ccxt.__version__} — offline source inspection, no venue contact")
print("=" * 92)

for venue, native in NATIVE_FIELD.items():
    print()
    print("-" * 92)
    print(f"{venue}   native field: {native}")
    print("-" * 92)
    cls = getattr(ccxt, venue, None)
    if cls is None:
        print("  absent from ccxt")
        out["venues"][venue] = {"present": False}
        continue

    found: dict = {"present": True, "native_field": native, "methods": {}}
    combined = ""
    for meth in CANDIDATE_METHODS:
        fn = getattr(cls, meth, None)
        if fn is None:
            continue
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        combined += "\n" + src
        mentions_unified = "clientOrderId" in src
        mentions_native = native in src
        if mentions_unified or mentions_native:
            found["methods"][meth] = {
                "mentions_clientOrderId": mentions_unified,
                "mentions_native_field": mentions_native,
            }

    # The translation signature: the unified key is READ out of `params`, and the native
    # key is WRITTEN into the request.
    #
    # Detecting the write is less obvious than it looks. An earlier version of this probe
    # required a literal-string subscript (`request['newClientOrderId'] = ...`) and so
    # reported BINANCE as untranslated — a false negative. Binance assigns through a
    # VARIABLE key:
    #
    #     clientOrderIdRequest = 'newClientStrategyId' if ... else 'newClientOrderId'
    #     request[clientOrderIdRequest] = clientOrderId
    #
    # So the write test accepts either a literal subscript or the native name appearing
    # anywhere in a source that also performs a `request[...] =` assignment. The matched
    # lines are recorded verbatim in the artifact so the inference stays auditable rather
    # than resting on a regex.
    reads_unified = bool(re.search(
        r"safe_string\w*\([^)]*['\"]clientOrderId['\"]", combined
    ))
    writes_native_literal = bool(re.search(
        rf"\[\s*['\"]{re.escape(native)}['\"]\s*\]\s*=", combined
    ))
    native_mentioned = bool(re.search(rf"['\"]{re.escape(native)}['\"]", combined))
    assigns_request = bool(re.search(r"request\[[^\]]+\]\s*=", combined))
    writes_native = writes_native_literal or (native_mentioned and assigns_request)
    omits_unified = bool(re.search(
        r"omit\(\s*params\s*,[^)]*clientOrderId", combined
    ))

    # Evidence lines, kept so the verdict can be checked by eye.
    found["evidence_lines"] = [
        ln.strip()[:150] for ln in combined.splitlines()
        if ("clientOrderId" in ln or native in ln) and not ln.strip().startswith("#")
    ][:12]

    # Does the venue INVENT a client order id when the caller supplies none? Binance
    # does (`brokerId + self.uuid22()`). That is not idempotency — a fresh random id on
    # every attempt means a retry is a NEW order as far as the venue is concerned.
    found["auto_generates_when_absent"] = bool(
        re.search(r"uuid22\(\)|uuid\(\)|brokerId \+", combined)
    )

    found["reads_unified_clientOrderId"] = reads_unified
    found["writes_native_field"] = writes_native
    found["omits_unified_from_params"] = omits_unified
    translated = reads_unified and writes_native
    found["translation_confirmed"] = translated

    print(f"  reads unified 'clientOrderId'  : {reads_unified}")
    print(f"  writes native '{native}'".ljust(33) + f": {writes_native}")
    print(f"  omits unified key from params  : {omits_unified}")
    print(f"  methods mentioning either      : {sorted(found['methods'])}")
    print(f"  -> TRANSLATION {'CONFIRMED' if translated else 'NOT CONFIRMED'}")

    if translated and not omits_unified:
        print("     note: the unified key appears NOT to be omitted after translation,")
        print("     so it may also be forwarded verbatim. Harmless if the venue ignores")
        print("     unknown fields; worth knowing if a venue rejects them.")

    out["venues"][venue] = found

print()
print("=" * 92)
print("SUMMARY")
print("=" * 92)
confirmed = [v for v, d in out["venues"].items()
             if d.get("translation_confirmed")]
unconfirmed = [v for v, d in out["venues"].items()
               if d.get("present") and not d.get("translation_confirmed")]
print(f"  translation CONFIRMED by source   : {confirmed}")
print(f"  translation NOT CONFIRMED         : {unconfirmed}")
print()
print("  LIMITATION, STATED PLAINLY. Source inspection shows ccxt INTENDS to translate.")
print("  It does NOT show that the venue accepts the id, echoes it back, or refuses a")
print("  second order carrying it. Those are venue behaviours and only a sandbox order")
print("  can establish them — scenario 8. Nothing here certifies duplicate protection.")

dest = ROOT / "staging" / "p11bc_client_order_id.json"
dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print(f"\nartifact -> {dest.relative_to(ROOT)}")
