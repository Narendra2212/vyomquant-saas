"""Re-add the TRADE-05 authorisation note to live_order_path.json, labelled as a
reconstruction.

WHY THIS SCRIPT EXISTS, STATED PLAINLY
--------------------------------------
While re-recording this baseline for TRADE-01 I ran `git checkout --` on
`tests/regression/baseline/live_order_path.json` to undo a bad intermediate write. The
file's TRADE-05 note was an UNCOMMITTED change from an earlier phase, and HEAD does not
contain it (`git show HEAD:... | grep _baseline_note` returns nothing), so the checkout
destroyed it. That is my error and it is recorded rather than hidden.

The surviving opening of the original, from a grep taken before the loss, was:

    "TRADE-05 AUTHORISES the addition of `risk_approved` and `risk_reason` to
     `ExecutionEngine.execute_with_idempotency`. Before the fix this method placed LIVE
     orders with no risk verdict of any kind: `execute_trade` forwarded none and the venue
     branch did not ask for one, so reaching the gateway conferred permission to skip
     risk. The verdict is now a pro[…truncated]"

Everything after that point is reconstructed from the code and tests the note refers to,
not recovered. The note below says so explicitly, so no reader mistakes a reconstruction
for the original text.
"""
from __future__ import annotations

import json
import pathlib

PATH = pathlib.Path(__file__).resolve().parents[1] / (
    "tests/regression/baseline/live_order_path.json"
)

KEY = "_baseline_note_parameters_risk_verdict"
NOTE = (
    "TRADE-05 AUTHORISES the addition of `risk_approved` and `risk_reason` to "
    "`ExecutionEngine.execute_with_idempotency`. Before the fix this method placed LIVE "
    "orders with no risk verdict of any kind: `execute_trade` forwarded none and the "
    "venue branch did not ask for one, so reaching the gateway conferred permission to "
    "skip risk. The verdict is now a property of the gateway rather than of the caller: "
    "`risk_approved` defaults to `None` and the venue branch refuses anything that is not "
    "`True` (`execution_engine.py`, the `self._live_risk_approved is not True` refusal), "
    "so a caller that does not evaluate risk cannot supply the evidence and cannot place "
    "a live order. That is what makes the invariant structural rather than a convention "
    "the caller is trusted to follow - it closes the two doors the audit flagged as "
    "inheriting `bot_runner` authorisation without inheriting the risk check "
    "(`distributed_execution/execution_worker.py` and `idempotent_exchange_submission.py`). "
    "Paper orders are unaffected: they do not reach that branch. Evidence: "
    "tests/test_trade04_live_order_gate_at_venue_boundary.py and "
    "tests/test_live_risk_gate_enforcement.py::test_unapproved_live_order_never_reaches_ccxt. "
    "PROVENANCE: this note is a RECONSTRUCTION. The original was an uncommitted working-tree "
    "change and was destroyed by a `git checkout --` on this file during the TRADE-01 "
    "re-record in Phase 8; HEAD never carried it. Its surviving opening ran to \"The verdict "
    "is now a pro...\" and the remainder above is rebuilt from the code and tests it cites, "
    "not recovered. Recorded in staging/REMEDIATION_REPORT.md's Phase 8 block."
)

doc = json.loads(PATH.read_text(encoding="utf-8"))

contract = doc.get("contract")
if not isinstance(contract, dict):
    raise SystemExit("live_order_path.json has no `contract` object to annotate")

#: `contract.parameters` is a MAPPING of name -> {annotation, has_default}, measured from
#: the file rather than assumed. The first version of this script expected a list and its
#: own guard refused, which is what the guard is for.
parameters = contract.get("parameters")
names = set(parameters) if isinstance(parameters, dict) else set()
print(f"CONTRACT_PARAMETERS={sorted(names)}")

missing = {"risk_approved", "risk_reason"} - names
if missing:
    raise SystemExit(
        f"refusing to add a TRADE-05 note: {sorted(missing)} is not in the recorded "
        f"parameters, so the note would authorise something this file does not record"
    )

contract[KEY] = NOTE
PATH.write_text(
    json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
)
print(f"RESTORED={KEY} into contract")
