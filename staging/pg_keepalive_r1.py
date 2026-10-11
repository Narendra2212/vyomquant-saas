"""Start the pgdata_r1 PostgreSQL instance and KEEP THE PROCESS ALIVE.

The crash-recovery of pgdata_r1 kept being killed by a console-control event (0xC000013A /
STATUS_CONTROL_C_EXIT) each time the launching foreground command exited, because pgserver's
postmaster is in the launcher's process group. Running this under the detached background
process runner keeps a parent alive, so recovery can finish undisturbed. It starts the
server, records the DSN, waits for connectivity, then sleeps indefinitely so the server
stays up for the regression lane. Stop it with the control_pwsh_process stop action.

PHASE 10 / WORKSTREAM H — the DSN write is now validated.
    This script used to do:

        srv = pgserver.get_server(str(DATA_DIR), cleanup_mode=None)
        DSN_FILE.write_text(srv.get_uri(), encoding="utf-8")   # unconditional
        ... then check connectivity and the table count, AFTER the fact

    so the shared DSN was committed before anything had confirmed the server was
    reachable or carried the provisioned schema. It is the second of the three writers of
    `staging/pg_dsn.txt` and the one the regression lane actually depends on, so it now
    goes through `bootstrap_pg.write_dsn_if_safe`, which connects, confirms the server is
    serving THIS data directory, requires the provisioned markers, and refuses to replace
    a better-provisioned target.

    If the write is refused the server is still left UP and the keepalive still runs: a
    reachable server is useful even when the DSN should not be repointed, and the refusal
    is printed rather than swallowed. The existing DSN is preserved in that case.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import time

#: Explicit, and belt-and-braces with the hardcoded DATA_DIR below. Kept because
#: `bootstrap_pg` now REFUSES to infer a directory when several clusters exist, and this
#: script must never be the thing that has to guess.
os.environ["PG_DATA_DIR"] = "pgdata_r1"

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN_FILE = ROOT / "staging" / "pg_dsn.txt"
DATA_DIR = ROOT / "staging" / "pgdata_r1"


def _load_bootstrap():
    """`staging/` is not a package, so the sibling module is loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "_keepalive_bootstrap_pg", ROOT / "staging" / "bootstrap_pg.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bp = _load_bootstrap()

import pgserver  # noqa: E402

print(f"starting pgserver on {DATA_DIR} ...", flush=True)
srv = pgserver.get_server(str(DATA_DIR), cleanup_mode=None)
dsn = srv.get_uri()
print(f"PG_STARTED port={dsn.rsplit(':', 1)[-1].split('/')[0]}", flush=True)

# Wait for the server to actually answer before judging it. Crash recovery on this cluster
# has taken over a minute (the data-directory fsync alone was 67s), so a single immediate
# probe would classify a perfectly healthy server as unreachable.
state = None
for attempt in range(12):
    state = bp._default_probe(dsn)
    if state.reachable:
        print(f"PG_READY {state.describe()}", flush=True)
        break
    print(f"  not ready yet (attempt {attempt + 1}): {state.error}", flush=True)
    time.sleep(5)
else:
    print("PG_NEVER_READY", flush=True)

# Record the DSN only if it survives validation. A refusal leaves the file untouched.
if state is not None and state.reachable:
    try:
        bp.write_dsn_if_safe(
            dsn,
            intended_data_dir=DATA_DIR,
            dsn_file=DSN_FILE,
            expect_provisioned=True,
        )
        print(f"PG_DSN_RECORDED {DSN_FILE}", flush=True)
    except bp.DsnWriteRefused as e:
        print("PG_DSN_NOT_RECORDED (server left up, existing DSN preserved):", flush=True)
        print(f"  {e}", flush=True)
else:
    print("PG_DSN_NOT_RECORDED: server never became reachable; existing DSN preserved",
          flush=True)

# Keep this process (and therefore the postmaster's parent) alive.
print("KEEPALIVE: sleeping to hold the server up. Stop this process to shut down.",
      flush=True)
while True:
    time.sleep(30)
