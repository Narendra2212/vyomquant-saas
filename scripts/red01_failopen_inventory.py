"""RED-01: find where a neutral Redis return is LAUNDERED into a usable value.

The shared Redis manager's plain proxy methods swallow a connection error and return a
neutral value -- 0, None, False, [] -- by design. That only matters where the neutral value
reaches a decision, and in this codebase it gets there through one idiom:

    int(await redis_manager.get(key) or 0)      # "Redis is down" becomes "zero used"
    await redis_manager.llen(key) or 0

The `or <literal>` and the `int(...)` are the problem, not the method and not the syntactic
position. They coerce the neutral value past the point where the caller could tell the
difference between "nothing recorded" and "could not read".

TWO EARLIER CLASSIFICATIONS GOT THIS WRONG, both corrected by reading the sites they
flagged rather than trusting the category:

  * `await redis.llen(k) or 0` was called a GATE because `or` is a BoolOp. It is not a
    gate, it is a DEFAULT -- and the default is the actual defect.
  * `int(await redis.get(k) or 0)` was called a READ because `get` is a read method. A
    `get` used as a counter IS a counter and fails open exactly like `scard`.

Importable: `scan()` returns the sites, so a test can call it without spawning a
subprocess. Run directly for the human-readable report.
"""
from __future__ import annotations

import ast
import json
import os
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "backend_app")

#: Proxy methods whose documented behaviour is to return a neutral value on failure.
PROXY = {"get", "incr", "incrby", "decr", "decrby", "incrbyfloat", "scard", "llen",
         "sismember", "exists", "hget", "hgetall", "smembers", "lrange", "zcard",
         "getdel", "zrange", "zrangebyscore"}

#: Receiver names that plausibly hold the shared manager. Deliberately broad: a false
#: positive costs one KNOWN entry in the guard test, a false negative hides a fail-open.
MANAGER_HINTS = ("redis_manager", "redis", "_redis", "cache", "shared_redis",
                 "redis_client", "_cache")


def _proxy_method(node) -> str | None:
    """The proxy method this expression calls on something manager-shaped, or None."""
    while isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if not isinstance(f, ast.Attribute) or f.attr not in PROXY:
        return None
    recv = f.value
    txt = (recv.id if isinstance(recv, ast.Name)
           else recv.attr if isinstance(recv, ast.Attribute)
           else type(recv).__name__)
    return f.attr if any(h in txt.lower() for h in MANAGER_HINTS) else None


def scan() -> list[dict]:
    """Every laundering site under backend_app/, as {file, line, method, idiom, src}."""
    hits: list[dict] = []
    for root, dirs, files in os.walk(PKG):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            try:
                src = open(path, encoding="utf-8").read()
                tree = ast.parse(src)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            lines = src.splitlines()
            rel = os.path.relpath(path, REPO).replace("\\", "/")

            for node in ast.walk(tree):
                # `<proxy call> or <literal>`
                if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                    for i, value in enumerate(node.values[:-1]):
                        method = _proxy_method(value)
                        if not method:
                            continue
                        tail = node.values[i + 1]
                        if isinstance(tail, ast.Constant):
                            hits.append({
                                "file": rel, "line": node.lineno, "method": method,
                                "idiom": f"or {tail.value!r}",
                                "src": " ".join(
                                    lines[node.lineno - 1].split())[:120],
                            })
                # `int(<proxy call> ...)` / `float(...)` / `len(...)`
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("int", "float", "len") and node.args):
                    inner = node.args[0]
                    if isinstance(inner, ast.BoolOp):
                        inner = inner.values[0]
                    method = _proxy_method(inner)
                    if method:
                        hits.append({
                            "file": rel, "line": node.lineno, "method": method,
                            "idiom": f"{node.func.id}()",
                            "src": " ".join(lines[node.lineno - 1].split())[:120],
                        })

    # One line can match both idioms; keep the first per (file, line, method).
    seen, uniq = set(), []
    for h in hits:
        key = (h["file"], h["line"], h["method"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    return uniq


def main() -> int:
    sites = scan()
    print(f"=== {len(sites)} laundering site(s) ===\n")
    by_file: dict[str, list] = defaultdict(list)
    for h in sites:
        by_file[h["file"]].append(h)
    for fl in sorted(by_file, key=lambda f: (-len(by_file[f]), f)):
        print(f"  {fl}  ({len(by_file[fl])})")
        for h in sorted(by_file[fl], key=lambda x: x["line"]):
            print(f"      :{h['line']:<6} {h['method']:<12} {h['idiom']:<12} "
                  f"{h['src']}")
        print()

    out_dir = os.path.join(REPO, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "red01_laundering.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(sites, fh, indent=1)
    print(f"wrote {os.path.relpath(out_path, REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
