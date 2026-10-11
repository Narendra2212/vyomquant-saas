"""Phase 10 / Workstream E — collection gate and the autouse production-credential guard.

WHY THIS DIRECTORY IS OPT-IN
----------------------------
Two independent reasons, and either alone would be sufficient.

1. **Some tests here are RED BY DESIGN.** They reproduce measured defects in the
   sandbox-routing layer (``test_sandbox_routing_contract.py``: EXCH-01, EXCH-04,
   EXCH-05). A reproduction that passes is not a reproduction. Those tests are
   Workstream E's evidence, and Workstream E does not own the shared ``pytest tests/``
   lane, so they must not red it for the other tabs working in this repository.

2. **Some tests here reach a third party.** The order-lifecycle suite talks to a real
   exchange *sandbox*. Nothing in the default lane may ever do that — which is exactly
   the ENV-01 defect this repository already found once, when
   ``test_connection_pooling_safety`` silently issued an authenticated request to
   ``api.binance.com`` from the unit lane.

So collection is gated on ``AERORA_EXCHANGE_SANDBOX_CERT=1``, following the precedent
``pytest.ini`` records for ``tests/property/conftest.py`` and
``AERORA_MARKET_DATA_LATENCY_EXPERIMENT``. A skip here is NOT a pass, and
``test_the_sandbox_gate_is_explicit`` asserts the gate exists so the skip cannot be
silent.

Shared constants and helpers live in ``harness.py``, not here: a conftest is not
importable from a sibling test module, and those constants are the subject of assertions
as well as the machinery behind them.
"""
from __future__ import annotations

import os

import pytest

from tests.exchange_sandbox.harness import GATE_ENV, PRODUCTION_CREDENTIAL_NAMES


#: This conftest's own directory. A subdirectory ``conftest.py``'s
#: ``pytest_collection_modifyitems`` receives the ENTIRE session's item list, NOT just the
#: items under its own directory -- so the skip MUST be scoped to this directory, or it
#: skips every test in the whole ``pytest tests/`` run. (That was the original defect: the
#: unscoped loop below marked all ~14,000 collected tests as skipped, turning the full
#: regression lane into 0 passed / everything skipped.)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _is_in_this_directory(item) -> bool:
    """Whether ``item`` lives under this conftest's own directory."""
    try:
        item_path = str(item.fspath)
    except Exception:  # noqa: BLE001 - a non-file item is, by definition, not ours
        return False
    return os.path.abspath(item_path).startswith(_THIS_DIR + os.sep)


def pytest_collection_modifyitems(config, items):
    """Skip THIS DIRECTORY's tests unless the gate is set, naming the reason.

    Scoped to this directory only: a subdirectory conftest's modifyitems hook sees the
    whole session, so an unscoped skip would deselect the entire suite.
    """
    if os.environ.get(GATE_ENV) == "1":
        return
    skip = pytest.mark.skip(
        reason=(
            f"exchange sandbox certification is opt-in: set {GATE_ENV}=1. This directory "
            f"contains (a) reproductions of measured sandbox-routing defects that are RED "
            f"BY DESIGN and (b) tests that reach a real exchange sandbox. Neither belongs "
            f"in the default lane. A skip here is NOT a pass — see "
            f"staging/EXCHANGE_SANDBOX_CERTIFICATION.md for what is and is not certified."
        )
    )
    for item in items:
        if _is_in_this_directory(item):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _assert_no_production_credentials():
    """Refuse to run if a production exchange credential is visible in this process.

    Autouse so it cannot be forgotten by a new test. This is what makes "non-production
    credentials only" a property of the harness rather than an instruction in a document.
    No value is read or logged — only the presence of a name.
    """
    leaked = sorted(n for n in PRODUCTION_CREDENTIAL_NAMES if os.environ.get(n))
    if leaked:
        pytest.fail(
            "REFUSING TO RUN: production-shaped exchange credential name(s) are set in "
            f"this environment: {leaked}. Sandbox certification must use non-production "
            "credentials supplied under AERORA_SANDBOX_<VENUE>_* names only. Unset the "
            "above and re-run. (No value was read or logged.)"
        )
    yield
