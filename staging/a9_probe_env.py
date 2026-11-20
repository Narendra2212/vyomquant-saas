"""Phase 11B-A9. READ-ONLY. Can a real Redis be reached or started on this host?

Every phase from A3 onward has reported the real-Redis assertions as UNEXECUTED locally.
If a Redis can be stood up here, the repair gets its first real execution without needing a
CI trigger -- which is the one piece of evidence no prior phase has. This probes, in order:

  1. is anything already listening on 6379 / 6380 / 6399?
  2. is `redis-server` or `redis-cli` on PATH?
  3. is Docker installed AND its daemon responding?
  4. is WSL present, and does it have redis?
  5. is `fakeredis` installed? (NOT a substitute -- recorded only so the report can say
     explicitly that an in-process fake was available and deliberately NOT used)

Nothing is started here. This only reports what is available.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess


def listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def cmd(args: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "<not found>"
    except subprocess.TimeoutExpired:
        return 124, "<timeout>"
    except Exception as exc:  # noqa: BLE001
        return 125, f"<{type(exc).__name__}>"


def main() -> int:
    print("=== 1. ports ===")
    for port in (6379, 6380, 6399):
        print(f"  127.0.0.1:{port} listening = {listening(port)}")
    print()

    print("=== 2. redis binaries on PATH ===")
    for exe in ("redis-server", "redis-cli", "memurai", "memurai-cli"):
        print(f"  {exe:<14} -> {shutil.which(exe) or '<absent>'}")
    print()

    print("=== 3. docker ===")
    which = shutil.which("docker")
    print(f"  docker on PATH -> {which or '<absent>'}")
    if which:
        rc, out = cmd(["docker", "--version"])
        print(f"  docker --version  exit={rc}  {out[:120]}")
        rc, out = cmd(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=25)
        print(f"  docker info       exit={rc}  {out[:200]}")
        print(f"  daemon usable     = {rc == 0}")
    print()

    print("=== 4. wsl ===")
    which = shutil.which("wsl")
    print(f"  wsl on PATH -> {which or '<absent>'}")
    if which:
        rc, out = cmd(["wsl", "-l", "-q"], timeout=25)
        distros = [d.strip() for d in out.replace("\x00", "").splitlines() if d.strip()]
        print(f"  wsl -l -q  exit={rc}  distros={distros}")
        if rc == 0 and distros:
            rc2, out2 = cmd(["wsl", "-e", "bash", "-lc", "command -v redis-server || echo NONE"], timeout=30)
            print(f"  redis-server in wsl  exit={rc2}  {out2[:160]}")
    print()

    print("=== 5. in-process fakes (recorded, NOT usable as evidence) ===")
    for mod in ("fakeredis", "redislite", "pytest_redis"):
        print(f"  {mod:<14} installed = {importlib.util.find_spec(mod) is not None}")
    print()

    print("=== relevant env ===")
    for var in ("AERORA_TEST_REDIS_URL", "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "DEV_MODE", "ENV"):
        print(f"  {var:<22} = {os.environ.get(var, '<unset>')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
