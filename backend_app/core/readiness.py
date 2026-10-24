"""OBS-01: liveness, readiness and dependency health, as three different questions.

THE DEFECT
==========
``/health/ready`` returned ``{"status": "ready"}`` unconditionally -- it checked nothing.
``/api/system/readiness`` and ``/api/system/dependencies`` returned the string
``"HEALTHY"`` as a literal for database, redis, risk_engine, execution_engine and
reconciliation, alongside fabricated detail like ``"circuit_breaker": "ARMED"``. And
``health()``'s ``all_ok`` considered only Supabase and QuestDB, so **Redis being down
still reported ``"ok"``**.

Taken together, an orchestrator could route production traffic to a pod with no database
and no Redis, and every health surface would agree it was fine. The
``FAILURE_INJECTION_AUDIT`` already noted the backend starts without a database
connection, which is what makes this reachable rather than theoretical.

THE THREE QUESTIONS
-------------------
**Liveness** -- "is this process alive?" Must NOT depend on external services. A pod whose
database is down is not a broken pod; restarting it does not fix the database, and an
orchestrator that kills it loses the capacity that would have served traffic once the
dependency returned. ``/health/live`` stays dependency-free.

**Readiness** -- "should this instance receive production traffic?" Depends on exactly the
dependencies the service cannot serve its traffic class without. Returns **503** when not
ready, because a 200 with a sad body in it is invisible to every load balancer.

**Dependency health** -- "what is the state of each thing we talk to?" Always 200; it is a
diagnostic, not a gate. Reports each dependency separately with whether it is REQUIRED.

WHICH DEPENDENCIES ARE MANDATORY, AND WHY
-----------------------------------------
* **Supabase / PostgreSQL -- REQUIRED.** Every authenticated request resolves its identity
  and RLS through it. Without it the API can serve nothing but health.
* **Redis -- REQUIRED outside a declared local environment.** Not a cache here: it backs
  the rate limiter (``core/rate_limit.py`` refuses to start without a reachable store
  outside a local environment -- API-03) and the distributed execution locks. A pod that
  cannot reach it cannot enforce either, and the API-03 decision already treats that as
  disqualifying.
* **QuestDB / telemetry -- OPTIONAL.** It degrades to a mock by design; losing metrics is
  not losing correctness, so readiness must not depend on it. Making readiness depend on
  an optional service is how an outage in a non-critical system becomes a total outage.
* **Fleet manager -- NOT a dependency.** It is in-process state, not something we talk to.

``local_state_is_acceptable()`` is reused for the Redis judgement, so the readiness gate
and the rate limiter cannot disagree about what counts as a local environment.

NO SECRETS. Probe detail is a short exception class name and a truncated message, never a
DSN, URL, key or credential -- these endpoints are typically unauthenticated
(``OBS-03`` notes ``/metrics`` is).

Regression: tests/test_obs01_readiness_reflects_dependencies.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("Readiness")

#: The three states a dependency can be in. Deliberately not a bare bool: "we could not
#: determine" is a distinct answer from "it is down", and conflating them is how a
#: hardcoded "HEALTHY" gets written in the first place.
UP = "UP"
DOWN = "DOWN"
UNKNOWN = "UNKNOWN"

#: How long a probe may take before it is treated as DOWN. A dependency that cannot
#: answer inside this budget cannot serve a request either, so a hung probe must not hang
#: the readiness endpoint -- an orchestrator reads a timeout as a failure anyway, but it
#: would read it as a failure of the POD rather than of the dependency.
PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class DependencyReport:
    """One dependency's measured state. ``required`` decides whether it gates readiness."""

    name: str
    state: str
    required: bool
    detail: str = ""

    @property
    def blocks_readiness(self) -> bool:
        """REQUIRED and not UP. ``UNKNOWN`` blocks too -- fail closed."""
        return self.required and self.state != UP


@dataclass(frozen=True)
class ReadinessReport:
    """The readiness verdict and the evidence for it."""

    ready: bool
    dependencies: Tuple[DependencyReport, ...]
    checked_at: str

    @property
    def http_status(self) -> int:
        """200 when ready, 503 when not.

        A non-ready readiness probe MUST be a non-2xx, or no load balancer will act on
        it. This is the single most important line in the module.
        """
        return 200 if self.ready else 503

    @property
    def blocking(self) -> Tuple[str, ...]:
        return tuple(d.name for d in self.dependencies if d.blocks_readiness)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": "READY" if self.ready else "NOT_READY",
            "checked_at": self.checked_at,
            "blocking": list(self.blocking),
            "dependencies": {
                d.name: {
                    "state": d.state,
                    "required": d.required,
                    # Truncated, and never a DSN or credential.
                    "detail": d.detail[:160],
                }
                for d in self.dependencies
            },
        }


def _safe_detail(exc: BaseException) -> str:
    """An exception rendered WITHOUT leaking a connection string.

    Provider exceptions routinely embed the URL they failed to reach. These endpoints are
    typically unauthenticated, so only the class name and a short, scrubbed message go
    out.
    """
    text = str(exc).splitlines()[0] if str(exc) else ""
    for marker in ("://", "password", "key=", "token", "secret"):
        if marker in text.lower():
            return type(exc).__name__
    return f"{type(exc).__name__}: {text[:100]}"


async def _probe_with_timeout(name: str, coro_factory, required: bool
                              ) -> DependencyReport:
    import asyncio

    try:
        await asyncio.wait_for(coro_factory(), timeout=PROBE_TIMEOUT_SECONDS)
        return DependencyReport(name, UP, required, "reachable")
    except asyncio.TimeoutError:
        return DependencyReport(
            name, DOWN, required,
            f"probe exceeded {PROBE_TIMEOUT_SECONDS}s",
        )
    except Exception as exc:  # noqa: BLE001
        return DependencyReport(name, DOWN, required, _safe_detail(exc))


async def probe_redis() -> DependencyReport:
    """Redis, by actually pinging it.

    REQUIRED outside a declared local environment: it backs the rate limiter (API-03) and
    the distributed execution locks. The same detector the rate limiter uses decides
    whether this environment is allowed to run without it.
    """
    from backend_app.core.runtime_env import local_state_is_acceptable

    required = not local_state_is_acceptable()

    async def _ping():
        from backend_app.core.cache import redis_manager
        result = await redis_manager.ping()
        if not result:
            # The facade is fail-soft and answers falsey rather than raising, so a
            # falsey ping must be converted into a real failure here or Redis being
            # down would read as UP.
            raise RuntimeError("ping returned a falsey result")

    return await _probe_with_timeout("redis", _ping, required)


async def probe_database() -> DependencyReport:
    """Supabase/PostgreSQL, by issuing a real statement.

    ALWAYS REQUIRED: every authenticated request resolves identity and RLS through it.
    """
    async def _query():
        from backend_app.core.database import SessionLocal
        from sqlalchemy import text as _text

        session = SessionLocal()
        try:
            session.execute(_text("SELECT 1"))
        finally:
            session.close()

    return await _probe_with_timeout("database", _query, required=True)


async def probe_telemetry() -> DependencyReport:
    """QuestDB / telemetry. OPTIONAL by design -- it degrades to a mock.

    Included so the dependency surface is honest, and marked NOT required so an outage in
    a metrics backend cannot take the API out of rotation.
    """
    async def _ping():
        from backend_app.core.state import app_state
        await app_state.telemetry.ping()

    return await _probe_with_timeout("telemetry", _ping, required=False)


async def probe_credential_vault_keys() -> DependencyReport:
    """Can the configured master key still decrypt stored exchange credentials? (VAULT-01)

    NOT REQUIRED, and that is a deliberate judgement rather than timidity. A wrong master
    key is caught at boot by
    ``credential_key_integrity.assert_master_key_can_read_stored_credentials``, which
    refuses to start -- so a serving pod reaching a mismatch here is an edge case, and the
    value of reporting it is diagnostic.

    More importantly, gating readiness on it would be actively harmful. The remedy for an
    unreadable credential is for the user to RE-ENTER it, through this very API. Taking
    every pod out of rotation would remove the only route to the fix while changing
    nothing about the cause. A metrics outage must not become a total outage (see
    QuestDB above); neither must a credential-integrity finding.

    Reported here so the condition is visible on the dependency surface rather than only
    in a boot log nobody re-reads.
    """
    import asyncio

    from backend_app.core import credential_key_integrity as cki

    try:
        report = await asyncio.wait_for(
            asyncio.to_thread(cki.check_master_key_integrity),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return DependencyReport(
            "credential_vault_keys", UNKNOWN, False,
            f"probe exceeded {PROBE_TIMEOUT_SECONDS}s",
        )
    except Exception as exc:  # noqa: BLE001
        return DependencyReport(
            "credential_vault_keys", UNKNOWN, False, _safe_detail(exc))

    # UNDETERMINED is UNKNOWN, not DOWN: not being able to read the sample is not a
    # finding about the key, and reporting it as DOWN would cry wolf on every database
    # blip. The three-state vocabulary exists for exactly this distinction.
    if report.verdict == cki.UNDETERMINED:
        state = UNKNOWN
    elif report.healthy:
        state = UP
    else:
        state = DOWN
    return DependencyReport(
        "credential_vault_keys", state, False,
        f"{report.verdict}: {report.summary()}",
    )


async def evaluate_readiness() -> ReadinessReport:
    """Probe every dependency and decide whether this instance should take traffic.

    Probes run concurrently: readiness is on an orchestrator's critical path and three
    sequential timeouts would exceed most probe budgets.
    """
    import asyncio

    reports: List[DependencyReport] = []
    try:
        gathered = await asyncio.gather(
            probe_database(), probe_redis(), probe_telemetry(),
            probe_credential_vault_keys(),
            return_exceptions=True,
        )
        for item in gathered:
            if isinstance(item, DependencyReport):
                reports.append(item)
            else:
                # A probe that raised outside its own handler. Fail closed: an
                # undeterminable REQUIRED dependency is not a reason to take traffic.
                reports.append(DependencyReport(
                    "unknown", UNKNOWN, True, _safe_detail(item)  # type: ignore[arg-type]
                ))
    except Exception as exc:  # noqa: BLE001
        logger.error("Readiness evaluation failed entirely: %s", exc)
        reports.append(DependencyReport("readiness", UNKNOWN, True, _safe_detail(exc)))

    ready = not any(d.blocks_readiness for d in reports)
    return ReadinessReport(
        ready=ready,
        dependencies=tuple(reports),
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


__all__ = [
    "UP", "DOWN", "UNKNOWN", "PROBE_TIMEOUT_SECONDS",
    "DependencyReport", "ReadinessReport",
    "probe_redis", "probe_database", "probe_telemetry",
    "probe_credential_vault_keys", "evaluate_readiness",
]
