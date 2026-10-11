"""Print the BUG-049 matrix in the shape a reviewer needs before approving a migration.

The JSON is the artefact; this is the reading view. Multi-line Python through PowerShell
`-c` fails in this workspace, so helpers live in files.
"""

from __future__ import annotations

import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "BUG049_IDENTITY_MATRIX.json")
data = json.loads(path.read_text(encoding="utf-8"))

print("CLASS A/B COLUMNS WHOSE TYPE MUST CHANGE")
print("-" * 100)
print(f"{'table':<26}{'column':<14}{'current':<22}{'null':<6}{'rows':<6}{'ON DELETE'}")
print("-" * 100)
for row in data["columns"]:
    if row["classification"] in ("A", "B") and row["current_type"] != "uuid":
        inv = row["value_inventory"]
        print(f"{row['table']:<26}{row['column']:<14}{row['current_type']:<22}"
              f"{row['nullable']:<6}{inv['total']:<6}{row['recommended_on_delete']}")

print()
print("CLASS C (external provider identifier) -- must NOT be converted")
print("-" * 100)
found = False
for row in data["columns"]:
    if row["classification"] == "C":
        found = True
        print(f"  {row['table']}.{row['column']} ({row['current_type']})")
        print(f"      {row['classification_evidence']}")
if not found:
    print("  (none matched by name among the columns examined)")

print()
print("CLASS D / E / UNCLASSIFIED -- excluded from conversion")
print("-" * 100)
for row in data["columns"]:
    if row["classification"] in ("D", "E", "UNCLASSIFIED"):
        print(f"  [{row['classification']}] {row['table']}.{row['column']} "
              f"({row['current_type']})")
        print(f"      {row['classification_evidence']}")

print()
print("CLASS A/B ALREADY uuid BUT WITH NO FOREIGN KEY")
print("-" * 100)
no_fk = [r for r in data["columns"]
         if r["classification"] in ("A", "B")
         and r["current_type"] == "uuid" and r["existing_fk"] is None]
print(f"  {len(no_fk)} column(s):")
for row in no_fk:
    print(f"    {row['table']:<30}{row['column']:<16}-> suggested ON DELETE "
          f"{row['recommended_on_delete']}")

print()
print("SUMMARY")
print("-" * 100)
for key, value in data["summary"].items():
    print(f"  {key:<32}{value}")
