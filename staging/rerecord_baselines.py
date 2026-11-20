"""Re-record exactly two authorised baseline deltas, and nothing else.

WHY THIS IS SURGICAL AND NOT A REGENERATION
-------------------------------------------
`tests/regression/capture_baseline.py` can regenerate a whole file, and using it here
would be wrong: a blanket overwrite absorbs EVERY difference, including any drift nobody
authorised. The point of re-recording is to accept two named changes and leave the
tripwire armed for everything else. So this script edits the two entries it names, writes
the authorisation beside them as a `_baseline_note…` member (the protocol
`test_baseline_unchanged.py` documents), and then the differ is re-run to confirm the
remaining comparison is clean.

THE TWO AUTHORISED CHANGES
--------------------------
1. live_order_path: `contract.parameters` gains `risk_approved` and `risk_reason`.
   Authorised by TRADE-05. `execute_with_idempotency` previously placed live orders with
   no risk verdict at all; the verdict is now a required property of the live call
   (`execution_engine.py:1022` refuses anything that is not True). Both parameters are
   optional with safe defaults (`None` and `""`), so no existing caller's signature
   breaks -- and `None` is precisely what makes an unevaluated live order refuse.

2. surface_exchange_connection: the `user` parameter of `delete_connection` and
   `store_keys` moves from `get_current_user` to `require_aal2`.
   Authorised by AUTH-01. Those two endpoints store and delete exchange API
   credentials. Step-up authentication is strictly stronger than the binding it
   replaces, and `require_aal2` resolves the same user object, so this is a hardening
   rather than a contract change. The other seven `get_current_user` bindings in this
   surface are deliberately untouched.

Run: .venv\\Scripts\\python.exe staging\\rerecord_baselines.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

BASE = ROOT / "tests" / "regression" / "baseline"

from tests.regression.capture_baseline import capture  # noqa: E402


def normalised(payload):
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


changes = []

# ── 1. live_order_path ─────────────────────────────────────────────────────
lop_path = BASE / "live_order_path.json"
lop = json.loads(lop_path.read_text(encoding="utf-8"))
live_now = normalised(capture("live_order_path"))

params = lop["contract"]["parameters"]
params_now = live_now["contract"]["parameters"]

for name in ("risk_approved", "risk_reason"):
    if name in params:
        changes.append(f"live_order_path: {name} already recorded, left alone")
        continue
    if name not in params_now:
        raise SystemExit(
            f"ABORT: {name} is not in the re-run capture either. The baseline failure "
            f"was not caused by this parameter, so re-recording it would be wrong."
        )
    params[name] = params_now[name]
    changes.append(f"live_order_path: recorded contract.parameters.{name} = "
                   f"{json.dumps(params_now[name])}")

# Guard: nothing else in contract.parameters may differ.
unauthorised = {
    k: (params.get(k), params_now.get(k))
    for k in set(params) | set(params_now)
    if not k.startswith("_baseline_note") and params.get(k) != params_now.get(k)
}
if unauthorised:
    raise SystemExit(
        "ABORT: contract.parameters differs in ways this script is not authorised to "
        f"re-record: {json.dumps(unauthorised, indent=2)}"
    )

lop["contract"]["_baseline_note_parameters_risk_verdict"] = (
    "TRADE-05 AUTHORISES the addition of `risk_approved` and `risk_reason` to "
    "`ExecutionEngine.execute_with_idempotency`. Before the fix this method placed LIVE "
    "orders with no risk verdict of any kind: `execute_trade` forwarded none and the "
    "venue branch did not ask for one, so reaching the gateway conferred permission to "
    "skip risk. The verdict is now a property of the call -- `risk_approved` defaults to "
    "None and `execution_engine.py:1022` refuses any live order whose value is not True, "
    "before the exchange adapter is consulted. Both parameters are OPTIONAL with safe "
    "defaults (None, \"\"), so no existing caller's signature breaks, and the None "
    "default is what makes an unevaluated live order fail closed rather than trade. "
    "Evidence: tests/test_trade04_live_order_gate_at_venue_boundary.py; "
    "tests/test_live_risk_gate_enforcement.py::"
    "test_unapproved_live_order_never_reaches_ccxt (the negative control) and "
    "::test_risk_approved_live_order_invokes_ccxt_exactly_once (the positive control). "
    "This is an ADDITION to a signature, not a removal, rename or retype of anything the "
    "baseline recorded."
)
lop_path.write_text(json.dumps(lop, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
changes.append("live_order_path: wrote _baseline_note_parameters_risk_verdict")

# ── 2. surface_exchange_connection ─────────────────────────────────────────
sec_path = BASE / "surface_exchange_connection.json"
sec = json.loads(sec_path.read_text(encoding="utf-8"))
sec_now = normalised(capture("surface_exchange_connection"))

AUTHORISED = {
    ("delete_connection", "user"): ("get_current_user", "require_aal2"),
    ("store_keys", "user"): ("get_current_user", "require_aal2"),
}

applied = set()
for call in sec.get("api_calls", []):
    for handler in call.get("handlers", []):
        hname = handler.get("handler")
        for pname, pval in (handler.get("parameters") or {}).items():
            key = (hname, pname)
            if key not in AUTHORISED or not isinstance(pval, dict):
                continue
            was, now = AUTHORISED[key]
            if pval.get("depends_on") == now:
                changes.append(f"surface_exchange_connection: {hname}.{pname} already "
                               f"{now}, left alone")
                applied.add(key)
                continue
            if pval.get("depends_on") != was:
                raise SystemExit(
                    f"ABORT: {hname}.{pname} records {pval.get('depends_on')!r}, not the "
                    f"expected {was!r}. Not re-recording an unexpected value."
                )
            pval["depends_on"] = now
            applied.add(key)
            changes.append(f"surface_exchange_connection: {hname}.{pname}.depends_on "
                           f"{was} -> {now}")

missing = set(AUTHORISED) - applied
if missing:
    raise SystemExit(f"ABORT: authorised entries not found in the baseline: {missing}")

sec["_baseline_note_auth01_require_aal2_on_credential_endpoints"] = (
    "AUTH-01 AUTHORISES the `user` dependency of `delete_connection` "
    "(DELETE /api/exchanges/{exchangeId}) and `store_keys` (POST /api/exchanges/keys) "
    "moving from `get_current_user` to `require_aal2`. Those two endpoints delete and "
    "store exchange API credentials -- the highest-value write surface in the "
    "application -- and `require_aal2`'s own docstring already named them as MUST-apply "
    "while they were bound to plain authentication. `require_aal2` resolves the same "
    "user object and additionally requires a step-up (AAL2) session, so the binding is "
    "STRICTLY STRONGER: every request that used to be authorised still is, unless it "
    "lacks step-up. No response field, status code or parameter name changed. The other "
    "seven `get_current_user` bindings in this surface are deliberately unchanged, which "
    "is why this note names two handlers rather than the surface. Evidence: "
    "tests/test_auth01_require_aal2_is_wired.py (16 passed). Remaining gap, tracked "
    "separately as AUTH-03: billing checkout, verify-payment, payment-methods, referral "
    "payouts and three marketplace routes are still on plain authentication."
)
sec_path.write_text(json.dumps(sec, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
changes.append("surface_exchange_connection: wrote "
               "_baseline_note_auth01_require_aal2_on_credential_endpoints")

print("RE-RECORDED BASELINE DELTAS")
print("=" * 74)
for line in changes:
    print(f"  {line}")
print("=" * 74)
print("Both files rewritten. The differ must now be re-run: any OTHER difference is "
      "still a failure, which is the point of patching surgically.")
