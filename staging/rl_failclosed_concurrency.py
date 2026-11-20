"""TASK 8 / 1D -- fail-closed under concurrency and across multiple limiter instances.

The property under test is NOT "an error is raised". It is: when the environment requires
a shared store and no shared store exists, **no process anywhere silently obtains a local
fake**. A single-process check cannot show that; the failure mode being guarded against is
precisely that each replica independently decides it is allowed to keep its own counters.

So: N processes started concurrently, in the same configuration, and every one of them
must refuse. Then the same N with a reachable store, where every one must select the
SHARED uri rather than `memory://`.
"""
from __future__ import annotations

import concurrent.futures
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
N = 6


class FakeRedis(threading.Thread):
    daemon = True

    def __init__(self) -> None:
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
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
                    conn.sendall(b"-ERR unknown command 'HELLO'\r\n")
                else:
                    conn.sendall(b"+OK\r\n")

    def shutdown(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


# Two limiter instances inside one process as well, so "multiple limiter instances" is
# covered and not just multiple processes.
CHILD = (
    "import backend_app.core.rate_limit as rl;"
    "from slowapi import Limiter;"
    "from slowapi.util import get_remote_address;"
    "a = Limiter(key_func=get_remote_address, storage_uri=rl.config.storage_uri);"
    "b = Limiter(key_func=get_remote_address, storage_uri=rl.config.storage_uri);"
    "print('STORAGE=' + rl.config.storage_uri);"
    "print('SHARED=' + str(rl.config.is_shared));"
    "print('INSTANCES=2')"
)


def child_env(overrides: dict) -> dict:
    env = dict(os.environ)
    for key in ("ENV", "ENVIRONMENT", "DEV_MODE", "VYOMQUANT_MODE", "AERORA_MODE",
                "REDIS_URL"):
        env.pop(key, None)
    env.update(overrides)
    env["PYTHONPATH"] = str(REPO)
    return env


def one(overrides: dict) -> dict:
    proc = subprocess.run([PY, "-c", CHILD], cwd=str(REPO), env=child_env(overrides),
                          capture_output=True, text=True, timeout=300)
    out = (proc.stdout or "") + (proc.stderr or "")
    storage = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith("STORAGE="):
            storage = line.split("=", 1)[1]
    return {
        "rc": proc.returncode,
        "refused": "no shared store" in out,
        "storage": storage,
        "got_memory": storage == "memory://",
    }


def burst(label: str, overrides: dict) -> list[dict]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=N) as pool:
        futures = [pool.submit(one, overrides) for _ in range(N)]
        results = [f.result() for f in futures]
    refused = sum(1 for r in results if r["refused"])
    memory = sum(1 for r in results if r["got_memory"])
    shared = sum(1 for r in results if r["storage"].startswith("redis://"))
    print()
    print(f"--- {label}  ({N} concurrent processes, 2 limiter instances each) ---")
    print(f"refused (fail closed)        : {refused}/{N}")
    print(f"silently got memory:// fake  : {memory}/{N}")
    print(f"selected the shared store    : {shared}/{N}")
    return results


fake = FakeRedis()
fake.start()
UP = f"redis://127.0.0.1:{fake.port}/0"
DOWN = "redis://127.0.0.1:1/0"

r_down = burst("production, store UNAVAILABLE", {"ENV": "production", "REDIS_URL": DOWN})
assert_down = all(r["refused"] for r in r_down) and not any(r["got_memory"] for r in r_down)
print(f"VERDICT: no process downgraded to a local fake -> {assert_down}")

r_none = burst("production, store NOT CONFIGURED", {"ENV": "production"})
assert_none = all(r["refused"] for r in r_none) and not any(r["got_memory"] for r in r_none)
print(f"VERDICT: no process downgraded to a local fake -> {assert_none}")

r_up = burst("production, store AVAILABLE", {"ENV": "production", "REDIS_URL": UP})
assert_up = all(r["storage"] == UP for r in r_up)
print(f"VERDICT: every process selected the SAME shared store -> {assert_up}")

r_paper = burst("paper mode, store UNAVAILABLE", {"VYOMQUANT_MODE": "paper",
                                                  "REDIS_URL": DOWN})
assert_paper = all(r["refused"] for r in r_paper) and not any(r["got_memory"] for r in r_paper)
print(f"VERDICT: a trading mode does not buy a local fake -> {assert_paper}")

fake.shutdown()

print()
print("OVERALL 1D:", "PASS" if all(
    (assert_down, assert_none, assert_up, assert_paper)
) else "FAIL")
print()
print(f"NONCE_END={NONCE}")
