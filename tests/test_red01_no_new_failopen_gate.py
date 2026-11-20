"""RED-01: no NEW place may launder an unreadable Redis value into a decision.

RED-01's remaining_action asked for every caller of the shared Redis manager's proxy
methods to be given an explicit fail-open/fail-closed choice, and recorded that the
remaining sites "were not individually reviewed". There are 625 such call sites across 90
files. Converting all of them is not a review, and 625 hand edits would be a worse artifact
than the thing being fixed.

WHAT THE REVIEW ACTUALLY FOUND. A swallowed error only matters where the neutral value it
returns reaches a decision, and in this codebase that happens through one idiom:

    int(await redis_manager.get(key) or 0)      # "Redis is down" becomes "zero used"
    await redis_manager.llen(key) or 0

`scripts/red01_failopen_inventory.py` finds exactly those -- a proxy call whose neutral
return is immediately coerced past the point where the caller could tell the difference.
Nine exist. Not one of them is in a wired safety path, and this test pins that, keyed on
(file, method) rather than line number so ordinary edits do not break it.

WHY A GUARD INSTEAD OF A REFACTOR. The three safety-critical categories RED-01 named --
the kill switch, quota enforcement and idempotency -- each already fail closed through
their own mechanism, which this suite tests elsewhere:

    GlobalKillSwitch._redis_get      raises RedisUnavailable  (KILL-01)
    usage_ledger.count               raises UsageReadFailed -> 503
                                     ("a figure that could not be read is not a figure
                                     of zero", subscription_dependencies.py)
    _atomic_check_and_set            raises in production      (FIN-CRITICAL-004)

So the live risk is not an unconverted call site. It is a NEW laundering site being added
to a wired path, and that is what this file refuses.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join("scripts", "red01_failopen_inventory.py")
REPORT = os.path.join(REPO_ROOT, "reports", "red01_laundering.json")

#: Every known site, with the measured reason its fail-open cannot reach a decision.
#: Keyed (file, method) -- NOT line -- so moving code does not fail this test, while
#: ADDING a laundering site anywhere does.
KNOWN = {
    ("backend_app/core/hard_quota_enforcer.py", "get"): (
        "DORMANT. scripts/reachability_audit.py reports this file unreachable from every "
        "deployment entry point, and the module's own docstring says the two live "
        "services that once constructed it 'no longer do'. QUOTA-01 keeps it deliberately "
        "unwired and tests/test_quota01_enforcement_is_wired.py asserts the call-site "
        "list stays empty."
    ),
    ("backend_app/core/hard_quota_enforcer.py", "incrbyfloat"): (
        "DORMANT, same module and same reason."
    ),
    ("backend_app/core/entitlement_engine.py", "get"): (
        "UNWIRED METHOD. The laundering sits in EntitlementEngine.check_quota_entitlement "
        "and in get_entitlement_status, and NEITHER has a production caller -- measured "
        "by AST, counting attribute calls rather than name mentions, because this module "
        "IS imported live for PlanMapper and FeatureEntitlements. The wired quota path is "
        "subscription_dependencies.enforce_quota -> usage_ledger.count, which raises "
        "UsageReadFailed and becomes a 503."
    ),
    ("backend_app/core/entitlement_engine.py", "scard"): (
        "UNWIRED. _get_position_count is called only from get_entitlement_status, which "
        "has no caller anywhere."
    ),
    ("backend_app/routers/dag_tasks.py", "llen"): (
        "OBSERVABILITY, not a gate. `dead_letter_count` is a field on "
        "RecoveryStatsResponse. A neutral 0 under-reports a queue depth on a stats "
        "endpoint; it does not admit or refuse anything. Worth fixing for honesty, not "
        "for safety."
    ),
}


@pytest.fixture(scope="module")
def sites():
    """The inventory, called IN-PROCESS rather than through a subprocess.

    An earlier version spawned `python scripts/red01_failopen_inventory.py` and failed with
    Windows 0xC0000142 (DLL initialisation failed) and empty output on a machine with
    ~0.9 GB free RAM -- a resource failure that looks exactly like a broken scanner. The
    scanner has no side effects worth isolating, so importing it removes a whole class of
    false red.
    """
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "red01_failopen_inventory", os.path.join(REPO_ROOT, SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.scan()


def test_every_laundering_site_is_a_known_one(sites):
    found = {(s["file"], s["method"]) for s in sites}
    unknown = found - set(KNOWN)
    assert not unknown, (
        "a Redis proxy call's neutral return is being coerced into a usable value at a "
        "place RED-01 has not reviewed:\n  "
        + "\n  ".join(f"{f}:{m}" for f, m in sorted(unknown))
        + "\n\nIf the value feeds a limit, quota, membership or lock decision, it must "
          "fail CLOSED -- raise, the way GlobalKillSwitch._redis_get raises "
          "RedisUnavailable and usage_ledger.count raises UsageReadFailed. If it only "
          "feeds a cache read or a response field, add it to KNOWN in this file with the "
          "measured reason."
    )


def test_the_known_set_has_not_silently_emptied(sites):
    # If the inventory stops finding anything, the likelier cause is a broken scanner
    # than nine simultaneous fixes. An empty result must not read as success.
    assert sites, (
        "the inventory found no laundering sites at all. Either every one was genuinely "
        "fixed -- in which case delete the corresponding KNOWN entries and this test -- "
        "or scripts/red01_failopen_inventory.py has stopped detecting the idiom."
    )


@pytest.mark.parametrize("key", sorted(KNOWN))
def test_each_known_entry_still_describes_a_real_site(sites, key):
    # Prevents KNOWN from accumulating stale exemptions: an entry that no longer matches
    # anything is an exemption nobody is checking.
    found = {(s["file"], s["method"]) for s in sites}
    assert key in found, (
        f"KNOWN names {key[0]}:{key[1]} but the inventory no longer finds it. If it was "
        f"fixed, remove the entry so the exemption list stays honest."
    )


def test_no_laundering_in_the_wired_quota_path(sites):
    """The specific regression this finding is about."""
    WIRED = ("backend_app/core/usage_ledger.py",
             "backend_app/core/subscription_dependencies.py")
    offenders = [s for s in sites if s["file"] in WIRED]
    assert not offenders, (
        "the WIRED quota path now launders an unreadable Redis value. That path exists "
        "precisely to raise UsageReadFailed instead -- 'a figure that could not be read "
        f"is not a figure of zero'. Offenders: {offenders}"
    )


def test_no_laundering_in_the_kill_switch(sites):
    """KILL-01's fix must not be undone."""
    offenders = [s for s in sites
                 if s["file"] == "backend_app/core/global_safety.py"]
    assert not offenders, (
        "global_safety.py launders a Redis neutral. KILL-01 exists because that made an "
        "unreadable halt state indistinguishable from 'not halted'; _redis_get and "
        f"_redis_smembers must raise RedisUnavailable. Offenders: {offenders}"
    )
