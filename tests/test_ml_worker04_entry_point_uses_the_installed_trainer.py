"""ML-WORKER-04 -- `python -m backend_app.backend.training_worker` uses the trainer it installs.

Production, 2026-11-21: the new training worker claimed the first real job and failed it
TRAINER_UNAVAILABLE ~10 s later, although the trainer installs fine. Cause: run with
`-m`, the file executes as the module `__main__`; `install_training_runtime()` then does
`from backend_app.backend.training_worker import register_training_backend`, which loads
the SAME file a second time under its real name. The trainer was registered in that
second module's `_TRAINING_BACKEND` while the running worker (`__main__`) read its own,
still empty, one. The entry point now hands control to the canonical module.
"""
import runpy
import sys

import pytest

import backend_app.backend.training_worker as canonical


def test_running_as_main_delegates_to_the_canonical_module(monkeypatch):
    called = []
    monkeypatch.setattr(canonical, "run_worker", lambda: called.append("canonical"))

    # If the __main__ copy runs its own loop (the bug), fail fast instead of hanging CI.
    import asyncio

    def _own_loop_started(*_a, **_k):
        raise AssertionError("the __main__ copy started its own worker loop")

    monkeypatch.setattr(asyncio, "run", _own_loop_started)
    # Exactly how the ECS task starts it: as __main__.
    runpy.run_module("backend_app.backend.training_worker", run_name="__main__")
    assert called == ["canonical"], (
        "the __main__ copy ran its own loop; a trainer installed via the real module "
        "name would be invisible to it (TRAINER_UNAVAILABLE in production)"
    )


def test_the_trainer_installed_by_the_runtime_is_the_one_the_worker_reads():
    from backend_app.backend.training_runtime import install_training_runtime

    canonical.register_training_backend(None)
    try:
        assert install_training_runtime() is True
        backend = canonical.current_training_backend()
        assert backend is not None and backend.name == "governed-tree-runtime"
    finally:
        canonical.register_training_backend(None)
