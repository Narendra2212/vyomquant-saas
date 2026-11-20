"""Which Redis primitives does SharedRedisManager actually expose?

The worker-durability fix needs: a dead-letter write (xadd), an idempotency marker
(set NX / setex / exists), a pending-entries reaper (xautoclaim or xpending+xclaim) and
atomic mark+ack (pipeline or eval). Rather than infer the facade's surface from greps,
ask the object.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.cache import redis_manager  # noqa: E402
from backend_app.core.cache.redis_manager import MockRedisClient  # noqa: E402

WANTED = [
    "get", "set", "setex", "delete", "exists", "incr", "expire",
    "eval", "eval_lua", "pipeline",
    "xadd", "xack", "xreadgroup", "xgroup_create",
    "xautoclaim", "xpending", "xpending_range", "xclaim", "xlen", "xinfo_groups",
    "connect", "get_client", "ping",
]

print("=== SharedRedisManager facade ===")
for name in WANTED:
    present = hasattr(redis_manager, name)
    sig = ""
    if present:
        attr = getattr(redis_manager, name)
        try:
            sig = str(inspect.signature(attr))
        except (TypeError, ValueError):
            sig = "<no signature>"
    print(f"{'YES' if present else 'no ':3}  {name:16} {sig}")

print()
print("=== MockRedisClient (DEV_MODE path) ===")
mock = MockRedisClient()
for name in WANTED:
    print(f"{'YES' if hasattr(mock, name) else 'no ':3}  {name}")

print()
print("=== set() signature on facade (need nx= support) ===")
try:
    print(inspect.signature(redis_manager.set))
except Exception as exc:  # noqa: BLE001
    print("unavailable:", exc)

print()
print("=== does the facade reach a real client? ===")
print("DEV_MODE:", os.environ.get("DEV_MODE"), "| ENV:", os.environ.get("ENV"))
