"""Authoritative tally over the MERGED connectivity artifact.

The runner's own summary only covers the venues IT attempted, so a targeted retry pass
prints a figure for 32 venues while the artifact holds 103. This reads the merged artifact
and is therefore the only number that should be quoted anywhere.

It also produces the per-venue launch list and the shortfall attribution, split the one
way that matters: failures that are facts about an exchange, versus failures that are
facts about the network this ran from.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
ART = ROOT / "staging" / "exchange_connectivity_results.json"
data = json.loads(ART.read_text(encoding="utf-8"))
per = data["per_exchange"]

TARGET_LOW, TARGET_HIGH = 100, 110

#: Failure classes that are properties of THIS HOST, not of the venue.
HOST_CAUSED = {"EGRESS_FILTERED"}
#: Failure classes that are genuine facts about the venue or the pinned library.
GENUINE = {"LIBRARY_DEFECT", "AUTH_REQUIRED", "NOT_SUPPORTED", "BAD_SYMBOL"}


def ev(eid: str, state: str) -> dict:
    return (per[eid].get("evidence") or {}).get(state) or {}


connected, market_data, failed = [], [], []
for eid in per:
    if ev(eid, "PUBLIC_CONNECTIVITY_VERIFIED").get("attained"):
        connected.append(eid)
        if ev(eid, "MARKET_DATA_VERIFIED").get("attained"):
            market_data.append(eid)
    else:
        failed.append(eid)

by_class: dict[str, list[str]] = defaultdict(list)
for eid in failed:
    by_class[str(ev(eid, "PUBLIC_CONNECTIVITY_VERIFIED").get("failure_class", "UNKNOWN"))]\
        .append(eid)

host_caused = [e for c, es in by_class.items() if c in HOST_CAUSED for e in es]
genuine = [e for c, es in by_class.items()
           if any(c.startswith(g) for g in GENUINE) for e in es]
ambiguous = [e for e in failed if e not in host_caused and e not in genuine]

print("=" * 86)
print("AUTHORITATIVE CONNECTIVITY TALLY  (merged artifact)")
print("=" * 86)
print(f"ccxt                           : {data['summary'].get('ccxt_version')}")
print(f"venues in artifact             : {len(per)}")
print()
print(f"PUBLIC_CONNECTIVITY_VERIFIED   : {len(connected)}")
print(f"MARKET_DATA_VERIFIED           : {len(market_data)}")
print(f"not verified                   : {len(failed)}")
print()
print(f"TARGET {TARGET_LOW}-{TARGET_HIGH}")
print(f"  verified now                 : {len(connected)}")
print(f"  shortfall to {TARGET_LOW}             : {max(0, TARGET_LOW - len(connected))}")
print(f"  met                          : {len(connected) >= TARGET_LOW}")
print()
print("-" * 86)
print("SHORTFALL ATTRIBUTION")
print("-" * 86)
print(f"  HOST-CAUSED (not venue facts) : {len(host_caused):>3}  {sorted(host_caused)}")
print(f"  GENUINE venue/library         : {len(genuine):>3}  {sorted(genuine)}")
print(f"  AMBIGUOUS (timeout/5xx)       : {len(ambiguous):>3}  {sorted(ambiguous)}")
print()
ceiling = len(connected) + len(host_caused) + len(ambiguous)
print(f"  Verifiable CEILING from an unfiltered network: <= {ceiling} of {len(per)}")
print(f"  Hard floor of unverifiable venues on this ccxt: {len(genuine)}")
print()
print("-" * 86)
print("FAILURES BY CLASS")
print("-" * 86)
for c, es in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
    print(f"  {c:<26} {len(es):>3}  {sorted(es)}")

print()
print("-" * 86)
print("STATES NOT ESTABLISHED (each requires its own evidence)")
print("-" * 86)
for s in ("AUTHENTICATED_ACCESS_VERIFIED", "SANDBOX_VERIFIED",
          "TRADING_CERTIFIED", "LIVE_TRADING_AUTHORIZED"):
    n = len([e for e in per if ev(e, s).get("attained")])
    print(f"  {s:<32} {n}")

out = {
    "ccxt_version": data["summary"].get("ccxt_version"),
    "venues_in_artifact": len(per),
    "public_connectivity_verified": sorted(connected),
    "market_data_verified": sorted(market_data),
    "not_verified": sorted(failed),
    "counts": {
        "public_connectivity_verified": len(connected),
        "market_data_verified": len(market_data),
        "not_verified": len(failed),
        "host_caused": len(host_caused),
        "genuine": len(genuine),
        "ambiguous": len(ambiguous),
        "verifiable_ceiling": ceiling,
    },
    "target": {
        "low": TARGET_LOW, "high": TARGET_HIGH,
        "current": len(connected),
        "shortfall_to_low": max(0, TARGET_LOW - len(connected)),
        "met": len(connected) >= TARGET_LOW,
    },
    "shortfall_attribution": {
        "host_caused": sorted(host_caused),
        "genuine": sorted(genuine),
        "ambiguous": sorted(ambiguous),
    },
    "failures_by_class": {c: sorted(es) for c, es in sorted(by_class.items())},
    "market_counts": {
        e: per[e].get("market_count") for e in sorted(connected)
    },
}
dest = ROOT / "staging" / "connectivity_tally.json"
dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"\ntally -> {dest.relative_to(ROOT)}")
