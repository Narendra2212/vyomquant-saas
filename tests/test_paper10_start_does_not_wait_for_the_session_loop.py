"""PAPER-10 -- starting a paper session must not wait for the session to end.

Production: ``POST /api/paper/sessions`` created and ran the session, then never answered (a 504 at the
gateway's 30 s). ``_spawn`` awaited whatever the spawner returned when it was awaitable, and the real
spawner returns the loop's ``asyncio.Task`` -- awaitable, and finished only when the session stops.
"""
from __future__ import annotations

import asyncio

from backend_app.backend.paper import paper_session_service as pss

SESSION = {"id": "s-1"}


def _run(spawner):
    return asyncio.run(_spawn_once(spawner))


async def _spawn_once(spawner):
    return await pss._spawn(spawner, SESSION, object(), config=object(), account_id="a-1")


def test_a_running_loop_task_is_returned_not_awaited():
    async def scenario():
        gate = asyncio.Event()

        async def long_running_loop():
            await gate.wait()  # a session loop: finishes only when the session stops
            return 7

        def spawner(session, feed, *, config, account_id):
            return asyncio.create_task(long_running_loop())

        # If _spawn awaited the task this would never finish and wait_for would raise.
        task = await asyncio.wait_for(
            pss._spawn(spawner, SESSION, object(), config=object(), account_id="a-1"), timeout=2
        )
        assert isinstance(task, asyncio.Task) and not task.done()
        gate.set()
        assert await task == 7

    asyncio.run(scenario())


def test_a_coroutine_spawner_is_still_awaited():
    async def spawner(session, feed, *, config, account_id):
        return "loop-handle"

    assert _run(spawner) == "loop-handle"


def test_a_plain_return_is_passed_through():
    assert _run(lambda session, feed, *, config, account_id: None) is None


def test_a_spawner_that_raises_is_logged_and_swallowed():
    def spawner(session, feed, *, config, account_id):
        raise RuntimeError("boom")

    assert _run(spawner) is None
