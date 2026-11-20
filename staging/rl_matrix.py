"""TASK 8 / 1B + 1D + 1E -- the rate-limit environment matrix, by execution.

Import-time configuration cannot be honestly re-tested in-process: once
`backend_app.core.rate_limit` is imported its decision is fixed, and clearing it from
`sys.modules` leaves the already-constructed `Limiter` behind. So every row here runs in
a FRESH SUBPROCESS with a controlled environment.

The "Redis available" rows are served by a minimal RESP responder rather than being
skipped. It answers `+PONG` to PING and `+OK` to redis-py's handshake chatter, which is
enough for `redis.from_url(url).ping()` to succeed -- so the reachable path is exercised
through the real client, not through an injected probe.

Prints a nonce at both ends so the caller can prove the output is from this invocation.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import uuid
from pathlib import Path

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "Scripts" / "python.exe")


# ── a Redis that is just reachable enough to be PINGed ──────────────────────
class FakeRedis(threading.Thread):
    daemon = True

    def __init__(self) -> None:
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._stop = False

    def run(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            while True:
                try:
                    data = conn.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                upper = data.upper()
                if b"PING" in upper:
                    conn.sendall(b"+PONG\r\n")
                elif b"HELLO" in upper:
                    # Refuse RESP3 so redis-py falls back to RESP2.
                    conn.sendall(b"-ERR unknown command 'HELLO'\r\n")
                else:
                    conn.sendall(b"+OK\r\n")

    def shutdown(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


PROBE = (
    "import backend_app.core.rate_limit as rl;"
    "print('STORAGE=' + rl.config.storage_uri);"
    "print('SHARED=' + str(rl.config.is_shared));"
    "print('ENVSEEN=' + repr(rl.config.env));"
    "print('LOCAL_ALLOWED=' + str(rl.config.local_allowed));"
    "print('LIMITER=' + type(rl.limiter).__name__)"
)


def run_row(label: str, env_overrides: dict[str, str | None]) -> dict:
    """Import rate_limit in a fresh interpreter under `env_overrides`."""
    env = dict(os.environ)
    # Start from a clean slate so the parent's values cannot leak in.
    for key in ("ENV", "ENVIRONMENT", "DEV_MODE", "VYOMQUANT_MODE", "AERORA_MODE",
                "REDIS_URL"):
        env.pop(key, None)
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    env["PYTHONPATH"] = str(REPO)

    proc = subprocess.run(
        [PY, "-c", PROBE],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    raised = "RuntimeError" in out and "no shared store" in out
    storage = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith("STORAGE="):
            storage = line.split("=", 1)[1]
    return {
        "label": label,
        "rc": proc.returncode,
        "raised_fail_closed": raised,
        "storage": storage,
        "tail": out.strip().splitlines()[-1][:150] if out.strip() else "",
    }


fake = FakeRedis()
fake.start()
UP = f"redis://127.0.0.1:{fake.port}/0"
DOWN = "redis://127.0.0.1:1/0"  # port 1: nothing listens

print(f"fake redis listening on 127.0.0.1:{fake.port}")
print()

ROWS = [
    # label                               ENV            DEV_MODE  REDIS_URL
    ("local(ENV=development) + redis UP",   {"ENV": "development", "REDIS_URL": UP}),
    ("local(ENV=development) + redis DOWN", {"ENV": "development", "REDIS_URL": DOWN}),
    ("local(ENV=development) + no REDIS",   {"ENV": "development"}),
    ("local(DEV_MODE=true) + no REDIS",     {"DEV_MODE": "true"}),
    ("test(ENV=testing) + redis UP",        {"ENV": "testing", "REDIS_URL": UP}),
    ("test(ENV=testing) + redis DOWN",      {"ENV": "testing", "REDIS_URL": DOWN}),
    ("test(ENV=testing) + no REDIS",        {"ENV": "testing"}),
    ("paper(VYOMQUANT_MODE=paper) + redis UP",   {"VYOMQUANT_MODE": "paper", "REDIS_URL": UP}),
    ("paper(VYOMQUANT_MODE=paper) + redis DOWN", {"VYOMQUANT_MODE": "paper", "REDIS_URL": DOWN}),
    ("paper(VYOMQUANT_MODE=paper) + no REDIS",   {"VYOMQUANT_MODE": "paper"}),
    ("production + redis UP",               {"ENV": "production", "REDIS_URL": UP}),
    ("production + redis DOWN",             {"ENV": "production", "REDIS_URL": DOWN}),
    ("production + no REDIS",               {"ENV": "production"}),
    ("ENV unset + no REDIS",                {}),
    ("ENV unset + redis UP",                {"REDIS_URL": UP}),
]

results = []
for label, overrides in ROWS:
    try:
        results.append(run_row(label, overrides))
    except subprocess.TimeoutExpired:
        results.append({"label": label, "rc": -1, "raised_fail_closed": False,
                        "storage": "", "tail": "TIMEOUT"})

print(f"{'row':44} {'rc':>3} {'fail_closed':>12} storage")
print("-" * 100)
for r in results:
    print(f"{r['label']:44} {r['rc']:>3} {str(r['raised_fail_closed']):>12} {r['storage']}")

print()
print("=== 1E: recovery, same environment, Redis restored ===")
down_first = run_row("production + redis DOWN (pre-recovery)",
                     {"ENV": "production", "REDIS_URL": DOWN})
print("with redis DOWN :", "REFUSED" if down_first["raised_fail_closed"] else "ALLOWED",
      down_first["storage"])
up_after = run_row("production + redis UP (post-recovery)",
                   {"ENV": "production", "REDIS_URL": UP})
print("with redis UP   :", "REFUSED" if up_after["raised_fail_closed"] else "ALLOWED",
      up_after["storage"])
recovered = (not up_after["raised_fail_closed"]) and up_after["storage"].startswith("redis://")
print("RECOVERS        :", recovered)

print()
print("=== does importing rate_limit alone pull in .env? ===")
leak = run_row("no overrides at all", {})
print("ENV unset + no REDIS_URL override -> fail_closed:", leak["raised_fail_closed"])
print("(if this says False, something in the import chain is loading .env)")

fake.shutdown()
print()
print(f"NONCE_END={NONCE}")
