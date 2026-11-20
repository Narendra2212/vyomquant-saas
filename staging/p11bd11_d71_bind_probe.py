"""D11 read-only probe: does every real `load_decrypted_keys` call site bind?

Source-aware, per the D11 brief: the call sites are extracted with `ast` from the actual
files, not hand-copied, and each extracted call is bound against the REAL
`inspect.signature(APIKeyVault.load_decrypted_keys)`.

`signature()` reads the function object, so the vault is never instantiated and no
Supabase client, environment variable or credential is touched.

This probe EDITS NOTHING. It exists to prove the D7-1 regression-test design works before
anyone is authorized to add it.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend_app.backend.api_key_vault import APIKeyVault  # noqa: E402

SIG = inspect.signature(APIKeyVault.load_decrypted_keys)

FILES = [
    "backend_app/routers/orders.py",
    "backend_app/routers/market.py",
    "backend_app/routers/exchange.py",
    "backend_app/api_ws/ws_routes.py",
    "backend_app/backend/master_executor.py",
    "backend_app/backend/portfolio_cache_updater.py",
]

ROOT = pathlib.Path(__file__).resolve().parents[1]


def call_sites(path: pathlib.Path):
    """Every `<anything>.load_decrypted_keys(...)` call, with its arity and keywords."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "load_decrypted_keys":
            kwargs = [k.arg for k in node.keywords if k.arg is not None]
            starstar = any(k.arg is None for k in node.keywords)
            yield node.lineno, len(node.args), kwargs, starstar


print(f"REAL SIGNATURE: load_decrypted_keys{SIG}")
print(f"  parameters: {list(SIG.parameters)}")
print()

total = 0
binds = 0
fails = 0
for rel in FILES:
    path = ROOT / rel
    if not path.is_file():
        print(f"  (absent) {rel}")
        continue
    for lineno, n_pos, kwargs, starstar in call_sites(path):
        total += 1
        # `self` is the bound instance; model it as a placeholder so the probe mirrors an
        # unbound call on the class, exactly as the proposed regression test does.
        args = ["<self>"] + ["<arg>"] * n_pos
        kw = {name: "<kw>" for name in kwargs}
        try:
            SIG.bind(*args, **kw)
            verdict = "BINDS"
            binds += 1
        except TypeError as exc:
            verdict = f"TypeError: {exc}"
            fails += 1
        extra = " (**kwargs present - undecidable)" if starstar else ""
        shown = ", ".join(kwargs) if kwargs else "-"
        print(f"  {verdict:<55} {rel}:{lineno}  positional={n_pos} keywords={shown}{extra}")

print()
print(f"call sites found : {total}")
print(f"bind OK          : {binds}")
print(f"bind TypeError   : {fails}")
print()
print("Proposed regression-test assertions, executed here:")
try:
    SIG.bind("<self>", "user-id", "binance")
    print("  OK      valid two-argument form binds")
except TypeError as exc:
    print(f"  BROKEN  valid form did not bind: {exc}")
try:
    SIG.bind("<self>", "user-id", "binance", access_token="t")
    print("  BROKEN  access_token= bound, so the defect is NOT detectable this way")
except TypeError as exc:
    print(f"  OK      access_token= raises TypeError: {exc}")
