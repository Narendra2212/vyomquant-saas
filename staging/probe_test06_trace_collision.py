"""Reproduce the exact collision in test_trace_and_export_surfaces_contain_no_protected_logic.

The Hypothesis failure named token 'vie' at $.details.operation. This probe reconstructs
the two refusal bodies that test builds and shows what their `operation` value actually is,
so the fix rests on the measured value rather than a guess about which operation name
collides with 'vie'.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from backend_app.backend.marketplace import subscriber_operation_guard as guard

    print("=" * 78)
    print("subscriber_operation_guard RestrictedOperation names")
    print("=" * 78)
    # Every RestrictedOperation the guard exposes -- these are the `operation.name` values
    # that land in the refusal body's `details.operation`.
    for attr in dir(guard):
        obj = getattr(guard, attr)
        name = getattr(obj, "name", None)
        if name and attr.isupper():
            print(f"  {attr}: operation.name = {name!r}")

    print()
    print("Which of those names contain the 3-char run 'vie'?")
    for attr in dir(guard):
        obj = getattr(guard, attr)
        name = getattr(obj, "name", None)
        if name and "vie" in str(name).lower():
            print(f"  {attr}: {name!r} contains 'vie'  (case-insensitive)")
            print(f"      case-sensitive 'vie' in name: {'vie' in str(name)}")

    print()
    print("The guard stub in the test raises with details={'operation': operation.name,")
    print("'listing_id': str(strategy_id)}. The preview route's operation:")
    # preview_node uses STRATEGY_NODE_PREVIEW or similar; compare uses VERSION_COMPARE.
    for cand in ("VERSION_COMPARE", "STRATEGY_NODE_PREVIEW", "NODE_PREVIEW",
                 "PREVIEW_NODE", "STRATEGY_PREVIEW"):
        obj = getattr(guard, cand, None)
        if obj is not None:
            print(f"  {cand}.name = {getattr(obj, 'name', None)!r}")

    print()
    print("CONCLUSION: if a generated token like 'vie' is a substring of an operation.name")
    print("value, that value is STRATEGY-INDEPENDENT (it is the route's own constant), so")
    print("the test's independent_text corpus should include every operation.name -- it")
    print("currently does not, which is the test defect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
