"""Is 'Subscription Renewed' missing from billing.py, or merely outside a 3000-char window?

test_billing_e2e.py::TestRenewalHandler::test_renewal_dispatches_notification slices
source[idx : idx+3000] from the first occurrence of 'invoice.payment_succeeded' and
requires the literal 'Subscription Renewed' inside it. That is a distance assertion
wearing a presence assertion's clothes, so the two must be told apart before anything is
changed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / "backend_app" / "routers" / "billing.py"
source = path.read_text(encoding="utf-8")

anchor = "invoice.payment_succeeded"
idx = source.index(anchor)
print(f"anchor {anchor!r} first occurs at offset {idx}")

needle = "Subscription Renewed"
positions = []
start = 0
while True:
    found = source.find(needle, start)
    if found == -1:
        break
    positions.append(found)
    start = found + 1

print(f"{needle!r} occurrences in the whole file: {len(positions)} at {positions}")
for p in positions:
    print(f"  offset {p} -> distance from anchor = {p - idx}")

print(f"\nwindow is source[{idx}:{idx + 3000}]")
inside = [p for p in positions if idx <= p < idx + 3000]
print(f"occurrences INSIDE the window: {len(inside)}")

# Also check the sibling assertion that still passes, for calibration.
other = "subscription_renewed"
op = source.find(other, idx)
print(f"\n{other!r} first occurrence at/after anchor: offset {op} "
      f"(distance {op - idx})" if op != -1 else f"{other!r} not found after anchor")

# How far would the window need to stretch?
if positions and not inside:
    nearest = min((p for p in positions if p > idx), default=None)
    if nearest is not None:
        print(f"\nVERDICT: present in the file but {nearest - idx} chars from the anchor, "
              f"i.e. {nearest - idx - 3000} chars beyond the window.")
elif inside:
    print("\nVERDICT: inside the window - the assertion should pass.")
else:
    print("\nVERDICT: the string is ABSENT from billing.py entirely.")
