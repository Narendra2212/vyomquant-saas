"""ML-WORKER-05 -- a failed training job's exception reaches Sentry with the job as tags.

Production, 2026-11-21: the first job the ECS training worker trained reached round 31
of 200 and ended FAILED / TRAINING_RUNTIME_ERROR. The job row holds only the classified
reason and the worker had no Sentry client, so the traceback existed in CloudWatch alone.
"""
import asyncio

import pytest

import backend_app.backend.training_worker as W

JOB = {"id": "job-1", "block_id": "xgboost", "version_id": "ver-1"}


class _Scope:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set_tag(self, k, v):
        self.sink.setdefault("tags", {})[k] = v

    def set_extra(self, k, v):
        self.sink.setdefault("extra", {})[k] = v


@pytest.fixture
def sentry(monkeypatch):
    import sentry_sdk

    sink = {"captured": []}
    monkeypatch.setattr(sentry_sdk, "new_scope", lambda: _Scope(sink))
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda e: sink["captured"].append(e))
    return sink


def test_a_runtime_error_is_sent_with_the_job(sentry):
    err = RuntimeError("xgboost exploded at round 31")
    W._report_training_failure(err, JOB, W.FAILURE_TRAINING_RUNTIME_ERROR)
    assert sentry["captured"] == [err]
    assert sentry["tags"] == {"training_failure_reason": "TRAINING_RUNTIME_ERROR",
                              "training_block_id": "xgboost"}
    assert sentry["extra"]["training_job_id"] == "job-1"


@pytest.mark.parametrize("exc", [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(0)])
def test_control_flow_exits_are_not_reported(sentry, exc):
    W._report_training_failure(exc, JOB, "CANCELLED")
    assert sentry["captured"] == []


def test_a_sentry_outage_never_changes_the_outcome(monkeypatch):
    import sentry_sdk

    def boom(*_a, **_k):
        raise OSError("sentry down")

    monkeypatch.setattr(sentry_sdk, "new_scope", boom)
    W._report_training_failure(RuntimeError("x"), JOB, "TRAINING_RUNTIME_ERROR")  # no raise


def test_the_worker_reports_from_its_failure_path():
    import inspect

    src = inspect.getsource(W)
    assert "_report_training_failure(exc, job, reason)" in src
    assert "initialize_sentry(" in inspect.getsource(W._main)
