"""Can the real-Redis lane run on this machine right now? Re-measured, not inherited.

PHASE 11B / WORKSTREAM 1B — Phase 4 deliverable evidence.

W1A reported this environment's Redis state. Three of the files involved
(`tests/real_redis_gate.py`, `tests/test_webhook_duplicate_delivery_real_redis.py`,
`tests/test_quota01_*.py`) have been modified by other agent sessions since that reading,
so every property is re-measured here.

WHAT IT CHECKS
--------------
1. ENVIRONMENT: the opt-in variable and its neighbours, as the gate would see them.
2. REACHABILITY: a real TCP connect to each candidate port. Not a ping -- a socket.
3. BINARIES: redis-server / redis-cli / docker / podman / memurai / wsl on PATH, and the
   documented `staging/redis/redis-server.exe` the runbook names.
4. CLIENT: whether `redis` is importable and at what version.
5. THE GATE ITSELF, by AST: which variable name it reads, whether it falls back to
   `REDIS_URL`, what its loopback rule is, and what it does when the endpoint is
   unreachable -- skip or fail. A gate that SKIPS on an unreachable endpoint cannot
   produce evidence; a gate that FAILS at least cannot be mistaken for evidence.
6. THE GATED SUITES: which tests carry the marker, and whether each asserts that it
   reached a REAL cache rather than the in-process mock.
7. CI: whether the workflow exports the opt-in variable, and on what trigger.

STRICTLY READ-ONLY. It opens TCP sockets to loopback ports and closes them immediately,
reads files, and parses. It installs nothing, starts no server, and writes no file.
"""

from __future__ import annotations

import ast
import os
import pathlib
import shutil
import socket
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GATE = REPO / "tests" / "real_redis_gate.py"
WORKFLOW = REPO / ".github" / "workflows" / "01-pr-check.yml"

ENV_VARS = ("AERORA_TEST_REDIS_URL", "AERORA_TEST_REDIS_ALLOW_REMOTE", "REDIS_URL",
            "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "CELERY_BROKER_URL")
PORTS = (6379, 6380, 6381, 6399, 7379)
BINARIES = ("redis-server", "redis-cli", "docker", "podman", "memurai",
            "memurai-server", "wsl", "valkey-server")
CANDIDATE_BINARY_PATHS = [
    REPO / "staging" / "redis" / "redis-server.exe",
    REPO / "staging" / "redis" / "redis-cli.exe",
    pathlib.Path(r"C:\Program Files\Memurai\memurai.exe"),
    pathlib.Path(r"C:\Program Files\Redis\redis-server.exe"),
]


def _tcp(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return "OPEN"
    except socket.timeout:
        return "timeout"
    except ConnectionRefusedError:
        return "refused"
    except OSError as exc:
        return f"{type(exc).__name__}"
    finally:
        sock.close()


def _ast_consts(path: pathlib.Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            try:
                out[stmt.targets[0].id] = ast.literal_eval(stmt.value)
            except (ValueError, SyntaxError):
                out[stmt.targets[0].id] = f"<expr: {ast.unparse(stmt.value)[:70]}>"
    return out, tree


def main() -> int:
    print("=" * 78)
    print("REAL-REDIS LANE READINESS (re-measured)")
    print("=" * 78)

    print("\n-- 1. environment, as the gate would read it ------------------------------")
    for name in ENV_VARS:
        raw = os.environ.get(name)
        if raw is None:
            print(f"   {name:34} UNSET")
        elif raw == "":
            print(f"   {name:34} SET BUT EMPTY  <- an empty string is not an opt-in")
        else:
            shown = raw if len(raw) < 48 and "@" not in raw else f"<{len(raw)} chars>"
            print(f"   {name:34} {shown!r}")

    print("\n-- 2. loopback reachability (real TCP connect) ----------------------------")
    open_ports = []
    for port in PORTS:
        state = _tcp(port)
        if state == "OPEN":
            open_ports.append(port)
        print(f"   127.0.0.1:{port:<6} {state}")
    print(f"   open ports: {open_ports or 'NONE'}")

    print("\n-- 3. server binaries ----------------------------------------------------")
    for name in BINARIES:
        found = shutil.which(name)
        print(f"   {name:18} {found or 'not on PATH'}")
    print("   documented / common install locations:")
    for path in CANDIDATE_BINARY_PATHS:
        print(f"     {str(path):62} exists={path.exists()}")
    staging_redis = REPO / "staging" / "redis"
    print(f"   staging/redis/ exists = {staging_redis.exists()}")

    print("\n-- 4. WSL ----------------------------------------------------------------")
    wsl = shutil.which("wsl")
    if not wsl:
        print("   wsl not on PATH")
    else:
        try:
            res = subprocess.run(
                ["wsl", "-e", "sh", "-c",
                 "command -v redis-server || echo NO_REDIS_SERVER"],
                capture_output=True, text=True, timeout=120)
            out = (res.stdout or res.stderr).strip().replace("\x00", "")
            print(f"   wsl present; redis-server inside WSL: {out!r}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            print(f"   wsl present but probe failed: {type(exc).__name__}: {exc}")

    print("\n-- 5. python client ------------------------------------------------------")
    try:
        import redis as _redis
        print(f"   redis-py importable, version {_redis.__version__}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"   redis-py NOT importable: {type(exc).__name__}: {exc}")

    print("\n-- 6. the gate, by AST ---------------------------------------------------")
    if not GATE.exists():
        print("   tests/real_redis_gate.py ABSENT")
    else:
        stat = GATE.stat()
        print(f"   file: {GATE.relative_to(REPO)}  {stat.st_size} bytes  "
              f"mtime {__import__('datetime').datetime.fromtimestamp(stat.st_mtime)}")
        consts, tree = _ast_consts(GATE)
        for key in sorted(consts):
            if key.isupper():
                print(f"     {key:34} = {consts[key]!r}")
        src = GATE.read_text(encoding="utf-8")

        # The fallback question answered from the FUNCTION BODY, not a string scan.
        # A scan of the whole file reports a fallback that is not there: this module's
        # docstring discusses `REDIS_URL` at length precisely to explain why the
        # fallback was REMOVED, and a comment on the constant repeats it. An earlier
        # revision of this probe did scan the file and reported "YES", which was wrong.
        reads: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "redis_url":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        f = sub.func
                        name = f.attr if isinstance(f, ast.Attribute) else getattr(
                            f, "id", "")
                        if name in ("get", "getenv"):
                            for arg in sub.args:
                                if isinstance(arg, ast.Constant):
                                    reads.append(repr(arg.value))
                                elif isinstance(arg, ast.Name):
                                    reads.append(f"{arg.id}={consts.get(arg.id)!r}")
                    if isinstance(sub, ast.Subscript) and isinstance(
                            sub.value, ast.Attribute) and sub.value.attr == "environ":
                        reads.append(ast.unparse(sub.slice))
        print(f"     redis_url() reads               : {reads or 'nothing resolvable'}")
        fallback = any("REDIS_URL'" in r and "AERORA" not in r for r in reads)
        print(f"     falls back to REDIS_URL?        : "
              f"{'YES' if fallback else 'NO -- opt-in variable only'}")
        funcs = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        print(f"     module-level functions          : {funcs}")
        for token, label in (("pytest.skip", "can SKIP"),
                             ("pytest.fail", "can FAIL"),
                             ("ping", "issues PING"),
                             ("allow_module_level", "module-level skip")):
            print(f"     {label:32}: {'yes' if token in src else 'no'}")

    print("\n-- 7. the gated suites ---------------------------------------------------")
    marker_users = []
    for path in sorted((REPO / "tests").glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "real_redis_required" in text or "require_real_redis" in text:
            stat = path.stat()
            marker_users.append((path.name, stat.st_size,
                                 __import__('datetime').datetime
                                 .fromtimestamp(stat.st_mtime).strftime("%H:%M:%S")))
    for name, size, mtime in marker_users:
        print(f"   {name:58} {size:>7}  mtime {mtime}")
    print(f"   suites referencing the gate: {len(marker_users)}")

    print("\n   does each assert it reached a REAL cache?")
    for name, _, _ in marker_users:
        text = (REPO / "tests" / name).read_text(encoding="utf-8", errors="replace")
        checks = []
        if "MockRedisClient" in text:
            checks.append("asserts-not-MockRedisClient")
        if "_bypass_dev_mode" in text:
            checks.append("bypasses dev-mode facade")
        if "is not None" in text and "cache" in text:
            checks.append("asserts cache is not None")
        if "_RealCache" in text:
            checks.append("injects its OWN _RealCache (bypasses the facade)")
        print(f"     {name:56} {checks or ['no real-cache assertion found']}")

    print("\n-- 8. CI ----------------------------------------------------------------")
    if not WORKFLOW.exists():
        print("   01-pr-check.yml ABSENT")
    else:
        text = WORKFLOW.read_text(encoding="utf-8")
        print(f"   {WORKFLOW.relative_to(REPO)}")
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if any(tok in low for tok in ("aerora_test_redis", "redis_url",
                                          "image: redis", "redis:", "on:",
                                          "pull_request", "workflow_dispatch",
                                          "push:")):
                print(f"     {i:>4}: {line.rstrip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
