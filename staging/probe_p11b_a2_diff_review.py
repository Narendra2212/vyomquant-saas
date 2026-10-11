"""PHASE 11B-A2 section 2+5 -- static verification of the six D5' confirmation points,
plus an inventory of the webhook tests the shared opt-in activates.

Read-only. Asserts on the CURRENT file contents, not on the prior report's description.
"""

from __future__ import annotations

import ast
import pathlib
import re

import yaml

WF = pathlib.Path(".github/workflows/01-pr-check.yml")
GATE = pathlib.Path("tests/real_redis_gate.py")
SLOT = pathlib.Path("tests/test_quota01_exchange_slot_is_atomic.py")
METER = pathlib.Path("tests/test_quota01_metered_reservation_is_hard.py")
HOOK = pathlib.Path("tests/test_webhook_duplicate_delivery_real_redis.py")

results = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if detail:
        print(f"         {detail}")


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def main():
    banner("POINT 1 -- CI unit-test job provides AERORA_TEST_REDIS_URL -> own service")
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    job = doc["jobs"]["unit-tests"]
    step = [s for s in job["steps"] if s.get("name") == "Run Unit Tests"][0]
    env = step["env"]
    svc = job["services"]["redis"]
    check("AERORA_TEST_REDIS_URL present on the Run Unit Tests step",
          "AERORA_TEST_REDIS_URL" in env, f"value = {env.get('AERORA_TEST_REDIS_URL')}")
    check("it points at the job's OWN service (localhost:6379) with a dedicated db index",
          str(env.get("AERORA_TEST_REDIS_URL", "")).startswith("redis://localhost:6379/"),
          f"service image = {svc.get('image')}, ports = {svc.get('ports')}")
    check("the service is the ephemeral GitHub Actions container, not a managed endpoint",
          svc.get("image") == "redis:7-alpine")

    banner("POINT 2 -- the gate reads the deliberate var, PINGs, and fails when unavailable")
    gate_src = GATE.read_text(encoding="utf-8")
    check("OPT_IN_VAR is AERORA_TEST_REDIS_URL",
          'OPT_IN_VAR = "AERORA_TEST_REDIS_URL"' in gate_src)
    check("reachability is decided by an actual PING", ".ping()" in gate_src)
    check("a configured-but-unreachable endpoint FAILS (pytest.fail), not skips",
          "pytest.fail(GATE_REASON)" in gate_src)
    check("the three states are distinct", all(
        s in gate_src for s in ('"skip"', '"fail"', '"run"')))

    banner("POINT 3 -- non-loopback denied unless the explicit opt-in is set")
    check("ALLOW_REMOTE_VAR exists",
          'ALLOW_REMOTE_VAR = "AERORA_TEST_REDIS_ALLOW_REMOTE"' in gate_src)
    check("loopback allow-list is enforced",
          "_LOOPBACK_HOSTS" in gate_src and "_remote_allowed()" in gate_src)

    banner("POINT 4 -- NO production REDIS_URL fallback anywhere in the gated path")
    for label, path in (("gate", GATE), ("slot tests", SLOT), ("metered tests", METER)):
        src = path.read_text(encoding="utf-8")
        # Only count real code references, not prose in docstrings/comments.
        code_refs = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and node.value == "REDIS_URL":
                code_refs.append(node.lineno)
        check(f"{label}: no code reads the string 'REDIS_URL'",
              not code_refs, f"lines: {code_refs}" if code_refs else "")

    banner("POINT 5 -- CI health check and concurrent workflow changes intact")
    opts = str(svc.get("options", ""))
    check("redis service retains its health check",
          "--health-cmd" in opts and "redis-cli ping" in opts, opts.replace("\n", " ")[:90])
    check("the concurrently-added provisioning job is still present",
          "provisioning-and-isolation" in doc["jobs"],
          f"jobs = {len(doc['jobs'])}")
    check("REDIS_URL retained on the step (unrelated lanes unchanged)",
          env.get("REDIS_URL") == "redis://localhost:6379")

    banner("POINT 6 -- no FLUSHDB/FLUSHALL and no unsafe global cleanup")
    for label, path in (("gate", GATE), ("slot tests", SLOT),
                        ("metered tests", METER), ("webhook tests", HOOK)):
        src = path.read_text(encoding="utf-8")
        bad = [k for k in ("flushdb", "flushall", "FLUSHDB", "FLUSHALL") if k in src]
        check(f"{label}: no flush of a shared database", not bad, f"found: {bad}" if bad else "")
    slot_src = SLOT.read_text(encoding="utf-8")
    meter_src = METER.read_text(encoding="utf-8")
    check("gated tests delete only their own namespaced keys",
          "q01-slot-" in slot_src and "q01-real-" in meter_src)
    check("gated tests close their client in a finally block",
          slot_src.count("finally:") >= 2 and "aclose()" in slot_src)

    banner("SECTION 5 -- the webhook tests the shared opt-in activates")
    hook_src = HOOK.read_text(encoding="utf-8")
    tree = ast.parse(hook_src)
    tests = [n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name.startswith("test_")]
    print(f"  test functions in {HOOK.name}: {len(tests)}")
    for t in tests:
        print(f"    - {t}")
    print()
    print("  its gate:")
    print(f"    reads AERORA_TEST_REDIS_URL only : "
          f"{'AERORA_TEST_REDIS_URL' in hook_src and 'os.environ.get(\"REDIS_URL\")' not in hook_src}")
    print(f"    gates on reachability (ping)     : {'.ping()' in hook_src}")
    unreachable_skips = "skipif" in hook_src and "_redis_reachable()" in hook_src
    print(f"    unreachable endpoint -> SKIP     : {unreachable_skips}")
    print("    NOTE: that differs from the quota gates, which FAIL on a configured but")
    print("          unreachable endpoint. Documented, deliberately NOT changed here.")

    banner("RESULT")
    print(f"  {sum(results)}/{len(results)} static checks passed")


if __name__ == "__main__":
    main()
