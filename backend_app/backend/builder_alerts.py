"""backend/builder_alerts.py - the four Strategy Builder alerts.

Spec: strategy-builder task 9.2. Requirements 24.4 and 24.5. ``design.md`` ->
Observability ("Alerts: any ``dag.intents.blocked_non_finite`` increment,
``market_data.feed_state = STALE`` sustained beyond 3 intervals on a live deployment,
training queue depth beyond threshold, and asset-universe age beyond 12 h").

WHAT THIS MODULE IS, AND WHAT IT IS NOT
---------------------------------------
It is **four conditions and one dispatch path**. Each condition is a pure, synchronous,
total function over facts the platform already measures - task 9.1's metrics, task 7.4's
feed classifier, task 6.4's authoritative queue count, task 7.1's universe TTL - and each
returns a :class:`BuilderAlert` or ``None``. Nothing here measures anything for the first
time, and nothing here re-derives a threshold that already exists somewhere else.

It is **not a second alerting system**. Delivery goes through
``core/alerting_system.py`` - the platform's existing multi-channel dispatcher, with its
log / webhook / email / Redis-pubsub fan-out and its five-minute deduplication window -
and the four alert types are added to that module's own :class:`AlertType` enum. There is
one alert pipeline in this codebase and these four alerts join it.

THE LOAD-BEARING PROPERTY: AN ALERT NEVER FAILS OR DELAYS THE ACT IT OBSERVES
----------------------------------------------------------------------------
Three of the four conditions are observed at a seam that is doing something that matters -
refusing a trade intent, classifying a feed, admitting a training job. So:

*(1) The condition is synchronous and total.* Every ``notice_*`` entry point is wrapped in
:func:`_never_fails`, which logs at debug and returns ``None``. A bad reading, a broken
metric object or an unimportable dependency cannot propagate into the caller.

*(2) Delivery is never awaited from the seam.* :func:`raise_alerts` schedules the send on
the running event loop through ``core.background_tasks.fire_and_forget_task`` - the
platform's existing tracked fire-and-forget helper, which holds a strong reference and
logs the failure - and returns immediately. When there is **no** running loop the alert is
written to the log at its own severity and delivery stops there: ``asyncio.run`` from a
synchronous seam would block the intent gate on a webhook, and a firewall that waits on
an HTTP POST is worse than one that only logs.

*(3) The conditions read state and return.* None of them is inside a branch a control
evaluates, and none of them writes anything a control reads.

WHAT NEVER APPEARS IN AN ALERT
------------------------------
No exchange identifier, api key, secret, passphrase or token (SB-06, Requirement 12.1),
and no per-tenant or per-resource identity - no user, tenant, strategy, version,
deployment, job or node id. ``tenant_id`` is passed to the dispatcher as ``None`` on
purpose: these four alerts are platform-health facts, not tenant events, and an operator
paged about a stopped feed does not need to know whose strategy noticed it. The rule is
enforced structurally by :func:`safe_details`, which drops any key whose name carries a
word from either vocabulary, rather than by a review convention. Both vocabularies are the
ones task 9.1 asserted over all eighteen metrics, named here so there is one list.

THE FOUR THRESHOLDS, AND WHERE EACH ONE COMES FROM
--------------------------------------------------
====================  =======================================================
non-finite intent     **none**. Requirement 24.4 says "when ... blocked", and
                      ``dag.intents.blocked_non_finite`` is unlabelled, so the
                      condition is "the one series rose" and nothing else.
feed ``STALE``        the classifier's own ``3 x`` boundary. Read from
                      :attr:`feed_state.FeedStateReport.stale_after_seconds`;
                      not recomputed here.
training queue depth  ``TRAINING_QUEUE_DRAIN_CYCLES`` x the shared pool width
                      ``ml_training_policy.DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL``.
universe age          ``UNIVERSE_AGE_TTL_MULTIPLE`` x
                      ``asset_universe.UNIVERSE_TTL_SECONDS`` = 12 h.
====================  =======================================================

Two of them are stated as *multiples of an existing number* rather than as absolutes, and
that is the point. An operator who widens the training fleet stops being paged for a queue
the fleet can now drain, and an operator who changes the universe TTL moves the staleness
alert with it. A hardcoded ``32`` and a hardcoded ``43200`` would both drift silently away
from the thing they were derived from.
"""

import asyncio
import functools
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("BuilderAlerts")


# ══════════════════════════════════════════════════════════════════════════
#  1. TOTALITY. An alert is not permitted to be the thing that breaks a seam.
# ══════════════════════════════════════════════════════════════════════════


def _never_fails(fn):
    """Swallow everything, log at debug, return ``None``.

    The same rule and the same reasoning as ``metrics.never_fails``: these calls sit
    inside the intent gate, the feed classifier and the training admission path. A missing
    alert is a gap on a pager; a propagated exception from an alert is an outage in a
    control. Only one of those two is acceptable, so the direction is not a choice.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - deliberately total; see the docstring
            try:
                logger.debug("Builder alert %s was not raised.", fn.__name__, exc_info=True)
            except Exception:  # noqa: BLE001 - a logger that raises must not either
                pass
            return None

    return wrapper


# ══════════════════════════════════════════════════════════════════════════
#  2. PAYLOAD HYGIENE  (SB-06 / Requirement 12.1, and cardinality)
# ══════════════════════════════════════════════════════════════════════════

#: Words that make a payload key a credential surface, whatever it happens to hold at the
#: time. Task 9.1's ``FORBIDDEN_CREDENTIAL_LABELS``, verbatim.
CREDENTIAL_WORDS: frozenset = frozenset(
    {
        "exchange",
        "venue",
        "api_key",
        "apikey",
        "key",
        "secret",
        "passphrase",
        "password",
        "token",
        "credential",
        "credentials",
    }
)

#: Words that make a payload key a per-tenant or per-resource identity. None of these is a
#: secret; every one of them ties a platform-health alert to one customer, and a pager that
#: names a customer is a pager an operator has to redact before forwarding.
IDENTITY_WORDS: frozenset = frozenset(
    {
        "user",
        "tenant",
        "account",
        "strategy",
        "version",
        "deployment",
        "job",
        "node",
        "email",
        "session",
        "request",
        "ip",
        "id",
    }
)

#: Cap on one rendered payload value. A detail is a figure or a vocabulary word, not a
#: document, and an unbounded string is how text that was never meant to leave the process
#: leaves the process.
DETAIL_VALUE_MAX_LENGTH = 200


def _key_words(name: Any) -> frozenset:
    return frozenset(str(name).replace(".", "_").replace("-", "_").lower().split("_"))


def forbidden_detail_key(name: Any) -> bool:
    """Whether ``name`` may not appear in an alert payload.

    Word-wise rather than substring-wise, so ``timeframe`` survives (``time``, ``frame``)
    while ``exchange_account_id`` does not. ``id`` is in the identity vocabulary, which is
    what makes every ``*_id`` key unrepresentable here without naming each one.
    """
    words = _key_words(name)
    return bool(words & CREDENTIAL_WORDS) or bool(words & IDENTITY_WORDS)


def safe_details(details: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """One alert payload, with every forbidden key removed and every value bounded.

    A filter and not an assertion: a future caller that adds a field carrying an account
    reference gets it dropped rather than delivered, and the drop is logged so it is
    findable. This is the backstop, not the policy - the four condition functions below
    build their payloads from figures and closed-vocabulary words in the first place.
    """
    clean: Dict[str, Any] = {}
    for name, value in dict(details or {}).items():
        if forbidden_detail_key(name):
            logger.debug("Alert detail %r was dropped: it names an identity or a credential.", name)
            continue
        if value is None or isinstance(value, (int, float, bool)):
            clean[str(name)] = value
            continue
        text = str(value)
        if len(text) > DETAIL_VALUE_MAX_LENGTH:
            text = text[:DETAIL_VALUE_MAX_LENGTH]
        clean[str(name)] = text
    return clean


# ══════════════════════════════════════════════════════════════════════════
#  3. THE ALERT VALUE
# ══════════════════════════════════════════════════════════════════════════

#: ``source`` values. The dispatcher deduplicates on ``(type, source, tenant_id)``, so the
#: source is part of the dedup identity and is therefore fixed per condition rather than
#: derived from a call site.
SOURCE_INTENT_GATE = "strategy_dag_intent_gate"
SOURCE_FEED_STATE = "strategy_builder_feed_state"
SOURCE_TRAINING_QUEUE = "strategy_builder_training_queue"
SOURCE_ASSET_UNIVERSE = "strategy_builder_asset_universe"


@dataclass(frozen=True)
class BuilderAlert:
    """One alert condition that has fired, before it has been delivered anywhere.

    Separate from ``alerting_system.Alert`` for the same reason
    :class:`feed_state.FeedObservation` is separate from
    :class:`feed_state.FeedStateReport`: this is the *verdict*, and it can be asserted over
    in a test with no event loop, no Redis, no webhook and no dispatcher anywhere near it.
    The dispatcher's ``Alert`` is what one delivery attempt produced.
    """

    alert_type_name: str
    severity_name: str
    title: str
    message: str
    source: str
    details: Dict[str, Any] = field(default_factory=dict)
    suggested_action: Optional[str] = None
    raised_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_type": self.alert_type_name,
            "severity": self.severity_name,
            "title": self.title,
            "message": self.message,
            "source": self.source,
            "details": dict(self.details),
            "suggested_action": self.suggested_action,
            "raised_at": self.raised_at,
        }


def _alert(
    *,
    alert_type_name: str,
    severity_name: str,
    title: str,
    message: str,
    source: str,
    details: Optional[Mapping[str, Any]] = None,
    suggested_action: Optional[str] = None,
) -> BuilderAlert:
    return BuilderAlert(
        alert_type_name=alert_type_name,
        severity_name=severity_name,
        title=title,
        message=message,
        source=source,
        details=safe_details(details),
        suggested_action=suggested_action,
        raised_at=datetime.now(timezone.utc).isoformat(),
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. LAZY, GUARDED DEPENDENCIES
# ══════════════════════════════════════════════════════════════════════════


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1).

    The same shape every task 9.1 seam uses, for the same reason: an unimportable metrics
    module must not be what breaks the thing being observed.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


def _alerting() -> Tuple[Any, Any, Any]:
    """``(system, AlertType, AlertSeverity)`` from ``core/alerting_system.py``.

    ``(None, None, None)`` when the module cannot be reached. Lazy because
    ``alerting_system`` imports ``aiohttp`` and the Redis manager at module scope, and
    ``feed_state`` / ``dag_engine`` are not permitted to acquire that import cost - or that
    failure mode - by adding an alert.
    """
    try:
        from backend_app.core.alerting_system import (
            AlertSeverity,
            AlertType,
            get_alerting_system,
        )

        return get_alerting_system(), AlertType, AlertSeverity
    except Exception:  # noqa: BLE001
        logger.debug("The alerting system could not be reached.", exc_info=True)
        return None, None, None


# ══════════════════════════════════════════════════════════════════════════
#  5. THRESHOLDS, EACH READ FROM THE AUTHORITY THAT ALREADY OWNS IT
# ══════════════════════════════════════════════════════════════════════════

#: Queue depths worth paging about, expressed in **drain cycles** of the shared training
#: pool. Four means "a job joining now waits behind four full passes of the fleet", which
#: is the point at which the queue is a capacity problem rather than a busy afternoon.
TRAINING_QUEUE_DRAIN_CYCLES = 4

#: Multiple of the asset-universe TTL at which the served snapshot is a fault rather than a
#: cache miss. Task 7.1 fixes the TTL at 6 h and refreshes at TTL/2, so two TTLs means the
#: refresher has missed at least **four** consecutive scheduled runs - infrastructure, not
#: luck. Two TTLs is also the 12 h the task and ``design.md`` name.
UNIVERSE_AGE_TTL_MULTIPLE = 2


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def training_pool_width() -> int:
    """The shared training pool's width, from ``ml_training_policy``'s own constant.

    Read through the policy module rather than re-reading ``ML_TRAINING_MAX_CONCURRENT_GLOBAL``
    here, so the alert and Requirement 16.5's saturation check cannot disagree about how
    wide the fleet is.
    """
    try:
        from backend_app.backend.ml_training_policy import (
            DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL,
        )

        width = int(DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL)
    except Exception:  # noqa: BLE001
        return 8
    return width if width > 0 else 8


def training_queue_depth_threshold() -> int:
    """Queue depth at or above which the queue-depth alert fires.

    ``TRAINING_QUEUE_DRAIN_CYCLES`` x the pool width, unless
    ``ML_TRAINING_QUEUE_DEPTH_ALERT`` names an absolute depth. The override exists because
    a deployment whose queue is deliberately deep (a batch-training weekend) should be able
    to say so without changing the pool width the caps are enforced against.
    """
    override = _env_int("ML_TRAINING_QUEUE_DEPTH_ALERT", 0)
    if override:
        return override
    return TRAINING_QUEUE_DRAIN_CYCLES * training_pool_width()


def universe_ttl_seconds() -> int:
    """Task 7.1's asset-universe TTL, in seconds. 6 h."""
    try:
        from backend_app.backend.asset_universe import UNIVERSE_TTL_SECONDS

        ttl = int(UNIVERSE_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        return 6 * 60 * 60
    return ttl if ttl > 0 else 6 * 60 * 60


def universe_age_threshold_seconds() -> int:
    """Age above which the served universe snapshot is alerted on. Two TTLs, i.e. 12 h."""
    return UNIVERSE_AGE_TTL_MULTIPLE * universe_ttl_seconds()


# ══════════════════════════════════════════════════════════════════════════
#  6. CONDITION 1 - ANY `dag.intents.blocked_non_finite` INCREMENT  (24.4)
# ══════════════════════════════════════════════════════════════════════════
#
# The counter is **unlabelled**, deliberately (task 9.1): "any increment" is then one
# series rather than a sum a rule has to remember to take, and this condition is a
# comparison against one number rather than a walk over a label set.
#
# The watermark is what makes the condition idempotent. It is read by two drivers - the
# intent gate pushes after each refusal, and :func:`evaluate` pulls when something scrapes -
# and neither must page twice for the same refusal. It also means the condition is honest
# when the gate is hit in a burst: the payload carries the total and the rise, so an
# operator sees "seven refusals since the last page", not seven pages.

_watermark_lock = threading.Lock()
_blocked_non_finite_watermark = 0.0


def reset_state() -> None:
    """Forget the watermark. For tests, and for nothing else."""
    global _blocked_non_finite_watermark
    with _watermark_lock:
        _blocked_non_finite_watermark = 0.0


def blocked_non_finite_total() -> Optional[float]:
    """The one series' current value, or ``None`` when it cannot be read."""
    collector = _metrics()
    if collector is None:
        return None
    counter = getattr(collector, "dag_intents_blocked_non_finite", None)
    if counter is None:
        return None
    try:
        return float(counter.total())
    except Exception:  # noqa: BLE001
        return None


def intent_blocked_non_finite_condition() -> Optional[BuilderAlert]:
    """Requirement 24.4. Fires when ``dag.intents.blocked_non_finite`` has risen.

    No threshold, because the requirement states none: the counter "must stay at zero"
    (``design.md`` -> Observability), so the first increment is the event. Only the
    ``NON_FINITE_ORDER_FIELD`` arm of ``assert_execution_safe`` increments it - a
    zero-size order raises ``NON_POSITIVE_QUANTITY`` and does **not** touch this counter -
    so this cannot page an author's way through a mis-sized order.

    Advances the watermark when it fires, so the same refusal is not reported twice.
    """
    global _blocked_non_finite_watermark
    total = blocked_non_finite_total()
    if total is None:
        return None
    with _watermark_lock:
        previous = _blocked_non_finite_watermark
        if total <= previous:
            # Includes a collector that was replaced under us and counted back down: a
            # lower total is not an increment, and re-paging on it would page on a restart.
            _blocked_non_finite_watermark = total
            return None
        _blocked_non_finite_watermark = total
    risen = total - previous
    return _alert(
        alert_type_name="BUILDER_INTENT_BLOCKED_NON_FINITE",
        severity_name="CRITICAL",
        title="Trade intent blocked: non-finite order field",
        message=(
            f"{int(risen)} trade intent(s) were refused at the DAG action boundary because "
            f"an order field held NaN or an infinity. No order was sent. This counter is "
            f"expected to stay at zero, so any increment is a defect in a block's "
            f"arithmetic or in its inputs."
        ),
        source=SOURCE_INTENT_GATE,
        details={
            "metric": "dag.intents.blocked_non_finite",
            "blocked_code": "NON_FINITE_ORDER_FIELD",
            "blocked_total": total,
            "risen_since_last_alert": risen,
        },
        suggested_action=(
            "Read the refused node's execution trace: a non-finite order field comes from "
            "a division by zero, an unwarmed indicator or a model output, and the fix is "
            "in the graph rather than in the gate. The gate refused correctly."
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
#  7. CONDITION 2 - SUSTAINED `STALE` ON A LIVE DEPLOYMENT  (24.5)
# ══════════════════════════════════════════════════════════════════════════


def feed_state_condition(
    report: Any, deployment_mode: Optional[str] = None
) -> Optional[BuilderAlert]:
    """Requirement 24.5. Fires on a **measured** ``STALE`` for a **live** deployment.

    Reads the classifier's verdict; derives nothing. ``feed_state.evaluate_feed_state``
    already applied Requirement 19.9's ``3 x`` boundary to produce ``STALE``, and it
    already published the boundary it used as
    :attr:`~feed_state.FeedStateReport.stale_after_seconds`. Both are read off the report,
    so there is exactly one place in the codebase that decides where three intervals falls
    and this is not it. A second threshold here could drift from the classifier's, and then
    the panel and the pager would disagree about whether a feed had stopped.

    **Two qualifications, both of which exclude a case that would otherwise be noise.**

    *Only a measured stop.* ``STALE`` is also the classifier's **fail-closed** answer when
    the age cannot be measured at all - no event has ever been observed
    (``NO_EVENT_OBSERVED``) or the pipeline publishes no interval for the bar label
    (``EXPECTED_INTERVAL_NOT_PUBLISHED``). Those are correct classifications and they are
    not "the feed stopped": one is a feed that never started and one is a limitation of the
    pipeline for that interval. Only ``AGE_AT_OR_OVER_3_INTERVALS`` - the arm the classifier
    reaches by comparing a real age against a real interval - pages. ``feed_state.py``'s own
    comment on that closure names this discrimination as the thing this alert needs.

    *Only a live deployment.* ``deployment_mode`` is
    ``market_data_contract.MODE_LIVE``/``MODE_PAPER``'s vocabulary, and ``None`` means the
    caller is not a deployment at all - a data preview, for instance, which is what the
    Builder's one existing feed-state caller is. ``None`` and ``paper`` both return
    ``None``: real orders are what make a stopped feed an emergency, and paging on a paper
    run would train an operator to ignore the page that matters.
    """
    if report is None:
        return None

    mode = str(deployment_mode or "").strip().lower()
    if not _is_live_mode(mode):
        return None

    state = getattr(getattr(report, "state", None), "value", getattr(report, "state", None))
    if str(state) != "STALE":
        return None

    reason = str(getattr(report, "reason", "") or "")
    if reason != _measured_stale_reason():
        return None

    age = getattr(report, "age_seconds", None)
    interval = getattr(report, "expected_interval_seconds", None)
    stale_after = getattr(report, "stale_after_seconds", None)
    intervals_behind = None
    if age is not None and interval:
        try:
            intervals_behind = round(float(age) / float(interval), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            intervals_behind = None

    timeframe = getattr(report, "timeframe", None)
    age_text = None
    try:
        from backend_app.backend.feed_state import format_age

        age_text = format_age(age)
    except Exception:  # noqa: BLE001
        age_text = None

    return _alert(
        alert_type_name="BUILDER_FEED_STALE",
        severity_name="CRITICAL",
        title="Live deployment is running on a stopped feed",
        message=(
            f"A live deployment's {timeframe or '?'} feed last delivered "
            f"{age_text or 'an unknown time'} ago, which is at or past "
            f"{stale_after if stale_after is not None else '3 intervals'}s - three expected "
            f"bar intervals. The feed is reported STALE and real orders are being routed "
            f"from whatever the deployment last computed."
        ),
        source=SOURCE_FEED_STATE,
        details={
            "metric": "market_data.feed_state",
            "state": "STALE",
            "reason": reason,
            "mode": mode,
            "timeframe": timeframe,
            "age_seconds": None if age is None else round(float(age), 3),
            "age_text": age_text,
            "expected_interval_seconds": interval,
            "stale_after_seconds": stale_after,
            "stale_at_intervals": _stale_at_intervals(),
            "intervals_behind": intervals_behind,
        },
        suggested_action=(
            "Check the market-data transport for this interval, then decide whether to "
            "pause the deployment. A STALE feed does not stop a deployment on its own; "
            "the runtime's readiness gate stops nodes that have no fresh input, and a "
            "position already open stays open."
        ),
    )


def _is_live_mode(mode: str) -> bool:
    """``mode`` against ``market_data_contract``'s vocabulary, not against a literal."""
    try:
        from backend_app.backend.market_data_contract import MODE_LIVE

        return mode == str(MODE_LIVE)
    except Exception:  # noqa: BLE001
        return mode == "live"


def _measured_stale_reason() -> str:
    """``feed_state.REASON_AGE_OVER_STALE``, the one arm that is a measured stop."""
    try:
        from backend_app.backend.feed_state import REASON_AGE_OVER_STALE

        return str(REASON_AGE_OVER_STALE)
    except Exception:  # noqa: BLE001
        return "AGE_AT_OR_OVER_3_INTERVALS"


def _stale_at_intervals() -> float:
    try:
        from backend_app.backend.feed_state import STALE_AT_INTERVALS

        return float(STALE_AT_INTERVALS)
    except Exception:  # noqa: BLE001
        return 3.0


# ══════════════════════════════════════════════════════════════════════════
#  8. CONDITION 3 - TRAINING QUEUE DEPTH BEYOND THRESHOLD
# ══════════════════════════════════════════════════════════════════════════


def training_queue_depth_condition(queued: Any) -> Optional[BuilderAlert]:
    """Fires when the authoritative ``QUEUED`` count reaches the threshold.

    ``queued`` is ``ml_training_policy.JobCounts.global_queued`` as read by
    ``training_worker.count_all_training_jobs`` - the service-role count, which is the only
    genuinely global one (an RLS-scoped read reports the caller's own jobs as a lower
    bound). No count is taken here; a second query would be a second answer.

    The threshold is :func:`training_queue_depth_threshold`, i.e. drain cycles of the pool
    width rather than an absolute. See the module docstring.
    """
    try:
        depth = int(queued)
    except (TypeError, ValueError):
        return None
    if depth < 0:
        return None

    threshold = training_queue_depth_threshold()
    if depth < threshold:
        return None

    width = training_pool_width()
    cycles = round(depth / width, 2) if width else None
    return _alert(
        alert_type_name="BUILDER_TRAINING_QUEUE_DEPTH",
        severity_name="MEDIUM",
        title="Training queue is deeper than the fleet can drain",
        message=(
            f"{depth} training jobs are QUEUED against a shared pool {width} jobs wide, "
            f"at or past the alert depth of {threshold}. A job joining now waits behind "
            f"roughly {cycles} full passes of the fleet."
        ),
        source=SOURCE_TRAINING_QUEUE,
        details={
            "metric": "training.jobs.by_status",
            "status": "QUEUED",
            "queued": depth,
            "threshold": threshold,
            "pool_width": width,
            "drain_cycles_at_threshold": TRAINING_QUEUE_DRAIN_CYCLES,
            "drain_cycles_now": cycles,
        },
        suggested_action=(
            "Check whether the training workers are alive and claiming, then widen the "
            "pool (ML_TRAINING_MAX_CONCURRENT_GLOBAL) or let the queue drain. Queued jobs "
            "are held, not rejected, so nothing has been lost."
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
#  9. CONDITION 4 - ASSET UNIVERSE AGE BEYOND 12 h
# ══════════════════════════════════════════════════════════════════════════


def asset_universe_age_condition(age_seconds: Any = None) -> Optional[BuilderAlert]:
    """Fires when the served universe snapshot is two TTLs old.

    ``age_seconds`` defaults to the current value of task 9.1's
    ``builder.assets.universe_age_seconds`` gauge, which is set from the snapshot's own
    ``source_meta`` every time the universe is served. A gauge nobody has set reads
    ``None`` - deliberately, per task 9.1 - and ``None`` is not an age of zero, so an
    unserved universe does not read as a fresh one **and** does not page.
    """
    age = age_seconds
    if age is None:
        collector = _metrics()
        gauge = getattr(collector, "builder_assets_universe_age_seconds", None) if collector else None
        if gauge is None:
            return None
        try:
            age = gauge.get()
        except Exception:  # noqa: BLE001
            return None
    if age is None:
        return None

    try:
        seconds = float(age)
    except (TypeError, ValueError):
        return None
    if seconds != seconds or seconds in (float("inf"), float("-inf")) or seconds < 0:
        return None

    threshold = universe_age_threshold_seconds()
    if seconds <= threshold:
        return None

    ttl = universe_ttl_seconds()
    age_text = None
    try:
        from backend_app.backend.feed_state import format_age

        age_text = format_age(seconds)
    except Exception:  # noqa: BLE001
        age_text = None

    return _alert(
        alert_type_name="BUILDER_ASSET_UNIVERSE_STALE",
        severity_name="HIGH",
        title="Asset universe snapshot is beyond two cache lifetimes",
        message=(
            f"The market universe being served is {age_text or f'{int(seconds)}s'} old, "
            f"past the {threshold}s alert age ({UNIVERSE_AGE_TTL_MULTIPLE} x the {ttl}s "
            f"TTL). The refresher runs every half TTL, so it has missed at least four "
            f"consecutive passes. Authors are choosing markets from a list that may name "
            f"delisted symbols and may omit new ones."
        ),
        source=SOURCE_ASSET_UNIVERSE,
        details={
            "metric": "builder.assets.universe_age_seconds",
            "age_seconds": round(seconds, 3),
            "age_text": age_text,
            "threshold_seconds": threshold,
            "ttl_seconds": ttl,
            "ttl_multiple": UNIVERSE_AGE_TTL_MULTIPLE,
        },
        suggested_action=(
            "Check the asset-universe refresher and the cache it writes to. Discovery runs "
            "off the request path, so a dead refresher degrades the symbol list silently "
            "rather than failing a request."
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
#  10. THE PULL PATH - what a scrape or a drill can evaluate with no seam
# ══════════════════════════════════════════════════════════════════════════


def evaluate() -> List[BuilderAlert]:
    """Every condition that can be decided from task 9.1's metrics alone.

    Two of the four: the non-finite counter and the universe-age gauge are both readable
    from the collector, so they can be evaluated by anything on any schedule. The other two
    need a fact the collector does not hold - which deployment mode observed the feed, and
    the service-role ``QUEUED`` count - so they are raised at the seam that holds it. That
    asymmetry is a property of the facts, not an omission.

    Never raises, and never partially fails: a condition that cannot be read contributes
    nothing.
    """
    alerts: List[BuilderAlert] = []
    for condition in (intent_blocked_non_finite_condition, asset_universe_age_condition):
        try:
            fired = condition()
        except Exception:  # noqa: BLE001
            logger.debug("Builder alert condition %s failed.", condition.__name__, exc_info=True)
            continue
        if fired is not None:
            alerts.append(fired)
    return alerts


# ══════════════════════════════════════════════════════════════════════════
#  11. DELIVERY - through the platform's dispatcher, never awaited at a seam
# ══════════════════════════════════════════════════════════════════════════


async def deliver(alert: BuilderAlert) -> Any:
    """Hand one :class:`BuilderAlert` to ``core/alerting_system.py``.

    ``tenant_id`` and ``execution_id`` are left ``None``: these are platform-health facts
    and the dispatcher deduplicates on ``(type, source, tenant_id)``, so a ``None`` tenant
    is what makes one condition one dedup identity rather than one per customer.

    Returns whatever the dispatcher returns - an ``Alert``, or ``None`` when the five-minute
    window deduplicated it. A deduplicated alert is not a failure.
    """
    system, alert_type_enum, severity_enum = _alerting()
    if system is None:
        _log_alert(alert, "the alerting system could not be reached")
        return None
    try:
        alert_type = getattr(alert_type_enum, alert.alert_type_name)
        severity = getattr(severity_enum, alert.severity_name)
    except AttributeError:
        _log_alert(alert, f"{alert.alert_type_name}/{alert.severity_name} is not a known alert")
        return None
    return await system.send_alert(
        alert_type=alert_type,
        severity=severity,
        title=alert.title,
        message=alert.message,
        source=alert.source,
        tenant_id=None,
        execution_id=None,
        details=dict(alert.details),
        suggested_action=alert.suggested_action,
    )


def _log_alert(alert: BuilderAlert, why: str) -> None:
    """The channel of last resort. An alert that cannot be delivered is still recorded."""
    line = f"BUILDER ALERT [{alert.severity_name}] {alert.title}: {alert.message} ({why}) {alert.details}"
    if alert.severity_name == "CRITICAL":
        logger.critical(line)
    elif alert.severity_name == "HIGH":
        logger.error(line)
    elif alert.severity_name == "MEDIUM":
        logger.warning(line)
    else:
        logger.info(line)


@_never_fails
def raise_alerts(alerts: Any) -> int:
    """Schedule delivery of zero or more alerts. **Returns immediately.**

    The whole point of this function. Delivery is an ``await`` over a webhook, an SMTP
    session and a Redis publish; the seams that call it are refusing a trade intent,
    classifying a feed and admitting a training job. So the coroutine is handed to
    ``core.background_tasks.fire_and_forget_task`` - the platform's tracked helper, which
    keeps a strong reference so the task is not garbage-collected mid-flight and logs any
    exception it raises - and this returns the number scheduled.

    With no running event loop there is nothing to schedule onto, and the alert is written
    to the log at its own severity instead. ``asyncio.run`` here would block the caller on
    an HTTP POST, which is precisely the delay this module is forbidden to introduce.

    Returns the number of alerts handed to the loop (0 when they were logged instead).
    """
    pending = [a for a in (alerts or []) if isinstance(a, BuilderAlert)]
    if not pending:
        return 0

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        for alert in pending:
            _log_alert(alert, "no running event loop, so delivery was not scheduled")
        return 0

    try:
        from backend_app.core.background_tasks import fire_and_forget_task
    except Exception:  # noqa: BLE001
        for alert in pending:
            _log_alert(alert, "the background task helper could not be reached")
        return 0

    scheduled = 0
    for alert in pending:
        try:
            fire_and_forget_task(deliver(alert), name=f"builder_alert:{alert.alert_type_name}")
            scheduled += 1
        except Exception:  # noqa: BLE001
            _log_alert(alert, "delivery could not be scheduled")
    return scheduled


# ══════════════════════════════════════════════════════════════════════════
#  12. THE SEAM ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════
#
# One per condition, each synchronous, total and non-blocking, each returning the
# `BuilderAlert` it raised (or `None`) so a test can assert the verdict without asserting
# on a delivery. These are the only names a call site needs.


@_never_fails
def notice_intent_blocked_non_finite() -> Optional[BuilderAlert]:
    """Requirement 24.4, called from ``dag_engine.assert_execution_safe``'s refusal."""
    alert = intent_blocked_non_finite_condition()
    if alert is not None:
        raise_alerts([alert])
    return alert


@_never_fails
def notice_feed_state(
    report: Any, deployment_mode: Optional[str] = None
) -> Optional[BuilderAlert]:
    """Requirement 24.5, called from ``feed_state.evaluate_feed_state``'s report closure."""
    alert = feed_state_condition(report, deployment_mode)
    if alert is not None:
        raise_alerts([alert])
    return alert


@_never_fails
def notice_training_queue_depth(queued: Any) -> Optional[BuilderAlert]:
    """Called from ``training_worker.count_all_training_jobs``'s service-role count."""
    alert = training_queue_depth_condition(queued)
    if alert is not None:
        raise_alerts([alert])
    return alert


@_never_fails
def notice_asset_universe_age(age_seconds: Any = None) -> Optional[BuilderAlert]:
    """Called where the universe age gauge is set, i.e. where the universe is served."""
    alert = asset_universe_age_condition(age_seconds)
    if alert is not None:
        raise_alerts([alert])
    return alert


#: The four alert type names this module raises, in the order the task lists them. Named
#: once so ``core/alerting_system.AlertType`` and any test read the same list.
BUILDER_ALERT_TYPE_NAMES: Tuple[str, ...] = (
    "BUILDER_INTENT_BLOCKED_NON_FINITE",
    "BUILDER_FEED_STALE",
    "BUILDER_TRAINING_QUEUE_DEPTH",
    "BUILDER_ASSET_UNIVERSE_STALE",
)


__all__ = [
    "BuilderAlert",
    "BUILDER_ALERT_TYPE_NAMES",
    "CREDENTIAL_WORDS",
    "IDENTITY_WORDS",
    "TRAINING_QUEUE_DRAIN_CYCLES",
    "UNIVERSE_AGE_TTL_MULTIPLE",
    "SOURCE_ASSET_UNIVERSE",
    "SOURCE_FEED_STATE",
    "SOURCE_INTENT_GATE",
    "SOURCE_TRAINING_QUEUE",
    "asset_universe_age_condition",
    "blocked_non_finite_total",
    "deliver",
    "evaluate",
    "feed_state_condition",
    "forbidden_detail_key",
    "intent_blocked_non_finite_condition",
    "notice_asset_universe_age",
    "notice_feed_state",
    "notice_intent_blocked_non_finite",
    "notice_training_queue_depth",
    "raise_alerts",
    "reset_state",
    "safe_details",
    "training_pool_width",
    "training_queue_depth_condition",
    "training_queue_depth_threshold",
    "universe_age_threshold_seconds",
    "universe_ttl_seconds",
]
