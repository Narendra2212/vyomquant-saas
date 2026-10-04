"""
tests/test_reconciliation_worker_claims_match_behaviour.py

The reconciliation worker reports capabilities it does not have, and the one block that
would correct drifted state is unreachable, built from objects that are thrown away.

P0-04 remainder.

DEFECT 1 -- TWO AVAILABILITY FLAGS THAT CANNOT BE FALSE
``reconciliation_worker.py`` opens with::

    try:
    #     import ccxt.async_support as ccxt
        CCXT_AVAILABLE = True
    except ImportError:
        CCXT_AVAILABLE = False

    try:
    #     from backend_app.core.reconciliation_engine import ReconciliationEngine
        RECONCILIATION_ENGINE_AVAILABLE = True
    except ImportError:
        RECONCILIATION_ENGINE_AVAILABLE = False

Both imports are commented out and both flags are hardcoded ``True``. The
``except ImportError`` can never fire, because there is no import left inside the ``try``
-- only an assignment, which always succeeds. So the module asserts that ccxt is loaded
and that the reconciliation engine is loaded, having loaded neither, and neither name is
bound.

That is not cosmetic. ``CCXT_AVAILABLE`` is the guard on both exchange reads::

    if not CCXT_AVAILABLE or not hasattr(client, "fetch_open_orders"):
        raise RuntimeError("Exchange order reconciliation is unavailable ...")

With the flag pinned ``True``, the ``not CCXT_AVAILABLE`` half is dead and the guard rests
entirely on ``hasattr``. In a deployment without ccxt the module would still claim it had
it. Measured in this environment: both commented-out imports ARE importable, so restoring
them changes no behaviour here and simply makes the flags true -- which is precisely why
there is no reason to leave them lying.

DEFECT 2 -- THE STATE-CORRECTION PATH IS BUILT AND DISCARDED
Inside ``_reconcile_user``, two classes are defined and then::

    ExchangeClientWrapper(client)
    StateServiceWrapper()

    engine_result = await self.reconciliation_engine.reconcile(
        tenant_id=user_id, exchange_name=exchange_name, user_id=user_id
    )

Both are bare expression statements. The objects are constructed, never bound to a name,
never passed to ``reconcile``, and immediately collected -- the same
evaluate-and-discard shape as the legacy backtest route's
``payload.get("stop_loss_pct", 0.02)``. ``reconcile`` receives three identifiers and
nothing else.

``StateServiceWrapper`` is where every correction was supposed to happen, and all six of
its mutating methods are log lines::

    async def create_fill(self, tenant_id, data):
        logger.info(f"[ReconciliationWorker] Would create fill: {data}")
    async def remove_fill(...):   "Would remove fill"
    async def create_order(...):  "Would create order"
    async def cancel_order(...):  "Would cancel order"
    async def create_position(...): "Would create position"
    async def update_position(...): "Would update position"

They could not run even if they were wired: ``get_orders`` calls
``self._fetch_local_orders(user_id)`` and ``ExchangeClientWrapper.get_orders`` calls
``self._fetch_exchange_orders(...)`` -- both are methods of the WORKER, not of the
wrappers, so each would raise ``AttributeError`` on first use.

And the whole block is behind ``if self.reconciliation_engine and
self.enable_fill_reconciliation:``. The module-level singleton is
``reconciliation_worker = ReconciliationWorker()`` with no arguments, so
``self.reconciliation_engine`` is ``None`` and the branch never executes at all.

DEFECT 3 -- THE DOCSTRING PROMISES SELF-HEALING
The module header draws a three-stage pipeline ending in "Correct Local State", lists five
scenarios each with an "Action" ("Update local order to filled", "Mark local order as
cancelled", "Update balance, investigate cause"), and states the expected result as::

    ✔ Local always matches exchange
    ✔ No hidden drift
    ✔ Automatic self-healing

None of that happens. Detection populates ``result.mismatches``; correction is six log
lines on an object nobody holds.

WHAT IS FIXED HERE AND WHAT IS NOT. The flags are made honest, the discarded wrappers are
deleted, and the docstring is corrected to describe detection without correction.
Reconciliation itself is NOT implemented: deciding what to do when the exchange and the
local ledger disagree about an open position is a money-critical design question -- which
side wins, whether a correction can itself place an order, what happens to the PnL
already reported -- and inventing an answer inside a cleanup pass would be worse than the
honest gap. Deleting the stubs removes the appearance of an implementation; it does not
remove a capability, because there was none.

NOTHING STARTS THIS WORKER, and that is recorded rather than fixed. The only importer is
``backend_app/core/order_state_engine.py``, which does
``_ = (reconciliation_worker, Order, OrderStatus, Position, state_service)`` -- an
import-and-discard that binds the names to quiet a linter. No entry in ``main.py``,
``worker.py``, ``startup.sh``, ``docker-compose.yml`` or a Procfile runs the loop.
``exchange_websocket_listener`` is in exactly the same position: it defines
``ExchangeWebSocketListener`` and ``get_exchange_websocket_listener()``, and the only other
reference to that name anywhere in the repository is a string literal used as an alert
``source`` label in ``core/alerting_system.py``.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import reconciliation_worker as rw

WORKER_SOURCE = inspect.getsource(rw)

#: The six methods that were supposed to correct drifted state.
STUBBED_CORRECTIONS = (
    "create_fill",
    "remove_fill",
    "create_order",
    "cancel_order",
    "create_position",
    "update_position",
)


#: The method that holds the engine call and held the discarded wrappers.
RECONCILE_METHOD = "_reconcile_user_exchange"


def _module_ast() -> ast.Module:
    return ast.parse(WORKER_SOURCE)


def _reconcile_user_ast() -> ast.AST:
    for node in ast.walk(_module_ast()):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == RECONCILE_METHOD
        ):
            return node
    raise AssertionError(
        f"{RECONCILE_METHOD} not found; this file's premise has changed"
    )


def _docstring_node_ids() -> set:
    """Identities of every docstring Constant in the module.

    Needed because this module's docstring and the comment left where the stubs were now
    DESCRIBE the removed code by name -- that is their purpose. A scan for the stub text
    has to look at code only. Comments never reach the AST at all, so excluding
    docstrings is the whole of it.
    """
    tree = _module_ast()
    ids = set()
    for node in [tree] + [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]:
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _code_string_literals() -> list:
    """Every string constant in the module that is NOT a docstring."""
    skip = _docstring_node_ids()
    return [
        node.value
        for node in ast.walk(_module_ast())
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
    ]


# ---------------------------------------------------------------------------
# 1. The availability flags must be capable of being false
# ---------------------------------------------------------------------------


class TestTheAvailabilityFlagsReflectReality:
    def test_each_availability_try_block_actually_imports_something(self):
        """A ``try`` whose body is only an assignment cannot raise ``ImportError``.

        Checked structurally: every ``try`` that has an ``except ImportError`` handler
        must contain at least one ``import`` statement, or its flag is a constant
        pretending to be a probe.
        """
        offenders = []
        for node in ast.walk(_module_ast()):
            if not isinstance(node, ast.Try):
                continue
            handles_import_error = any(
                isinstance(handler.type, ast.Name) and handler.type.id == "ImportError"
                for handler in node.handlers
            )
            if not handles_import_error:
                continue
            imports = [
                statement
                for statement in ast.walk(node)
                if isinstance(statement, (ast.Import, ast.ImportFrom))
            ]
            if not imports:
                offenders.append(node.lineno)

        assert not offenders, (
            f"try/except ImportError block(s) at line(s) {offenders} contain no import, "
            f"so the except branch is unreachable and the flag they set can never be "
            f"False"
        )

    def test_ccxt_is_bound_when_it_is_claimed_available(self):
        if rw.CCXT_AVAILABLE:
            assert getattr(rw, "ccxt", None) is not None, (
                "CCXT_AVAILABLE is True but the module has no `ccxt` name bound"
            )

    def test_the_engine_is_bound_when_it_is_claimed_available(self):
        if rw.RECONCILIATION_ENGINE_AVAILABLE:
            assert getattr(rw, "ReconciliationEngine", None) is not None, (
                "RECONCILIATION_ENGINE_AVAILABLE is True but the module has no "
                "`ReconciliationEngine` name bound"
            )

    def test_both_dependencies_are_genuinely_importable_here(self):
        """The reason restoring the imports was safe, recorded as a measurement.

        If either became unimportable, the flags would correctly go False and the
        exchange-read guards would start refusing -- which is the designed behaviour, and
        worth knowing is reachable rather than theoretical.
        """
        import importlib

        for module_name in ("ccxt.async_support", "backend_app.core.reconciliation_engine"):
            assert importlib.import_module(module_name) is not None


# ---------------------------------------------------------------------------
# 2. Nothing is constructed and thrown away
# ---------------------------------------------------------------------------


class TestNothingIsBuiltAndDiscarded:
    def test_reconcile_user_has_no_discarded_constructor_calls(self):
        """``ExchangeClientWrapper(client)`` on a line of its own does nothing at all.

        Any call expression used as a bare statement whose result is dropped is suspect;
        a CONSTRUCTOR used that way cannot be doing anything but wasting a line, because
        the object has no other reference.
        """
        discarded = []
        for node in ast.walk(_reconcile_user_ast()):
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            # A class constructor, by the repository's naming convention.
            if name[:1].isupper():
                discarded.append((node.lineno, name))

        assert not discarded, (
            f"_reconcile_user constructs and discards {discarded}; the objects are never "
            f"bound, never passed to reconcile(), and collected immediately"
        )

    def test_the_stub_wrapper_classes_are_gone(self):
        """Asserted on declared classes, not on source text.

        The comment left in their place names them deliberately, so a text search would
        match the explanation.
        """
        declared = {
            node.name for node in ast.walk(_module_ast()) if isinstance(node, ast.ClassDef)
        }

        for name in ("ExchangeClientWrapper", "StateServiceWrapper"):
            assert name not in declared, (
                f"{name} is still declared. Its methods were log-only stubs, it was "
                f"never passed to reconcile(), and its read methods called the WORKER's "
                f"private helpers off `self`, so they would raise AttributeError."
            )

    @pytest.mark.parametrize("method", STUBBED_CORRECTIONS)
    def test_no_would_do_it_log_line_remains(self, method):
        """A log line announcing an action is a claim, not an action.

        One case per stubbed correction, so a partial cleanup names which one survived
        rather than reporting a single opaque failure. Scanned over non-docstring string
        literals: the module docstring now explains what these were, so it would match
        itself otherwise.
        """
        verb, _, noun = method.partition("_")
        stub = f"Would {verb} {noun}"

        offenders = [
            literal for literal in _code_string_literals() if stub in literal
        ]

        assert not offenders, (
            f"the stub log line {stub!r} is still present in code; it reads as a "
            f"correction the worker performs, and it was in fact on an object that was "
            f"never bound"
        )


# ---------------------------------------------------------------------------
# 3. The docstring describes what the module does
# ---------------------------------------------------------------------------


class TestTheModuleDocstringIsHonest:
    def test_it_does_not_promise_self_healing(self):
        doc = rw.__doc__ or ""
        for claim in (
            "Automatic self-healing",
            "Local always matches exchange",
            "No hidden drift",
        ):
            assert claim not in doc, (
                f"the module docstring still promises {claim!r}. Correction is six log "
                f"lines on an object nobody holds."
            )

    def test_it_states_that_correction_is_not_implemented(self):
        doc = (rw.__doc__ or "").lower()
        assert "detect" in doc, "the docstring should say what the worker does do"
        assert (
            "not implemented" in doc
            or "does not correct" in doc
            or "no correction" in doc
        ), (
            "the docstring does not state that detected drift is reported rather than "
            "corrected"
        )


# ---------------------------------------------------------------------------
# 4. Recorded gaps: two components nothing starts
# ---------------------------------------------------------------------------


class TestTheUnstartedComponentsAreRecorded:
    def test_nothing_starts_the_reconciliation_loop(self):
        """Asserted so the gap is a decision on record rather than a discovery.

        If someone wires the worker into a process entrypoint, this fails and the note in
        the module docstring gets revisited along with it.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        starters = []
        for candidate in (
            "backend_app/main.py",
            "backend_app/worker.py",
            "startup.sh",
            "docker-compose.yml",
            "Procfile",
        ):
            path = root / candidate
            if path.is_file() and "reconciliation_worker" in path.read_text(
                encoding="utf-8", errors="replace"
            ):
                starters.append(candidate)

        assert not starters, (
            f"{starters} now reference the reconciliation worker. If it is genuinely "
            f"started, this recorded gap is closed and this test should go."
        )

    def test_nothing_starts_the_exchange_websocket_listener(self):
        """Same shape, same reason. Defined, complete-looking, and never run."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        starters = []
        for candidate in (
            "backend_app/main.py",
            "backend_app/worker.py",
            "startup.sh",
            "docker-compose.yml",
            "Procfile",
        ):
            path = root / candidate
            if path.is_file() and "exchange_websocket_listener" in path.read_text(
                encoding="utf-8", errors="replace"
            ):
                starters.append(candidate)

        assert not starters, (
            f"{starters} now reference the exchange websocket listener. If it is "
            f"genuinely started, this recorded gap is closed."
        )

    def test_the_only_importer_still_discards_it(self):
        """``_ = (reconciliation_worker, ...)`` binds the name to quiet a linter."""
        from backend_app.core import order_state_engine as ose

        source = inspect.getsource(ose)
        assert "reconciliation_worker" in source
        assert "_ = (" in source, (
            "order_state_engine no longer import-and-discards the worker; if it now uses "
            "it, this recorded gap has changed"
        )
