"""BILL-09 — a failed plan-expiry sweep must say what went wrong, and not leak a DSN.

WHAT THE FINDING WAS
====================
Sentry issue 7848599556 recorded **304 occurrences** of

    [BILLING] plan-expiry sweep failed: APIError

between 2026-11-15 and 2026-11-17, logger `backend_app.core.plan_expiry`,
environment production. That string is the entire record. Both handlers in
`plan_expiry.py` logged `type(exc).__name__` and nothing else, so 304 failures
of a money path produced no cause, no table, no statement and no stack.

`APIError` is PostgREST's wrapper and carries the answer in its
`message`/`code`/`hint`/`details`. All four were being discarded.

WHAT WAS CHECKED BEFORE CHANGING ANYTHING, because the obvious guess was wrong
=============================================================================
This looked like BILL-07 and SUP-02 a third time -- a money path naming columns
the table never declared. It is not. Measured read-only against production:

  * `profiles` declares all six columns the sweep names: id, subscription_tier,
    next_billing_date, payment_provider, subscription_status,
    cancel_at_period_end.
  * Profiles currently lapsed and still on a paid tier: **0**. Nothing has
    escaped expiry, so no money was leaking.

The cause is the instance, not the billing code: the same window carried 119
`psycopg2.OperationalError: SSL connection has been closed unexpectedly` events
and the breadcrumbs on this very issue show `[OrderWatchdog] Leadership check
failed (TimeoutError: Timeout connecting to server)` -- Redis timing out at the
same moment. Roughly 425 of 495 events in 24h trace to database connectivity on
a CPU-starved NANO instance (see docs/audits/VYOMQUANT_PERFORMANCE_BASELINE.md
§6 and vq_outage_diagnosis.md).

So this file does not claim to fix the sweep. It fixes the thing that made 304
failures undiagnosable, which is why the real cause took a database probe to
find rather than a log read.

WHY THE REDACTION MATTERS
=========================
`str(exc)` is the obvious thing to log and the wrong one. A psycopg2
`OperationalError` -- which is what this sweep is actually hitting -- renders
with the full connection string, host and user included. This project already
has one unresolved credential-exposure incident; a log line that prints the
production DSN on every sweep failure would be a second, written by the fix.
Hence structured fields first, and a URI-redacting fallback. Same reasoning as
`core/readiness.py::_safe_detail`.
"""
from __future__ import annotations

import logging

import pytest

from backend_app.core.plan_expiry import _describe, run_plan_expiry_loop


class FakeAPIError(Exception):
    """Shaped like supabase-py's APIError: structured fields beside the text."""

    def __init__(self, message, code=None, hint=None, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.details = details


class TestTheCauseSurvivesLogging:
    def test_a_postgrest_error_reports_its_message_and_code(self):
        """The 304 production events said only "APIError". This is the fix."""
        described = _describe(FakeAPIError(
            'column profiles.foo does not exist', code='42703',
            hint='Perhaps you meant "fop".'))
        assert "column profiles.foo does not exist" in described
        assert "42703" in described
        assert 'Perhaps you meant "fop".' in described

    def test_a_plain_exception_still_says_something(self):
        assert "connection reset by peer" in _describe(
            OSError("connection reset by peer"))

    def test_an_exception_with_no_detail_at_all_is_not_an_empty_string(self):
        """An empty log line is the defect this file exists to remove."""
        assert _describe(Exception("")).strip() != ""

    def test_the_detail_is_bounded(self):
        """A driver can render a very long error. A log line is not a dump."""
        assert len(_describe(Exception("x" * 5000))) <= 300


class TestNoCredentialReachesTheLog:
    def test_a_dsn_in_the_exception_text_is_redacted(self):
        """psycopg2's OperationalError renders with the whole connection string.

        This is the case that would have turned a diagnostic improvement into a
        credential leak.
        """
        described = _describe(OSError(
            'could not connect to server: '
            'postgresql://postgres.abcd:SUPERSECRET@aws-1-ap-southeast-1.'
            'pooler.supabase.com:5432/postgres'))
        assert "SUPERSECRET" not in described
        assert "pooler.supabase.com" not in described
        assert "<uri-redacted>" in described

    def test_structured_fields_are_preferred_over_the_raw_text(self):
        """When both exist, the safe source wins.

        A PostgREST `message` is the server's account of a rejected statement
        and carries no credential; the exception's own text may.
        """
        exc = FakeAPIError('permission denied for table profiles', code='42501')
        exc.args = ('postgresql://u:p@host/db',)
        described = _describe(exc)
        assert "permission denied for table profiles" in described
        assert "postgresql://" not in described


class TestTheLoopStillSurvivesAFailure:
    @pytest.mark.asyncio
    async def test_a_failing_sweep_is_logged_with_detail_and_does_not_kill_the_loop(
        self, caplog
    ):
        """The swallow is deliberate and must stay.

        A sweep that raises must not end the loop -- next hour's sweep is what
        recovers from a transient database problem. What must change is that the
        failure is now reportable.
        """
        calls = {"n": 0}

        def get_client():
            calls["n"] += 1
            raise FakeAPIError('relation "profiles" does not exist', code='42P01')

        # One iteration, then break out of the infinite loop via the sleep.
        async def one_pass(_interval):
            raise asyncio.CancelledError

        import asyncio as _asyncio
        original_sleep = _asyncio.sleep
        _asyncio.sleep = one_pass
        try:
            with caplog.at_level(logging.ERROR, logger="backend_app.core.plan_expiry"):
                with pytest.raises(_asyncio.CancelledError):
                    await run_plan_expiry_loop(get_client, interval=1)
        finally:
            _asyncio.sleep = original_sleep

        assert calls["n"] == 1, "the sweep should have been attempted once"
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "plan-expiry sweep failed" in text
        assert 'relation "profiles" does not exist' in text, (
            "the cause must reach the log -- this is the whole point of BILL-09"
        )
        assert "42P01" in text


import asyncio  # noqa: E402  (imported late; the test above swaps asyncio.sleep)
