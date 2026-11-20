"""Re-record exactly two authorised Phase 9 baseline deltas, and nothing else.

WHY THIS IS SURGICAL, NOT A REGENERATION
----------------------------------------
`tests/regression/test_baseline_unchanged.py` permits a re-record ONLY where a finding
authorises the change and the authorisation travels with the file as a `_baseline_note…`
member: "A re-record with no such note is a regression that was papered over, and is to be
treated as one." A blanket overwrite would absorb EVERY difference, including drift nobody
authorised, and would silently drop the `_baseline_note*` members already in both files.

So this script re-runs each capture, carries every existing `_baseline_note*` member back
to the exact path it occupied (refusing if a note's home disappeared), applies ONLY the
two named Phase 9 deltas with guards that abort on anything else, adds a provenance note
for each, and leaves the differ to confirm the rest is clean.

THE TWO AUTHORISED CHANGES
--------------------------
1. auth_admin_role: `admin_routes[2].handlers[0].returns[0]` (the return dict of
   `global_kill_switch`, POST /api/admin/kill-all) gains three keys: `kill_switch_active`,
   `holds`, `holds_readable`.
   Authorised by KILL-02 / KILL-03. The handler now activates the authoritative
   `GlobalKillSwitch` BEFORE fanning out `fleet.shutdown_all()`, and reports the switch's
   resulting authoritative state and the active hold registry in its response. These are
   ADDED keys on an operator-only administrative response; no existing key is removed,
   renamed or retyped (`message`, `reason`, `status` are unchanged).

2. live_order_path: `scenarios[0].collaborator_sequence[7].arguments` (the argument-name
   set of `ExecutionRecordRepository.update_status` on the completing live order)
   replaces `order_id` with `venue_order_id`.
   Authorised by TRADE-12. `execution_records.order_id` carries a FOREIGN KEY to
   `orders(id)`, but the code was writing the VENUE order id into it, so every real venue
   write raised 23503 and left the row stuck in EXECUTING -- a guaranteed producer of the
   stale-EXECUTING population TRADE-07 remediates. Migration 030 adds a dedicated
   `venue_order_id` column (no FK) and the engine now passes the venue identifier as
   `venue_order_id=`. The call, its position in the sequence, and the orders FK are all
   unchanged; only which argument name carries the venue identifier changed.

Run: .venv\\Scripts\\python.exe staging\\rerecord_baselines_phase9.py
Then re-run: pytest tests/regression/test_baseline_unchanged.py -k "auth_admin_role or live_order_path"
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.regression.capture_baseline import (  # noqa: E402
    baseline_path,
    capture,
    write_baseline,
)

NOTE_PREFIX = "_baseline_note"
ADDITIVE_FIELDS = frozenset(
    {"execution_environment", "is_simulated", "session_id", "stale", "last_price_at"}
)


def normalised(payload):
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


# ── note carrying (identical mechanism to rerecord_live_order_path.py) ───────
def collect_notes(node, path=()):
    found = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.startswith(NOTE_PREFIX):
                found[path + (key,)] = value
            else:
                found.update(collect_notes(value, path + (key,)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.update(collect_notes(value, path + (index,)))
    return found


def place(root, path, value):
    node = root
    for step in path[:-1]:
        if isinstance(node, dict):
            if step not in node:
                return False
            node = node[step]
        elif isinstance(node, list):
            if not isinstance(step, int) or step >= len(node):
                return False
            node = node[step]
        else:
            return False
    if not isinstance(node, dict):
        return False
    node[path[-1]] = value
    return True


def carry_notes(old_doc, fresh_doc, name):
    notes = collect_notes(old_doc)
    print(f"[{name}] EXISTING_NOTES={len(notes)}")
    carried, lost = 0, []
    for path, value in notes.items():
        print(f"    carry: {'.'.join(str(p) for p in path)}")
        if place(fresh_doc, path, value):
            carried += 1
        else:
            lost.append(path)
    if lost:
        print(f"[{name}] REFUSING: existing notes have no home in the new capture:")
        for path in lost:
            print(f"    {'.'.join(str(p) for p in path)}")
        raise SystemExit(1)
    print(f"[{name}] NOTES_CARRIED={carried}")


def strip_notes(node):
    """A deep copy of ``node`` with every ``_baseline_note*`` member removed, so a
    structural equality guard compares contract keys only."""
    if isinstance(node, dict):
        return {
            k: strip_notes(v)
            for k, v in node.items()
            if not (isinstance(k, str) and k.startswith(NOTE_PREFIX))
        }
    if isinstance(node, list):
        return [strip_notes(v) for v in node]
    return node


# ════════════════════════════════════════════════════════════════════════════
#  1. auth_admin_role — KILL-02/KILL-03 kill-all response gains three keys
# ════════════════════════════════════════════════════════════════════════════
AAR = "auth_admin_role"
KILL_RETURN_ADDED = ("holds", "holds_readable", "kill_switch_active")
AAR_NOTE_KEY = "_baseline_note_kill_all_authoritative_state_report"
AAR_NOTE = (
    "KILL-02/KILL-03 AUTHORISE three ADDED keys on the return dict of `global_kill_switch` "
    "(POST /api/admin/kill-all): `kill_switch_active`, `holds` and `holds_readable`. Before "
    "the fix the handler fanned out `fleet.shutdown_all()` and reported only {status, "
    "message, reason}, while the authoritative `GlobalKillSwitch` was never engaged on this "
    "route (KILL-03: `enforce_kill_switch` had zero call sites and `/api/admin/kill-all` did "
    "not reference `GlobalKillSwitch` at all). The handler now ACTIVATES the authoritative "
    "switch BEFORE the fleet fan-out -- the order is load-bearing, because `shutdown_all` can "
    "raise partway and must not leave the switch disengaged -- and reports the switch's "
    "resulting authoritative state (`kill_switch_active`) and the active-hold registry "
    "(`holds`, `holds_readable`) that KILL-02's hold set introduced, so an operator sees what "
    "the halt actually did rather than only that the fan-out was attempted. These are "
    "ADDITIONS: `status`, `message` and `reason` are unchanged in name, type and meaning. "
    "This is NOT one of the five paper additive fields; it is authorised by finding, which is "
    "the other re-record path the differ documents. Evidence: "
    "tests/test_kill03_authoritative_global_halt.py (42 passed), including the activate-before-"
    "shutdown ordering and the state reported back to the operator."
)

old_aar = json.loads(baseline_path(AAR).read_text(encoding="utf-8"))
fresh_aar = normalised(capture(AAR))

ret_old = old_aar["admin_routes"][2]["handlers"][0]["returns"][0]
ret_new = fresh_aar["admin_routes"][2]["handlers"][0]["returns"][0]

# Guard A: the route this entry describes must be the kill-all handler, unchanged.
h_old = old_aar["admin_routes"][2]["handlers"][0]
h_new = fresh_aar["admin_routes"][2]["handlers"][0]
if not (h_old.get("handler") == h_new.get("handler") == "global_kill_switch"
        and h_old.get("route_path") == h_new.get("route_path")
        and str(h_new.get("route_path")).endswith("/admin/kill-all")):
    raise SystemExit(
        f"ABORT [auth_admin_role]: admin_routes[2] is not the kill-all handler on both "
        f"sides. old={h_old.get('handler')}@{h_old.get('route_path')} "
        f"new={h_new.get('handler')}@{h_new.get('route_path')}"
    )

# Guard B: every key the baseline recorded must still be present, same value; the ONLY
# differences permitted are the three authorised ADDED keys.
added = set(ret_new) - set(ret_old)
removed = set(ret_old) - set(ret_new)
changed = {k for k in set(ret_old) & set(ret_new) if ret_old[k] != ret_new[k]}
if removed or changed:
    raise SystemExit(
        f"ABORT [auth_admin_role]: returns[0] has unauthorised changes. "
        f"removed={sorted(removed)} retyped={sorted(changed)}"
    )
if added != set(KILL_RETURN_ADDED):
    raise SystemExit(
        f"ABORT [auth_admin_role]: added keys {sorted(added)} != authorised "
        f"{sorted(KILL_RETURN_ADDED)}. Not re-recording an unexpected addition."
    )

# Guard C: NOTHING ELSE in the whole document may differ (notes aside). This is the
# load-bearing guarantee that a surgical re-record stays surgical.
expected_rest = strip_notes(old_aar)
# apply the authorised delta onto the expected-rest copy, then it must equal fresh (notes aside)
expected_rest["admin_routes"][2]["handlers"][0]["returns"][0] = ret_new
if strip_notes(expected_rest) != strip_notes(fresh_aar):
    # find and report the first offending path for the operator
    def diff_paths(a, b, prefix=""):
        out = []
        if isinstance(a, dict) and isinstance(b, dict):
            for k in set(a) | set(b):
                p = f"{prefix}.{k}" if prefix else k
                if k not in a:
                    out.append(f"+{p}")
                elif k not in b:
                    out.append(f"-{p}")
                else:
                    out += diff_paths(a[k], b[k], p)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                out.append(f"~{prefix} (len {len(a)}->{len(b)})")
            for i in range(min(len(a), len(b))):
                out += diff_paths(a[i], b[i], f"{prefix}[{i}]")
        elif a != b:
            out.append(f"~{prefix} ({a!r}->{b!r})")
        return out
    offenders = diff_paths(strip_notes(expected_rest), strip_notes(fresh_aar))
    raise SystemExit(
        "ABORT [auth_admin_role]: differences beyond the single authorised return-dict "
        "delta were found; this script is not authorised to re-record them:\n  "
        + "\n  ".join(offenders[:40])
    )

carry_notes(old_aar, fresh_aar, AAR)
fresh_aar[AAR_NOTE_KEY] = AAR_NOTE
path_aar = write_baseline(AAR, fresh_aar)
print(f"[auth_admin_role] WROTE={path_aar}")
print(f"[auth_admin_role] added keys recorded: {sorted(KILL_RETURN_ADDED)}")


# ════════════════════════════════════════════════════════════════════════════
#  2. live_order_path — TRADE-12 update_status arg order_id -> venue_order_id
# ════════════════════════════════════════════════════════════════════════════
LOP = "live_order_path"
LOP_NOTE_KEY = "_baseline_note_update_status_venue_order_id_trade12"
LOP_NOTE = (
    "TRADE-12 AUTHORISES the replacement of the `order_id` argument with `venue_order_id` "
    "in the `ExecutionRecordRepository.update_status` call on the completing live order "
    "(collaborator_sequence[7]). ONE argument name changes; the call itself, its POSITION in "
    "the sequence, and every other argument (`execution_id`, `result`, `status`, "
    "`tenant_id`) are unchanged. WHY. `execution_records.order_id` carries a FOREIGN KEY to "
    "`orders(id)`, but the engine was writing the VENUE order id into it. No `orders` row has "
    "the venue's id as its primary key, so every real completion raised 23503 "
    "(foreign_key_violation) and the execution row was left stuck in EXECUTING -- a "
    "guaranteed producer of exactly the stale-EXECUTING population TRADE-07 remediates. "
    "Migration 030 (`030_execution_venue_order_id.sql`) adds a dedicated `venue_order_id` "
    "column with NO foreign key, and `update_status` now records the venue identifier there. "
    "The orders FK is unchanged and still constrains `order_id`; this is a corrected target "
    "for a value that never belonged under the FK, not a removal, retype or re-meaning of a "
    "recorded field. Evidence: tests/test_trade07_stale_execution_recovery_postgres.py "
    "(27 passed, real PostgreSQL) and migration 030's append-only RLS postflight."
)

old_lop = json.loads(baseline_path(LOP).read_text(encoding="utf-8"))
fresh_lop = normalised(capture(LOP))

seq_old = old_lop["scenarios"][0]["collaborator_sequence"]
seq_new = fresh_lop["scenarios"][0]["collaborator_sequence"]

# Guard A: the call-name sequence (order) must be identical across the whole scenario.
calls_old = [c.get("call") for c in seq_old]
calls_new = [c.get("call") for c in seq_new]
if calls_old != calls_new:
    raise SystemExit(
        f"ABORT [live_order_path]: collaborator call ORDER changed, which is a contract.\n"
        f"  before: {calls_old}\n  after : {calls_new}"
    )

# Guard B: entry [7] must be update_status, and the ONLY argument delta is order_id->venue_order_id.
e_old, e_new = seq_old[7], seq_new[7]
if not (e_old.get("call") == e_new.get("call") == "ExecutionRecordRepository.update_status"):
    raise SystemExit(
        f"ABORT [live_order_path]: sequence[7] is not update_status on both sides "
        f"(old={e_old.get('call')} new={e_new.get('call')})."
    )
args_old = set(e_old.get("arguments", []))
args_new = set(e_new.get("arguments", []))
if args_old - args_new != {"order_id"} or args_new - args_old != {"venue_order_id"}:
    raise SystemExit(
        f"ABORT [live_order_path]: update_status argument delta is not the authorised "
        f"order_id->venue_order_id swap. removed={sorted(args_old - args_new)} "
        f"added={sorted(args_new - args_old)}"
    )
if e_old.get("status") != e_new.get("status"):
    raise SystemExit(
        f"ABORT [live_order_path]: update_status recorded status changed "
        f"({e_old.get('status')!r}->{e_new.get('status')!r})."
    )

# Guard C: nothing else in the document may differ (notes aside) beyond entry [7].arguments.
expected_rest = strip_notes(old_lop)
expected_rest["scenarios"][0]["collaborator_sequence"][7] = e_new
if strip_notes(expected_rest) != strip_notes(fresh_lop):
    def diff_paths(a, b, prefix=""):
        out = []
        if isinstance(a, dict) and isinstance(b, dict):
            for k in set(a) | set(b):
                p = f"{prefix}.{k}" if prefix else k
                if k not in a:
                    out.append(f"+{p}")
                elif k not in b:
                    out.append(f"-{p}")
                else:
                    out += diff_paths(a[k], b[k], p)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                out.append(f"~{prefix} (len {len(a)}->{len(b)})")
            for i in range(min(len(a), len(b))):
                out += diff_paths(a[i], b[i], f"{prefix}[{i}]")
        elif a != b:
            out.append(f"~{prefix} ({a!r}->{b!r})")
        return out
    offenders = diff_paths(strip_notes(expected_rest), strip_notes(fresh_lop))
    raise SystemExit(
        "ABORT [live_order_path]: differences beyond the single authorised update_status "
        "argument swap were found; not authorised to re-record them:\n  "
        + "\n  ".join(offenders[:40])
    )

carry_notes(old_lop, fresh_lop, LOP)
fresh_lop[LOP_NOTE_KEY] = LOP_NOTE
path_lop = write_baseline(LOP, fresh_lop)
print(f"[live_order_path] WROTE={path_lop}")
print(f"[live_order_path] update_status args: {sorted(args_old)} -> {sorted(args_new)}")

print("=" * 74)
print("RE-RECORDED 2 AUTHORISED PHASE 9 BASELINE DELTAS. The differ must now be re-run; "
      "any OTHER difference is still a failure, which is the point of patching surgically.")
