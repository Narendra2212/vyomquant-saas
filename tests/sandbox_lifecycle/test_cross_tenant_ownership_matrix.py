"""
The cross-tenant ownership matrix. Requirement 20.4, task 22.1.

Requirement 20.4 mandates an automated suite that asserts, **for every read and write
endpoint introduced or extended by this specification**, that a non-owning authenticated
user receives *the identical non-existence response* Criterion 20.2 defines - never the
requested data, and never any indication the resource exists - across strategy, version,
backtest, backtest result, deployment, signal and exchange-account resource types.

WHAT "IDENTICAL" IS MEASURED AS HERE, AND WHY IT IS NOT A STATUS CODE
---------------------------------------------------------------------
A suite that asserted ``response.status_code == 404`` would be asserting a convention, not
Requirement 20.2. Measured against the real app, this surface answers a non-owner in four
different ways: 404 with a body (most of it), **200 with an empty collection** (the version
history, both backtest listings, the deployment listing, the signal-trace list, the
per-signal timeline and the export - a listing has no missing-resource case), and 200
``{"success": true}`` (the backtest delete, which answers that to every caller by design -
see :class:`TestDeleteBacktestReportsWhatItActuallyDeleted`). A hardcoded 404 would fail on
the honest ones and, worse, would pass on an endpoint that answered 404 with a *different
body* for the two cases - which is exactly the leak Criterion 20.2 forbids.

So what is asserted, on every endpoint, is an **equality between two responses**:

    the stranger naming the OWNER'S id   ==   the stranger naming an id that names nothing

compared on the status code, on every response header (bar ``Date``), and on the body -
whatever those happen to be. Both requests are made by the same authenticated stranger,
against the same running app, one after the other, and the only thing that differs between
them is whether the identifier in the URL corresponds to a row. If any observable part of
the answer differs, the endpoint is an existence oracle and this suite fails.

Two normalisations are applied before the comparison, and both are narrow:

* **The identifier the caller itself supplied** is replaced by a placeholder. Several
  handlers echo it (``{"strategy_id": ..., "versions": []}``, ``"Strategy 'x' not found."``).
  Echoing back what the caller sent reveals nothing the caller did not already know, and
  the two ids are the same length so ``Content-Length`` still has to match.
* **Freshly minted values** - any other UUID and any ISO-8601 timestamp - are replaced by
  placeholders, because a newly created row's id and ``created_at`` differ between two
  requests for reasons that have nothing to do with tenancy. Only the OWNER-side controls
  reach code that mints anything (``backtests.create`` writes a row, ``backtests.results``
  stamps a ``completed_at``); a stranger is refused before either.

Nothing else is masked. In particular no field is skipped, no key is allow-listed, and the
comparison is over the whole decoded body, at every depth.

THE SURFACE IS ENUMERATED, NOT DISCOVERED
-----------------------------------------
:data:`ENDPOINT_MATRIX` is a literal, auditable list, per Requirement 20.4's own wording
that this is a mandated suite rather than a property generator (``design.md``: "This test
enumerates the endpoint surface (a finite, explicit list this design maintains) rather than
generating requests"). It covers the task's matrix -
``{strategy: list, get, versions, update, archive, duplicate}`` x
``{backtests: list, create, get, archive}`` (plus ``results``, the backtest_result WRITE
Requirement 20.4's resource list names but ``tasks.md``'s cell grid does not) x
``{deployments: list, create, get, start, pause, stop}`` x
``{signal trace: list, get}`` - and :class:`TestTheMatrixCoversTheMandatedCells` fails if a
cell of it is ever left without a probe. Enumerating by hand is the point: a runtime crawl
of ``app.routes`` would silently stop covering an endpoint the day it was renamed, and
would give the reviewer nothing to read.

EVERY PATH IS RESOLVED AGAINST THE REAL ``backend_app.main.app``
----------------------------------------------------------------
A path that resolves to nothing answers 404 for the owner's id and 404 for a missing id,
and would sail through the equality assertion while testing precisely nothing. Worse, this
repository has a live shadowing hazard: ``routers/strategies.py`` is mounted at
``/api/strategies`` **before** ``routers/strategy_operations.py`` is mounted at ``/api``,
and first-match-wins - so ``/api/strategies/{id}/clone`` is served by ``strategies.py``'s
handler and ``strategy_operations.py``'s same-path handler is unreachable. Every probe
therefore declares the handler it believes it is testing, and
:class:`TestEveryProbedPathResolvesToTheHandlerItNames` resolves the concrete URL through
Starlette's own matcher over ``app.routes`` and asserts the first full match is that
handler. A shadowed route cannot pass as tested.

THE MATRIX CANNOT PASS VACUOUSLY
--------------------------------
An endpoint that answered "not found" to everybody, owner included, would satisfy the
equality above and be useless. So every probe that can be answered at all in this
environment is paired with an **owner control**: the owner makes the identical request for
the identical id, and the answer must DIFFER from the missing-id answer. That is what
proves the seeded row is reachable through this endpoint, and therefore that the stranger's
refusal was a refusal about something real. Two probes cannot have one and say so
individually, each with the reason (:data:`Probe.owner_control_note`).

And a refusal must leave nothing behind: :class:`TestARefusedRequestWritesNothing` runs the
whole stranger matrix and then compares every row of every table against a snapshot taken
before it, so "the stranger changed nothing" is a fact about the rows, not about a mock's
call count.

WHAT IS REAL, AND WHAT IS NOT
-----------------------------
Real: the mounted ``backend_app.main.app``, its routing and mounting order, every request
model, every router in the path, ``StrategyService``, ``BacktestService``,
``SignalService``, ``strategy_archive``, ``deployment_binding``, ``strategy_lifecycle`` and
the ownership predicate of every one of them.

Doubles: the four ``tests/sandbox_lifecycle/`` seams task 21.1 established, unchanged - the
PostgREST client (:class:`~tests.sandbox_lifecycle.harness.SandboxDatabase`, which *applies*
``.eq`` and the rest, so an endpoint whose ``user_id`` filter went missing selects the
owner's row and is caught), Redis, the seeded feed and the sandbox venue.

WHAT THIS FILE CANNOT PROVE, AND SAYS SO
----------------------------------------
**Database row-level security is not exercised.** There is no PostgreSQL on this host, so
``strategies_owner_select`` and the policies migration 004/005 declare are not in force
here, and nothing below demonstrates that they filter a query. What is demonstrated is the
half that lives in this repository's Python: the application-layer ownership check
Requirement 20.1 requires *in addition to* RLS. That is the strict direction - an endpoint
that relies on RLS alone fails here.

**Relationship to ``tests/security/test_builder_tenant_isolation.py``.** That file is the
strategy-builder spec's task 8.7 suite. It covers a different endpoint set (training jobs,
model versions, exchange accounts) and asserts the weaker predicate "403, or 404, or an
empty collection". This file asserts the stronger one Requirement 20.2 actually states -
that the two answers are the *same* answer - over the endpoints THIS specification adds or
extends. Neither subsumes the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

import pytest
from starlette.routing import Match

from backend_app.main import app

from tests.sandbox_lifecycle.harness import (
    MISSING_BACKTEST_ID,
    MISSING_DEPLOYMENT_ID,
    MISSING_SIGNAL_ID,
    MISSING_STRATEGY_ID,
    OWNED_BACKTEST_ID,
    OWNED_DEPLOYMENT_ID,
    OWNED_PAPER_ACCOUNT_ID,
    OWNED_PAPER_ORDER_ID,
    OWNED_PAPER_SESSION_ID,
    OWNED_SIGNAL_ID,
    OWNED_STRATEGY_ID,
    OWNED_VERSION_ID,
    OWNED_VERSION_LABEL,
    PAPER_SESSION_OWNED_TABLES,
    SandboxWorld,
)

#: The three routers this specification's endpoint surface lives in.
_STRATEGIES = "backend_app.routers.strategies"
_OPS = "backend_app.routers.strategy_operations"
_TRACE = "backend_app.routers.signal_trace"


# ══════════════════════════════════════════════════════════════════════════
# 1. THE TWO SETS OF IDENTIFIERS
# ══════════════════════════════════════════════════════════════════════════
#
# One name per path placeholder, so a probe's URL, its query string and its body all
# substitute from the same mapping and cannot disagree about which id they are naming.

#: The owner's real rows (seeded by ``SandboxWorld.seed_cross_tenant_rows``).
OWNED_IDS: Dict[str, str] = {
    "strategy_id": OWNED_STRATEGY_ID,
    "version": OWNED_VERSION_LABEL,
    "backtest_id": OWNED_BACKTEST_ID,
    "deployment_id": OWNED_DEPLOYMENT_ID,
    "signal_id": OWNED_SIGNAL_ID,
}

#: Identifiers that name nothing at all. ``version`` is the SAME label in both mappings on
#: purpose: the deploy and preflight probes vary the strategy, and holding the version label
#: fixed is what makes "this strategy is not yours" and "this strategy does not exist" the
#: only difference between the two requests.
MISSING_IDS: Dict[str, str] = {
    "strategy_id": MISSING_STRATEGY_ID,
    "version": OWNED_VERSION_LABEL,
    "backtest_id": MISSING_BACKTEST_ID,
    "deployment_id": MISSING_DEPLOYMENT_ID,
    "signal_id": MISSING_SIGNAL_ID,
}


# ══════════════════════════════════════════════════════════════════════════
# 2. THE ENUMERATED SURFACE
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Probe:
    """One endpoint of the mandated surface, and everything needed to ask it twice."""

    #: ``<axis>.<action>``, where the axis and the action are Requirement 20.4's own matrix
    #: cells. Used as the pytest parameter id, so a failure names the cell.
    key: str
    method: str
    #: The path as the REAL app has it mounted, placeholders included. Asserted to resolve,
    #: and asserted to be the path of the registration that actually answers - which is what
    #: pins WHICH registration was probed when one module declares a path twice.
    path: str
    #: ``module:function`` of the handler this probe believes serves that path. Asserted,
    #: because of the ``/api/strategies`` shadowing hazard in this module's docstring.
    #:
    #: A string rather than the function object, because two handlers in
    #: ``strategy_operations.py`` are both called ``list_backtests`` - the module attribute
    #: is the second one, so an identity check against it would fail for the first for a
    #: reason that has nothing to do with routing. The pair (matched route's own ``path``,
    #: handler's ``module:name``) identifies one registration unambiguously.
    handler: str
    #: Query parameters. Values are ``str.format``ed with the id mapping.
    query: Mapping[str, str] = field(default_factory=dict)
    #: JSON body, for the write probes. ``None`` sends no body at all.
    body: Optional[Mapping[str, Any]] = None
    #: ``False`` when the owner's own answer to this request is legitimately the same as the
    #: missing-id answer, so no positive control can be drawn from it. Always paired with a
    #: stated reason.
    owner_control: bool = True
    owner_control_note: str = ""
    #: Why this endpoint is in scope: what this specification added or extended.
    scope: str = ""


#: A window wide enough to satisfy ``BacktestExecuteRequest`` and the create model. No
#: simulation ever runs on it: a stranger is refused at the ownership read, and the owner
#: control stops at the seeded version's empty graph (422 ``BACKTEST_VERSION_UNAVAILABLE``),
#: which is a *different* answer from "not found" and so serves the control. The dates
#: therefore only have to parse.
_WINDOW = {"start_date": "2024-01-01T00:00:00+00:00", "end_date": "2024-02-01T00:00:00+00:00"}


ENDPOINT_MATRIX: Tuple[Probe, ...] = (
    # ══════════════════ strategy ══════════════════
    Probe(
        key="strategy.get",
        method="GET",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:get_strategy_route",
        scope="the strategy read the archived-strategy work (task 5.1/5.2) extends",
    ),
    Probe(
        key="strategy.versions",
        method="GET",
        path="/api/strategies/{strategy_id}/versions",
        handler=f"{_OPS}:get_version_history",
        scope="the version history task 8.3 extended with the per-version canvas block",
    ),
    Probe(
        key="strategy.update",
        method="PUT",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:update_strategy",
        body={"name": "renamed by a stranger"},
        scope="the edit path task 5.1 gave Requirement 3.3's archived refusal",
    ),
    Probe(
        key="strategy.rename",
        method="PUT",
        path="/api/strategies/{strategy_id}/rename",
        handler=f"{_STRATEGIES}:rename_strategy",
        body={"name": "renamed by a stranger"},
        scope="NEW in task 5.3 - PUT /api/strategies/{id}/rename",
    ),
    Probe(
        key="strategy.archive",
        method="DELETE",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:delete_strategy",
        scope="REWIRED in task 5.1 - DELETE is now a soft archive, not a row deletion",
    ),
    Probe(
        key="strategy.duplicate",
        method="POST",
        path="/api/strategies/{strategy_id}/clone",
        handler=f"{_STRATEGIES}:clone_strategy",
        scope=(
            "the duplicate action of Requirement 22.1's list. NOTE the shadowing: "
            "strategy_operations.py declares this same path and is unreachable at it"
        ),
    ),
    # ══════════════════ backtests ══════════════════
    Probe(
        key="backtests.list",
        method="GET",
        path="/api/strategies/{strategy_id}/backtests",
        handler=f"{_OPS}:list_backtests",
        scope="the per-strategy backtest history Requirement 10 lists",
    ),
    Probe(
        key="backtests.list_filtered",
        method="GET",
        path="/api/backtests",
        handler=f"{_OPS}:list_backtests",
        query={"strategy_id": "{strategy_id}"},
        scope=(
            "the same listing reached with the strategy as a FILTER rather than a path "
            "segment - a distinct way to name another tenant's strategy"
        ),
    ),
    Probe(
        key="backtests.create",
        method="POST",
        path="/api/strategies/{strategy_id}/backtests",
        handler=f"{_OPS}:create_backtest",
        body={"version": 1, "blueprint": {}, "dataset": "seeded", **_WINDOW},
        scope=(
            "the backtest creation of Requirement 22.1's list. It carries an owner control "
            "because the handler now RESOLVES the strategy before building the row: the "
            "owner is answered 'created' and everyone else the same 404 a nonexistent "
            "strategy id gets. It did not, and that was this suite's Requirement 20.1 "
            "finding - see TestTheCreateBacktestFindingIsFixed"
        ),
    ),
    Probe(
        key="backtests.execute",
        method="POST",
        path="/api/strategy-operations/strategies/{strategy_id}/backtests/execute",
        handler=f"{_OPS}:execute_backtest",
        body=dict(_WINDOW),
        scope="REWRITTEN in task 6.1 - runs the version's own persisted plan",
    ),
    Probe(
        key="backtests.execute_sibling_path",
        method="POST",
        path="/api/strategies/{strategy_id}/backtests/execute",
        handler=f"{_OPS}:execute_backtest",
        body=dict(_WINDOW),
        scope=(
            "task 6.1 registers the same handler at a second path; both have to be "
            "probed, because 'the endpoint is safe' is a claim about every path that "
            "reaches it"
        ),
    ),
    Probe(
        key="backtests.get",
        method="GET",
        path="/api/backtests/{backtest_id}",
        handler=f"{_OPS}:get_backtest",
        scope="the backtest-result read Requirement 10.3 opens",
    ),
    Probe(
        key="backtests.report",
        method="GET",
        path="/api/backtests/{backtest_id}/report",
        handler=f"{_OPS}:get_backtest_report",
        scope="the backtest_result resource type Requirement 20.4 names explicitly",
    ),
    Probe(
        key="backtests.results",
        method="PUT",
        path="/api/backtests/{backtest_id}/results",
        handler=f"{_OPS}:update_backtest_results",
        body={
            "results": {
                # Values no seeded row carries, so if this write ever lands on the owner's
                # row the snapshot comparison in
                # TestARefusedRequestWritesNothing names them.
                "total_return_pct": -99.75,
                "win_rate": 0.0,
                "total_trades": 4242,
            }
        },
        scope=(
            "the backtest_result WRITE. Requirement 20.4 covers 'every read and write "
            "endpoint', and the backtest_result resource type by name; this is the endpoint "
            "that writes one, and it is the second half of the two-step save "
            "(POST .../backtests then PUT .../results) task 6.1 replaced but did not "
            "remove - test_task_6_1_backtest_execute.py pins both as still reachable, so "
            "both have to be probed. It filtered by backtest id ALONE, so a stranger's "
            "metrics landed on the owner's row and the response echoed it back: a "
            "cross-tenant write and an existence oracle in one"
        ),
    ),
    Probe(
        key="backtests.archive",
        method="DELETE",
        path="/api/backtests/{backtest_id}",
        handler=f"{_OPS}:delete_backtest",
        owner_control=False,
        owner_control_note=(
            "The handler answers {'success': true} unconditionally, for the owner and for "
            "a stranger alike, and DELIBERATELY so - forwarding the truthful boolean the "
            "service now returns would make the response a test for whether the caller "
            "owns the named row. The two answers therefore cannot differ and no control "
            "can be drawn from them. What IS asserted instead is the row and the service: "
            "TestDeleteBacktestReportsWhatItActuallyDeleted checks both halves, and "
            "test_the_whole_stranger_matrix_leaves_the_owners_rows_untouched proves the "
            "owner's backtest row survives the stranger's request."
        ),
        scope="the backtest delete/archive action of Requirement 22.1's list",
    ),
    # ══════════════════ deployments ══════════════════
    Probe(
        key="deployments.list",
        method="GET",
        path="/api/strategies/{strategy_id}/deployments",
        handler=f"{_OPS}:list_deployments",
        owner_control=False,
        owner_control_note=(
            "This handler reads the IN-PROCESS deployment registry, not the "
            "strategy_deployments table. No worker has attached in this environment, so "
            "the registry is empty for the owner too and both answers are the empty list. "
            "The equality Requirement 20.2 asks for holds; a positive control would need a "
            "running fleet, which this host does not have."
        ),
        scope="the deployment listing of Requirement 22.1's list",
    ),
    Probe(
        key="deployments.create",
        method="POST",
        path="/api/strategies/{strategy_id}/versions/{version}/deploy",
        handler=f"{_OPS}:deploy_version",
        scope="EXTENDED in task 8.2 - the versioned Deployment_Binding surface",
    ),
    Probe(
        key="deployments.create_preflight",
        method="GET",
        path="/api/strategy-operations/strategies/{strategy_id}/versions/{version}/deploy/preflight",
        handler=f"{_OPS}:preflight_deploy_version",
        scope="NEW in task 8.2 - the read-only preflight for the same binding",
    ),
    Probe(
        key="deployments.create_preflight_sibling_path",
        method="GET",
        path="/api/strategies/{strategy_id}/versions/{version}/deploy/preflight",
        handler=f"{_OPS}:preflight_deploy_version",
        scope="task 8.2's second registration of the preflight; both paths must refuse alike",
    ),
    Probe(
        key="deployments.get",
        method="GET",
        path="/api/deployments/{deployment_id}",
        handler=f"{_OPS}:get_deployment",
        scope="the deployment read task 8.3 re-pointed at the authoritative row",
    ),
    Probe(
        key="deployments.start",
        method="POST",
        path="/api/deployments/{deployment_id}/resume",
        handler=f"{_OPS}:resume_deployment",
        body={"reason": "started by a stranger"},
        scope="the start action of Requirement 13.8's four transitions (PAUSED -> RUNNING)",
    ),
    Probe(
        key="deployments.pause",
        method="POST",
        path="/api/deployments/{deployment_id}/pause",
        handler=f"{_OPS}:pause_deployment",
        body={"reason": "paused by a stranger"},
        scope="EXTENDED in task 8.3 - the gated, tenant-scoped pause",
    ),
    Probe(
        key="deployments.stop",
        method="POST",
        path="/api/deployments/{deployment_id}/stop",
        handler=f"{_OPS}:stop_deployment",
        body={"reason": "stopped by a stranger"},
        scope="EXTENDED in task 8.3 - the gated, tenant-scoped stop",
    ),
    Probe(
        key="deployments.restart",
        method="POST",
        path="/api/deployments/{deployment_id}/restart",
        handler=f"{_OPS}:restart_deployment",
        scope=(
            "not one of Requirement 13.8's four transitions, but it is a deployment write "
            "that takes a deployment id, so Requirement 20.4's 'every write endpoint' "
            "covers it"
        ),
    ),
    Probe(
        key="deployments.start_strategy_surface",
        method="POST",
        path="/api/strategies/{strategy_id}/resume",
        handler=f"{_STRATEGIES}:resume_strategy",
        scope="the strategy-level start surface the Strategies_Page still calls",
    ),
    Probe(
        key="deployments.pause_strategy_surface",
        method="POST",
        path="/api/strategies/{strategy_id}/pause",
        handler=f"{_STRATEGIES}:pause_strategy",
        scope="the strategy-level pause surface the Strategies_Page still calls",
    ),
    Probe(
        key="deployments.stop_strategy_surface",
        method="POST",
        path="/api/strategies/{strategy_id}/stop",
        handler=f"{_STRATEGIES}:stop_bot",
        scope=(
            "the strategy-level stop surface the Strategies_Page still calls. Its owner "
            "control is a 500 rather than a 200: the owner's request gets PAST the "
            "ownership read (which is the point of the control - the row was found) and "
            "then reaches the fleet/notification seam, which cannot complete on a host "
            "with no worker fleet. A stranger never gets that far, which is exactly the "
            "distinction the control is asserting."
        ),
    ),
    # ══════════════════ signal trace ══════════════════
    Probe(
        key="signal_trace.list",
        method="GET",
        path="/api/signal-trace/signals",
        handler=f"{_TRACE}:list_signals",
        query={"strategy_id": "{strategy_id}"},
        scope="NEW in task 13.1 - GET /api/signal-trace/signals, filtered by strategy",
    ),
    Probe(
        key="signal_trace.list_by_deployment",
        method="GET",
        path="/api/signal-trace/signals",
        handler=f"{_TRACE}:list_signals",
        query={"deployment_id": "{deployment_id}"},
        scope="the same list naming another tenant's DEPLOYMENT instead of their strategy",
    ),
    Probe(
        key="signal_trace.get",
        method="GET",
        path="/api/signal-trace/signals/{signal_id}",
        handler=f"{_TRACE}:get_signal",
        scope="EXTENDED in task 13.2 - the full per-signal trace detail",
    ),
    Probe(
        key="signal_trace.timeline",
        method="GET",
        path="/api/signal-trace/signals/{signal_id}/timeline",
        handler=f"{_TRACE}:get_signal_timeline",
        scope=(
            "the per-signal timeline the Signal_Trace_Page renders alongside the trace. "
            "Note that this endpoint answers 200 + an empty timeline for a signal that is "
            "not the caller's, where the trace detail next door answers 404 - two "
            "different conventions for the same fact. Both are indistinguishable from "
            "non-existence, which is all Requirement 20.2 asks."
        ),
    ),
    Probe(
        key="signal_trace.export",
        method="GET",
        path="/api/signal-trace/signals/export",
        handler=f"{_TRACE}:export_signals",
        query={"strategy_id": "{strategy_id}", "format": "json"},
        scope="NEW in task 13.x - the export of whatever the filtered list is showing",
    ),
)


# ══════════════════════════════════════════════════════════════════════════
# 3. RESOLVING A PATH AGAINST THE REAL APP
# ══════════════════════════════════════════════════════════════════════════


def resolve(method: str, url_path: str):
    """Every route on the real app that fully matches ``method url_path``, in match order.

    Starlette's own matcher over ``app.routes``, so this is the resolution the server would
    perform - not a lookup in a router assembled for the test. First match wins, which is
    what makes shadowing observable. The idiom is
    ``tests/test_task_8_2_deploy_preflight.py``'s, which established it for the same hazard.
    """
    scope = {
        "type": "http",
        "method": method,
        "path": url_path,
        "path_params": {},
        "headers": [],
        "root_path": "",
        "query_string": b"",
    }
    return [route for route in app.routes if route.matches(scope)[0] == Match.FULL]


def url_for(probe: Probe, ids: Mapping[str, str]) -> str:
    return probe.path.format(**ids)


def query_for(probe: Probe, ids: Mapping[str, str]) -> Dict[str, str]:
    return {name: value.format(**ids) for name, value in probe.query.items()}


# ══════════════════════════════════════════════════════════════════════════
# 4. WHAT "IDENTICAL" MEANS, MECHANICALLY
# ══════════════════════════════════════════════════════════════════════════

_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?")

#: ``main.SecurityHeadersMiddleware`` mints a fresh ``secrets.token_urlsafe(16)`` per request
#: and embeds it in ``Content-Security-Policy``'s ``script-src``. It is a per-REQUEST value,
#: not a per-tenant or per-resource one, so masking it is not masking anything about
#: existence - and the rest of the CSP header is still compared, character for character.
#: Found by this suite: without this mask every probe fails on the header comparison.
_CSP_NONCE = re.compile(r"nonce-[A-Za-z0-9_\-]{8,}")

#: ``asgi_correlation_id.CorrelationIdMiddleware`` (mounted in ``main.py``) stamps an
#: ``X-Request-ID`` of ``uuid4().hex`` - 32 hex characters, no dashes - on every response.
#: Per-request, like the CSP nonce, and masked for the same reason. Also found by this
#: suite: it is the second of the two headers that made every probe differ.
_CORRELATION_ID = re.compile(r"\b[0-9a-f]{32}\b")

#: The identifiers that actually DIFFER between the two requests, masked by one common
#: placeholder so "the caller's own id, echoed back" is not counted as a difference.
#:
#: Derived rather than listed, and deliberately excluding anything the two mappings agree
#: on: ``version`` is the same label (``"v1"``) in both, so it is identical in both
#: responses already and masking it would mask nothing - while a two-character needle
#: applied to whole response bodies collides with unrelated text. It did: ``"v1"`` occurred
#: inside a CSP nonce, the nonce mask then failed to match the mangled token, and every
#: probe failed intermittently on the header comparison. Hence
#: :func:`test_no_probed_identifier_is_short_enough_to_collide`.
_PROBED_IDS = tuple(
    sorted(
        {
            value
            for name, value in (*OWNED_IDS.items(), *MISSING_IDS.items())
            if OWNED_IDS[name] != MISSING_IDS[name]
        },
        key=len,
        reverse=True,
    )
)

#: Headers that differ between any two requests for reasons no tenant controls.
_VOLATILE_HEADERS = frozenset({"date"})


def normalise(value: Any) -> Any:
    """``value`` with the caller's own identifiers, foreign UUIDs and timestamps masked.

    Recursive, so a nested body is normalised at every depth and no key is skipped. Applied
    to header values as well as to the body - an endpoint that put a resource id in a header
    would otherwise escape the comparison.
    """
    if isinstance(value, str):
        # The per-request values first, so a randomly generated token cannot be mangled by
        # an identifier substitution and then fail to match its own pattern.
        text = _CSP_NONCE.sub("nonce-<PER-REQUEST-NONCE>", value)
        text = _CORRELATION_ID.sub("<PER-REQUEST-CORRELATION-ID>", text)
        for identifier in _PROBED_IDS:
            text = text.replace(identifier, "<PROBED-ID>")
        text = _TIMESTAMP.sub("<TIMESTAMP>", text)
        text = _UUID.sub("<MINTED-UUID>", text)
        return text
    if isinstance(value, Mapping):
        return {normalise(k): normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalise(item) for item in value]
    return value


def observable(response) -> Dict[str, Any]:
    """Everything a caller can see, normalised: the status, the headers and the body.

    The body is compared as decoded JSON where it is JSON and as text otherwise (the export
    endpoint answers CSV), so the comparison is structural rather than a string diff of
    whitespace.
    """
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return {
        "status": response.status_code,
        "headers": {
            name.lower(): normalise(value)
            for name, value in response.headers.items()
            if name.lower() not in _VOLATILE_HEADERS
        },
        "body": normalise(body),
    }


def ask(client, probe: Probe, ids: Mapping[str, str]):
    return client.request(
        probe.method,
        url_for(probe, ids),
        params=query_for(probe, ids),
        json=dict(probe.body) if probe.body is not None else None,
    )


@pytest.fixture
def owned(sandbox: SandboxWorld) -> SandboxWorld:
    """The owner's five resources, seeded, plus the vault and risk rows task 21.1 seeds.

    ADDITIVE, task 34.4: the owner also holds one Paper_Session and a row in every table
    ``009_paper_trading.sql`` gives it - ``paper_sessions``, ``paper_accounts``, ``paper_orders``,
    ``paper_fills``, ``paper_positions``, ``paper_events``, ``paper_equity_snapshots``. Nothing
    that was asserted about the five original resources changes: those rows are seeded exactly as
    before, by the same call, and the ``paper_*`` rows are additional rows in additional tables.
    What they add is reach for the two assertions below that were previously blind to them -
    :func:`test_the_whole_stranger_matrix_leaves_the_owners_rows_untouched` and
    :func:`test_another_tenants_rows_are_unobservable_on_the_unfiltered_collections` - plus
    :func:`test_the_stranger_matrix_never_names_the_owners_paper_session_rows`.
    """
    sandbox.seed_cross_tenant_rows()
    sandbox.seed_paper_session_rows()
    return sandbox


def _ids(probe: Probe) -> None:
    """Guard: a probe whose path names a placeholder neither mapping defines is a typo."""
    probe.path.format(**OWNED_IDS)


# ══════════════════════════════════════════════════════════════════════════
# 5. THE SURFACE IS REAL
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("probe", ENDPOINT_MATRIX, ids=lambda p: p.key)
class TestEveryProbedPathResolvesToTheHandlerItNames:
    """Without this class the whole matrix could pass on paths that serve nothing."""

    def test_the_real_app_resolves_it(self, probe: Probe):
        _ids(probe)
        matched = resolve(probe.method, url_for(probe, OWNED_IDS))
        assert matched, (
            f"{probe.key}: {probe.method} {probe.path} resolves to NO route on the real "
            "app, so probing it would assert nothing. Either the path is wrong or the "
            "endpoint is gone."
        )

    def test_it_is_served_by_the_handler_this_probe_names(self, probe: Probe):
        """First-match-wins, so the first match is the handler that will answer."""
        matched = resolve(probe.method, url_for(probe, OWNED_IDS))
        served_by = matched[0].endpoint
        served_by_name = f"{served_by.__module__}:{getattr(served_by, '__name__', served_by)}"
        assert served_by_name == probe.handler, (
            f"{probe.key}: {probe.method} {probe.path} is served by {served_by_name}, not "
            f"by {probe.handler}. A shadowed route cannot pass as tested - fix the probe, "
            "or fix the mounting order."
        )

    def test_the_registration_that_answers_is_the_one_this_probe_names(self, probe: Probe):
        """Pins WHICH registration answered, where a module declares one path twice.

        ``strategy_operations.py`` declares ``DELETE /backtests/{id}`` and
        ``POST /deployments/{id}/stop`` twice each, and holds two different handlers both
        named ``list_backtests``. The handler name alone cannot tell those apart; the
        matched route's own ``path`` can.
        """
        matched = resolve(probe.method, url_for(probe, OWNED_IDS))
        assert matched[0].path == probe.path, (
            f"{probe.key}: the registration that answers {probe.method} "
            f"{url_for(probe, OWNED_IDS)} is declared at {matched[0].path}, not at "
            f"{probe.path}."
        )

    def test_a_missing_identifier_resolves_to_the_same_handler(self, probe: Probe):
        """The two halves of the comparison must reach the same code to be comparable."""
        owned_match = resolve(probe.method, url_for(probe, OWNED_IDS))
        missing_match = resolve(probe.method, url_for(probe, MISSING_IDS))
        assert missing_match, f"{probe.key}: the missing-id URL resolves to no route"
        assert owned_match[0].endpoint is missing_match[0].endpoint


# ══════════════════════════════════════════════════════════════════════════
# 6. THE MATRIX ITSELF (Requirement 20.4)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("probe", ENDPOINT_MATRIX, ids=lambda p: p.key)
def test_a_non_owner_gets_the_identical_non_existence_response(
    probe: Probe, owned: SandboxWorld, stranger_client
):
    """Requirement 20.4, the whole point of this file.

    Two requests, one authenticated stranger, one running app. The only difference between
    them is whether the identifier in the URL names a row that exists. Every observable part
    of the answer - status, headers, body - must be the same.
    """
    foreign = observable(ask(stranger_client, probe, OWNED_IDS))
    absent = observable(ask(stranger_client, probe, MISSING_IDS))

    assert foreign["status"] == absent["status"], (
        f"{probe.key}: naming ANOTHER TENANT'S resource answers "
        f"{foreign['status']} while naming a nonexistent one answers {absent['status']}. "
        "The status code alone tells a stranger the resource exists (Requirement 20.2)."
    )
    assert foreign["headers"] == absent["headers"], (
        f"{probe.key}: the two answers differ in their HEADERS, so existence leaks even "
        "though the bodies match."
    )
    assert foreign["body"] == absent["body"], (
        f"{probe.key}: the two answers share a status but differ in their BODIES. A "
        "stranger can distinguish 'not yours' from 'not there' (Requirement 20.2).\n"
        f"  another tenant's id -> {foreign['body']!r}\n"
        f"  nonexistent id      -> {absent['body']!r}"
    )


@pytest.mark.parametrize(
    "probe",
    [p for p in ENDPOINT_MATRIX if p.owner_control],
    ids=lambda p: p.key,
)
def test_the_owner_is_answered_differently_so_the_refusal_was_not_vacuous(
    probe: Probe, owned: SandboxWorld, client
):
    """The positive control. An endpoint that refuses everybody proves nothing.

    The OWNER makes the identical request for the identical identifier, and the answer must
    differ from the missing-identifier answer. That is what establishes that the seeded row
    is reachable through this endpoint - and therefore that the equality asserted above is
    an equality between two refusals about something real, not two 404s from a dead route.
    """
    as_owner = observable(ask(client, probe, OWNED_IDS))
    as_owner_missing = observable(ask(client, probe, MISSING_IDS))

    assert as_owner != as_owner_missing, (
        f"{probe.key}: the OWNER gets the same answer for their own resource as for an "
        f"identifier that names nothing ({as_owner['status']}, {as_owner['body']!r}). The "
        "cross-tenant assertion for this endpoint is therefore vacuous: it is not "
        "measuring an ownership check, it is measuring an endpoint that answers "
        "'not found' to everybody. Either the seeded row is not reachable here, or this "
        "probe needs owner_control=False with a stated reason."
    )


def test_no_probed_identifier_is_short_enough_to_collide():
    """A masked needle is applied to whole bodies and headers, so it has to be specific.

    Guards the failure mode that actually happened while this file was being written: a
    short identifier (``"v1"``) matched inside a randomly generated CSP nonce, which broke
    the nonce mask and made every probe fail on one run in three. 16 characters is well
    past the length at which a hex/urlsafe token collision stops being credible.
    """
    for identifier in _PROBED_IDS:
        assert len(identifier) >= 16, (
            f"{identifier!r} is too short to be masked safely in a whole response body. "
            "Either make the seeded identifier longer, or make it equal in OWNED_IDS and "
            "MISSING_IDS so it needs no mask at all."
        )


def test_every_probe_without_an_owner_control_states_why():
    """``owner_control=False`` is an admission, so it has to be argued in the file."""
    for probe in ENDPOINT_MATRIX:
        if not probe.owner_control:
            assert len(probe.owner_control_note) > 80, (
                f"{probe.key} opts out of the positive control without explaining why. "
                "An unexplained opt-out is how a vacuous assertion survives review."
            )


# ══════════════════════════════════════════════════════════════════════════
# 7. THE COLLECTIONS THAT TAKE NO IDENTIFIER
# ══════════════════════════════════════════════════════════════════════════
#
# ``GET /api/strategies`` (and its ``?include_archived`` variant, which task 5.2 added) name
# no resource, so they have no "nonexistent id" counterpart and cannot join the matrix
# above. The equality Requirement 20.2 asks for still has a form here, and it is the
# strongest one available: the stranger's answer must be the same whether the owner's rows
# are in the database or not. If the presence of another tenant's data is unobservable, the
# listing cannot be used to confirm it exists.

ID_LESS_COLLECTIONS: Tuple[Tuple[str, str, Dict[str, str]], ...] = (
    ("strategy.list", "/api/strategies", {}),
    ("strategy.list_include_archived", "/api/strategies", {"include_archived": "true"}),
    ("backtests.list_all", "/api/backtests", {}),
    ("signal_trace.list_unfiltered", "/api/signal-trace/signals", {}),
    ("signal_trace.export_unfiltered", "/api/signal-trace/signals/export", {"format": "json"}),
)


@pytest.mark.parametrize(
    "key,path,params", ID_LESS_COLLECTIONS, ids=[cell[0] for cell in ID_LESS_COLLECTIONS]
)
def test_another_tenants_rows_are_unobservable_on_the_unfiltered_collections(
    key: str, path: str, params: Dict[str, str], owned: SandboxWorld, stranger_client
):
    """Requirement 20.4 for the endpoints that name no identifier.

    Asked twice: once with the owner's five rows present, once with the tables emptied of
    them. The stranger's answer must be byte-identical, which is a stronger statement than
    "the response does not contain the owner's id" - it also rules out a count, a total, a
    pagination hint or a header that changes with somebody else's data.
    """
    assert resolve("GET", path), f"{key}: {path} resolves to no route"

    with_owner_rows = observable(stranger_client.get(path, params=params))

    snapshot = {table: list(rows) for table, rows in owned.db.tables.items()}
    # ``PAPER_SESSION_OWNED_TABLES`` is appended (task 34.4): the owner now holds a
    # Paper_Session and its six children, so "with the owner's rows" and "without them" have to
    # differ in those rows too, or a collection that reported somebody else's session count
    # would answer identically either way and pass.
    for table in ("strategies", "strategy_versions", "strategy_backtests",
                  "strategy_deployments", "signals") + PAPER_SESSION_OWNED_TABLES:
        owned.db.tables[table] = []
    try:
        without_owner_rows = observable(stranger_client.get(path, params=params))
    finally:
        owned.db.tables.update(snapshot)

    assert with_owner_rows == without_owner_rows, (
        f"{key}: a stranger's answer CHANGES depending on whether another tenant's rows "
        "exist, so this collection can be used to confirm they do (Requirement 20.2).\n"
        f"  with the owner's rows -> {with_owner_rows!r}\n"
        f"  without them          -> {without_owner_rows!r}"
    )

    for identifier in OWNED_IDS.values():
        if identifier == OWNED_VERSION_LABEL:
            continue  # "v1" is not an identifier a stranger could learn anything from.
        assert identifier not in str(with_owner_rows), (
            f"{key}: the owner's identifier {identifier} appears in a stranger's response."
        )


# ══════════════════════════════════════════════════════════════════════════
# 8. A REFUSED REQUEST WRITES NOTHING
# ══════════════════════════════════════════════════════════════════════════


def test_the_whole_stranger_matrix_leaves_the_owners_rows_untouched(
    owned: SandboxWorld, stranger_client
):
    """Requirement 20.1: a non-owner may not modify or delete another user's resource.

    Every write probe in the matrix - update, rename, archive, duplicate, execute, deploy,
    start, pause, stop, restart - is fired at the owner's identifiers, and then every row of
    every table is compared with the snapshot taken beforehand. Measured on the ROWS rather
    than on a call count, because "the request was refused" and "the request was applied and
    then reported as refused" look the same from the outside.

    NOTHING IS EXEMPT ANY MORE
        ``backtests.create`` used to be excluded, and that exclusion was the finding: it was
        the one probe here that was NOT refused, because the handler never resolved the
        strategy it was given. It resolves it now, so it takes part like every other write
        and its refusal is measured on the rows here rather than argued in a docstring.
        ``backtests.results`` - the other write that used to land on the owner's row -
        is in the matrix and therefore in this loop too.

    THE PAPER_SESSION-OWNED TABLES ARE WATCHED TOO (task 34.4)
        The owner holds a Paper_Session and a row in each of ``paper_sessions``,
        ``paper_accounts``, ``paper_orders``, ``paper_fills``, ``paper_positions``,
        ``paper_events`` and ``paper_equity_snapshots``. They are appended to ``watched`` rather
        than replacing anything, so every table this test already covered is still covered
        byte-for-byte. The reach that adds is real: a strategy stop, a deployment stop or a
        signal export that cascaded into another tenant's simulated orders, fills or events would
        have been invisible here before, because the tables held no row to change.
    """
    watched = (
        "strategies",
        "strategy_versions",
        "strategy_backtests",
        "strategy_deployments",
        "signals",
        "exchange_keys",
        "risk_settings",
    ) + PAPER_SESSION_OWNED_TABLES
    before = {table: [dict(row) for row in owned.db.rows(table)] for table in watched}

    for probe in ENDPOINT_MATRIX:
        ask(stranger_client, probe, OWNED_IDS)

    after = {table: [dict(row) for row in owned.db.rows(table)] for table in watched}

    for table in watched:
        assert after[table] == before[table], (
            f"the stranger's requests CHANGED {table}. A refused request must leave every "
            f"previously committed row exactly as it was (Requirements 20.1, 21.7).\n"
            f"  before -> {before[table]!r}\n"
            f"  after  -> {after[table]!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 8b. THE OWNER'S PAPER_SESSION IS UNOBSERVABLE TO A STRANGER (task 34.4)
# ══════════════════════════════════════════════════════════════════════════
#
# The paragraph above measures that the stranger's matrix CHANGED no Paper_Session-owned row.
# This is the other half of Requirement 21.8's third assertion over the same requests: no
# identifier of the owner's Paper_Session, Paper_Account or paper order may appear in any answer
# the stranger receives from any endpoint of the matrix.
#
# WHY THE /api/paper/sessions/* ENDPOINTS THEMSELVES ARE NOT PROBED HERE
#     They are, and thoroughly - in ``tests/test_tenant_isolation_library_paper.py``, over the
#     ONE Persistence_Layer double that implements ``009_paper_trading.sql``'s unique indexes,
#     column defaults and UPDATE triggers. ``SandboxDatabase`` implements none of those (it is
#     this package's store for the ``001``/``003``/``005b`` surface and answers ``execute()``
#     asynchronously, which ``paper_repository`` does not call), so probing the paper routes here
#     would have meant either teaching it 009's semantics - a SECOND Persistence_Layer double for
#     the paper tables, which ``tests/paper_seed.py`` records as the thing this repository has
#     exactly one of - or asserting an equality between two 503s that never reach the tenant
#     boundary. What belongs here is the claim this package is for: that the owner's paper rows
#     exist, and that the strategy, backtest, deployment and signal-trace surface neither changes
#     them nor mentions them.


def test_the_stranger_matrix_never_names_the_owners_paper_session_rows(
    owned: SandboxWorld, stranger_client
):
    """Requirement 21.8: no answer to the stranger carries a Paper_Session identifier of the owner's.

    Every probe of the matrix is fired at the owner's identifiers as the stranger, and the whole
    observable answer - status, headers and body - is searched for the owner's Paper_Session,
    Paper_Account and paper order identifiers. Searched over the RAW text rather than over parsed
    keys, so an identifier embedded in a message, a URL or a serialised blob is caught too.

    The premise is asserted first: the rows really are in the database while the stranger is being
    answered. A version of this test that ran against empty ``paper_*`` tables would pass without
    measuring anything, which is exactly what it did before task 34.4 seeded them.
    """
    for table in PAPER_SESSION_OWNED_TABLES:
        assert owned.db.rows(table), (
            f"{table} holds no row, so 'the stranger never saw the owner's Paper_Session' would "
            f"be true of a database that has no Paper_Session in it. "
            f"SandboxWorld.seed_paper_session_rows is the premise this test needs."
        )

    forbidden = (OWNED_PAPER_SESSION_ID, OWNED_PAPER_ACCOUNT_ID, OWNED_PAPER_ORDER_ID)

    leaks = []
    for probe in ENDPOINT_MATRIX:
        response = ask(stranger_client, probe, OWNED_IDS)
        seen = f"{response.status_code} {dict(response.headers)!r} {response.text}"
        for identifier in forbidden:
            if identifier in seen:
                leaks.append(f"{probe.key} carried {identifier!r}")

    assert not leaks, (
        f"a stranger's answer names the owner's Paper_Session rows: {leaks}. Requirement 21.8 "
        f"requires that no field value belonging to the second account appears in the response."
    )


# ══════════════════════════════════════════════════════════════════════════
# 9. THE MANDATED CELLS ARE ALL COVERED
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 20.4's matrix as ``tasks.md`` states it, verbatim in structure.
MANDATED_CELLS: Dict[str, Tuple[str, ...]] = {
    "strategy": ("list", "get", "versions", "update", "archive", "duplicate"),
    "backtests": ("list", "create", "get", "archive"),
    "deployments": ("list", "create", "get", "start", "pause", "stop"),
    "signal_trace": ("list", "get"),
}


class TestTheMatrixCoversTheMandatedCells:
    """The enumeration is maintained by hand, so something has to check it is complete."""

    def _covered(self) -> Dict[str, set]:
        covered: Dict[str, set] = {axis: set() for axis in MANDATED_CELLS}
        keys = [p.key for p in ENDPOINT_MATRIX] + [cell[0] for cell in ID_LESS_COLLECTIONS]
        for key in keys:
            axis, _, action = key.partition(".")
            if axis in covered:
                # ``strategy.list_include_archived`` covers ``list``; the suffix marks a
                # variant of the same cell, not a different one.
                covered[axis].add(action.split("_")[0])
        return covered

    @pytest.mark.parametrize("axis", sorted(MANDATED_CELLS))
    def test_every_action_on_this_axis_has_a_probe(self, axis: str):
        missing = set(MANDATED_CELLS[axis]) - self._covered()[axis]
        assert not missing, (
            f"Requirement 20.4's matrix names {sorted(missing)} on the '{axis}' axis and "
            "no probe covers it. The enumeration is the deliverable - a missing cell is a "
            "missing test, not a missing comment."
        )

    def test_every_probe_key_names_a_cell_of_the_matrix(self):
        for probe in ENDPOINT_MATRIX:
            axis = probe.key.partition(".")[0]
            assert axis in MANDATED_CELLS, (
                f"{probe.key}: '{axis}' is not one of Requirement 20.4's four axes."
            )

    def test_every_probe_states_why_it_is_in_scope(self):
        """Requirement 20.4 is scoped to what this specification adds or extends."""
        for probe in ENDPOINT_MATRIX:
            assert probe.scope, f"{probe.key} does not say why it is in this specification's scope"

    def test_no_two_probes_share_a_key(self):
        keys = [p.key for p in ENDPOINT_MATRIX]
        assert len(keys) == len(set(keys)), "a duplicated key would hide one of the probes"


# ══════════════════════════════════════════════════════════════════════════
# 10. THE THREE REQUIREMENT 20.1 DEFECTS THIS SUITE FOUND, ASSERTED AS FIXED
# ══════════════════════════════════════════════════════════════════════════
#
# The matrix above measures indistinguishability (Requirement 20.2). Requirement 20.1 is a
# stronger and separate claim - "no endpoint SHALL permit a user to read, modify, delete, or
# reference ... another user's resource" - and three sites on the backtest surface failed it
# while satisfying 20.2, which is exactly why they needed tests of their own:
#
#   1. ``POST /api/strategies/{id}/backtests`` never resolved the strategy it was given, so
#      a stranger could persist a ``strategy_backtests`` row referencing another tenant's
#      ``strategy_id``. (It answered every caller "created", so the matrix passed it.)
#   2. ``PUT /api/backtests/{id}/results`` filtered by ``id`` alone, so a stranger's metrics
#      landed on the owner's row and the response echoed it back.
#   3. ``BacktestService.delete_backtest`` returned ``True`` unconditionally, reporting
#      success for a row it had not touched.
#
# Each is now asserted in the positive direction: the refusal, and the absence of the write.


class TestTheCreateBacktestFindingIsFixed:
    """Requirement 20.1 for ``POST /api/strategies/{strategy_id}/backtests``.

    The handler resolves the strategy through the ownership-scoped
    ``StrategyService.get_strategy`` (``id`` AND ``user_id``) before ``BacktestService.
    create_backtest`` builds a row from the path parameter. A caller who does not own the
    named strategy is refused with the SAME 404 ``STRATEGY_NOT_FOUND`` a strategy id that
    names nothing receives - the shape ``execute_backtest`` and ``strategy_archive`` already
    use - so Requirement 20.2 still holds while 20.1 is enforced.
    """

    def _probe(self) -> Probe:
        return next(p for p in ENDPOINT_MATRIX if p.key == "backtests.create")

    def test_a_stranger_naming_the_owners_strategy_is_refused(
        self, owned: SandboxWorld, stranger_client
    ):
        response = ask(stranger_client, self._probe(), OWNED_IDS)

        assert response.status_code == 404, (
            "a stranger may not create a backtest against another tenant's strategy "
            f"(Requirement 20.1); the endpoint answered {response.status_code}: "
            f"{response.text}"
        )
        assert response.json()["detail"]["error"] == "STRATEGY_NOT_FOUND", (
            "the refusal must be the established non-existence answer, not a new code "
            "that would itself distinguish 'not yours' from 'not there' (Requirement 20.2)"
        )

    def test_the_refusal_writes_no_row(self, owned: SandboxWorld, stranger_client):
        """The point of the fix: no ``strategy_backtests`` row referencing a foreign strategy.

        Measured on the rows, not on the status code - "refused" and "applied, then reported
        as refused" are indistinguishable from outside.
        """
        before = owned.db.rows("strategy_backtests")

        ask(stranger_client, self._probe(), OWNED_IDS)

        after = owned.db.rows("strategy_backtests")
        assert after == before, (
            "the stranger's create CHANGED strategy_backtests. A refused request must "
            f"leave every row as it was.\n  before -> {before!r}\n  after  -> {after!r}"
        )
        foreign = [
            row
            for row in after
            if str(row.get("strategy_id")) == OWNED_STRATEGY_ID
            and str(row.get("user_id")) != owned.user["id"]
        ]
        assert not foreign, (
            "a strategy_backtests row references the owner's strategy under another "
            f"tenant's user_id: {foreign!r} (Requirement 20.1's 'reference ... by naming "
            "it as a related entity in a request body')"
        )

    def test_the_owner_can_still_create_one_so_the_refusal_is_not_blanket(
        self, owned: SandboxWorld, client
    ):
        """Falsifiability: an endpoint that refused everybody would pass the two above."""
        before = len(owned.db.rows("strategy_backtests"))

        response = ask(client, self._probe(), OWNED_IDS)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "created"
        rows = owned.db.rows("strategy_backtests")
        assert len(rows) == before + 1
        mine = [
            row
            for row in rows
            if str(row.get("strategy_id")) == OWNED_STRATEGY_ID
            and str(row.get("user_id")) == owned.user["id"]
        ]
        assert mine, "the owner's own backtest row was not written"


class TestTheResultsWriteIsScopedToItsOwner:
    """Requirement 20.1 for ``PUT /api/backtests/{backtest_id}/results``.

    ``BacktestService.update_backtest_results`` scopes its UPDATE by ``user_id`` as well as
    by ``id``. The equality Requirement 20.2 asks for is asserted by the matrix probe
    ``backtests.results``; what is asserted here is the row the write did NOT reach.
    """

    def _probe(self) -> Probe:
        return next(p for p in ENDPOINT_MATRIX if p.key == "backtests.results")

    def test_a_strangers_metrics_do_not_land_on_the_owners_row(
        self, owned: SandboxWorld, stranger_client
    ):
        before = owned.db.row("strategy_backtests", OWNED_BACKTEST_ID)
        assert before is not None, "the seeded row must exist for this to mean anything"

        response = ask(stranger_client, self._probe(), OWNED_IDS)

        assert response.status_code == 404, (
            f"a stranger's results write was not refused: {response.status_code} "
            f"{response.text}"
        )
        after = owned.db.row("strategy_backtests", OWNED_BACKTEST_ID)
        assert after == before, (
            "the stranger's PUT overwrote the owner's backtest row - a cross-tenant WRITE "
            f"(Requirement 20.1).\n  before -> {before!r}\n  after  -> {after!r}"
        )

    def test_the_response_does_not_echo_the_row_it_refused_to_write(
        self, owned: SandboxWorld, stranger_client
    ):
        """The oracle half: the handler used to return the row it had just overwritten."""
        response = ask(stranger_client, self._probe(), OWNED_IDS)

        # ``OWNED_BACKTEST_ID`` is excluded on purpose: the caller supplied it in the URL,
        # so echoing it back tells them nothing they did not already know. Every OTHER
        # field of the row is a fact about the owner's data.
        body = response.text
        assert OWNED_STRATEGY_ID not in body and OWNED_VERSION_ID not in body, (
            "the refusal carries fields off the owner's row, so a stranger learns it "
            f"exists (Requirement 20.2): {body!r}"
        )

    def test_the_owner_can_still_write_results_so_the_refusal_is_not_blanket(
        self, owned: SandboxWorld, client
    ):
        response = ask(client, self._probe(), OWNED_IDS)

        assert response.status_code == 200, response.text
        row = owned.db.row("strategy_backtests", OWNED_BACKTEST_ID) or {}
        assert row["total_trades"] == 4242, "the owner's own results were not persisted"
        assert row["status"] == "completed"


class TestDeleteBacktestReportsWhatItActuallyDeleted:
    """Requirement 20.1 for ``BacktestService.delete_backtest``, and the line it must not cross.

    The method used to ``return True`` below the DELETE without reading it, so it reported
    success for a row it had not touched. It reports the truth now.

    The HTTP endpoint deliberately does NOT forward that value: ``{"success": true}`` for
    every caller is what keeps "not yours" and "not there" indistinguishable
    (Requirement 20.2). Both halves are asserted here, together, because the two
    obligations pull in opposite directions and a future change that "tidied up" the handler
    to echo the service would reintroduce an oracle.
    """

    def _service(self):
        from backend_app.backend.backtest_service import BacktestService

        # ``conftest.py`` patches ``create_request_supabase_async`` at this module, so the
        # service's own client seam already returns the sandbox database.
        return BacktestService()

    def test_the_service_says_false_when_the_scoped_delete_matched_nothing(
        self, owned: SandboxWorld
    ):
        import asyncio

        from tests.sandbox_lifecycle.harness import STRANGER_USER

        service = self._service()

        not_there = asyncio.run(
            service.delete_backtest(user=dict(owned.user), backtest_id=MISSING_BACKTEST_ID)
        )
        not_mine = asyncio.run(
            service.delete_backtest(user=dict(STRANGER_USER), backtest_id=OWNED_BACKTEST_ID)
        )

        assert not_there is False, "a nonexistent backtest was reported as deleted"
        assert not_mine is False, "another tenant's backtest was reported as deleted"
        assert owned.db.row("strategy_backtests", OWNED_BACKTEST_ID) is not None, (
            "the stranger's DELETE removed the owner's row (Requirement 20.1)"
        )

    def test_the_service_says_true_when_it_really_deleted_the_row(self, owned: SandboxWorld):
        import asyncio

        service = self._service()

        deleted = asyncio.run(
            service.delete_backtest(user=dict(owned.user), backtest_id=OWNED_BACKTEST_ID)
        )

        assert deleted is True
        assert owned.db.row("strategy_backtests", OWNED_BACKTEST_ID) is None

    def test_the_http_layer_answers_the_same_thing_to_everybody(
        self, owned: SandboxWorld, client, stranger_client
    ):
        """Requirement 20.2 over the truthful service: the wire answer is a constant.

        Three requests - the owner deleting their own row, a stranger naming it, and anyone
        naming an id that exists nowhere - and one answer. If this ever starts forwarding
        the service's boolean, the owner's ``true`` beside a stranger's ``false`` becomes a
        test for whether the caller owns the named backtest.
        """
        probe = next(p for p in ENDPOINT_MATRIX if p.key == "backtests.archive")

        stranger_on_owners_row = observable(ask(stranger_client, probe, OWNED_IDS))
        stranger_on_nothing = observable(ask(stranger_client, probe, MISSING_IDS))
        owner_on_nothing = observable(ask(client, probe, MISSING_IDS))
        owner_on_own_row = observable(ask(client, probe, OWNED_IDS))

        assert stranger_on_owners_row == stranger_on_nothing
        assert owner_on_own_row["body"] == owner_on_nothing["body"] == {"success": True}
        assert owner_on_own_row["body"] == stranger_on_owners_row["body"], (
            "the delete endpoint now distinguishes a caller who owns the named backtest "
            "from one who does not (Requirement 20.2)"
        )
