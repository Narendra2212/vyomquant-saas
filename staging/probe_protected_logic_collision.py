"""Is the 'vie' failure a Protected_Logic leak, or a substring collision?

Hypothesis reported: token 'vie' appears at $.details.operation.
The generated strategy contained a params key 'vie'. If $.details.operation is a FIXED
literal that merely contains the letters v-i-e, nothing leaked: the oracle's `in` test
collided with an English word. That distinction decides whether this is a security defect
or a test defect, so it is measured rather than argued.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from backend_app.backend.marketplace import subscriber_operation_guard as guard  # noqa: E402

print("subscriber_operation_guard operation identifiers")
print("=" * 70)
candidates = {}
for name in dir(guard):
    if name.startswith("_"):
        continue
    value = getattr(guard, name)
    if isinstance(value, str):
        candidates[name] = value
    elif hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        candidates[name] = value.value
    elif hasattr(value, "operation"):
        try:
            op = getattr(value, "operation")
            if isinstance(op, str):
                candidates[f"{name}.operation"] = op
        except Exception:
            pass

for name, value in sorted(candidates.items()):
    flag = "  <-- CONTAINS 'vie'" if "vie" in value else ""
    print(f"  {name:<45} = {value!r}{flag}")

print()
print("VERDICT")
print("=" * 70)
hits = {n: v for n, v in candidates.items() if "vie" in v}
if hits:
    for n, v in hits.items():
        print(f"  'vie' is a substring of the FIXED literal {v!r} ({n}).")
    print("  The generated params key 'vie' never reached the response; the oracle's")
    print("  substring test matched a hardcoded English word. SUBSTRING COLLISION,")
    print("  not a Protected_Logic leak.")
else:
    print("  No fixed literal containing 'vie' found -- the leak claim is NOT explained")
    print("  by a collision and must be treated as real.")

# How short can a generated token be? If the generator emits 3-char tokens and the oracle
# does a naive `in`, collisions with ordinary English words are inevitable.
try:
    from backend_app.backend.marketplace.listing_projection import protected_logic_tokens
    sample = {
        "buy_logic": [{"node_id": "n", "indicator": "i",
                       "params": {"vie": "400"}, "operator": ">", "threshold": "400"}],
    }
    tokens = protected_logic_tokens(sample)
    short = sorted(t for t in tokens if isinstance(t, str) and len(t) <= 4)
    print()
    print(f"  protected_logic_tokens emitted {len(tokens)} tokens for a minimal strategy;")
    print(f"  {len(short)} of them are <= 4 characters: {short[:20]}")
    print("  A <=4-character token searched with `in` against prose is a false-positive")
    print("  generator. This is the same defect class as the four text-scan false")
    print("  positives this engagement already hit in its own detectors.")
except Exception as exc:  # noqa: BLE001
    print(f"  (could not sample protected_logic_tokens: {exc!r})")
