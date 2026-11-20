"""PHASE 10 WORKSTREAM I — Stage 0 baseline. Strictly read-only.

Records the DSN checksum, the server-reported `current_setting('data_directory')`, the
shared provisioning classification, and which schema markers are present -- measured now
rather than quoted from the Workstream H report, because a reachable port does not prove
the intended database is serving requests and the recorded values may have moved.

Starts, stops and restarts nothing. Writes nothing except its own stdout.
"""
import hashlib
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN_FILE = ROOT / "staging" / "pg_dsn.txt"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bp = _load("_p10i_bootstrap", "staging/bootstrap_pg.py")

print("=" * 78)
print("PHASE 10 / WS-I / STAGE 0 - DSN baseline (read-only, nothing started)")
print("=" * 78)

if not DSN_FILE.exists():
    print("staging/pg_dsn.txt ABSENT - database-dependent steps are BLOCKED")
    sys.exit(1)

raw = DSN_FILE.read_bytes()
dsn = raw.decode("utf-8").strip()
print(f"DSN                : {dsn}")
print(f"DSN sha256         : {hashlib.sha256(raw).hexdigest()}")
print(f"DSN bytes          : {len(raw)}")

state = bp._default_probe(dsn)
print("-" * 78)
print(f"reachable          : {state.reachable}")
if not state.reachable:
    print(f"error              : {state.error}")
    print("VERDICT: the active database cannot be verified -> database-dependent")
    print("         operations must STOP and the blocker be documented.")
    sys.exit(2)

print(f"public_tables      : {state.public_tables}")
print(f"data_directory     : {state.data_directory}   (server-reported)")
print(f"classification     : {state.classification}  (rank {state.rank})")
present = [m for m in bp.SCHEMA_MARKERS if m not in state.missing_markers]
print(f"markers present    : {len(present)}/{len(bp.SCHEMA_MARKERS)}  {present}")
print(f"markers missing    : {list(state.missing_markers) or 'none'}")

print("-" * 78)
print("data directories on disk (not started, not modified):")
for d in sorted(p for p in (ROOT / "staging").iterdir()
                if p.is_dir() and (p / "PG_VERSION").is_file()):
    base = d / "base"
    size = sum(f.stat().st_size for f in base.rglob("*") if f.is_file()) if base.exists() else 0
    print(f"   {d.name:<12} base_bytes={size:,}")

print("-" * 78)
print(f"is the live target pgdata_r1? "
      f"{bp._same_directory(state.data_directory, ROOT / 'staging' / 'pgdata_r1')}")
print("=" * 78)
sys.exit(0)
