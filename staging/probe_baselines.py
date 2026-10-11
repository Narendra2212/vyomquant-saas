"""Show the exact regions of the two failing baseline files that need re-recording."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "tests" / "regression" / "baseline"

print("=" * 74)
print("live_order_path.json -> contract.parameters")
print("=" * 74)
lop = json.loads((BASE / "live_order_path.json").read_text(encoding="utf-8"))
print("top-level keys:", sorted(lop.keys()))
contract = lop.get("contract", {})
print("contract keys:", sorted(contract.keys()))
params = contract.get("parameters", {})
print(f"contract.parameters has {len(params)} entries:")
print(json.dumps(params, indent=2)[:2600])
notes = [k for k in contract if k.startswith("_baseline_note")]
print("existing notes in contract:", notes)
notes_p = [k for k in params if k.startswith("_baseline_note")]
print("existing notes in contract.parameters:", notes_p)

print()
print("=" * 74)
print("surface_exchange_connection.json -> api_calls[0] and [5] depends_on")
print("=" * 74)
sec = json.loads((BASE / "surface_exchange_connection.json").read_text(encoding="utf-8"))
print("top-level keys:", sorted(sec.keys()))
calls = sec.get("api_calls", [])
print(f"api_calls count = {len(calls)}")
for i in (0, 5):
    if i >= len(calls):
        continue
    c = calls[i]
    print(f"\napi_calls[{i}]: {c.get('method')} {c.get('path_template')} via {c.get('via')!r}")
    for h in c.get("handlers", []):
        print(f"  handler={h.get('handler')} module={h.get('module')}")
        for pname, pval in (h.get("parameters") or {}).items():
            if isinstance(pval, dict) and "depends_on" in pval:
                print(f"    param {pname}: depends_on={pval['depends_on']!r} "
                      f"annotation={pval.get('annotation')!r}")
        print(f"  existing notes in handler: "
              f"{[k for k in h if k.startswith('_baseline_note')]}")
    print(f"  existing notes in api_calls[{i}]: "
          f"{[k for k in c if k.startswith('_baseline_note')]}")

print("\nALL depends_on values across the file:")
seen = {}


def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "depends_on" and isinstance(v, str):
                seen.setdefault(v, []).append(path)
            walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]")


walk(sec)
for val, paths in sorted(seen.items()):
    print(f"  {val!r}: {len(paths)} occurrence(s)")
    for p in paths[:8]:
        print(f"      {p}")
