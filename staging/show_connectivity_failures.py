"""Phase 4 triage — group connectivity failures by their ACTUAL venue message.

The point is to separate three very different causes that the runner's coarse
classification lumps together, because the honest shortfall number depends on it:

  * RUNNER ARTIFACT   — my own timeout/cancellation, not the venue's fault. Re-runnable.
  * ENVIRONMENT       — geo-block or network egress restriction from this host. The venue
                        is fine; this vantage point cannot reach it.
  * GENUINE           — the venue is delisted/defunct, or ccxt 4.3.92 cannot parse it.

Only GENUINE failures are a real shortfall against the launch target. Reporting a runner
timeout as a dead exchange would understate connectivity; reporting a defunct exchange as
"retry later" would overstate it.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
data = json.loads(
    (ROOT / "staging" / "exchange_connectivity_results.json").read_text(encoding="utf-8")
)
per = data["per_exchange"]

failures = []
for eid, res in per.items():
    ev = (res.get("evidence") or {}).get("PUBLIC_CONNECTIVITY_VERIFIED") or {}
    if not ev.get("attained"):
        failures.append((eid, ev.get("failure_class", "?"), ev.get("detail") or "",
                         ev.get("method") or ""))

print("=" * 100)
print(f"CONNECTIVITY FAILURES: {len(failures)}")
print("=" * 100)

#: Signatures that identify the real cause from the venue's own words.
PATTERNS = [
    ("RUNNER_ARTIFACT",  r"CancelledError|budget exceeded|TimeoutError"),
    ("DNS_NXDOMAIN",     r"getaddrinfo|Name or service not known|nodename nor servname|"
                         r"Temporary failure in name resolution|NameResolutionError"),
    ("TLS",              r"SSL|certificate|CERTIFICATE_VERIFY"),
    ("HTTP_403_GEO",     r"\b403\b|forbidden|restricted|jurisdiction|not available in|"
                         r"unavailable in your|eligibility|region"),
    ("HTTP_451_LEGAL",   r"\b451\b|unavailable for legal"),
    ("HTTP_404",         r"\b404\b|not found"),
    ("HTTP_5XX",         r"\b50[0234]\b|bad gateway|service unavailable|gateway time"),
    ("CONN_REFUSED",     r"refused|reset by peer|ConnectionResetError|"
                         r"ServerDisconnected|Cannot connect to host"),
    ("MAINTENANCE",      r"maintenance|upgrading|suspend"),
    ("AUTH",             r"api[_ ]?key|apiKey|signature|authentication|unauthorized|401"),
    ("CCXT_PARSE",       r"TypeError|AttributeError|KeyError|IndexError|ValueError|"
                         r"unsupported operand|NoneType"),
]


def signature(detail: str) -> str:
    for label, pat in PATTERNS:
        if re.search(pat, detail, re.IGNORECASE):
            return label
    return "OTHER"


groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
for eid, fc, detail, method in failures:
    groups[signature(detail)].append((eid, fc, detail))

#: How each signature maps onto the three causes above.
CAUSE = {
    "RUNNER_ARTIFACT": "RUNNER ARTIFACT — re-runnable with a longer budget",
    "DNS_NXDOMAIN":    "GENUINE — host does not resolve; venue likely defunct/renamed",
    "TLS":             "ENVIRONMENT or GENUINE — needs inspection",
    "HTTP_403_GEO":    "ENVIRONMENT — geo/IP restriction from this host",
    "HTTP_451_LEGAL":  "ENVIRONMENT — legal block from this host",
    "HTTP_404":        "GENUINE — endpoint gone; ccxt path stale for this venue",
    "HTTP_5XX":        "TRANSIENT or GENUINE — venue-side error; re-runnable",
    "CONN_REFUSED":    "GENUINE or ENVIRONMENT — host unreachable",
    "MAINTENANCE":     "TRANSIENT — venue in maintenance",
    "AUTH":            "GENUINE — venue requires credentials for market data",
    "CCXT_PARSE":      "GENUINE — ccxt 4.3.92 defect parsing this venue",
    "OTHER":           "NEEDS INSPECTION",
}

for sig in sorted(groups, key=lambda s: -len(groups[s])):
    items = groups[sig]
    print()
    print("-" * 100)
    print(f"{sig}  ({len(items)})  ->  {CAUSE.get(sig, '?')}")
    print("-" * 100)
    for eid, fc, detail in sorted(items):
        print(f"  {eid:<20} [{fc}]")
        print(f"      {detail[:160]}")

print()
print("=" * 100)
print("TALLY BY SIGNATURE")
print("=" * 100)
for sig in sorted(groups, key=lambda s: -len(groups[s])):
    print(f"  {sig:<18} {len(groups[sig]):>3}   {CAUSE.get(sig,'?')}")

retryable = [e for s in ("RUNNER_ARTIFACT", "HTTP_5XX", "MAINTENANCE")
             for e, _, _ in groups.get(s, [])]
print()
print(f"WORTH RE-RUNNING ({len(retryable)}): {sorted(retryable)}")
(ROOT / "staging" / "connectivity_retry_list.json").write_text(
    json.dumps(sorted(set(retryable)), indent=2), encoding="utf-8"
)
print("retry list -> staging/connectivity_retry_list.json")
