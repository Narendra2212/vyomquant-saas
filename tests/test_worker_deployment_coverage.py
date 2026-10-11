"""Every ``WorkerBase`` subclass must be startable from a deployment entry point.

``backend_app/workers/marketplace_expiry_worker.py`` is a complete, specced, tested
worker - ``WorkerBase`` subclass, 30-second interval, health heartbeat, deliberate
non-swallowing of ``ExpirySweepFailed`` - that nothing starts. It has no entry in
``local.worker_definitions`` in ``terraform/ecs.tf`` (which holds exactly ``backtest``,
``dag`` and ``command``), no ``docker-compose.yml`` service, and no reference in any
Dockerfile, shell script or workflow. ``startup.sh`` ends with
``exec gunicorn backend_app.main:app``, so nothing is spawned alongside the API, and
``backend_app/main.py`` contains no ``create_task``/``ensure_future``/``run_worker``.
``expiry_sweep.sweep`` is reachable only from that worker.

The consequence is a subscription whose ``period_expiry`` has passed is never
transitioned out of ``active``/``suspended``, so paid access outlives the paid period.
``design.md`` specifies a 30-second sweep to satisfy Requirement 11.8's "no greater than
60 seconds"; the real interval is never.

The worker's own health design cannot catch this. It publishes ``unhealthy`` after three
consecutive *failures*, and ``marketplace.expiry_sweep.last_run_at`` goes stale when
passes stop succeeding - both of which require the process to exist. A worker that was
never started publishes nothing to ``worker:health:marketplace_expiry_worker``, so no
probe fires. The failure that actually happened is the one not instrumented, which is
why the guard belongs here, in the test suite, rather than in a runtime health check.

Two tests, doing different jobs:

``test_no_new_unhosted_workers`` is the regression guard. It passes today and goes red
the moment a fourth worker is added without a deployment entry. That is the recurrence
this file exists to prevent.

``test_known_unhosted_workers_are_still_unhosted`` is ``xfail(strict=True)``. It asserts
the invariant as it should be, and is expected to fail while the two known gaps remain.
Because it is strict, fixing the gaps makes it *pass*, which fails the run and forces
whoever fixed it to update the ledger below. An allowlist that simply made the suite
green was rejected deliberately: that is the shape of ``scripts/verify_rls.py``, whose
policy check initialises ``all_pass = True`` and never reassigns it, and so reports
success over a real gap.
"""

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Where worker classes are looked for. Narrower than the whole package because
#: WorkerBase lives in core and the subclasses are all under these roots.
WORKER_SEARCH_ROOTS = (
    REPO / "backend_app" / "workers",
    REPO / "backend_app" / "backend",
    REPO / "backend_app",
)

#: Files that can start a process. Each is scanned for `python -m <module>`.
DEPLOYMENT_MANIFESTS = (
    REPO / "terraform" / "ecs.tf",
    REPO / "docker-compose.yml",
)

#: Workers known to be defined but unhosted, with why each is that way. They are NOT
#: excused by the regression guard's accounting - they are its baseline, so a third
#: unhosted worker still fails. Remove an entry when the worker is genuinely deployed.
KNOWN_UNHOSTED = {
    "MarketplaceExpiryWorker": (
        "Built to spec (marketplace-subscriptions-paper-trading task 20.1) and never "
        "wired up. Absent from local.worker_definitions in terraform/ecs.tf, from "
        "docker-compose.yml, and from every Dockerfile and workflow. Subscriptions "
        "past period_expiry are therefore never expired. This is a live billing "
        "defect, not dead code."
    ),
    "ReconciliationWorker": (
        "Instantiated as a module-level singleton (reconciliation_worker.py line 779) "
        "and exposed via get_reconciliation_worker(), but that accessor is never "
        "called outside tests and scripts and .start() is never invoked. Most likely "
        "superseded by backend_app/tee/main.py's own reconciler_loop(), which does run "
        "under the tee ECS service - the README describes a consolidation that removed "
        "duplicated execution engines. Confirm it is genuinely orphaned, then delete "
        "it; an unhosted reconciler that looks live is worse than no reconciler."
    ),
}


def _module_path(path: Path) -> str:
    """``backend_app/workers/command_worker.py`` -> ``backend_app.workers.command_worker``."""
    relative = path.relative_to(REPO).with_suffix("")
    return ".".join(relative.parts)


@lru_cache(maxsize=1)
def _sources():
    """``{module_path: source}`` for the search roots, read once.

    Cached because the roots overlap - ``backend_app`` contains ``backend_app/backend``
    and ``backend_app/workers`` - and because the reference scan would otherwise re-read
    the whole package once per candidate worker. Without this the file takes ~84s; with
    it, a few seconds.
    """
    collected = {}

    for root in WORKER_SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if "__pycache__" in parts or "archived_migrations" in parts:
                continue
            module = _module_path(path)
            if module in collected:
                continue
            try:
                collected[module] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

    return collected


@lru_cache(maxsize=1)
def _worker_base_subclasses():
    """Map ``{class_name: module_path}`` for every ``WorkerBase`` subclass.

    Uses AST rather than importing, because importing the worker modules pulls in Redis
    handles, Supabase clients and the metrics registry - a structural question should
    not need the runtime to stand up.
    """
    found = {}

    for module, source in _sources().items():
        # Substring prefilter before the parse. A file that never mentions WorkerBase
        # cannot declare a subclass of it, and AST-parsing the whole package is the
        # dominant cost in this file.
        if "WorkerBase" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
                if name == "WorkerBase":
                    found[node.name] = module

    return found


@lru_cache(maxsize=1)
def _deployed_modules():
    """Every module named by a ``python -m <module>`` in a deployment manifest."""
    pattern = re.compile(r"python3?[\"',\s]+(?:-m)[\"',\s]+([A-Za-z_][\w.]*)")
    modules = set()

    for manifest in DEPLOYMENT_MANIFESTS:
        if manifest.is_file():
            modules.update(pattern.findall(manifest.read_text(encoding="utf-8")))

    for dockerfile in REPO.glob("Dockerfile*"):
        if dockerfile.is_file():
            modules.update(pattern.findall(dockerfile.read_text(encoding="utf-8")))

    return frozenset(modules)


def _class_referenced_outside_own_module(class_name: str, own_module: str) -> set:
    """Modules other than ``own_module`` that mention ``class_name``.

    A worker can legitimately be hosted inside another deployed process rather than
    having its own entry point, so naming it elsewhere counts as hosting. Tests and
    scripts are outside the search roots, so a test constructing a worker is correctly
    not treated as a deployment.
    """
    return {
        module
        for module, source in _sources().items()
        if module != own_module and class_name in source
    }


@lru_cache(maxsize=1)
def _unhosted_workers():
    """``{class_name: module_path}`` for workers no deployment can start."""
    deployed = _deployed_modules()
    unhosted = {}

    for class_name, module in _worker_base_subclasses().items():
        if module in deployed:
            continue
        hosts = _class_referenced_outside_own_module(class_name, module)
        if hosts & deployed:
            continue
        unhosted[class_name] = module

    return unhosted


# ── Sanity: the discovery itself works ────────────────────────────────────────


def test_discovery_finds_the_known_workers():
    """If discovery silently found nothing, both tests below would be vacuous."""
    workers = _worker_base_subclasses()

    for expected in ("CommandWorker", "DAGWorker", "BacktestWorker"):
        assert expected in workers, f"{expected} not discovered; the AST scan is broken"

    assert workers["CommandWorker"] == "backend_app.workers.command_worker"


def test_deployment_manifests_are_parsed():
    """A regex that matched nothing would make every worker look unhosted."""
    deployed = _deployed_modules()

    assert "backend_app.workers.command_worker" in deployed, (
        f"terraform/ecs.tf worker_definitions not parsed; got {sorted(deployed)}"
    )
    assert "backend_app.backend.dag_worker" in deployed
    assert "backend_app.worker" in deployed


def test_hosted_workers_are_recognised_as_hosted():
    """The three deployed workers must not be reported as gaps."""
    unhosted = _unhosted_workers()

    for hosted in ("CommandWorker", "DAGWorker", "BacktestWorker"):
        assert hosted not in unhosted, (
            f"{hosted} has a deployment entry but was reported unhosted - the guard "
            f"would produce false positives"
        )


# ── The regression guard ──────────────────────────────────────────────────────


def test_no_new_unhosted_workers():
    """No worker beyond the documented two may be undeployable.

    This is the test that earns its keep. It is green today, and it goes red the moment
    someone adds a WorkerBase subclass without an entry in terraform/ecs.tf or
    docker-compose.yml - which is exactly how MarketplaceExpiryWorker got here.
    """
    unhosted = _unhosted_workers()
    unexpected = {k: v for k, v in unhosted.items() if k not in KNOWN_UNHOSTED}

    assert not unexpected, (
        "New worker(s) defined but not startable from any deployment entry point:\n"
        + "\n".join(f"  - {name} ({module})" for name, module in sorted(unexpected.items()))
        + "\n\nAdd an entry to local.worker_definitions in terraform/ecs.tf (which "
        "creates both the task definition and the ECS service via for_each), or a "
        "docker-compose service. A worker with no entry point runs nowhere, and its "
        "own health heartbeat cannot report that - nothing is listening for a process "
        "that never started."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "MarketplaceExpiryWorker and ReconciliationWorker are both defined but "
        "unhosted. strict=True means fixing them turns this into an unexpected pass, "
        "which fails the run and forces KNOWN_UNHOSTED to be updated - so the ledger "
        "cannot quietly outlive the gap it documents."
    ),
)
def test_known_unhosted_workers_are_still_unhosted():
    """The invariant as it should hold. Expected to fail until the gaps are closed."""
    unhosted = _unhosted_workers()

    assert not unhosted, (
        "Workers defined but not startable from any deployment entry point:\n"
        + "\n".join(
            f"  - {name} ({module})\n      {KNOWN_UNHOSTED.get(name, 'undocumented')}"
            for name, module in sorted(unhosted.items())
        )
    )


def test_known_unhosted_ledger_has_no_stale_entries():
    """An entry that names a worker which no longer exists is misleading."""
    workers = _worker_base_subclasses()
    stale = [name for name in KNOWN_UNHOSTED if name not in workers]

    assert not stale, (
        f"KNOWN_UNHOSTED names worker(s) that no longer exist: {stale}. Remove them."
    )


def test_expiry_worker_is_the_documented_billing_gap():
    """Pin the specific finding, so a refactor cannot quietly lose it.

    Named explicitly rather than left to the general guard because the consequence is
    not a dormant feature: subscriptions are never expired, so paid access persists
    after the paid period.
    """
    unhosted = _unhosted_workers()

    if "MarketplaceExpiryWorker" not in unhosted:
        pytest.skip(
            "MarketplaceExpiryWorker now has a deployment entry. Remove it from "
            "KNOWN_UNHOSTED and delete this test."
        )

    assert unhosted["MarketplaceExpiryWorker"] == (
        "backend_app.workers.marketplace_expiry_worker"
    )
