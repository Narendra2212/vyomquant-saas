"""Where does slowapi keep the Limiter, so it can be found WITHOUT gc.get_objects()?

`gc.get_objects()` raised MemoryError on this process (the app plus hypothesis is millions
of objects), so the conftest guard needs a targeted route. This finds out which of three
cheap places actually holds a reference:

  1. the endpoint function's __closure__ cells
  2. an attribute slowapi sets on the endpoint function
  3. app.state / the module attribute (already known)
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "true"
os.environ["REDIS_URL"] = ""
os.environ["DATABASE_URL"] = ""
os.environ["VYOMQUANT_MODE"] = "safe"
os.environ["DEFAULT_EXCHANGE"] = "binance"

from slowapi import Limiter  # noqa: E402

import backend_app.core.rate_limit as rl  # noqa: E402
from backend_app.main import app  # noqa: E402

print(f"slowapi Limiter from {Limiter.__module__}")
print(f"module limiter id   = {id(rl.limiter):#x}")
print(f"app.state.limiter   = {id(app.state.limiter):#x}")

# -- 1. closures ----------------------------------------------------------
from_closures = set()
checked = 0
for route in app.routes:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        continue
    checked += 1
    func = endpoint
    for _ in range(6):  # unwrap a short chain
        closure = getattr(func, "__closure__", None) or ()
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, Limiter):
                from_closures.add(id(value))
        nxt = getattr(func, "__wrapped__", None)
        if nxt is None:
            break
        func = nxt

print(f"\nroutes inspected        = {checked}")
print(f"Limiters found in closures = {len(from_closures)} {[hex(i) for i in from_closures]}")

# -- 2. attributes slowapi sets on the endpoint ---------------------------
attr_names = set()
for route in app.routes:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        continue
    for name in dir(endpoint):
        if "limit" in name.lower() and not name.startswith("__"):
            attr_names.add(name)
print(f"endpoint attributes mentioning 'limit': {sorted(attr_names)}")

# -- 3. what does Limiter itself expose? ----------------------------------
print(f"\nLimiter public attributes: "
      f"{sorted(a for a in dir(rl.limiter) if not a.startswith('__'))}")

storage = getattr(rl.limiter, "_storage", None)
print(f"storage type = {type(storage).__name__}")
print(f"storage attrs = {sorted(a for a in dir(storage) if not a.startswith('__'))}")

# -- 4. is `limits` caching storage per URI (would make them shared)? -----
try:
    from limits.storage import storage_from_string

    one = storage_from_string("memory://")
    two = storage_from_string("memory://")
    print(f"\nstorage_from_string('memory://') returns the same object twice: {one is two}")
except Exception as exc:
    print(f"\ncould not test storage_from_string: {exc}")
