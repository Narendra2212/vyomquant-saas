"""Read-only inspection of the exact baseline regions that shifted, and the fresh
capture at the same paths. Writes nothing. Used to confirm the deltas are precisely the
authorised Phase 9 changes (KILL-03 return fields; TRADE-12 venue_order_id rename) and
nothing else, before re-recording."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.regression.capture_baseline import baseline_path, capture  # noqa: E402


def normalised(payload):
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


print("=" * 78)
print("auth_admin_role :: admin_routes[2].handlers[0].returns[0]")
print("=" * 78)
old = json.loads(baseline_path("auth_admin_role").read_text(encoding="utf-8"))
new = normalised(capture("auth_admin_role"))
ar_old = old["admin_routes"][2]
ar_new = new["admin_routes"][2]
print("baseline route path  :", ar_old["handlers"][0].get("route_path"))
print("baseline handler     :", ar_old["handlers"][0].get("handler"))
print("current  route path  :", ar_new["handlers"][0].get("route_path"))
print("current  handler     :", ar_new["handlers"][0].get("handler"))
print("baseline returns[0]  :", json.dumps(ar_old["handlers"][0]["returns"][0], indent=2, sort_keys=True))
print("current  returns[0]  :", json.dumps(ar_new["handlers"][0]["returns"][0], indent=2, sort_keys=True))

print()
print("=" * 78)
print("live_order_path :: scenarios[0].collaborator_sequence[7]")
print("=" * 78)
lop_old = json.loads(baseline_path("live_order_path").read_text(encoding="utf-8"))
lop_new = normalised(capture("live_order_path"))
seq_old = lop_old["scenarios"][0]["collaborator_sequence"]
seq_new = lop_new["scenarios"][0]["collaborator_sequence"]
print("baseline len seq =", len(seq_old), " current len seq =", len(seq_new))
print("baseline [7]:", json.dumps(seq_old[7], indent=2, sort_keys=True))
print("current  [7]:", json.dumps(seq_new[7], indent=2, sort_keys=True))

# Confirm the FULL sequence of 'call' names is identical in order (only arguments shifted)
calls_old = [c.get("call") for c in seq_old]
calls_new = [c.get("call") for c in seq_new]
print("call-name order identical:", calls_old == calls_new)
if calls_old != calls_new:
    print("  OLD:", calls_old)
    print("  NEW:", calls_new)
