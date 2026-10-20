"""
tests/test_hy02_reachability_audit_entry_points.py

Regression guard for HY-02's tooling fix to scripts/reachability_audit.py.

WHY THIS FILE EXISTS
---------------------
HY-02 (docs/launch_readiness/REMEDIATION_LEDGER.json) flagged that
scripts/reachability_audit.py -- already wired into
.github/workflows/04-nightly-audit.yml, non-blocking -- was producing
demonstrable FALSE POSITIVES, not just maybe:

1. Package ``__init__.py`` files (e.g. ``backend_app/__init__.py``,
   ``backend_app/api/__init__.py``) were flagged unreachable even though
   something imports a module several levels underneath them. The tool only
   recorded the LITERAL dotted strings it found in each file's AST
   (``from backend_app.api.auth_router import router`` records
   ``backend_app.api.auth_router`` but never ``backend_app.api`` or
   ``backend_app`` on their own) instead of modeling that Python always
   loads every ancestor package's ``__init__.py`` first.

2. ``backend_app/workers/marketplace_expiry_worker.py`` was flagged
   unreachable, but it is DEPLOYED TODAY -- see terraform/ecs.tf's
   ``worker_definitions.marketplace_expiry`` entry and
   tests/test_worker_deployment_coverage.py, which guards that it stays
   hosted. The tool's entry-point list never included any module only ever
   launched as its own ``python -m <module>`` process (an ECS worker task,
   or a standalone Docker image), so nothing reachable from main.py alone
   could ever "see" it.

This file pins both fixes directly against the real tool and the real repo
tree (not a synthetic fixture), so a future entry-point or import-graph
change cannot silently regress either false-positive class without a test
failing. It intentionally does NOT assert a single tight unreachable-file
count: that number is expected to drift as the codebase grows (the ledger
itself already saw it drift from 168/404 to 169/418 between two sessions).
What must never regress is the SET of specific files this session confirmed
are false positives.
"""

import importlib.util
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "reachability_audit.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("reachability_audit", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit_module():
    return _load_module()


# ---------------------------------------------------------------------------
# Unit-level: the ancestor-expansion helper itself
# ---------------------------------------------------------------------------


class TestAddWithAncestors:
    def test_expands_every_ancestor_prefix(self, audit_module):
        imports = set()
        audit_module._add_with_ancestors(imports, "backend_app.api.auth_router")
        assert imports == {
            "backend_app",
            "backend_app.api",
            "backend_app.api.auth_router",
        }

    def test_single_segment_is_its_own_only_entry(self, audit_module):
        imports = set()
        audit_module._add_with_ancestors(imports, "backend_app")
        assert imports == {"backend_app"}

    def test_empty_string_is_a_no_op(self, audit_module):
        imports = set()
        audit_module._add_with_ancestors(imports, "")
        assert imports == set()

    def test_extract_imports_records_ancestor_of_a_from_import(self, audit_module, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text("from backend_app.routers import admin, exchange\n")
        imports = audit_module.extract_imports_from_file(str(src), "scratch.mod")
        # The literal module written in source, AND its ancestor package.
        assert "backend_app.routers" in imports
        assert "backend_app" in imports
        # The `from X import name` alias-as-submodule guess still works too.
        assert "backend_app.routers.admin" in imports


# ---------------------------------------------------------------------------
# Integration: run the real tool against the real repo tree
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def audit_results():
    """Run the actual CLI tool exactly as the nightly workflow does, from the
    repo root, and return its parsed unreachable-file list. This exercises
    the real entry-point list and the real BFS, not a mock.
    """
    proc = subprocess.run(
        [sys.executable, "scripts/reachability_audit.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    unreachable = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("[!] "):
            unreachable.add(line[len("[!] "):].strip())
    return unreachable


# Confirmed false positives (HY-02 ledger evidence) that the entry-point and
# ancestor-package fixes must keep OFF the unreachable list.
KNOWN_FALSE_POSITIVES_NOW_FIXED = {
    "backend_app/__init__.py",
    "backend_app/api_ws/__init__.py",
    "backend_app/mds/__init__.py",
    "backend_app/strategies/__init__.py",
    "backend_app/workers/__init__.py",
    "backend_app/backend/observability/__init__.py",
    "backend_app/workers/marketplace_expiry_worker.py",
    "backend_app/backend/exchange_simulator.py",
}

# Files that are correctly STILL flagged: RT-11 deliberately unmounted
# backend_app.routers.distributed_execution (not imported anywhere in
# main.py's router list; kept only for tests pinning its unstarted state,
# which is not the same thing as production reachability).
KNOWN_STILL_UNREACHABLE = {
    "backend_app/routers/distributed_execution.py",
}


class TestKnownFalsePositivesAreFixed:
    def test_no_known_false_positive_is_still_flagged(self, audit_results):
        still_broken = KNOWN_FALSE_POSITIVES_NOW_FIXED & audit_results
        assert not still_broken, (
            f"These confirmed HY-02 false positives are still flagged unreachable: "
            f"{sorted(still_broken)}"
        )

    def test_genuinely_unmounted_router_is_still_correctly_flagged(self, audit_results):
        # Sanity check the other direction: the fix must not have made
        # EVERYTHING reachable. RT-11's deliberately-unmounted router has no
        # production entry point and should remain in the list.
        missing = KNOWN_STILL_UNREACHABLE - audit_results
        assert not missing, (
            f"Expected these genuinely-unmounted files to still be flagged, but "
            f"they are not (the fix may have over-widened reachability): {sorted(missing)}"
        )

    def test_unreachable_count_did_not_regress_above_pre_fix_baseline(self, audit_results):
        # Pre-fix baseline measured this session: 167 flagged (416 files
        # analyzed). The fix must only ever REDUCE false positives relative
        # to that baseline, never silently grow past it -- a growth would
        # mean either a new entry point stopped resolving or a real import
        # stopped being traced.
        assert len(audit_results) <= 167, (
            f"Unreachable count ({len(audit_results)}) exceeds the documented "
            f"pre-fix baseline of 167 -- investigate before trusting this run."
        )

    def test_entry_point_files_all_exist_on_disk(self, audit_module):
        # Guards against an entry point path silently going stale (renamed
        # or moved file) and being dropped from the walk without anyone
        # noticing, since main() filters missing paths out silently.
        import ast

        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        # Re-derive the literal entrypoints list by executing just the
        # module (already done in audit_module); inspect main()'s source
        # instead of re-running main(), since main() has side effects
        # (writes reports/reachability_audit_results.json).
        assert "backend_app/workers/marketplace_expiry_worker.py" in source
        assert "backend_app/backend/training_worker.py" in source
        assert "backend_app/backend/exchange_simulator.py" in source
        assert "backend_app/worker.py" in source
        for rel in [
            "backend_app/main.py",
            "backend_app/backend/dag_worker.py",
            "backend_app/backend/master_executor.py",
            "backend_app/workers/command_worker.py",
            "backend_app/worker.py",
            "backend_app/workers/marketplace_expiry_worker.py",
            "backend_app/backend/training_worker.py",
            "backend_app/backend/exchange_simulator.py",
            "backend_app/mds/main.py",
        ]:
            assert os.path.isfile(os.path.join(REPO_ROOT, rel)), f"missing entry point file: {rel}"
