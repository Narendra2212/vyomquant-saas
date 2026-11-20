"""Prove live customer trading is OFF by executing the gate, not by reading the config.

The launch report asserts that live trading is disabled as deployed. That claim is worth
only as much as the evidence behind it, and "the task definition does not set the variable"
is an argument about a file. This runs the actual guard in a subprocess with the exact
environment `ecs-task-definition-full.json` specifies, and reports what the guard answers.

Four cases are checked, because "it is off" needs to mean "off for the right reason and
not by accident":

  1. Production as deployed: ENV=production, VYOMQUANT_MODE=paper, the enable flag unset.
     Live execution must be refused.
  2. The flag set to true but the mode still paper. Must STILL be refused -- the design
     requires both, and if the flag alone were sufficient, a single environment variable
     would be the only thing between paper and real money.
  3. Mode set to live with the flag unset. Must be refused.
  4. The deprecated alias AERORA_ENABLE_LIVE_TRADING set to true, mode paper. Must be
     refused, i.e. the legacy name is not a side door.

No real order is placed in any case: only the guard is invoked. Case 2 and 4 set the
enable flag INSIDE A SUBPROCESS that does nothing but ask the guard a question, and the
expected answer is a refusal; nothing downstream of the guard is reached.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# Asks the guard whether a live order would be permitted, and prints the verdict.
#
# The operation name is "live_order" exactly. `SafetyMonitor.check_execution_allowed`
# compares `operation == "live_order"` for the paper-mode refusal, so a descriptive name
# like "live_order_submission" falls through that branch and -- correctly -- returns None,
# because paper mode does permit paper execution. Getting this wrong makes the guard look
# broken when it is not, which is why the exact string matters.
#
# The function returns None when execution is ALLOWED and a "[BLOCKED] ..." string when it
# is refused, so the absence of a verdict is the dangerous answer, not the safe one.
PROBE = r"""
import json
from backend_app.core.safety_config import (
    ExecutionFlags, SafetyMonitor, get_live_trading_confirmation, get_vyomquant_mode,
)
verdict = SafetyMonitor.check_execution_allowed("live_order")
print("PROBE_RESULT:" + json.dumps({
    "mode": get_vyomquant_mode("safe"),
    "enable_flag": get_live_trading_confirmation("false"),
    "flag_live_trading_enabled": bool(ExecutionFlags.LIVE_TRADING_ENABLED),
    "verdict": "ALLOWED (returned None)" if verdict is None else str(verdict),
    "blocked": verdict is not None,
}))
"""

CASES = [
    (
        "production as deployed (mode=paper, flag unset)",
        {"ENV": "production", "VYOMQUANT_MODE": "paper"},
        {"VYOMQUANT_ENABLE_LIVE_TRADING", "AERORA_ENABLE_LIVE_TRADING", "AERORA_MODE"},
    ),
    (
        "flag=true but mode still paper (both are required)",
        {"ENV": "production", "VYOMQUANT_MODE": "paper",
         "VYOMQUANT_ENABLE_LIVE_TRADING": "true"},
        {"AERORA_ENABLE_LIVE_TRADING", "AERORA_MODE"},
    ),
    (
        "mode=live but flag unset",
        {"ENV": "production", "VYOMQUANT_MODE": "live"},
        {"VYOMQUANT_ENABLE_LIVE_TRADING", "AERORA_ENABLE_LIVE_TRADING", "AERORA_MODE"},
    ),
    (
        "deprecated alias AERORA_ENABLE_LIVE_TRADING=true, mode paper",
        {"ENV": "production", "VYOMQUANT_MODE": "paper",
         "AERORA_ENABLE_LIVE_TRADING": "true"},
        {"VYOMQUANT_ENABLE_LIVE_TRADING", "AERORA_MODE"},
    ),
    # THE DECISIVE CASE. Both environment switches set to the values that are supposed to
    # enable live trading. If this were permitted, two environment variables would be the
    # only thing between this deployment and real customer money.
    #
    # It is refused, and by a THIRD control: `ExecutionFlags.LIVE_TRADING_ENABLED` is a
    # hardcoded `bool = False` class attribute in safety_config.py (line 114) that is read
    # from no environment variable at all. Enabling live trading therefore requires a code
    # change or an explicit in-process assignment, not a configuration change. That is the
    # strongest form this guarantee can take and it is worth recording explicitly.
    (
        "BOTH env switches set: mode=live AND flag=true",
        {"ENV": "production", "VYOMQUANT_MODE": "live",
         "VYOMQUANT_ENABLE_LIVE_TRADING": "true"},
        {"AERORA_ENABLE_LIVE_TRADING", "AERORA_MODE"},
    ),
]


def run_case(overrides: dict[str, str], unset: set[str]):
    env = dict(os.environ)
    for name in unset:
        env.pop(name, None)
    env.update(overrides)
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONIOENCODING"] = "utf-8"
    # Keep the probe off any real database.
    env["DATABASE_URL"] = ""
    env["AERORA_TEST_DATABASE_URL"] = ""
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE_RESULT:"):
            return json.loads(line[len("PROBE_RESULT:"):]), proc
    return None, proc


def main() -> int:
    out: list[str] = []

    def emit(s: str = "") -> None:
        out.append(s)
        print(s.encode("ascii", "replace").decode("ascii"))

    emit("=" * 78)
    emit("LIVE TRADING GATE -- executed, not inferred")
    emit("=" * 78)

    failures = 0
    for label, overrides, unset in CASES:
        result, proc = run_case(overrides, unset)
        emit()
        emit(f"CASE: {label}")
        shown = {k: v for k, v in overrides.items()}
        emit(f"  env        : {shown}")
        emit(f"  unset      : {sorted(unset)}")
        if result is None:
            emit("  RESULT     : PROBE DID NOT REPORT")
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
            for line in tail:
                emit(f"    {line[:150]}")
            failures += 1
            continue
        emit(f"  mode       : {result['mode']!r}")
        emit(f"  enable flag: {result['enable_flag']!r}")
        emit(f"  LIVE_TRADING_ENABLED: {result['flag_live_trading_enabled']}")
        emit(f"  verdict    : {result['verdict'][:120]}")
        if result["blocked"]:
            emit("  RESULT     : BLOCKED (expected)")
        else:
            emit("  RESULT     : *** NOT BLOCKED -- LIVE EXECUTION WOULD BE PERMITTED ***")
            failures += 1

    emit()
    emit("-" * 78)
    if failures:
        emit(f"RESULT: {failures} case(s) did not refuse live execution. DO NOT LAUNCH.")
    else:
        emit("RESULT: every case refused live execution. Live customer trading is OFF.")
        emit()
        emit("        It stays off with either required switch set alone, with the")
        emit("        deprecated alias, and -- the decisive case -- with BOTH environment")
        emit("        switches set to the values that are meant to enable it. That last")
        emit("        case is refused by ExecutionFlags.LIVE_TRADING_ENABLED, a hardcoded")
        emit("        `bool = False` read from no environment variable, so enabling live")
        emit("        trading requires a CODE CHANGE and not a configuration change.")

    (REPO / "staging" / "live_trading_gate.txt").write_text("\n".join(out), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
