#!/usr/bin/env python3
"""Does the repaired sandbox verification still REFUSE what it must?

WS-03's ccxt upgrade broke `verify_sandbox_endpoint`: its third condition compared the
armed endpoint against `urls['apiBackup']`, which ccxt 4.3.92 filled with PRODUCTION
before swapping. ccxt 4.5.90 leaves `apiBackup` equal to the TEST urls, so the comparison
stopped meaning anything and began refusing every legitimate sandbox connection. It
failed CLOSED, so nothing was endangered, but testnet trading and DUP-01's certification
harness were both dead.

The fix compares against a PRISTINE client's production endpoint instead. Relaxing a
money-safety check to make a test pass is exactly the move that must not go unexamined,
so this asserts BOTH directions, offline and with no credentials:

  * a venue that genuinely swaps (binance, bybit) must VERIFY;
  * a venue that arms sandbox mode and stays on production (bitget, the venue EXCH-01
    withheld for precisely this) must still RAISE. If this half ever passes, the control
    has been hollowed out and EXCH-01's defect is live again.

    python scripts/verify_sandbox_control_still_refuses.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")


def main() -> int:
    import ccxt

    from backend_app.backend.exchange_executor import (SANDBOX_MECHANISMS,
                                                       SandboxMechanism,
                                                       SandboxVerificationFailed,
                                                       verify_sandbox_endpoint)

    print(f"ccxt {ccxt.__version__}")
    print()

    failures = 0

    # ── must VERIFY ────────────────────────────────────────────────────────────
    for eid in ("binance", "bybit"):
        mech, _ = SANDBOX_MECHANISMS.get(eid, (None, None))
        if mech is not SandboxMechanism.URL_SWAP:
            print(f"{eid}: not URL_SWAP in SANDBOX_MECHANISMS, skipping")
            continue
        client = getattr(ccxt, eid)({"sandbox": True, "options": {"sandbox": True}})
        try:
            v = verify_sandbox_endpoint(client, eid)
            print(f"{eid:<9} VERIFIED   mechanism={v.mechanism}")
            print(f"            sandbox   : {str(v.endpoint)[:72]}")
            print(f"            production: {str(v.production_endpoint)[:72]}")
            if v.endpoint == v.production_endpoint:
                print(f"            REFUSED EXPECTATION: sandbox == production but it "
                      f"verified anyway")
                failures += 1
        except SandboxVerificationFailed as exc:
            print(f"{eid:<9} RAISED (should have verified): {exc}")
            failures += 1
        print()

    # ── must RAISE ─────────────────────────────────────────────────────────────
    # bitget arms sandbox mode and stays on production: its set_sandbox_mode does not call
    # super() and it publishes no urls['test']. EXCH-01 withheld it from routing for that.
    # Constructed DIRECTLY, bypassing assert_exchange_routable, so the VERIFIER's own
    # judgement is what is under test rather than the routing block.
    #
    # TWO REFUSALS ARE CHECKED, AND THE SECOND IS THE ONE THAT MATTERS. Under its own id
    # bitget trips the unmeasured-venue branch before any endpoint is looked at, so that
    # pass proves only that the venue table is closed -- it would stay green even if the
    # endpoint logic were deleted outright. Evaluating the SAME REAL CLIENT under a
    # URL_SWAP declaration is what forces the endpoint evidence to be judged.
    real_bitget = getattr(ccxt, "bitget")({"sandbox": True, "options": {"sandbox": True}})
    cases = (
        ("bitget", "bitget", "the venue table is closed to unmeasured venues"),
        ("bitget", "binance", "the ENDPOINT EVIDENCE is judged, not the venue name"),
    )
    for label, declared_as, what_it_proves in cases:
        try:
            v = verify_sandbox_endpoint(real_bitget, declared_as)
            print(f"{label} as {declared_as:<8} VERIFIED -- THIS IS THE FAILURE. A venue "
                  f"that never left production was certified as sandboxed. "
                  f"endpoint={str(v.endpoint)[:60]}")
            failures += 1
        except SandboxVerificationFailed as exc:
            print(f"{label} as {declared_as:<8} RAISED as required "
                  f"({what_it_proves})")
            print(f"                         {str(exc)[:120]}")
        except Exception as exc:  # noqa: BLE001
            # Any other exception is still a refusal, but say so rather than counting it
            # as the refusal that was asked for.
            print(f"{label} as {declared_as:<8} raised {type(exc).__name__} (a refusal, "
                  f"though not SandboxVerificationFailed): {str(exc)[:90]}")
        print()

    print("=" * 74)
    if failures:
        print(f"{failures} problem(s). The control is NOT behaving correctly in both "
              f"directions; do not ship this.")
        return 1
    print("The control verifies real sandboxes AND still refuses a venue that stays on")
    print("production. The repair did not hollow it out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
