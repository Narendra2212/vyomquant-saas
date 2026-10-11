"""Print one table's full isolation-probe result from the JSON matrix."""

from __future__ import annotations

import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "staging/isolation_matrix_local.json"
wanted = sys.argv[2:] or ["profiles"]

with open(path, encoding="utf-8") as fh:
    data = json.load(fh)

for probe in data["probes"]:
    if probe["table"] in wanted:
        print(f"{probe['table']}  seeded={probe['seeded']}  "
              f"rows_before={probe['rows_before']}")
        if probe["seed_error"]:
            print(f"  seed_error: {probe['seed_error']}")
        for name, outcome in probe["results"].items():
            print(f"  {name:<20} {outcome}")
        if probe.get("verdict"):
            print(f"  verdict: {probe['verdict']}")
        for leak in probe.get("leaks", []):
            print(f"  LEAK: {leak}")
        print()
