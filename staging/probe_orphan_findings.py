"""Print the full array entry for every finding id that the remediation block omits.

Needed because those ids cannot be added to the authoritative current-state block honestly
without reading what each one actually says.
"""

import json
import pathlib

STAGING = pathlib.Path(__file__).resolve().parent
FILES = [
    "full_audit_results.json",
    "marketplace_audit.json",
    "money_flow_audit.json",
    "trading_audit.json",
]
WANTED = {
    "MKT-01",
    "MKT-02",
    "MONEY-03",
    "TRADE-01",
    "TRADE-02",
    "TRADE-07",
    "TRADE-08",
    "TRADE-09",
}

seen = {}
for name in FILES:
    path = STAGING / name
    if not path.exists():
        continue
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        continue
    for key, value in doc.items():
        if key == "remediation" or not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("id") in WANTED:
                seen.setdefault(item["id"], item)

for fid in sorted(seen):
    entry = seen[fid]
    print("=" * 78)
    print(f"ID       : {fid}")
    for field in ("severity", "status", "fixed", "title", "component", "owner"):
        if field in entry:
            print(f"{field:9}: {entry[field]}")
    for field in ("description", "detail", "finding", "evidence", "impact",
                  "remediation", "note"):
        if field in entry:
            print(f"{field:9}: {str(entry[field])[:700]}")
    extra = sorted(set(entry) - {
        "id", "severity", "status", "fixed", "title", "component", "owner",
        "description", "detail", "finding", "evidence", "impact", "remediation", "note",
    })
    if extra:
        print(f"other keys: {extra}")
