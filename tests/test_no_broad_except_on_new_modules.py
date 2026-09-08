"""The guard against a broad ``except`` that swallows a failure on a new module.

Feature: marketplace-subscriptions-paper-trading, task 33.6.
Requirements 30.2, 30.5. Requirement 30.5 is the one this file enforces: "THE Implementation
SHALL NOT catch a broad exception on a correctness-critical path without re-raising or returning
a defined error outcome."

WHAT IS AUDITED
---------------
Every ``.py`` file under ``backend_app/backend/marketplace/`` and ``backend_app/backend/paper/``,
found by a glob at collection time so a module added to either package is audited without editing
this file. Nothing is imported: the whole audit is ``ast`` over source text, so no FastAPI app, no
supabase client and no event loop is constructed.

THE RULE, STATED EXACTLY
------------------------
A handler is **broad** when its ``except`` clause is bare (``except:``), names ``Exception`` or
``BaseException``, or is a tuple one of whose members is ``Exception`` or ``BaseException``
(``except (Exception, TimeoutError):`` is broad; ``except (ValueError, KeyError):`` is not).

A broad handler is a **violation** unless its body contains a **propagating** ``raise``. "Contains"
is not a plain subtree search, because a ``raise`` can be written inside the handler and still never
leave it. A ``raise`` counts as propagating exactly when it is reachable from the handler body
without being enclosed by one of these three constructs:

1. the ``body`` of a nested ``try`` at least one of whose own handlers does not itself propagate -
   that nested handler can catch the ``raise`` and drop it, so it earns no credit. The rule is
   applied recursively: a nested ``try`` whose every handler re-raises cannot swallow, so a
   ``raise`` in its body **does** count. A ``raise`` written in a nested handler, ``else`` or
   ``finally`` block always counts, because no handler of that ``try`` guards those positions;
2. the body of a ``with contextlib.suppress(...)`` block - ``suppress`` is a ``try``/``except``/
   ``pass`` spelled as a context manager, and it swallows exactly as one;
3. a nested ``def``, ``async def``, ``lambda`` or ``class`` body - that code does not run when the
   handler runs, so a ``raise`` inside it does not leave this handler.

A conditional ``raise`` - one inside an ``if`` - **does** count. That is deliberate: translating
some failures and handling the rest is the normal shape of an error boundary, and a rule that
demanded an unconditional ``raise`` would reject every ``if is_missing_table(exc): ... else:
raise`` in the repository. What the rule refuses is a handler from which no exception can leave at
all.

Both spellings of a re-raise count, as Requirement 30.5 allows: a bare ``raise`` and a
``raise MarketplaceError(...) from exc`` that translates the failure into this codebase's error
vocabulary.

WHAT A FAILURE REPORTS
----------------------
Module, line, the ``except`` clause as written, the enclosing function's qualified name, and the
handler's first statement rendered back from the AST - so the reader can act on the report without
opening the file to find out what was swallowed.

THE REVIEWED REGISTER, AND WHY IT IS NOT AN EXCLUSION
----------------------------------------------------
The two packages hold 41 broad handlers that do not re-raise, every one of them written
deliberately: a compensating delete after a failed create, a probe whose failure is classified
rather than propagated, an audit line that must not roll back the transition it describes, a
delivery loop that must not let one dead socket stop the others. Each is listed in
:data:`REVIEWED`, individually, with its module, its enclosing function, its line at the time of
review and the reason it is correct. There is no package-level, directory-level or file-level
exclusion anywhere in this file, and a new module or a new handler is audited the moment it is
written.

The register is not a list of names to skip. Each entry also **declares what the handler does
instead of raising**, as one of :class:`Outcome`, and
:func:`test_every_reviewed_handler_still_has_the_outcome_it_was_reviewed_for` re-derives that from
the source and fails when the declaration and the code disagree. So an entry whose handler is
edited later - the log line deleted, the ``return`` removed - stops being justified and the test
says so. Requirement 30.5's "or returning a defined error outcome" is exactly what those
declarations record, and ``SILENT`` is the declaration that no outcome is detectable, which is the
case a reviewer must argue for in prose.

WHY THIS TEST CANNOT PASS VACUOUSLY
-----------------------------------
Three ways a static guard fails silently, all closed here: the glob finding no modules
(:func:`test_the_audit_covers_both_packages` asserts both packages parsed and that the audit found
broad handlers at all), the detector never firing (the synthetic cases in
:class:`TestTheDetectorBites` drive the same functions this file's audit uses over sources that
must and must not trip it), and the register rotting into a blanket pass
(:func:`test_the_register_has_no_stale_entry` fails on an entry that no longer matches a real
handler).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

import pytest

# ══════════════════════════════════════════════════════════════════════════
#  WHAT IS AUDITED
# ══════════════════════════════════════════════════════════════════════════

REPO_ROOT: Path = Path(__file__).resolve().parents[1]

#: The two packages this spec created. Globbed, never enumerated, so a module added to either is
#: audited without an edit here.
AUDITED_PACKAGES: Tuple[Path, ...] = (
    REPO_ROOT / "backend_app" / "backend" / "marketplace",
    REPO_ROOT / "backend_app" / "backend" / "paper",
)


def audited_modules() -> List[Path]:
    """Every ``.py`` file under the audited packages, sorted, existence-checked.

    A package directory that is absent is reported rather than passed over: a silent empty list
    would make every assertion below succeed by auditing nothing, so the absence is raised as a
    skip that names the missing directory.
    """
    missing = [d for d in AUDITED_PACKAGES if not d.is_dir()]
    if missing:
        pytest.skip(
            "the audited package(s) "
            + ", ".join(str(d.relative_to(REPO_ROOT)) for d in missing)
            + " do not exist, so there is nothing to audit; this is not a pass"
        )
    modules: List[Path] = []
    for package in AUDITED_PACKAGES:
        modules.extend(sorted(package.rglob("*.py")))
    return modules


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# ══════════════════════════════════════════════════════════════════════════
#  THE DETECTOR
# ══════════════════════════════════════════════════════════════════════════

#: Deferred code: a ``raise`` written here does not run when the handler runs.
_DEFERRED: Tuple[type, ...] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
)

#: ``ast.TryStar`` exists from Python 3.11. Treated exactly like ``ast.Try``.
_TRY_NODES: Tuple[type, ...] = tuple(
    node for node in (ast.Try, getattr(ast, "TryStar", None)) if node is not None
)

_BROAD_NAMES: frozenset = frozenset({"Exception", "BaseException"})


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """True for ``except:``, ``except Exception``/``BaseException`` and tuples containing one."""
    clause = handler.type
    if clause is None:
        return True
    if isinstance(clause, ast.Name):
        return clause.id in _BROAD_NAMES
    if isinstance(clause, ast.Tuple):
        return any(
            isinstance(element, ast.Name) and element.id in _BROAD_NAMES
            for element in clause.elts
        )
    return False


def _suppresses(statement: ast.stmt) -> bool:
    """True for ``with contextlib.suppress(...)`` (or ``with suppress(...)``), which swallows."""
    if not isinstance(statement, (ast.With, ast.AsyncWith)):
        return False
    for item in statement.items:
        call = item.context_expr
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "suppress":
            return True
    return False


def _propagates(body: Sequence[ast.stmt]) -> bool:
    """True when a ``raise`` in ``body`` can leave it - the rule in this module's docstring."""
    return any(_statement_propagates(statement) for statement in body)


def _statement_propagates(statement: ast.stmt) -> bool:
    """One statement's contribution to :func:`_propagates`."""
    if isinstance(statement, ast.Raise):
        return True
    if isinstance(statement, _DEFERRED):
        return False
    if isinstance(statement, _TRY_NODES):
        handlers = list(statement.handlers)
        # The nested ``try``'s own body earns credit only when nothing there can swallow: either
        # it has no handlers at all (``try``/``finally``) or every handler propagates in turn.
        if _propagates(statement.body) and all(
            _propagates(handler.body) for handler in handlers
        ):
            return True
        # A ``raise`` in a nested handler, ``else`` or ``finally`` is not guarded by that
        # ``try``'s handlers, so it always counts.
        if any(_propagates(handler.body) for handler in handlers):
            return True
        return _propagates(statement.orelse) or _propagates(statement.finalbody)
    if _suppresses(statement):
        return False
    # A ``raise`` is a statement in Python, never an expression, so only statement children can
    # carry one. Recursing over statements alone is therefore exact, not an approximation.
    for child in ast.iter_child_nodes(statement):
        if isinstance(child, _DEFERRED) or not isinstance(child, ast.stmt):
            continue
        if _statement_propagates(child):
            return True
    return False


# ── what a non-raising handler does instead (Requirement 30.5's second half) ──


class Outcome(Enum):
    """The defined outcome a non-raising broad handler produces, in priority order.

    Derived from the handler's source by :func:`_outcome`, and declared in :data:`REVIEWED`, so
    the two can be compared. ``SILENT`` is the absence of any detectable outcome - the case a
    reviewer has to justify in prose rather than point at.
    """

    LOGS = "logs"
    RECORDS = "records"
    RETURNS = "returns"
    SILENT = "silent"


#: Names bound to a logging facility in this repository's modules.
_LOG_OBJECTS: frozenset = frozenset({"logger", "log", "logging", "LOGGER", "_logger"})

#: Collection mutations that record a failure as data (``failures.append(SweepFailure(...))``).
_RECORDING_METHODS: frozenset = frozenset({"append", "add", "extend", "update", "setdefault"})

#: Call names that record a failure somewhere durable rather than logging it.
_RECORDING_VERBS = re.compile(
    r"(?:^|_)(?:record|audit|note|report|emit|observe|track|count|increment|warn)"
)


def _calls_logger(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        target = node.func.value
        if isinstance(target, ast.Name) and target.id in _LOG_OBJECTS:
            return True
        if isinstance(target, ast.Attribute) and target.attr in _LOG_OBJECTS:
            return True
    return False


def _records(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if not isinstance(name, str):
            continue
        if name in _RECORDING_METHODS or _RECORDING_VERBS.search(name):
            return True
    return False


def _returns(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, _DEFERRED):
            continue
        if isinstance(node, (ast.Return, ast.Continue, ast.Break)):
            return True
    return False


def _outcome(handler: ast.ExceptHandler) -> Outcome:
    """What the handler does instead of raising, most specific signal first."""
    if _calls_logger(handler):
        return Outcome.LOGS
    if _records(handler):
        return Outcome.RECORDS
    if _returns(handler):
        return Outcome.RETURNS
    return Outcome.SILENT


# ── the sites ──


@dataclass(frozen=True)
class Site:
    """One broad ``except`` handler, located and classified."""

    module: str
    qualname: str
    ordinal: int
    line: int
    clause: str
    first_statement: str
    propagates: bool
    outcome: Outcome

    @property
    def key(self) -> Tuple[str, str, int]:
        return (self.module, self.qualname, self.ordinal)

    def describe(self) -> str:
        return (
            f"  {self.module}:{self.line}  in {self.qualname}\n"
            f"      {self.clause}\n"
            f"      first statement: {self.first_statement}\n"
            f"      what it does instead of raising: {self.outcome.value}"
        )


def _qualnames(tree: ast.AST) -> Dict[int, str]:
    """``id(node) -> enclosing dotted function/class name``, innermost winning."""
    names: Dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}.{child.name}" if prefix else child.name
                for descendant in ast.walk(child):
                    names[id(descendant)] = qualified
                walk(child, qualified)

    walk(tree, "")
    return names


def _clause_source(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return "except:" + (f"  # as {handler.name}" if handler.name else "")
    rendered = ast.unparse(handler.type)
    return f"except {rendered}" + (f" as {handler.name}" if handler.name else "") + ":"


def _first_statement(handler: ast.ExceptHandler) -> str:
    if not handler.body:
        return "<empty>"
    rendered = " ".join(ast.unparse(handler.body[0]).split())
    return rendered if len(rendered) <= 120 else rendered[:117] + "..."


def sites_from_source(source: str, module: str) -> List[Site]:
    """Every broad handler in one module's source, in line order, classified.

    Source-addressable rather than path-addressable so :class:`TestTheDetectorBites` can drive the
    identical code over synthetic modules and know it is exercising what the real audit runs.
    """
    tree = ast.parse(source, filename=module)
    qualnames = _qualnames(tree)
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _is_broad(node)
    ]
    counters: Dict[str, int] = {}
    sites: List[Site] = []
    for handler in sorted(handlers, key=lambda node: node.lineno):
        qualname = qualnames.get(id(handler), "<module>")
        ordinal = counters.get(qualname, 0)
        counters[qualname] = ordinal + 1
        sites.append(
            Site(
                module=module,
                qualname=qualname,
                ordinal=ordinal,
                line=handler.lineno,
                clause=_clause_source(handler),
                first_statement=_first_statement(handler),
                propagates=_propagates(handler.body),
                outcome=_outcome(handler),
            )
        )
    return sites


def broad_handler_sites(path: Path) -> List[Site]:
    """:func:`sites_from_source` for a file on disk."""
    return sites_from_source(path.read_text(encoding="utf-8"), _relative(path))


def audited_sites() -> List[Site]:
    """Every broad handler in both audited packages."""
    sites: List[Site] = []
    for path in audited_modules():
        sites.extend(broad_handler_sites(path))
    return sites


def violations(sites: Sequence[Site]) -> List[Site]:
    """The broad handlers with no propagating ``raise`` - the rule, applied."""
    return [site for site in sites if not site.propagates]


# ══════════════════════════════════════════════════════════════════════════
#  THE REVIEWED REGISTER
# ══════════════════════════════════════════════════════════════════════════


class Allowed(NamedTuple):
    """One reviewed broad handler.

    ``module``/``qualname``/``ordinal`` is the key. The key is deliberately not the line number: a
    line moves whenever anything above it is edited, and an entry that went stale on an unrelated
    edit would train readers to re-baseline the register instead of reading it. ``line`` is carried
    for navigation and is reported when it drifts, not asserted.

    ``ordinal`` is the handler's index among the broad handlers of the same function, in line
    order, so a function with three of them justifies each separately.

    ``outcome`` is the claim about what the handler does instead of raising. It is re-derived from
    the source and compared - see
    :func:`test_every_reviewed_handler_still_has_the_outcome_it_was_reviewed_for`.
    """

    module: str
    qualname: str
    ordinal: int
    line: int
    outcome: Outcome
    reason: str


_M = "backend_app/backend/marketplace"
_P = "backend_app/backend/paper"

#: Every broad handler in the two packages that does not re-raise, reviewed one at a time.
#:
#: The shape repeats because the situation repeats: a failure that has already been decided about -
#: the request is already refused, the state transition is already persisted, the create is already
#: being unwound - where a second exception thrown from the handler would replace a known outcome
#: with an unknown one. Requirement 30.5 permits exactly that when a defined error outcome is
#: returned instead, which is what the ``outcome`` column records and the tests below verify.
REVIEWED: Tuple[Allowed, ...] = (
    # ── marketplace ───────────────────────────────────────────────────────
    Allowed(f"{_M}/checkout_service.py", "_record_failure", 0, 1203, Outcome.LOGS,
            "the checkout has already failed and the caller already has its error; this writes "
            "payment_failed onto the pending subscription. If that write also fails the row stays "
            "PENDING - never deleted, never ACTIVE - so no entitlement follows from it, and the "
            "failure is logged at error level for reconciliation."),
    Allowed(f"{_M}/eligibility_gate.py", "_version_graph_is_valid", 0, 614, Outcome.RETURNS,
            "the question asked is 'is this version graph valid', and load_graph raising IS the "
            "answer 'no'. Returning False is the defined outcome; re-raising would turn an "
            "unparseable graph into a 500 instead of an ineligible submission."),
    Allowed(f"{_M}/eligibility_gate.py", "evaluate", 0, 329, Outcome.RECORDS,
            "Requirement 2.13's unevaluable verdict: a read that did not complete yields "
            "unevaluable=True, a distinct wire code from 'not eligible', and the evaluation is "
            "still written to the audit trail naming the failing exception type. The verdict is "
            "the defined outcome, and no admission can follow from it."),
    Allowed(f"{_M}/expiry_sweep.py", "stop_running_sessions_and_deployments", 0, 837,
            Outcome.RECORDS,
            "one subscription's deployment stop failed. It is appended to the sweep's failures "
            "list, which the sweep returns and its worker reports, so the sweep continues over "
            "the remaining subscriptions instead of abandoning them. The failure is data, not a "
            "dropped exception."),
    Allowed(f"{_M}/expiry_sweep.py", "stop_running_sessions_and_deployments", 1, 871,
            Outcome.RECORDS,
            "the same disposition for the paper-session stop stage of the same loop: recorded as a "
            "SweepFailure and returned, so P-13's idempotent sweep can be re-run over whatever "
            "did not stop."),
    Allowed(f"{_M}/library_entries.py", "_price_display", 0, 902, Outcome.RETURNS,
            "a minor-unit amount that will not convert - a malformed row, a currency with no "
            "persisted exponent - yields None so the caller OMITS the display field (Requirement "
            "28.5). The exact price_minor still travels; the alternative is inventing an "
            "approximate amount, which is the thing 28.5 forbids."),
    Allowed(f"{_M}/submission_service.py", "_compensate_admin_action", 0, 894, Outcome.SILENT,
            "compensation: the admin action failed and this reverts submission_state. A second "
            "exception here would replace the real failure the caller is about to raise with the "
            "revert's failure, and the caller's error is the one that describes what happened. A "
            "failed revert leaves the row in the state the admin action moved it to, which is a "
            "reconciliation item named in the function's own docstring."),
    Allowed(f"{_M}/submission_service.py", "_compensate_create", 0, 611, Outcome.SILENT,
            "the same compensation shape, unwinding the backtest-evidence rows of a create that "
            "failed after them. A failed delete leaves an orphan row no reader reaches, because "
            "the submission it belongs to was never returned."),
    Allowed(f"{_M}/submission_service.py", "_compensate_create", 1, 617, Outcome.SILENT,
            "the submission-row half of the same unwind, for the same reason: masking the original "
            "create failure with a cleanup failure would lose the only error that describes why "
            "the create did not happen."),
    Allowed(f"{_M}/submission_service.py", "_record_submission_created", 0, 644, Outcome.RETURNS,
            "the audit facility is imported lazily here to keep the import graph acyclic. If the "
            "import fails there is no logger to log with, so the function returns; the created "
            "submission is already persisted and is not conditioned on its audit line."),
    Allowed(f"{_M}/submission_service.py", "_record_submission_created", 1, 665, Outcome.SILENT,
            "the audit write itself. Requirement 2.11 wants the evaluation recorded; it does not "
            "make the create conditional on the record, and a strict audit sink must not be able "
            "to fail a submission that is already stored."),
    Allowed(f"{_M}/subscriber_operation_guard.py", "_entitlement", 0, 595, Outcome.LOGS,
            "fail-closed by construction: None means 'no entitlement established', so the guard "
            "refuses the subscriber operation. Turning a broken read into an admission is the one "
            "outcome that would breach Requirement 12.x, and re-raising would turn it into a 500 "
            "the caller cannot act on."),
    Allowed(f"{_M}/subscriber_operation_guard.py", "_listings_for_strategy", 0, 533, Outcome.LOGS,
            "an empty correlation list means the guard cannot tie the strategy to a Listing, so "
            "the handler's own refusal stands unchanged. Logged at error level, and no operation "
            "is admitted on the strength of a read that did not complete."),
    Allowed(f"{_M}/subscriber_operation_guard.py", "_service_client", 0, 620, Outcome.LOGS,
            "an unconfigured or unavailable service-role client is not a caller error. None "
            "propagates as 'the guard could not establish entitlement', which fails closed."),
    Allowed(f"{_M}/subscriber_operation_guard.py", "_strategy_id_for_model_version", 0, 558,
            Outcome.LOGS,
            "None means the model version could not be correlated to a strategy, so the handler's "
            "own 404 stands. The alternative - raising - converts a missing correlation into a "
            "500 for a caller who asked about a row that may not be theirs."),
    Allowed(f"{_M}/subscription_reinstatement.py", "_revert_status", 0, 641, Outcome.LOGS,
            "the last resort of a reinstatement that could neither be audited nor reverted. It "
            "logs at error level that the subscription needs operator reconciliation; raising "
            "here would mask the audit failure that caused the revert."),
    # ── paper ─────────────────────────────────────────────────────────────
    Allowed(f"{_P}/paper_channel.py", "PaperChannelRegistry._close_fell_behind", 0, 923,
            Outcome.LOGS,
            "the socket is being closed for falling behind (Requirement 19.11). If the courtesy "
            "CLIENT_FELL_BEHIND frame cannot be written the close proceeds anyway - the frame is "
            "the notification, the close is the outcome."),
    Allowed(f"{_P}/paper_channel.py", "PaperChannelRegistry._deliver_to_handlers", 0, 971,
            Outcome.LOGS,
            "Requirement 19.13 exactly: one subscriber's handler raising must not stop delivery to "
            "the others. The handler name is appended to the returned failures list and logged at "
            "error level, so the failure is both reported and contained."),
    Allowed(f"{_P}/paper_channel.py", "PaperChannelRegistry._release", 0, 603, Outcome.LOGS,
            "a ws_manager registry that refused a removal is a leak and is logged as one, but it "
            "must not stop the socket from being closed, which is the part the client observes."),
    Allowed(f"{_P}/paper_channel.py", "PaperChannelRegistry._release", 1, 627, Outcome.LOGS,
            "the second close attempt, made after the first raised TypeError on the close_code "
            "keyword. A socket that reports an error while being closed is already gone; there is "
            "no third thing to try and nothing for a caller to do."),
    Allowed(f"{_P}/paper_channel.py", "PaperChannelRegistry._release", 2, 633, Outcome.LOGS,
            "the same disposition for the first close attempt's non-TypeError failures, for the "
            "same reason."),
    Allowed(f"{_P}/paper_channel.py", "PaperChannelRegistry.broadcast", 0, 852, Outcome.LOGS,
            "a connection that cannot be written to is released and added to the returned failed "
            "list, and the loop continues to the remaining subscribers. Raising would let one dead "
            "socket drop an event for every other subscriber of the session."),
    Allowed(f"{_P}/paper_channel.py", "PaperChannelRegistry.session_owner", 0, 762, Outcome.LOGS,
            "the pre-emit ownership read. None means 'ownership not established' and the caller "
            "emits to nobody - fail-closed, which is what tenant isolation requires of a read that "
            "did not complete (Requirement 21.4)."),
    Allowed(f"{_P}/paper_market_feed.py", "FeedHandle._close_pubsub", 0, 1566, Outcome.LOGS,
            "teardown noise on a pubsub object that is being discarded. CancelledError is "
            "re-raised by the preceding handler, so a real cancellation still propagates."),
    Allowed(f"{_P}/paper_market_feed.py", "FeedHandle._emit_feed_disconnected", 0, 1483,
            Outcome.LOGS,
            "the DEGRADED state is already persisted when this runs, so Requirement 14.5's no-fill "
            "guarantee is in force regardless. An audit line that could not be written must not "
            "roll back the transition it describes, and it is logged at error level so the gap is "
            "visible where the record should have been."),
    Allowed(f"{_P}/paper_market_feed.py", "FeedHandle.reconnect", 0, 1537, Outcome.LOGS,
            "a failed reconnect attempt is a defined outcome: False, counted in "
            "paper.feed.reconnects, and retried on the next iteration. CancelledError is re-raised "
            "by the preceding handler."),
    Allowed(f"{_P}/paper_market_feed.py", "_audit_mock_interface_refusal", 0, 732, Outcome.LOGS,
            "the refusal has already been logged and is about to be raised by the caller. If the "
            "audit facility cannot even be imported there is nothing to write with, and the "
            "refusal itself stands."),
    Allowed(f"{_P}/paper_market_feed.py", "_exact_decimal", 0, 1005, Outcome.RETURNS,
            "a value that will not convert exactly - a float decoded without parse_float=Decimal - "
            "returns None, and the caller classifies the event as invalid and counts it "
            "(Requirement 18.1). Storing str(0.07) instead is the thing 18.1 forbids."),
    # ``paper_market_feed._metrics`` was here, and is gone: the guarded lazy accessor it held -
    # copied byte for byte into four more paper modules by task 33.5 - has been promoted to
    # ``backend_app.backend.metrics.guarded_collector``. The paper package now calls that, so the
    # broad handler this entry justified no longer exists anywhere in either audited package and
    # the entry would be stale. The one remaining broad ``except`` is inside ``metrics.py``, which
    # is outside both packages; it returns ``None`` in every case and says so in its docstring.
    Allowed(f"{_P}/paper_market_feed.py", "_validate_candle", 0, 1252, Outcome.LOGS,
            "a validator that did not complete is not a pass. The candle is dropped with a reason "
            "string and the failure is logged at error level; admitting the candle because the "
            "validator raised would invert what the validator is for."),
    Allowed(f"{_P}/paper_market_feed.py", "publish_unsubscribe", 0, 268, Outcome.LOGS,
            "the mds unsubscribe is a courtesy release on a session that is already stopping. "
            "False is returned and the un-released stream is named in the log; raising would fail "
            "a stop that has otherwise completed. CancelledError is re-raised above."),
    Allowed(f"{_P}/paper_repository.py", "_note_session_write", 0, 2717, Outcome.LOGS,
            "a cache-invalidation listener failed after the write it is told about already "
            "succeeded. The bounded consequence is stated in the docstring: the cached owner keeps "
            "its five-second life, which is staleness the design already accepts."),
    Allowed(f"{_P}/paper_repository.py", "paper_persistence_supported", 0, 821, Outcome.LOGS,
            "the probe classifies the exception rather than dropping it: a missing-table error "
            "means persistence is unavailable and is remembered as such, and anything else returns "
            "True so the real statement runs and its real failure surfaces. An inconclusive probe "
            "must not be reported as an unapplied migration."),
    Allowed(f"{_P}/paper_session_service.py", "_emit", 0, 1314, Outcome.LOGS,
            "the event is already recorded in paper_events at its sequence when delivery is "
            "attempted, so a reconnecting client replays it (Requirements 19.8, 19.9). A delivery "
            "failure must not undo a recorded event."),
    Allowed(f"{_P}/paper_session_service.py", "_record_signal", 0, 3634, Outcome.LOGS,
            "the Signal_Trace record is a projection of an order that already exists in "
            "paper_orders and paper_events. The session's own history stays complete; the trace "
            "has a gap, and the gap is logged at error level with the session named."),
    Allowed(f"{_P}/paper_session_service.py", "_settle_loop", 0, 2437, Outcome.LOGS,
            "the session loop is being stopped and ended with an exception on its way out. The stop "
            "continues because the loop is no longer stepping either way, and the loop's own "
            "containment already logged the bar that failed. Our own cancellation IS re-raised by "
            "the preceding handler, so a shutdown is not misread as a completed settle."),
    Allowed(f"{_P}/paper_session_service.py", "_spawn", 0, 1421, Outcome.LOGS,
            "None tells the caller the loop did not start while the session is RUNNING with an "
            "open feed, which is what it needs in order to stop it; the log line names the "
            "session. Raising here would leave that session running with nobody able to name it."),
    Allowed(f"{_P}/paper_session_service.py", "release_session_resources", 0, 2769, Outcome.LOGS,
            "a leak must not be silent: the un-released registrations are logged at error level "
            "and the stop is NOT reported complete (Requirement 19.12), while the remaining "
            "release stages still run."),
    Allowed(f"{_P}/paper_session_service.py", "session_loop", 0, 4392, Outcome.LOGS,
            "Requirement 14.7's containment at bar scope: one bar that failed is logged with "
            "logger.exception and the next bar runs. A session that stopped here would report "
            "nothing about later bars and would leave its feed open with nobody draining it."),
    Allowed(f"{_P}/paper_simulator.py", "audit_simulator_misconfigured", 0, 424, Outcome.LOGS,
            "the caller is already on a refusal path with an error to raise. If the audit facility "
            "cannot be imported there is nothing to write with, and the refusal stands "
            "(Requirement 26.5)."),
    Allowed(f"{_P}/paper_simulator.py", "audit_simulator_misconfigured", 1, 462, Outcome.LOGS,
            "the same, for the audit write itself: logged at error level, and the refusal the "
            "record describes is unaffected."),
)

REVIEWED_BY_KEY: Dict[Tuple[str, str, int], Allowed] = {
    (entry.module, entry.qualname, entry.ordinal): entry for entry in REVIEWED
}


def _reviewed(site: Site) -> Optional[Allowed]:
    return REVIEWED_BY_KEY.get(site.key)


# ══════════════════════════════════════════════════════════════════════════
#  THE ASSERTIONS
# ══════════════════════════════════════════════════════════════════════════


def test_no_new_broad_except_swallows_a_failure() -> None:
    """No broad ``except`` without a propagating ``raise``, outside the reviewed register.

    The rule is the one stated in this module's docstring. A failure names the module, the line,
    the ``except`` clause as written, the enclosing function and the handler's first statement.

    To clear a failure: make the handler specific (``except PaperRepositoryError``), or re-raise
    (bare, or translated into this codebase's error vocabulary with ``from exc``), or - if
    swallowing really is correct - add one :class:`Allowed` entry naming that handler, the line
    and the reason, which puts the decision in front of a reviewer instead of leaving it implicit.

    _Requirements: 30.2, 30.5_
    """
    unreviewed = [site for site in violations(audited_sites()) if _reviewed(site) is None]
    assert not unreviewed, (
        f"{len(unreviewed)} broad except handler(s) catch a failure and cannot let one out "
        "(Requirement 30.5):\n" + "\n".join(site.describe() for site in unreviewed)
    )


def test_the_register_has_no_stale_entry() -> None:
    """Every reviewed entry still points at a real broad handler that still swallows.

    Without this the register decays into a blanket pass: a handler that was fixed - made
    specific, or given a re-raise - would leave its justification behind, and the next handler to
    land in that function at that ordinal would inherit a reason written about different code.

    _Requirements: 30.2, 30.5_
    """
    live = {site.key for site in violations(audited_sites())}
    stale = [
        entry
        for entry in REVIEWED
        if (entry.module, entry.qualname, entry.ordinal) not in live
    ]
    assert not stale, (
        f"{len(stale)} register entry(ies) no longer match a swallowing broad handler - either the "
        "handler was fixed (delete the entry) or it moved to another function (re-review it):\n"
        + "\n".join(
            f"  {entry.module} :: {entry.qualname} #{entry.ordinal} (was line {entry.line})"
            for entry in stale
        )
    )


def test_every_reviewed_handler_still_has_the_outcome_it_was_reviewed_for() -> None:
    """The declared outcome is re-derived from the source and must still match.

    This is what makes the register a check rather than a list of names to skip. Requirement 30.5
    allows a broad catch that returns a defined error outcome; each entry declares which outcome it
    relies on, and this test recomputes it. Delete the log line from a handler justified as
    ``LOGS`` and it becomes ``SILENT`` here, the declaration stops being true, and the failure
    reports both values.

    _Requirements: 30.2, 30.5_
    """
    disagreements: List[str] = []
    for site in violations(audited_sites()):
        entry = _reviewed(site)
        if entry is None or entry.outcome is site.outcome:
            continue
        disagreements.append(
            f"  {site.module}:{site.line} in {site.qualname} #{site.ordinal} was reviewed as "
            f"{entry.outcome.value!r} but its source now reads {site.outcome.value!r}\n"
            f"      first statement: {site.first_statement}"
        )
    assert not disagreements, (
        f"{len(disagreements)} reviewed handler(s) no longer do what their justification claims "
        "(Requirement 30.5's 'or returning a defined error outcome'):\n"
        + "\n".join(disagreements)
    )


def test_every_reviewed_entry_carries_a_reason() -> None:
    """A register entry without an argument is an exclusion, which this file does not have.

    Enforced mechanically, because a bare key with an empty reason is exactly how a reviewed
    register turns into a silent skip list. A reason must be a sentence rather than a word: 40
    characters is the floor, and the handful of non-reasons that appear when the field is filled in
    to make a test pass are rejected by name.

    _Requirements: 30.2, 30.5_
    """
    non_reasons = {
        "best-effort",
        "best effort",
        "intentional",
        "deliberate",
        "see the docstring",
        "n/a",
        "wontfix",
    }
    thin = [
        entry
        for entry in REVIEWED
        if len(entry.reason.strip()) < 40
        or entry.reason.strip().lower().rstrip(".") in non_reasons
    ]
    assert not thin, (
        "register entry(ies) without a stated reason:\n"
        + "\n".join(
            f"  {entry.module} :: {entry.qualname} #{entry.ordinal}: {entry.reason!r}"
            for entry in thin
        )
    )


def test_no_two_register_entries_share_a_key() -> None:
    """Two entries under one key would justify one handler twice and another not at all.

    _Requirements: 30.2_
    """
    keys = [(entry.module, entry.qualname, entry.ordinal) for entry in REVIEWED]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, f"duplicated register key(s): {duplicates}"


def test_the_audit_covers_both_packages() -> None:
    """The audit parsed both packages and found handlers, so it cannot pass by finding none.

    Three ways this file could pass while checking nothing: the glob matching no module, a package
    renamed out from under it, or a detector that never fires. The first two are asserted here; the
    third is :class:`TestTheDetectorBites`.

    _Requirements: 30.2, 30.5_
    """
    modules = audited_modules()
    empty = sorted(
        package.name
        for package in AUDITED_PACKAGES
        if not any(package in path.parents for path in modules)
    )
    assert not empty, f"the audit found no module at all under {empty}"

    sites = audited_sites()
    assert sites, (
        f"the audit parsed {len(modules)} module(s) and found no broad except handler anywhere. "
        "Both packages are full of them, so an empty result means the detector or the walk is "
        "broken and every assertion in this file would pass vacuously."
    )


def test_the_reviewed_lines_are_reported_when_they_drift() -> None:
    """Line drift is surfaced as a message, never as a failure.

    The register is keyed on function and ordinal precisely so that an edit above a handler does
    not fail this suite. The recorded line is still worth keeping - it is how a reader navigates to
    the handler - so a drift is printed for whoever next edits the register, and the test passes.
    The assertion here is only that every recorded line is a plausible one.

    _Requirements: 30.2_
    """
    live = {site.key: site for site in violations(audited_sites())}
    drifted = [
        (entry, live[(entry.module, entry.qualname, entry.ordinal)])
        for entry in REVIEWED
        if (entry.module, entry.qualname, entry.ordinal) in live
        and live[(entry.module, entry.qualname, entry.ordinal)].line != entry.line
    ]
    if drifted:
        print(
            "\nregister lines have drifted (informational; the keys are function+ordinal):\n"
            + "\n".join(
                f"  {entry.module} :: {entry.qualname} #{entry.ordinal}: "
                f"{entry.line} -> {site.line}"
                for entry, site in drifted
            )
        )
    assert all(entry.line > 0 for entry in REVIEWED)


# ══════════════════════════════════════════════════════════════════════════
#  PROOF THAT THE DETECTOR BITES
# ══════════════════════════════════════════════════════════════════════════


class TestTheDetectorBites:
    """The rule, exercised over synthetic sources by the functions the real audit uses.

    Every case here calls :func:`sites_from_source`, which is what :func:`audited_sites` calls, so
    a detector that stopped detecting fails these before it silently empties the audit above.
    """

    @staticmethod
    def _only(source: str) -> Site:
        sites = sites_from_source(source, "<synthetic>")
        assert len(sites) == 1, f"expected exactly one broad handler, got {len(sites)}"
        return sites[0]

    def test_a_swallowed_exception_is_a_violation(self) -> None:
        site = self._only("try:\n    f()\nexcept Exception:\n    pass\n")
        assert not site.propagates
        assert site.outcome is Outcome.SILENT

    def test_a_bare_except_is_broad(self) -> None:
        site = self._only("try:\n    f()\nexcept:\n    pass\n")
        assert site.clause == "except:"
        assert not site.propagates

    def test_base_exception_is_broad(self) -> None:
        assert not self._only("try:\n    f()\nexcept BaseException:\n    pass\n").propagates

    def test_a_tuple_containing_exception_is_broad(self) -> None:
        source = "try:\n    f()\nexcept (TimeoutError, Exception):\n    pass\n"
        assert not self._only(source).propagates

    def test_a_narrow_handler_is_not_audited_at_all(self) -> None:
        source = "try:\n    f()\nexcept (ValueError, KeyError):\n    pass\n"
        assert sites_from_source(source, "<synthetic>") == []

    def test_a_bare_reraise_is_credited(self) -> None:
        assert self._only("try:\n    f()\nexcept Exception:\n    raise\n").propagates

    def test_a_translated_raise_is_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception as exc:\n"
            "    logger.error('x')\n"
            "    raise PaperError('translated') from exc\n"
        )
        assert self._only(source).propagates

    def test_a_conditional_raise_is_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception as exc:\n"
            "    if is_missing_table(exc):\n"
            "        return False\n"
            "    raise\n"
        )
        assert self._only(source).propagates

    def test_a_raise_inside_an_if_only_is_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception as exc:\n"
            "    if fatal(exc):\n"
            "        raise\n"
        )
        assert self._only(source).propagates

    def test_a_raise_a_nested_try_can_swallow_is_not_credited(self) -> None:
        """The case the task singles out: the ``raise`` is written, and cannot get out."""
        source = (
            "try:\n"
            "    f()\n"
            "except Exception:\n"
            "    try:\n"
            "        raise\n"
            "    except Exception:\n"
            "        pass\n"
        )
        sites = sites_from_source(source, "<synthetic>")
        outer = [site for site in sites if site.line == 3]
        assert len(outer) == 1
        assert not outer[0].propagates, (
            "a raise enclosed by a nested try that swallows it must earn no credit"
        )

    def test_a_raise_a_nested_try_cannot_swallow_is_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception:\n"
            "    try:\n"
            "        raise\n"
            "    except Exception:\n"
            "        raise\n"
        )
        sites = sites_from_source(source, "<synthetic>")
        outer = [site for site in sites if site.line == 3]
        assert len(outer) == 1 and outer[0].propagates

    def test_a_raise_in_a_nested_handler_is_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception:\n"
            "    try:\n"
            "        cleanup()\n"
            "    except ValueError:\n"
            "        raise\n"
        )
        outer = [
            site for site in sites_from_source(source, "<synthetic>") if site.line == 3
        ]
        assert len(outer) == 1 and outer[0].propagates

    def test_a_raise_in_a_nested_finally_is_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception:\n"
            "    try:\n"
            "        cleanup()\n"
            "    finally:\n"
            "        raise\n"
        )
        outer = [
            site for site in sites_from_source(source, "<synthetic>") if site.line == 3
        ]
        assert len(outer) == 1 and outer[0].propagates

    def test_a_raise_inside_contextlib_suppress_is_not_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception:\n"
            "    with contextlib.suppress(Exception):\n"
            "        raise\n"
        )
        assert not self._only(source).propagates

    def test_a_raise_in_a_nested_function_is_not_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception:\n"
            "    def later():\n"
            "        raise\n"
            "    schedule(later)\n"
        )
        assert not self._only(source).propagates

    def test_a_raise_in_a_loop_body_is_credited(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception:\n"
            "    for item in items:\n"
            "        if bad(item):\n"
            "            raise\n"
        )
        assert self._only(source).propagates

    def test_the_outcome_classifier_reads_a_log_call(self) -> None:
        source = "try:\n    f()\nexcept Exception as exc:\n    logger.warning('x', exc)\n"
        assert self._only(source).outcome is Outcome.LOGS

    def test_the_outcome_classifier_reads_a_recorded_failure(self) -> None:
        source = (
            "try:\n"
            "    f()\n"
            "except Exception as exc:\n"
            "    failures.append(SweepFailure(message=str(exc)))\n"
        )
        assert self._only(source).outcome is Outcome.RECORDS

    def test_the_outcome_classifier_reads_a_returned_outcome(self) -> None:
        source = "try:\n    f()\nexcept Exception:\n    return None\n"
        assert self._only(source).outcome is Outcome.RETURNS

    def test_handlers_are_numbered_per_function_in_line_order(self) -> None:
        source = (
            "def outer():\n"
            "    try:\n"
            "        a()\n"
            "    except Exception:\n"
            "        pass\n"
            "    try:\n"
            "        b()\n"
            "    except Exception:\n"
            "        pass\n"
        )
        sites = sites_from_source(source, "<synthetic>")
        assert [(site.qualname, site.ordinal, site.line) for site in sites] == [
            ("outer", 0, 4),
            ("outer", 1, 8),
        ]

    def test_a_method_handler_is_named_by_class_and_method(self) -> None:
        source = (
            "class Registry:\n"
            "    def release(self):\n"
            "        try:\n"
            "            self.close()\n"
            "        except Exception:\n"
            "            pass\n"
        )
        assert self._only(source).qualname == "Registry.release"

    def test_a_module_level_handler_is_reported_as_module(self) -> None:
        assert self._only("try:\n    import x\nexcept Exception:\n    x = None\n").qualname == (
            "<module>"
        )

    def test_the_report_names_the_first_statement(self) -> None:
        source = "try:\n    f()\nexcept Exception as exc:\n    logger.error('boom %s', exc)\n"
        described = self._only(source).describe()
        assert "logger.error('boom %s', exc)" in described
        assert "<synthetic>:3" in described

    def test_violations_selects_exactly_the_non_propagating_handlers(self) -> None:
        source = (
            "def a():\n"
            "    try:\n"
            "        f()\n"
            "    except Exception:\n"
            "        raise\n"
            "def b():\n"
            "    try:\n"
            "        f()\n"
            "    except Exception:\n"
            "        pass\n"
        )
        found = violations(sites_from_source(source, "<synthetic>"))
        assert [site.qualname for site in found] == ["b"]
