"""PHASE 10 WORKSTREAM H — exercise the REAL validation code against the LIVE cluster.

The unit suite (tests/test_p10h_bootstrap_pg_selection_is_explicit.py) proves the logic
with injected fakes. This proves the *real* prober -- actual psycopg2, actual
`current_setting('data_directory')`, actual marker queries -- agrees with it when pointed
at the live provisioned cluster.

NON-DISRUPTIVE BY CONSTRUCTION:
  * `pgserver` is never imported and no server is started, stopped or restarted.
  * every write goes to a throwaway DSN file in the system temp directory;
    `staging/pg_dsn.txt` is read and never written. It is also checksummed before and
    after, and the probe fails if it changed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
REAL_DSN_FILE = ROOT / "staging" / "pg_dsn.txt"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_p10h_live", ROOT / "staging" / "bootstrap_pg.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bp = _load()


def _digest(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(absent)"


before = _digest(REAL_DSN_FILE)
live_dsn = REAL_DSN_FILE.read_text(encoding="utf-8").strip()
failures = 0

print("=" * 78)
print("PHASE 10 / WS-H - real validation code vs the LIVE cluster (no server touched)")
print("=" * 78)
print(f"live DSN under test: {bp.redact(live_dsn) if hasattr(bp, 'redact') else live_dsn}")

# ── 1. The real prober's verdict on the live cluster ────────────────────────
print("-" * 78)
print("1. REAL PROBE of the live cluster")
state = bp._default_probe(live_dsn)
print(f"   {state.describe()}")
print(f"   classification = {state.classification}  (rank {state.rank})")
if state.classification != "PROVISIONED":
    failures += 1
    print("   FAIL: the live cluster did not classify as PROVISIONED")
else:
    print("   ok: the live cluster classifies as PROVISIONED")

# ── 2. Explicit selection agrees with what the server reports ──────────────
print("-" * 78)
print("2. EXPLICIT SELECTION matches the server's own data_directory")
chosen = bp.resolve_data_dir(explicit="pgdata_r1", env={})
print(f"   resolve_data_dir('pgdata_r1') = {chosen}")
print(f"   server data_directory         = {state.data_directory}")
same = bp._same_directory(state.data_directory, chosen)
print(f"   _same_directory               = {same}")
if not same:
    failures += 1
    print("   FAIL: the resolved directory and the server's own report disagree")
else:
    print("   ok: selection and the running server agree")

# ── 3. Recording the live DSN into a throwaway file is ALLOWED ─────────────
print("-" * 78)
print("3. A verified provisioned target IS recorded (to a temp file)")
with tempfile.TemporaryDirectory() as td:
    tmp_dsn = pathlib.Path(td) / "pg_dsn.txt"
    try:
        got = bp.write_dsn_if_safe(
            live_dsn, intended_data_dir=chosen, dsn_file=tmp_dsn, env={},
            expect_provisioned=True,
        )
        ok = tmp_dsn.read_text(encoding="utf-8").strip() == live_dsn
        print(f"   recorded = {ok}   classification = {got.classification}")
        if not ok:
            failures += 1
    except bp.DsnWriteRefused as e:
        failures += 1
        print(f"   FAIL: refused a genuinely provisioned target: {e}")

# ── 4. A downgrade against the REAL current target is refused ──────────────
print("-" * 78)
print("4. DOWNGRADE REFUSED: an empty cluster may not replace the live provisioned one")
with tempfile.TemporaryDirectory() as td:
    tmp_dsn = pathlib.Path(td) / "pg_dsn.txt"
    tmp_dsn.write_text(live_dsn, encoding="utf-8")   # current = the real provisioned DSN
    empty_dir = pathlib.Path(td) / "pgdata"
    empty_dir.mkdir()
    (empty_dir / "PG_VERSION").write_text("16\n", encoding="utf-8")
    fake_empty_dsn = "postgresql://postgres:@127.0.0.1:1/postgres"

    def mixed_probe(dsn: str):
        # The CURRENT target is probed for real; the proposed one is a declared empty
        # cluster. Only the proposed side is synthetic, because standing up a second real
        # cluster would be exactly the disruption this workstream is preventing.
        if dsn == live_dsn:
            return bp._default_probe(dsn)
        return bp.TargetState(dsn=dsn, reachable=True, public_tables=0,
                              data_directory=str(empty_dir),
                              missing_markers=tuple(bp.SCHEMA_MARKERS))

    try:
        bp.write_dsn_if_safe(fake_empty_dsn, intended_data_dir=empty_dir,
                             dsn_file=tmp_dsn, probe=mixed_probe, env={})
        failures += 1
        print("   FAIL: the downgrade was allowed")
    except bp.DsnWriteRefused as e:
        preserved = tmp_dsn.read_text(encoding="utf-8").strip() == live_dsn
        print("   ok: refused")
        print(f"   DSN preserved = {preserved}")
        print("   reason: " + str(e).strip().splitlines()[0])
        if not preserved:
            failures += 1

# ── 5. The shared DSN was not modified ─────────────────────────────────────
print("-" * 78)
after = _digest(REAL_DSN_FILE)
print("5. SHARED DSN UNTOUCHED")
print(f"   sha256 before = {before[:16]}...")
print(f"   sha256 after  = {after[:16]}...")
if before != after:
    failures += 1
    print("   FAIL: staging/pg_dsn.txt was modified by this probe")
else:
    print("   ok: staging/pg_dsn.txt is byte-for-byte unchanged")

print("=" * 78)
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
sys.exit(0 if failures == 0 else 1)
