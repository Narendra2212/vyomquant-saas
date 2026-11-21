"""DEPLOY-GRACE-03: say WHICH startup step was slow, not only that startup was.

Production 2026-11-20/21: 101 "DEPLOY-GRACE-01: slow startup" events at 69-365 s, with
many tasks landing at almost exactly 365 s -- the signature of one step waiting out a
fixed timeout. The event carried only the total. ``main.lifespan`` now times each step
and this module attaches those times to the report: one extra per step and the slowest
step as a tag, so Sentry can group and filter by it.
"""
from __future__ import annotations

from typing import Dict


def slowest_step(phases: Dict[str, float]):
    if not phases:
        return ("unknown", 0.0)
    return max(phases.items(), key=lambda kv: kv[1])


def report_slow_startup(total_seconds: float, phases: Dict[str, float]) -> None:
    """Send the slow startup to Sentry with its per-step breakdown. Never raises."""
    try:
        import sentry_sdk
        name, secs = slowest_step(phases)
        with sentry_sdk.new_scope() as scope:
            for step, value in phases.items():
                scope.set_extra(f"step_{step}", value)
            scope.set_tag("startup_slowest_step", name[:200])
            sentry_sdk.capture_message(
                f"DEPLOY-GRACE-01: slow startup {total_seconds}s (slowest: {name} {secs}s)",
                level="warning",
            )
    except Exception:  # noqa: BLE001 - reporting must never block serving
        pass
