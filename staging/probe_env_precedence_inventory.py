"""Phase 6 Task 8 -- environment-precedence inventory.

DISCOVERY, NOT A GUESS LIST. Every environment variable `backend_app` reads is extracted
from the AST first; the switch families named in the brief are then selected from what was
actually found. A hand-written list of variable names would only prove that the names I
guessed exist.

For every read site it records:

  * file:line, the variable, and the DEFAULT the code supplies;
  * whether the ENCLOSING FUNCTION also consults a deployment-environment variable
    (`ENV` / `ENVIRONMENT`) -- the RUNTIME-01 question. A secondary flag that decides
    behaviour without ever looking at the declared environment cannot honour a precedence
    policy, because it never learns what the environment is;
  * which direction the default points. `os.getenv("X", "true") == "true"` is open by
    default; `os.getenv("X", "false") == "true"` is closed by default.

Recognised sink shapes: ``os.getenv(name[, default])``, ``os.environ.get(name[, default])``,
``os.environ[name]``.

Run:  .venv\\Scripts\\python.exe staging\\probe_env_precedence_inventory.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend_app"

ENVIRONMENT_VARS = {"ENV", "ENVIRONMENT", "APP_ENV", "DEPLOY_ENV", "NODE_ENV"}

#: The switch families the brief names, as REGEXES matched against discovered names.
FAMILIES: dict[str, str] = {
    "deployment environment": r"^(ENV|ENVIRONMENT|APP_ENV|DEPLOY_ENV|NODE_ENV)$",
    "dev mode": r"DEV_MODE|DEVELOPMENT_MODE|IS_DEV",
    "trading mode": r"VYOMQUANT_MODE|AERORA_MODE|TRADING_MODE|SYSTEM_MODE",
    "live-trading enable": r"ENABLE_LIVE|LIVE_TRADING|LIVE_ENABLED|ALLOW_LIVE",
    "paper/live flag": r"PAPER",
    "test / sandbox": r"^(TESTING|CI|PYTEST|GITHUB_ACTIONS)|TESTNET|SANDBOX|TEST_MODE|USE_TESTNET",
    "debug": r"^DEBUG|DEBUG_MODE|VERBOSE|TRACEBACK",
    "local mode": r"LOCAL_MODE|LOCAL_DEV|OFFLINE|USE_LOCAL|IS_LOCAL",
    "rate-limit bypass": r"RATE_LIMIT|RATELIMIT|THROTTLE",
    "auth bypass": r"SKIP_AUTH|DISABLE_AUTH|AUTH_BYPASS|BYPASS_AUTH|ALLOW_ANON|DEV_TOKEN|NO_AUTH|REQUIRE_AAL2|MFA",
    "webhook bypass": r"WEBHOOK|SIGNATURE|HMAC_",
    "Redis dependency": r"REDIS",
    "database fallback": r"DATABASE_URL|USE_SQLITE|SQLITE|DB_FALLBACK|SUPABASE_URL",
    "worker local mode": r"WORKER|CELERY|QUEUE_",
    "execution router / path flags": r"PRODUCTION_ROUTER|DAG_TRADING|EVENT_LOOP_TRADING|API_ORDERS|ML_INFERENCE|MOCK_PREDICTION|KILL_SWITCH|FREEZE",
}

OPEN_DEFAULTS = {"true", "1", "yes", "on", "enabled", "True", "TRUE"}


class Site:
    __slots__ = ("path", "lineno", "var", "default", "func", "consults_env", "shape")

    def __init__(self, path, lineno, var, default, func, consults_env, shape):
        self.path = path
        self.lineno = lineno
        self.var = var
        self.default = default
        self.func = func
        self.consults_env = consults_env
        self.shape = shape


def const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def env_reads(node: ast.AST) -> tuple[str, str | None, str] | None:
    """-> (var, default_repr, shape) for an environment read, else None."""
    if isinstance(node, ast.Call):
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            if func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
                name = "os.getenv"
            elif func.attr == "get" and isinstance(func.value, ast.Attribute) and func.value.attr == "environ":
                name = "os.environ.get"
        if name and node.args:
            var = const_str(node.args[0])
            if var is None:
                return None
            default = None
            if len(node.args) > 1:
                default = ast.unparse(node.args[1])
            for kw in node.keywords:
                if kw.arg == "default":
                    default = ast.unparse(kw.value)
            return var, default, name
    if isinstance(node, ast.Subscript):
        value = node.value
        if isinstance(value, ast.Attribute) and value.attr == "environ":
            var = const_str(node.slice)
            if var is not None:
                return var, "<KeyError>", "os.environ[...]"
    return None


def collect() -> tuple[list[Site], dict[str, int]]:
    sites: list[Site] = []
    counts: dict[str, int] = defaultdict(int)

    for path in sorted(BACKEND.rglob("*.py")):
        if any(p in {"__pycache__", ".venv"} for p in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        # Map every node to its enclosing function, and record which functions mention
        # a deployment-environment variable anywhere in their body.
        owner: dict[int, ast.AST] = {}
        env_aware: set[int] = set()

        def descend(scope: ast.AST, node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    descend(child, child)
                else:
                    owner[id(child)] = scope
                    descend(scope, child)

        for top in ast.iter_child_nodes(tree):
            if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
                descend(top, top)
            else:
                owner[id(top)] = tree
                descend(tree, top)

        for scope in [tree, *[n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]]:
            body_nodes = ast.walk(scope) if scope is not tree else []
            for n in body_nodes:
                read = env_reads(n)
                if read and read[0] in ENVIRONMENT_VARS:
                    env_aware.add(id(scope))
                    break

        for n in ast.walk(tree):
            read = env_reads(n)
            if not read:
                continue
            var, default, shape = read
            counts[var] += 1
            scope = owner.get(id(n), tree)
            func_name = getattr(scope, "name", "<module>")
            sites.append(
                Site(
                    str(path.relative_to(ROOT)),
                    getattr(n, "lineno", -1),
                    var,
                    default,
                    func_name,
                    id(scope) in env_aware,
                    shape,
                )
            )
    return sites, dict(counts)


def family_of(var: str) -> str | None:
    for label, pattern in FAMILIES.items():
        if re.search(pattern, var):
            return label
    return None


def main() -> int:
    sites, counts = collect()
    print(f"DISTINCT_ENV_VARS_READ={len(counts)}")
    print(f"TOTAL_READ_SITES={len(sites)}")

    in_family = [s for s in sites if family_of(s.var)]
    print(f"SITES_IN_A_BRIEFED_SWITCH_FAMILY={len(in_family)}")

    print("\n=== FAMILY COVERAGE: which briefed families exist in this codebase ===")
    by_family: dict[str, set[str]] = defaultdict(set)
    for s in in_family:
        by_family[family_of(s.var)].add(s.var)
    for label in FAMILIES:
        found = sorted(by_family.get(label, ()))
        status = "FOUND" if found else "none"
        print(f"  {label:<32} {status:<6} {', '.join(found) if found else ''}")

    print("\n=== OPEN-BY-DEFAULT SWITCHES (default makes the permissive branch win) ===")
    opens = [
        s
        for s in in_family
        if s.default and s.default.strip("'\"") in OPEN_DEFAULTS
    ]
    if not opens:
        print("  none")
    for s in sorted(opens, key=lambda s: (s.var, s.path, s.lineno)):
        print(
            f"  {s.var:<28} default={s.default:<8} env_aware={str(s.consults_env):<5} "
            f"{s.path}:{s.lineno} ({s.func})"
        )

    print("\n=== SECONDARY SWITCHES THAT NEVER CONSULT THE DECLARED ENVIRONMENT ===")
    print("    (the RUNTIME-01 shape: a flag that decides without knowing where it is)")
    secondary = {"dev mode", "local mode", "test / sandbox", "debug"}
    blind = [
        s
        for s in in_family
        if family_of(s.var) in secondary and not s.consults_env
    ]
    for s in sorted(blind, key=lambda s: (s.var, s.path, s.lineno)):
        print(f"  {s.var:<24} {s.path}:{s.lineno} ({s.func})  default={s.default}")
    print(f"  COUNT={len(blind)}")

    print("\n=== EVERY DEV_MODE READ, WITH ITS ENV-AWARENESS ===")
    for s in sorted(
        (s for s in sites if "DEV_MODE" in s.var), key=lambda s: (s.path, s.lineno)
    ):
        print(
            f"  env_aware={str(s.consults_env):<5} default={str(s.default):<10} "
            f"{s.path}:{s.lineno} ({s.func})"
        )

    print("\n=== EVERY TRADING-MODE READ ===")
    for s in sorted(
        (s for s in sites if family_of(s.var) == "trading mode"),
        key=lambda s: (s.path, s.lineno),
    ):
        print(
            f"  {s.var:<18} env_aware={str(s.consults_env):<5} "
            f"default={str(s.default):<10} {s.path}:{s.lineno} ({s.func})"
        )

    print("\n=== EVERY DECLARED-ENVIRONMENT READ AND ITS DEFAULT ===")
    for s in sorted(
        (s for s in sites if s.var in ENVIRONMENT_VARS), key=lambda s: (s.path, s.lineno)
    ):
        print(f"  {s.var:<12} default={str(s.default):<18} {s.path}:{s.lineno} ({s.func})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
