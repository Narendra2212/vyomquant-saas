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
    row["proof_url_swap"] = bool(
        row["has_apiBackup"]
        and row["armed_api"] == row["test_url"]
        and row["armed_api"] != row["apiBackup"]
    )
    row["proof_header"] = bool(row["headers_added"])
    row["any_proof"] = row["proof_url_swap"] or row["proof_header"]
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
