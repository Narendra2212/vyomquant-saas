"""PAPER-06 -- the feed-disconnect writer now shares the one sequence allocator.

THE DEFECT
    ``paper_market_feed.FeedHandle._emit_feed_disconnected`` allocated its sequence number from
    ``paper_repository.next_session_event_sequence`` (``max(paper_events.sequence) + 1``, read off
    the LOG), while every other Paper_Channel event (``paper_events.record_event`` ->
    ``next_sequence`` -> ``paper_repository.allocate_session_event_sequence``) allocates from an
    atomic compare-and-swap on the ``paper_sessions.event_sequence`` COUNTER.

    Those two can disagree: the feed writer's INSERT advances the log but never touches the
    counter. So after a feed-disconnect record lands at log sequence N, the counter is still
    stuck one or more behind it. The next ordinary event's counter-based allocation then reads
    that STALE counter and can allocate a number <= N, colliding with the sequence the
    feed-disconnect writer already used. ``uq_paper_event_seq`` refuses the ordinary event's
    INSERT with ``PaperDuplicateSessionEvent``, and nothing between ``record_event`` and the
    bar-stepping loop (``paper_session_service._emit`` / ``_step_bar``) catches and retries it --
    so that bar is dropped rather than recorded.

THE FIX
    ``_emit_feed_disconnected`` now allocates from ``allocate_session_event_sequence``, the same
    counter every other event uses. There is only one source of truth for "the next sequence
    number" left, so this race cannot happen at all -- not "retried successfully", eliminated.

WHAT THIS FILE PROVES
    1. The feed-disconnect writer's own source code calls ``allocate_session_event_sequence``,
       not ``next_session_event_sequence`` (so a regression that reverts the one-line fix is
       caught even if the scenario below somehow didn't trip it).
    2. After a real feed-disconnect event, the COUNTER (``paper_sessions.event_sequence``) matches
       the LOG's own highest sequence -- the two can no longer diverge.
    3. End to end: a feed disconnect followed immediately by an ordinary event (exactly the
       sequence that used to collide) allocates CONSECUTIVE sequence numbers and both rows land,
       with no ``PaperDuplicateSessionEvent`` anywhere.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

from backend_app.backend.paper import paper_events as events
from backend_app.backend.paper import paper_market_feed as feed
from tests.test_paper_market_feed_selection import (
    SESSION,
    USER,
    RecordingRedis,
    _admitting,
    _client,
    _open,
    _run_coroutine,
)
from tests.test_paper_market_feed_selection import (
    _fresh_probe,  # noqa: F401 - autouse, used by name
)


def test_the_feed_disconnect_writer_uses_the_shared_counter_allocator() -> None:
    """A regression that reverts PAPER-06's one-line fix is caught even without a live collision."""
    source = textwrap.dedent(inspect.getsource(feed.FeedHandle._emit_feed_disconnected))
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"allocate_session_event_sequence", "next_session_event_sequence"}
    }
    assert called == {"allocate_session_event_sequence"}, (
        "the feed-disconnect writer must allocate from the same counter-based "
        "allocator every other Paper_Channel event uses"
    )


def test_a_feed_disconnect_advances_the_shared_counter_not_just_the_log() -> None:
    client = _client()
    redis = RecordingRedis()
    handle = _open(redis=redis, measurements=_admitting(), supabase=client)

    _run_coroutine(handle.mark_degraded("the subscription ended"))

    assert len(client.events) == 1
    log_sequence = client.events[0]["sequence"]
    counter_value = client.sessions[0]["event_sequence"]
    assert counter_value == log_sequence == 1, (
        "the counter paper_sessions.event_sequence and the log's own highest sequence must "
        "agree after a feed-disconnect write -- they are now the same allocator"
    )


def test_a_feed_disconnect_followed_by_an_ordinary_event_does_not_collide() -> None:
    """The exact scenario PAPER-06 describes: a feed-disconnect event immediately followed by an
    ordinary event used to be able to collide on the sequence number. Now they cannot."""
    client = _client()
    redis = RecordingRedis()
    handle = _open(redis=redis, measurements=_admitting(), supabase=client)

    _run_coroutine(handle.mark_degraded("the subscription ended"))
    assert len(client.events) == 1
    assert client.events[0]["sequence"] == 1

    envelope = events.record_event(
        client,
        user_id=USER,
        session_id=SESSION,
        event_type=events.PaperEvent.SESSION_PAUSED,
        payload={
            "session_id": SESSION,
            "session_state": "PAUSED",
            "at": events.utc_now(),
            "actor_id": USER,
        },
    )

    assert envelope.sequence == 2, (
        "the ordinary event must allocate the NEXT sequence after the feed-disconnect write, "
        "not a number that collides with it"
    )
    assert len(client.events) == 2
    assert [row["sequence"] for row in client.events] == [1, 2]
