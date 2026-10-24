#!/usr/bin/env python3
"""DUP-01: does each venue support the clientOrderId mechanism the platform depends on?

WHAT THIS ANSWERS, AND WHAT IT DOES NOT
---------------------------------------
``exchange_executor.place_order`` forwards the platform's ``execution_id`` to the venue as
``clientOrderId`` (line ~1679) and ``find_order_by_client_order_id`` later adjudicates an
ambiguous submission by asking the venue about that id. The whole DUP-01 design rests on
the venue ACCEPTING, PRESERVING and being QUERYABLE BY that id.

This is a CAPABILITY audit, read out of the installed ccxt, not a behavioural
certification. It answers "is the mechanism the platform relies on published by this
venue's implementation at all?" It cannot answer "does the venue reject a duplicate id?"
-- that needs a real order against a real endpoint, which is what the sandbox
certification is for and what testnet credentials are needed for.

The distinction matters because DUP-01 records coinbase and kraken as PERMANENTLY
uncertifiable (no sandbox). If their implementations also do not support clientOrderId,
that is a far stronger finding than "we could not test it": the platform would be relying
on a mechanism those venues do not offer. If they DO support it, the gap is only the
missing behavioural proof.

    python scripts/audit_venue_client_order_id_support.py
"""
from __future__ import annotations

import inspect
import json
import sys

#: The venues exchange_executor.SANDBOX_MECHANISMS names, plus the sibling ccxt ids for
#: the same brands. The siblings are included because "coinbase has no sandbox" is a fact
#: about the ``coinbase`` ID, not about the company -- and a reader deciding whether
#: certification is possible needs to see that distinction rather than infer it.
VENUES = [
    ("binance", "SANDBOX_MECHANISMS: URL_SWAP"),
    ("bybit", "SANDBOX_MECHANISMS: URL_SWAP"),
    ("okx", "SANDBOX_MECHANISMS: HEADER x-simulated-trading"),
    ("coinbase", "SANDBOX_MECHANISMS: UNSUPPORTED"),
    ("kraken", "SANDBOX_MECHANISMS: UNSUPPORTED"),
    ("kucoin", "SANDBOX_MECHANISMS: UNSUPPORTED"),
    ("coinbaseexchange", "sibling id, not in SANDBOX_MECHANISMS"),
    ("krakenfutures", "sibling id, not in SANDBOX_MECHANISMS"),
]

#: The UNIFIED spelling. This is the one that matters, because exchange_executor sends
#: ``extra_params["clientOrderId"]`` and relies on ccxt translating it to the venue's
#: native field. If an implementation never reads this key, the platform's id is dropped.
UNIFIED = "clientOrderId"

#: Native per-venue field names, so a venue that reads the unified key and forwards it can
#: be distinguished from one that only accepts its own spelling (which the platform would
#: have to send explicitly). Collected from the venues this platform addresses.
NATIVE_SPELLINGS = (
    "newClientOrderId", "origClientOrderId",   # binance
    "orderLinkId",                              # bybit
    "clOrdId", "algoClOrdId",                   # okx
    "client_order_id", "client_oid", "clientOid",  # coinbase / kucoin
    "userref", "cl_ord_id",                     # kraken
)

#: METHOD SCANNING IS NOT ENOUGH, and the first version of this script got the answer
#: wrong because of it: ccxt implementations delegate param handling to request builders
#: (``create_order_request``, ``spot_order_request``, ...), so ``create_order``'s own
#: source frequently contains none of these spellings while the venue fully supports them.
#: It reported binance as PARTIAL and bybit as NOT SUPPORTED, both false. The whole class
#: source is scanned instead, and the per-method result is reported beside it as the
#: narrower, stronger signal rather than as the verdict.
ORDER_PATH_HINTS = ("create_order", "order_request", "edit_order", "fetch_order",
                    "cancel_order")


def method_source(exchange, name: str) -> str:
    fn = getattr(exchange, name, None)
    if fn is None:
        return ""
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        return ""


def class_source(exchange) -> str:
    """The whole implementation, including the request builders create_order delegates to."""
    try:
        return inspect.getsource(type(exchange))
    except (OSError, TypeError):
        return ""


def order_path_source(exchange) -> str:
    """Every method whose name looks like part of the order path, concatenated.

    Narrower than the whole class and wider than one method -- this is what actually
    answers "is the id handled on the way to the venue".
    """
    chunks = []
    for name in dir(exchange):
        if not any(h in name for h in ORDER_PATH_HINTS):
            continue
        if not callable(getattr(exchange, name, None)):
            continue
        chunks.append(method_source(exchange, name))
    return "\n".join(chunks)


def spellings_in(text: str) -> list:
    found = []
    if UNIFIED in text:
        found.append(UNIFIED)
    found.extend(s for s in NATIVE_SPELLINGS if s in text)
    return found


def main() -> int:
    try:
        import ccxt
    except ImportError:
        print("ccxt is not installed", file=sys.stderr)
        return 2

    print(f"ccxt {ccxt.__version__}")
    print("=" * 78)
    rows = []
    for venue, note in VENUES:
        klass = getattr(ccxt, venue, None)
        if klass is None:
            print(f"\n{venue}: NO SUCH EXCHANGE in this ccxt")
            continue
        ex = klass()
        urls = ex.urls or {}
        has = ex.has if isinstance(ex.has, dict) else {}

        whole = class_source(ex)
        path = order_path_source(ex)
        fetch_src = method_source(ex, "fetch_order")

        unified_on_path = UNIFIED in path
        natives_on_path = [s for s in NATIVE_SPELLINGS if s in path]
        unified_in_fetch = UNIFIED in fetch_src
        natives_in_fetch = [s for s in NATIVE_SPELLINGS if s in fetch_src]

        row = {
            "venue": venue,
            "note": note,
            "sandbox_url": bool(urls.get("test")),
            "has_sandbox": has.get("sandbox"),
            "fetchOrder": has.get("fetchOrder"),
            "unified_on_order_path": unified_on_path,
            "native_on_order_path": natives_on_path,
            "unified_in_fetch_order": unified_in_fetch,
            "class_mentions_unified": UNIFIED in whole,
        }
        rows.append(row)

        print(f"\n{venue}  ({note})")
        print(f"   sandbox: urls['test']={row['sandbox_url']} "
              f"has['sandbox']={row['has_sandbox']}  "
              f"has['fetchOrder']={row['fetchOrder']}")
        print(f"   order path reads {UNIFIED!r:15} : {unified_on_path}")
        print(f"   order path native spellings    : {natives_on_path or 'none'}")
        print(f"   fetch_order reads {UNIFIED!r:14} : {unified_in_fetch}"
              f"  native: {natives_in_fetch or 'none'}")

        # The platform sends the UNIFIED key, so that is the deciding signal. A venue that
        # only handles its native spelling would need exchange_executor to send that
        # instead -- a real gap, reported as such rather than as full support.
        if unified_on_path and unified_in_fetch:
            verdict = "SUPPORTED: unified id is sent AND fetch_order resolves by it"
        elif unified_on_path and has.get("fetchOrder") is False:
            verdict = ("SUPPORTED FOR SUBMIT; this venue has no fetchOrder at all, so "
                       "adjudication must use the open-orders scan by design")
        elif unified_on_path:
            verdict = ("SUBMIT OK, LOOKUP UNCLEAR: fetch_order does not read the unified "
                       "key; adjudication falls back to the open-orders scan")
        elif natives_on_path:
            verdict = ("GAP: only the native spelling " + ", ".join(natives_on_path[:2])
                       + " is handled, and the platform sends 'clientOrderId'")
        else:
            verdict = ("NOT SUPPORTED: no clientOrderId handling anywhere on the order "
                       "path -- the platform's duplicate protection has nothing to "
                       "attach to here")
        row["verdict"] = verdict
        print(f"   => {verdict}")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for r in rows:
        certifiable = "sandbox" if r["sandbox_url"] else "NO SANDBOX"
        print(f"   {r['venue']:18} {certifiable:11} {r['verdict'][:52]}")

    print("\nWhat this does and does not establish:")
    print("   * A venue with NO SANDBOX cannot be certified behaviourally, whatever its")
    print("     capability flags say. That is DUP-01's permanent half.")
    print("   * A venue whose create_order does NOT read any clientOrderId spelling is a")
    print("     STRONGER finding than 'untested': the platform's duplicate protection has")
    print("     nothing to attach to there.")
    print("   * Sibling ids are listed because 'coinbase has no sandbox' is a fact about")
    print("     the `coinbase` ID. coinbaseexchange and krakenfutures DO publish sandbox")
    print("     endpoints -- for DIFFERENT products, so certifying against them would")
    print("     certify a different API and does not substitute.")

    print("\n" + json.dumps(rows, indent=2, default=str)[:60] + " ... (full rows above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
