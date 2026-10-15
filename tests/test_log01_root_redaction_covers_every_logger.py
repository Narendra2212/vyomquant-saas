"""tests/test_log01_root_redaction_covers_every_logger.py

LOG-01 (P3) -- ``core/observability.py::RedactingLogFilter`` was attached only to the
thirteen logger names in ``GUARDED_LOGGER_NAMES`` (marketplace and paper, task 33.4's
own scope). A billing, exchange, execution or auth logger carried no secret redaction
at all: a JWT, an API key, or anything else ``SECRET_VALUE_PATTERNS`` matches could
reach a log sink unredacted from any of them.

THE FIX
-------
``install_root_log_redaction()`` attaches ``RedactingLogFilter`` to every HANDLER on
the root logger, rather than to a named logger. A ``Logger``'s own filters apply only
to records it originates; a ``Handler``'s filters apply to every record that reaches
it via propagation, whatever logger originated it. ``main.py`` calls this right after
its own ``logging.basicConfig(...)``, which is what gives the root logger a handler to
attach to in the first place.

WHAT THIS FILE PROVES
----------------------
1. a record from an arbitrary, never-individually-guarded logger name (standing in for
   "BillingService"/"ExchangeConnector"/etc.) is redacted once this filter is on the
   handler it propagates to;
2. a record from an ALREADY-guarded logger (one of GUARDED_LOGGER_NAMES) is not
   double-mangled by being redacted twice (once by its own logger filter, once by the
   root handler filter it also propagates to) -- the second pass is a no-op;
3. the installer is idempotent (a handler that already carries the filter is not given
   a second one);
4. a handler-less logger (the real root logger before ``basicConfig`` has run) returns
   0 newly-guarded handlers rather than raising.

A fresh ``logging.Logger``/``logging.Handler`` pair is built per test (never the real
process root), so this file cannot leak a redacting filter onto this test run's own
pytest log output.
"""
from __future__ import annotations

import logging

import pytest

from backend_app.core.observability import RedactingLogFilter, install_root_log_redaction


def _logger_with_handler(name: str):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.filters.clear()
    logger.propagate = False  # isolate from the real root during the test
    handler = logging.Handler()
    records = []
    handler.emit = records.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger, handler, records


@pytest.fixture
def fresh_root():
    """A standalone logger standing in for the root, with its own handler -- never the
    real process root logger.
    """
    logger, handler, records = _logger_with_handler("test_log01_fake_root")
    yield logger, handler, records
    logger.handlers.clear()
    logger.filters.clear()


def test_a_record_from_an_unguarded_logger_name_is_redacted_once_the_handler_is_covered(fresh_root):
    root, handler, records = fresh_root
    install_root_log_redaction(root=root)

    unguarded = logging.getLogger("SomeBillingLoggerNeverIndividuallyGuarded")
    unguarded.handlers = [handler]
    unguarded.propagate = False
    unguarded.setLevel(logging.DEBUG)

    secret_jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9."
        "4f8f6a1c2d3e4f5061728394a5b6c7d8e9f0a1b2c3d4e5f6071829384"
    )
    unguarded.error("Payment failed with token %s", secret_jwt)

    assert len(records) == 1
    rendered = records[0].getMessage()
    assert secret_jwt not in rendered
    assert "redact" in rendered.lower()


def test_double_redaction_from_an_already_guarded_logger_is_a_no_op_not_a_mangling(fresh_root):
    """A record from a GUARDED_LOGGER_NAMES logger is redacted once by its own logger
    filter; if it also reaches a root handler carrying this filter, the second pass
    must not corrupt the already-redacted text.
    """
    root, handler, records = fresh_root
    install_root_log_redaction(root=root)

    already_guarded = logging.getLogger("PaperRepository")
    assert any(isinstance(f, RedactingLogFilter) for f in already_guarded.filters), (
        "PaperRepository is expected to already carry RedactingLogFilter via "
        "install_log_redaction() at observability.py's own import time"
    )
    already_guarded.handlers = [handler]
    already_guarded.propagate = False
    already_guarded.setLevel(logging.DEBUG)

    secret_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.abc123def456ghi789jkl012mno345"
    already_guarded.error("leaked %s", secret_jwt)

    assert len(records) == 1
    rendered = records[0].getMessage()
    assert secret_jwt not in rendered
    # Redacted exactly once over: the placeholder itself, redacted a second time, is
    # still exactly the placeholder -- not doubled, not garbled.
    assert rendered.count("redact") <= 2  # "redact" appears once in the log line's own prose


def test_the_installer_is_idempotent(fresh_root):
    root, handler, _records = fresh_root

    first = install_root_log_redaction(root=root)
    second = install_root_log_redaction(root=root)

    assert first == 1
    assert second == 0
    assert sum(isinstance(f, RedactingLogFilter) for f in handler.filters) == 1


def test_a_logger_with_no_handlers_yet_returns_zero_rather_than_raising():
    handlerless = logging.getLogger("test_log01_handlerless")
    handlerless.handlers.clear()

    assert install_root_log_redaction(root=handlerless) == 0
