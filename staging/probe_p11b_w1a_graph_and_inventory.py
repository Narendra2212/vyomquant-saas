"""PHASE 11B-W1A -- alembic graph + gated-test inventory. READ-ONLY, no DB connection.

Resolves what the A3 probe could not: the real Alembic revision graph (A3-05), by parsing
the actual assignment values including `None`, tuples and Sequence forms.
"""

from __future__ import annotations

import ast
import pathlib
import re

VERSIONS = pathlib.Path("backend_app/alembic/versions")


def banner(t: str) -> None:
    print("=" * 78); print(t); print("=" * 78)


def _literal(src: str, name: str):
    """The assigned value of a module-level `name`, via AST. Handles None/str/tuple."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return f"<unparsed: {ast.unparse(node.value)[:60]}>"
    return "<absent>"


def main():
    banner("1. ALEMBIC REVISION GRAPH (raw values, AST-parsed)")
    rows = []
    for p in sorted(VERSIONS.glob("*.py")):
        if p.name == "__init__.py":
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        rows.append((p.name, _literal(src, "revision"), _literal(src, "down_revision")))

    for name, rev, down in rows:
        print(f"  {name:52s} revision={rev!r:28} down={down!r}")

    revs = {r for _, r, _ in rows if isinstance(r, str)}
    referenced = set()
    for _, _, down in rows:
        if isinstance(down, str):
            referenced.add(down)
        elif isinstance(down, (list, tuple)):
            referenced.update(x for x in down if isinstance(x, str))

    heads = sorted(revs - referenced)
    roots = [r for _, r, d in rows if d is None]
    print(f"\n  revisions   : {len(revs)}")
    print(f"  roots (down_revision is None): {roots}")
    print(f"  HEADS (no other revision names them): {len(heads)}")
    for h in heads:
        nm = next(n for n, r, _ in rows if r == h)
        print(f"    {h}   ({nm})")
    dangling = sorted(referenced - revs)
    if dangling:
        print(f"  DANGLING down_revision targets (not defined here): {dangling}")

    banner("2. WHAT `alembic upgrade head` WOULD MEAN")
    if len(heads) == 1:
        print(f"  Single head: {heads[0]} -- `upgrade head` is unambiguous.")
    elif len(heads) == 0:
        print("  ZERO heads -> a cycle exists. `upgrade head` cannot resolve.")
    else:
        print(f"  {len(heads)} heads -> `upgrade head` is AMBIGUOUS and alembic will error")
        print("  unless a merge revision exists. Check merge_heads_20260820.py.")
    print(f"  provision_database.py ALEMBIC_TARGET = "
          f"{_literal(pathlib.Path('scripts/provision_database.py').read_text(encoding='utf-8', errors='replace'), 'ALEMBIC_TARGET')!r}")

    banner("3. GATED TEST FILE INVENTORY (source inspection, nothing executed)")
    pg, redis, sandbox, other = [], [], [], []
    for p in sorted(pathlib.Path("tests").rglob("test_*.py")):
        if "__pycache__" in p.parts:
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        if "exchange_sandbox" in p.as_posix():
            sandbox.append(p.as_posix()); continue
        if "AERORA_TEST_REDIS_URL" in t or "real_redis_gate" in t:
            redis.append(p.as_posix())
        if "AERORA_TEST_DATABASE_URL" in t or p.name.endswith("_postgres.py"):
            pg.append(p.as_posix())
    print(f"  PostgreSQL-gated files : {len(pg)}")
    print(f"  real-Redis-gated files : {len(redis)}")
    for f in redis:
        print(f"      {f}")
    print(f"  exchange-sandbox files : {len(sandbox)}")
    for f in sandbox:
        print(f"      {f}")

    banner("4. WHICH GATED FILES ARE UNTRACKED (not reviewable in CI)")
    import subprocess
    def tracked(path):
        return bool(subprocess.run(["git", "ls-files", path], capture_output=True,
                                   text=True, encoding="utf-8").stdout.strip())
    for label, group in (("PG", pg), ("REDIS", redis), ("SANDBOX", sandbox)):
        un = [f for f in group if not tracked(f)]
        print(f"  {label:8s} {len(un)} of {len(group)} UNTRACKED")
        for f in un[:14]:
            print(f"      {f}")
        if len(un) > 14:
            print(f"      ... and {len(un) - 14} more")

    banner("5. W1 TOOLING TRACKED STATE")
    for f in ("scripts/provision_database.py", "scripts/verify_schema_drift.py",
              "scripts/verify_provisioning.py", "staging/run_all_probes.py",
              "staging/bootstrap_pg.py", "staging/dsn.py", "staging/probe_pg_ready.py",
              "tests/real_redis_gate.py", "tests/conftest.py",
              "backend_app/alembic/env.py", "alembic.ini"):
        ex = pathlib.Path(f).exists()
        print(f"  {f:44s} exists={ex!s:5s} tracked={tracked(f) if ex else '-'}")


if __name__ == "__main__":
    main()
