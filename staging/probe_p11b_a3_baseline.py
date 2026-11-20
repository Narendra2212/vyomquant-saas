"""PHASE 11B-A3 -- baseline probe: test services, DB identity, migrations, release ops.

Strictly read-only. No connection is made with credentials, no DSN is printed, no migration
is applied, no service is started, no git write occurs.
"""

from __future__ import annotations

import os
import pathlib
import re
import socket
import subprocess

REPO = pathlib.Path(".")


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def sh(*args: str) -> str:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return (r.stdout or "").strip()


# ──────────────────────────────────────────────────────────────────────────────────
def probe_services():
    banner("1. LOCAL TEST SERVICES, AND WHAT SILENTLY SKIPS WITHOUT THEM")
    for name in ("AERORA_TEST_DATABASE_URL", "DATABASE_URL", "AERORA_TEST_REDIS_URL",
                 "REDIS_URL", "AERORA_EXCHANGE_SANDBOX_CERT", "AERORA_SANDBOX_VENUE",
                 "MASTER_ENCRYPTION_KEYS"):
        v = os.environ.get(name)
        print(f"  {name:32s} {'set (value withheld)' if v else 'unset'}")

    print("\n  reachability (one attempt each, no retries):")
    dsn = REPO / "staging/pg_dsn.txt"
    targets = [("127.0.0.1", 6379, "redis default"), ("127.0.0.1", 5432, "postgres default")]
    if dsn.is_file():
        m = re.search(r"@?([0-9A-Za-z.\-]+):(\d+)/", dsn.read_text(encoding="utf-8").strip())
        if m:
            targets.append((m.group(1), int(m.group(2)), "staging/pg_dsn.txt bootstrap"))
    for host, port, label in targets:
        s = socket.socket(); s.settimeout(0.4)
        try:
            s.connect((host, port)); print(f"    {host}:{port:<6} OPEN    ({label})")
        except Exception as e:
            print(f"    {host}:{port:<6} closed  ({label}, {type(e).__name__})")
        finally:
            s.close()

    print("\n  gated test inventory (counted from source, not run):")
    pg_files, redis_files, sandbox_files = [], [], []
    for p in sorted(pathlib.Path("tests").rglob("test_*.py")):
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "AERORA_TEST_DATABASE_URL" in t or "_postgres.py" in p.name:
            pg_files.append(p.as_posix())
        if "AERORA_TEST_REDIS_URL" in t or "real_redis_gate" in t:
            redis_files.append(p.as_posix())
        if "exchange_sandbox" in p.as_posix():
            sandbox_files.append(p.as_posix())
    print(f"    PostgreSQL-gated test files : {len(pg_files)}")
    print(f"    real-Redis-gated test files  : {len(redis_files)}")
    for f in redis_files:
        print(f"        {f}")
    print(f"    exchange-sandbox test files  : {len(sandbox_files)}")


# ──────────────────────────────────────────────────────────────────────────────────
def probe_migrations():
    banner("2. MIGRATIONS AND SCHEMA CLASSIFICATION")
    alembic = sorted(pathlib.Path("backend_app/alembic/versions").glob("*.py"))
    print(f"  alembic revision files: {len(alembic)}")
    # Resolve the head: a revision no other revision names as down_revision.
    revs, downs = {}, set()
    for p in alembic:
        t = p.read_text(encoding="utf-8", errors="replace")
        rid = re.search(r"^revision\s*[:=]\s*['\"]([^'\"]+)", t, re.M)
        drv = re.findall(r"^down_revision\s*[:=].*?['\"]([^'\"]+)", t, re.M)
        if rid:
            revs[rid.group(1)] = p.name
        for d in drv:
            downs.add(d)
    heads = [r for r in revs if r not in downs]
    print(f"  computed head(s): {len(heads)}")
    for h in heads:
        print(f"    {h}  ({revs[h]})")
    if len(heads) > 1:
        print("    WARNING: multiple heads -> an `alembic upgrade head` would be ambiguous")

    sql_dirs = [("backend_app/migrations", "*.sql"), ("migrations", "*.sql")]
    for d, pat in sql_dirs:
        files = sorted(pathlib.Path(d).glob(pat)) if pathlib.Path(d).is_dir() else []
        untracked = [f.as_posix() for f in files
                     if not sh("git", "ls-files", f.as_posix())]
        print(f"\n  {d}: {len(files)} .sql file(s), {len(untracked)} UNTRACKED")
        for u in untracked:
            print(f"    UNTRACKED: {u}")


# ──────────────────────────────────────────────────────────────────────────────────
def probe_data_dir_safeguards():
    banner("3. DATA-DIRECTORY SAFEGUARDS")
    gi = pathlib.Path(".gitignore").read_text(encoding="utf-8", errors="replace")
    for pat in ("pgdata", "redis/", "*.log", "pg_dsn.txt", ".env", "terraform.tfstate"):
        print(f"  .gitignore covers {pat:20s} : {pat in gi}")
    for d in ("staging/pgdata_r1", "pgdata_z1", "staging/redis"):
        print(f"  {d:22s} present: {pathlib.Path(d).exists()}")


# ──────────────────────────────────────────────────────────────────────────────────
def probe_ci():
    banner("4. CI WORKFLOWS -- triggers and job dependencies")
    import yaml
    for p in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  {p.name}: UNPARSEABLE ({type(e).__name__})")
            continue
        trig = doc.get("on", doc.get(True))
        keys = list(trig.keys()) if isinstance(trig, dict) else trig
        jobs = doc.get("jobs", {}) or {}
        print(f"\n  {p.name}  ({doc.get('name')})")
        print(f"    triggers: {keys}")
        print(f"    jobs    : {len(jobs)}")
        for jn, jb in jobs.items():
            needs = jb.get("needs")
            svcs = list((jb.get("services") or {}).keys())
            print(f"      - {jn:34s} needs={needs} services={svcs or '-'}")


# ──────────────────────────────────────────────────────────────────────────────────
def probe_release_ops():
    banner("5. RELEASE AND ROLLBACK PROCEDURES -- which artefacts exist")
    candidates = [
        "DATABASE_PROVISIONING_RUNBOOK.md",
        "deployment_go_no_go.md",
        ".github/workflows/03-deploy.yml",
        "docs/adr",
        "staging/PHASE10_WSJ_POSTGRES_READINESS_REPORT.md",
        "staging/PHASE10_WSH_POSTGRES_SAFETY.md",
    ]
    for c in candidates:
        p = pathlib.Path(c)
        if p.is_dir():
            print(f"  {c:52s} DIR, {len(list(p.glob('*')))} entries")
        elif p.is_file():
            print(f"  {c:52s} {p.stat().st_size} bytes")
        else:
            print(f"  {c:52s} ABSENT")

    dep = pathlib.Path(".github/workflows/03-deploy.yml")
    if dep.is_file():
        t = dep.read_text(encoding="utf-8", errors="replace")
        print("\n  03-deploy.yml:")
        print(f"    mentions rollback       : {'rollback' in t.lower()}")
        print(f"    mentions a test gate    : {'pytest' in t.lower() or 'needs:' in t}")
        print(f"    mentions migration/alembic: {'alembic' in t.lower()}")


def main():
    banner("PHASE 11B-A3 BASELINE PROBE (READ-ONLY)")
    probe_services()
    probe_migrations()
    probe_data_dir_safeguards()
    probe_ci()
    probe_release_ops()


if __name__ == "__main__":
    main()
