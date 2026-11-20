"""EXCH-01 — what evidence of sandbox routing is available LOCALLY? Offline, no credential.

The verifier this probe informs must decide "is this client actually on a sandbox
endpoint?" without a network call. So the question is what ccxt leaves behind on the
client object after sandbox mode is armed.

ccxt 4.3.92 `Exchange.set_sandbox_mode`:

    if enabled:
        if 'test' in self.urls:
            self.urls['apiBackup'] = self.urls['api']
            self.urls['api'] = self.urls['test']
        else:
            raise NotSupported(...)

So on a successful URL swap the client carries THREE facts, all locally readable:

    urls['api']        the endpoint it will now send to
    urls['test']       the declared sandbox endpoint
    urls['apiBackup']  the production endpoint it was moved off

That is enough to prove the swap happened AND that it landed on the venue's DECLARED
sandbox endpoint — strictly stronger than "the URL changed", and it needs no reference
client. This probe confirms that is true for each approved venue, and records which
venues use a different mechanism entirely (OKX selects demo trading by header, so its
URL legitimately does not change).

NO NETWORK. Constructors and local attribute reads only.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ccxt  # noqa: E402

from backend_app.backend.exchange_executor import (SAFETY_BLOCKED_EXCHANGES,  # noqa: E402
                                                   ExchangeExecutorFactory)

APPROVED = list(ExchangeExecutorFactory.SUPPORTED_EXCHANGES)
BLOCKED = sorted(SAFETY_BLOCKED_EXCHANGES)


def flat(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def probe(eid: str) -> dict:
    row: dict = {"exchange": eid}
    cls = getattr(ccxt, eid, None)
    if cls is None:
        row["error"] = "absent from ccxt"
        return row

    pristine = cls({})
    row["prod_api"] = flat((pristine.urls or {}).get("api"))
    row["declares_test"] = (pristine.urls or {}).get("test") is not None
    row["test_url"] = flat((pristine.urls or {}).get("test")) if row["declares_test"] else None
    base_headers = set(pristine.headers or {})

    # Arm sandbox exactly the way CCXTExchangeExecutor.connect does: via the constructor
    # config, which ccxt reads out of `options` and turns into a set_sandbox_mode call.
    try:
        armed = cls({"sandbox": True, "options": {"sandbox": True}})
    except Exception as exc:  # noqa: BLE001
        row["armed"] = f"RAISED {type(exc).__name__}: {exc}"
        return row

    urls = armed.urls or {}
    row["armed"] = "constructed"
    row["armed_api"] = flat(urls.get("api"))
    row["has_apiBackup"] = "apiBackup" in urls
    row["apiBackup"] = flat(urls.get("apiBackup")) if "apiBackup" in urls else None
    row["headers_added"] = sorted(set(armed.headers or {}) - base_headers)
    row["sandboxMode_option"] = (armed.options or {}).get("sandboxMode", "<absent>")

    # The two candidate proofs, evaluated independently.
    #
    # THE URL-SWAP PROOF USED TO BE A STRING COMPARISON AND THAT WAS VERSION-SPECIFIC.
    # It read `row["armed_api"] == row["test_url"]`, i.e. the armed `urls['api']` had to
    # serialise IDENTICALLY to `urls['test']`. That held on ccxt 4.3.92, whose
    # set_sandbox_mode did a wholesale `self.urls['api'] = self.urls['test']`. On ccxt
    # 4.5.90 the same call MERGES instead: keys that have a testnet counterpart are
    # swapped and keys that do not (binance's `sapi`, `papi`, ...) are kept, so the armed
    # map is a superset of `test` and the strings differ even though every swappable
    # endpoint moved.
    #
    # The consequence was a probe that reported the opposite of the truth: under 4.5.90 it
    # printed "*** NONE - ARMED BUT STILL ON PRODUCTION ***" for binance and bybit while
    # its own `armed_api` line showed testnet.binancefuture.com. Believing it would have
    # meant concluding that upgrading ccxt removes binance's sandbox -- and DUP-01's live
    # certification on 2026-10-25 disproves that directly: it armed sandbox on binance via
    # this same path and asserted all 7 resolved URLs were testnet hosts before submitting.
    #
    # So the proof is now STRUCTURAL rather than textual, and asks the question that
    # actually matters: for every endpoint `test` provides, does the armed map now carry
    # the test value rather than the production one? That is true on both ccxt versions,
    # and it is the same principle
    # scripts/certify_venue_duplicate_protection.assert_pointed_at_sandbox applies against
    # a live client.
    prod_map = (pristine.urls or {}).get("api")
    test_map = (pristine.urls or {}).get("test")
    armed_map = urls.get("api")

    def _swapped(prod, test, armed) -> bool:
        """Did every endpoint `test` declares move off its production value?"""
        if isinstance(test, dict) and isinstance(armed, dict):
            checked = 0
            for key, test_value in test.items():
                if key not in armed:
                    return False
                prod_value = prod.get(key) if isinstance(prod, dict) else None
                if armed[key] != test_value:
                    return False
                # A key whose test and production values are identical proves nothing
                # either way; it is only evidence when the two differ.
                if prod_value is not None and prod_value != test_value:
                    checked += 1
            return checked > 0
        if isinstance(test, str) and isinstance(armed, str):
            return armed == test and armed != prod
        return False

    row["proof_url_swap"] = bool(
        row["has_apiBackup"]
        and test_map is not None
        and _swapped(prod_map, test_map, armed_map)
    )
    row["proof_header"] = bool(row["headers_added"])
    row["any_proof"] = row["proof_url_swap"] or row["proof_header"]
    #: Recorded so a future reader can tell "the swap happened" from "the serialisations
    #: matched", which is the distinction that made the old proof wrong.
    row["armed_api_equals_test_verbatim"] = row["armed_api"] == row["test_url"]
    return row


def main() -> None:
    print("=" * 94)
    print("EXCH-01 — LOCALLY AVAILABLE SANDBOX ROUTING EVIDENCE")
    print("offline · no credential · no network · no order")
    print("=" * 94)
    print(f"ccxt           : {ccxt.__version__}")
    print(f"approved venues: {APPROVED}")
    print(f"safety-blocked : {BLOCKED}")
    print()

    rows = {}
    for eid in APPROVED + BLOCKED:
        r = probe(eid)
        rows[eid] = r
        tag = "APPROVED" if eid in APPROVED else "BLOCKED "
        print("-" * 94)
        print(f"{eid}  [{tag}]")
        if "error" in r:
            print(f"   {r['error']}")
            continue
        if str(r.get("armed", "")).startswith("RAISED"):
            print(f"   declares urls['test'] : {r['declares_test']}")
            print(f"   arming sandbox        : {r['armed']}")
            print(f"   -> MECHANISM: UNSUPPORTED (no sandbox on this ccxt)")
            continue
        print(f"   declares urls['test'] : {r['declares_test']}")
        print(f"   production api        : {r['prod_api'][:78]}")
        print(f"   armed api             : {r['armed_api'][:78]}")
        print(f"   urls['apiBackup'] set : {r['has_apiBackup']}")
        print(f"   headers added         : {r['headers_added']}")
        print(f"   options['sandboxMode']: {r['sandboxMode_option']}")
        print(f"   PROOF url swap        : {r['proof_url_swap']}")
        print(f"   PROOF header          : {r['proof_header']}")
        mech = ("URL_SWAP" if r["proof_url_swap"]
                else "HEADER" if r["proof_header"]
                else "*** NONE — ARMED BUT STILL ON PRODUCTION ***")
        print(f"   -> MECHANISM: {mech}")

    print()
    print("=" * 94)
    print("CONCLUSION FOR THE VERIFIER DESIGN")
    print("=" * 94)
    url_swap = [e for e, r in rows.items() if r.get("proof_url_swap")]
    header = [e for e, r in rows.items()
              if r.get("proof_header") and not r.get("proof_url_swap")]
    unsupported = [e for e, r in rows.items() if str(r.get("armed", "")).startswith("RAISED")]
    none_proof = [e for e, r in rows.items()
                  if r.get("armed") == "constructed" and not r.get("any_proof")]

    print(f"URL_SWAP (apiBackup + api == test) : {url_swap}")
    print(f"HEADER only                        : {header}")
    print(f"UNSUPPORTED (constructor raises)   : {unsupported}")
    print(f"NO PROOF — armed yet on production : {none_proof}")
    print()
    print("`apiBackup` is present for every URL_SWAP venue, so the swap can be verified")
    print("FROM THE CLIENT ALONE — no reference client and no network call required.")
    if none_proof:
        print()
        print(f"{none_proof} would pass any check based on 'set_sandbox_mode did not raise'.")
        print("This is exactly the EXCH-01 condition and is why the verifier must require")
        print("positive endpoint evidence rather than the absence of an exception.")

    out = ROOT / "staging" / "exch01_sandbox_endpoint_evidence.json"
    out.write_text(json.dumps({
        "ccxt_version": ccxt.__version__,
        "approved": APPROVED,
        "blocked": BLOCKED,
        "per_venue": rows,
        "mechanisms": {
            "url_swap": url_swap,
            "header_only": header,
            "unsupported": unsupported,
            "no_proof": none_proof,
        },
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nartifact -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
