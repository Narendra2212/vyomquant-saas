"""PHASE 10 / WORKSTREAM K -- read-only Redis availability and AUTHORIZATION probe.

Answers one question: is there an ALREADY-CONFIGURED, ALREADY-AUTHORIZED, isolated test Redis
this workstream may use for the QUOTA-01 gate?

Strictly read-only. It does not start, install, configure or write to anything. It opens a TCP
connection only to decide reachability, and closes it immediately without sending a command.

SECRET HANDLING. No credential, token or full connection string is printed. A URL is reported
as scheme + host + port + database index only; userinfo is replaced with "<redacted>" and the
raw value is never echoed. Presence of a NAME is reported; its VALUE is not.
"""

from __future__ import annotations

import os
import pathlib
import re
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def safe_url(value: str) -> str:
    """scheme://<redacted>@host:port/db -- never the credential, never a query string."""
    if not value:
        return "<empty>"
    v = value.split("?")[0]
    v = re.sub(r"://[^@/]*@", "://<redacted>@", v)
    # If there is no userinfo, there is nothing to redact; keep host:port/db.
    return v


# ──────────────────────────────────────────────────────────────────────────────────────
# 1. Environment configuration
# ──────────────────────────────────────────────────────────────────────────────────────
def probe_env():
    banner("1. ENVIRONMENT CONFIGURATION (names and redacted shapes only)")
    redis_names = sorted(k for k in os.environ if "REDIS" in k.upper())
    print(f"  process env var NAMES containing 'REDIS': {redis_names or 'NONE'}")

    configured = {}
    for key in (
        "AERORA_TEST_REDIS_URL",
        "REDIS_URL",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "CELERY_BROKER_URL",
    ):
        value = os.environ.get(key)
        if value is None:
            print(f"    {key:24s} unset")
        elif value == "":
            print(f"    {key:24s} set but EMPTY (the repository default)")
        else:
            if "PASSWORD" in key or "TOKEN" in key:
                print(f"    {key:24s} PRESENT (value withheld)")
            else:
                print(f"    {key:24s} {safe_url(value)}")
            configured[key] = value
    return configured


# ──────────────────────────────────────────────────────────────────────────────────────
# 2. .env files -- names only, never values
# ──────────────────────────────────────────────────────────────────────────────────────
def probe_env_files():
    banner("2. .env FILES -- which declare a Redis key (NAMES ONLY, no values read out)")
    findings = []
    for name in (".env", ".env.example", ".env.staging.example"):
        p = pathlib.Path(name)
        if not p.is_file():
            print(f"  {name:28s} absent")
            continue
        declared = []
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            value = line.split("=", 1)[1].strip()
            if "REDIS" in key.upper():
                state = "EMPTY" if value in ("", '""', "''") else "non-empty (value withheld)"
                declared.append(f"{key}={state}")
        print(f"  {name:28s} {declared if declared else 'no REDIS keys'}")
        findings.extend(declared)
    return findings


# ──────────────────────────────────────────────────────────────────────────────────────
# 3. Approved project infrastructure -- is a test Redis DOCUMENTED anywhere?
# ──────────────────────────────────────────────────────────────────────────────────────
def probe_infra_docs():
    banner("3. APPROVED PROJECT INFRASTRUCTURE -- is a test Redis provisioned/documented?")
    candidates = [
        "DATABASE_PROVISIONING_RUNBOOK.md",
        "docker-compose.yml",
        "docker-compose.yaml",
        "docker-compose.test.yml",
        "Makefile",
        ".github/workflows/01-pr-check.yml",
        ".github/workflows/02-build.yml",
    ]
    for name in candidates:
        p = pathlib.Path(name)
        if not p.is_file():
            print(f"  {name:40s} absent")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        hits = [
            ln.strip()[:100]
            for ln in text.splitlines()
            if "redis" in ln.lower() and not ln.strip().startswith("#")
        ]
        print(f"  {name:40s} {len(hits)} redis mention(s)")
        for h in hits[:4]:
            print(f"      | {h}")

    # Terraform: is a managed Redis (ElastiCache) declared? That would be PRODUCTION, not a
    # test instance, and must NOT be used here.
    tf = sorted(pathlib.Path("terraform").glob("*.tf")) if pathlib.Path("terraform").is_dir() else []
    tf_hits = []
    for p in tf:
        text = p.read_text(encoding="utf-8", errors="replace")
        if "elasticache" in text.lower() or "redis" in text.lower():
            tf_hits.append(p.name)
    print(f"  terraform/*.tf declaring redis/elasticache: {tf_hits or 'NONE'}")
    if tf_hits:
        print("      NOTE: a managed/ElastiCache instance is PRODUCTION infrastructure.")
        print("      It is NOT an authorized test target for this gate.")


# ──────────────────────────────────────────────────────────────────────────────────────
# 4. Reachability -- one attempt per port, no retries
# ──────────────────────────────────────────────────────────────────────────────────────
def probe_reachability():
    banner("4. REACHABILITY -- single attempt per endpoint, no retries, no commands sent")
    reachable = []
    for port in (6379, 6380, 6399):
        s = socket.socket()
        s.settimeout(0.4)
        try:
            s.connect(("127.0.0.1", port))
            print(f"  127.0.0.1:{port}  OPEN")
            reachable.append(port)
        except Exception as exc:
            print(f"  127.0.0.1:{port}  closed ({type(exc).__name__})")
        finally:
            s.close()
    return reachable


# ──────────────────────────────────────────────────────────────────────────────────────
# 5. Can a Redis even be run here?
# ──────────────────────────────────────────────────────────────────────────────────────
def probe_tooling():
    banner("5. LOCAL TOOLING -- recorded to show what authorization would be needed")
    import shutil

    for tool in ("redis-server", "redis-cli", "docker", "podman", "memurai", "wsl"):
        path = shutil.which(tool)
        print(f"  {tool:14s} {'FOUND  ' + path if path else 'absent'}")

    # Is the python client installed? Its absence would block the gate independently.
    try:
        import redis  # noqa: F401

        print(f"  redis-py       installed (version {redis.__version__})")
    except Exception as exc:
        print(f"  redis-py       NOT importable ({type(exc).__name__})")


def main():
    banner("QUOTA-01 REAL-REDIS GATE -- AUTHORIZATION AND AVAILABILITY PROBE (READ-ONLY)")
    configured = probe_env()
    probe_env_files()
    probe_infra_docs()
    reachable = probe_reachability()
    probe_tooling()

    banner("VERDICT")
    usable = bool(
        (configured.get("AERORA_TEST_REDIS_URL") or "").startswith(("redis://", "rediss://"))
        or (configured.get("REDIS_URL") or "").startswith(("redis://", "rediss://"))
    )
    print(f"  A gated-test URL is configured and well-formed : {usable}")
    print(f"  A local endpoint answered a TCP connect         : {bool(reachable)}")
    if usable and reachable:
        print("  -> An endpoint exists. It must still be CONFIRMED to be a dedicated test")
        print("     instance (not shared, not production) before any test is run.")
    else:
        print("  -> GATE BLOCKED. No authorized, isolated test Redis is configured or")
        print("     reachable. Installing or starting one is NOT authorized by this")
        print("     workstream's brief, so the gate is recorded NOT VERIFIED.")


if __name__ == "__main__":
    main()
