"""ML-STORE-01 -- trained models are stored in the shared bucket, on the API and workers.

Production, 2026-11-21: the ECS training worker trained the QA XGBoost model to early
stopping and then failed saving it: PermissionError: '/app/user_strategies'. A file on
the worker's own disk would also be unreadable by the API tasks that load models. The
code already supports a Supabase Storage bucket via STRATEGY_MODEL_ARTIFACT_BUCKET; the
deploy now sets it on every container it renders.
"""
import sys

sys.path.insert(0, "scripts")
import ecs_deploy_and_diagnose as d  # noqa: E402


def _names(c):
    return {e["name"]: e["value"] for e in c.get("environment", [])}


def test_the_bucket_is_added_to_a_container():
    c = {"name": "vyomquant-api", "environment": []}
    added = d.ensure_model_store_env(c)
    assert "STRATEGY_MODEL_ARTIFACT_BUCKET" in added
    assert _names(c)["STRATEGY_MODEL_ARTIFACT_BUCKET"] == "model-artifacts"


def test_an_operator_value_is_never_overwritten():
    c = {"environment": [{"name": "STRATEGY_MODEL_ARTIFACT_BUCKET", "value": "other"}]}
    d.ensure_model_store_env(c)
    assert _names(c)["STRATEGY_MODEL_ARTIFACT_BUCKET"] == "other"


def test_the_code_reads_the_same_variable():
    from backend_app.backend import model_versioning as mv

    assert mv.ARTIFACT_BUCKET_ENV == "STRATEGY_MODEL_ARTIFACT_BUCKET"


def test_both_the_api_and_worker_render_paths_apply_it():
    import inspect
    src = inspect.getsource(d)
    assert src.count("ensure_observability_env(container) + ensure_model_store_env(container)") == 2
