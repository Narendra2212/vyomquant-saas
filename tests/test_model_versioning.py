"""
tests/test_model_versioning.py

Model versioning and artifact handling: what gets stored, what gets recorded, what a
client can see, and what retraining cannot touch.

Spec: strategy-builder task 6.5 (`design.md` -> Training workflow ->
``persist_artifact``/``insert_model_version``/``bind_model_to_version_node``, -> Database
-> "-- 3. Model versions", -> Security surface -> "Model version + artifact").
Requirements 17.1, 17.2, 17.3, 17.5, 17.8, 17.9, 21.2, 21.7.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
Six properties, each of which would be a real defect - and three of them a security
defect - if it stopped holding:

1. **Retraining cannot overwrite the artifact a running deployment is executing**
   (Requirement 17.3, the ML-3 class). Asserted twice over, because the implementation
   guards it twice: the key carries the artifact's own checksum, and the store refuses to
   write a key that already holds different bytes. A second run for the same node produces
   a second row and a second file, and the first file's bytes are still readable
   afterwards.
2. **At most one active model version per (version, node)** (Requirement 17.4). The
   database double enforces ``uq_mv_active_per_node``'s partial predicate, so "the
   superseded row is deactivated in the same operation as the insert" is asserted by the
   insert being *accepted*, and the compensating re-activation is asserted by making the
   insert fail.
3. **A version is ``READY`` only when EVERY ML node is bound** (Requirement 17.5). A
   partial binding is not ``READY``, and the unbound node is named.
4. **``artifact_uri`` never reaches a client** (Requirement 17.8, and ``design.md``'s
   security surface). Asserted on the binder's return value, on the realtime
   ``training.completed`` frame the worker publishes from it, and on the HTTP response of
   both endpoints - by searching the whole serialised payload for the string, not by
   checking one key.
5. **Every path segment and every served file name goes through
   ``ml_models._safe_filename``** - the existing ML-5 fix, reused. A ``../../etc/passwd``
   in a user id, a strategy id, a node id or a block id produces a key inside the root and
   a ``Content-Disposition`` with no path in it.
6. **The signed download is ownership-checked at both ends.** A tampered, expired,
   wrong-purpose or foreign token opens nothing, and every refusal is the same 404.

Plus: the row records what Requirement 17.1 lists (reference, checksum, size,
serialization, feature schema, hyperparameters and train/val/test metrics); the
hyperparameters document is built from a whitelist, so a key injected into
``training_jobs.config`` is not served back (Requirement 21.7); and every path touching
``model_versions`` degrades with a warning naming ``004d_training_and_models.sql`` rather
than raising a 500.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: the assembled registry; a canonical graph built from published descriptors; the
compiler; the validator; ``market_data_validation``; the feature pipeline; ``ml_dataset``;
``ml_training_policy``; ``core.ml_safety``'s ``SafeModelLoader``, ``MemoryMonitor`` and
``DeterministicEnforcer``; ``strategy_service``'s whole admission path, which is what
CREATES the jobs these tests run; the whole of ``training_worker``; the whole of
``model_versioning``; the real FastAPI app and router for the endpoint tests; and a real
filesystem artifact store writing real bytes into ``tmp_path``.

Doubles, and only these four - the same three task 6.4 used, plus the artifact store's
root being a temporary directory:

* **The database.** ``ModelStore`` below extends task 6.4's ``FakeSupabase`` with the
  ``model_versions`` table and its two unique constraints. A double because there is no
  local PostgreSQL and ``004d_training_and_models.sql`` is unapplied.
* **The candle feed** (``strategy_service.fetch_training_bars``), the one I/O boundary.
* **The realtime hand-off**, recorded rather than broadcast.

**The trainer is not a double**: ``GradientDescentTrainer`` from the task 6.4 suite is a
real logistic regression fitted over the real dataset, and it is installed through the
production ``TrainingBackend`` seam - with :func:`model_versioning.bind_trained_model` as
the binder, which is the production binder and not a recording stub. The artifacts written
below are real joblib bytes of a real fitted model.

WHAT IS NOT VERIFIED HERE
-------------------------
No real ``model_versions`` table: ``uq_mv_active_per_node``, ``uq_mv_version_node``, the
RLS policies (``mv_owner_select``, ``mv_owner_insert``, and the deliberate ABSENCE of an
UPDATE policy) and the grants are exercised against a Python double shaped like them, so
live enforcement remains 004d's own VERIFICATION queries plus task 8.7's isolation matrix.
No Supabase storage bucket is reachable, so :class:`SupabaseStorageArtifactStore` is
exercised against a double of the bucket API and not against Supabase. Nothing here
deploys a model or runs inference against one; the checksum-at-load and
feature-schema-at-deploy dispositions (Requirements 17.6, 17.7) belong to phase 8.
"""

import hashlib
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import model_versioning as MV
from backend_app.backend import strategy_service as S
from backend_app.backend import training_worker as W
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.core import ml_safety

from tests.test_training_worker import (  # noqa: E402 - shared doubles, deliberately
    PAID_TIER,
    STRATEGY_ID,
    USER_ID,
    FakeSupabase,
    GradientDescentTrainer,
    MissingRelation,
    UniqueViolation,
    bounded_bars,
    model_graph,
    owner,
    queue_job,
)

SECRET = "test-artifact-signing-secret-at-least-32-chars"


# ═══════════════════════════════════════════════════════════════════════════
# The database double, extended with model_versions and its two unique rules
# ═══════════════════════════════════════════════════════════════════════════


class ModelStore(FakeSupabase):
    """Task 6.4's store plus ``model_versions`` and the constraints this file is about.

    Enforced, because the tests turn on them:

    * ``uq_mv_active_per_node`` - the **partial** unique index over
      ``(version_id, node_id) WHERE is_active``, on inserts AND on updates, because
      "flip one row active" is the other way two active rows could appear;
    * ``uq_mv_version_node`` - ``(version_id, node_id, model_version)``, so a reused
      version number is a rejected row rather than an ambiguous history;
    * the NOT NULL set 004d declares, so a row missing ``artifact_checksum`` or
      ``feature_schema`` is refused here the way PostgreSQL would refuse it.

    Not enforced, and not pretended to be: RLS, foreign keys, column types, and the
    absence of an UPDATE policy. Those belong to 004d's VERIFICATION queries and to task
    8.7's isolation matrix.
    """

    #: 004d's NOT NULL columns, minus the ones with defaults.
    REQUIRED = (
        "user_id",
        "training_job_id",
        "strategy_id",
        "version_id",
        "node_id",
        "block_id",
        "model_version",
        "artifact_uri",
        "artifact_checksum",
        "artifact_bytes",
        "serialization",
        "feature_schema",
        "hyperparameters",
    )

    @classmethod
    def seeded(cls, *, with_training_jobs=True, with_model_versions=True, tier=PAID_TIER):
        store = super().seeded(with_training_jobs=with_training_jobs, tier=tier)
        if with_model_versions:
            store.tables["model_versions"] = []
        store.insert_failure = None
        store.counter = 0
        return store

    @property
    def models(self):
        return self.tables.get("model_versions", [])

    def active_models(self, version_id=None, node_id=None):
        return [
            row
            for row in self.models
            if row.get("is_active")
            and (version_id is None or row.get("version_id") == version_id)
            and (node_id is None or row.get("node_id") == node_id)
        ]

    def _assert_model_constraints(self, candidate, *, ignore=None):
        missing = [c for c in self.REQUIRED if candidate.get(c) is None]
        if missing:
            raise Exception(
                f'null value in column "{missing[0]}" of relation "model_versions" '
                f"violates not-null constraint"
            )
        for existing in self.models:
            if existing is ignore:
                continue
            same_node = (
                existing.get("version_id") == candidate.get("version_id")
                and existing.get("node_id") == candidate.get("node_id")
            )
            if not same_node:
                continue
            if existing.get("model_version") == candidate.get("model_version"):
                raise UniqueViolation("uq_mv_version_node")
            if existing.get("is_active") and candidate.get("is_active"):
                raise UniqueViolation("uq_mv_active_per_node")

    def run(self, query):
        if query.op == "insert" and query.table == "model_versions":
            if "model_versions" not in self.tables:
                raise MissingRelation("model_versions")
            if self.insert_failure is not None:
                raise self.insert_failure
            self.counter += 1
            row = dict(query.payload)
            row.setdefault("id", f"mv_{self.counter:04d}")
            row.setdefault("is_active", True)
            row.setdefault("created_at", "2026-01-01T00:00:00+00:00")
            self._assert_model_constraints(row)
            self.models.append(row)
            return _result([dict(row)])

        if query.op == "update" and query.table == "model_versions":
            matched = self._match(query)
            self.writes.append(
                {
                    "table": query.table,
                    "payload": dict(query.payload),
                    "filters": list(query.filters),
                    "matched": len(matched),
                }
            )
            for row in matched:
                self._assert_model_constraints({**row, **query.payload}, ignore=row)
                row.update(query.payload)
            return _result([dict(row) for row in matched])

        return super().run(query)


def _result(data):
    from tests.test_training_worker import FakeResult

    return FakeResult(data)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures - the task 6.4 suite's, re-declared because fixtures are per-module
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def reg():
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def default_hooks():
    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


@pytest.fixture(autouse=True)
def forget_column_probe():
    S.reset_canonical_column_support()
    yield
    S.reset_canonical_column_support()


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """No installed backend, no installed store, artifacts under ``tmp_path``.

    Every one of these is process-global; leaving one set would make a later test in this
    file - or in another file - pass or fail depending on collection order.
    """
    W.reset_training_backend()
    W.reset_isolation_latch()
    MV.reset_artifact_store()
    memory_before = ml_safety.MemoryMonitor._config
    training_before = ml_safety.TrainingIsolator._config
    ml_safety.TrainingIsolator.configure(
        ml_safety.TrainingConfig(enable_process_isolation=False)
    )
    monkeypatch.setenv(MV.ARTIFACT_ROOT_ENV, str(tmp_path / "artifacts"))
    monkeypatch.delenv(MV.ARTIFACT_BUCKET_ENV, raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    yield
    ml_safety.MemoryMonitor._config = memory_before
    ml_safety.TrainingIsolator._config = training_before
    W.reset_training_backend()
    W.reset_isolation_latch()
    MV.reset_artifact_store()


@pytest.fixture
def window(monkeypatch):
    async def _fetch(symbol, timeframe, bars):
        return bounded_bars(int(bars))

    monkeypatch.setattr(S, "fetch_training_bars", _fetch)
    return {}


@pytest.fixture(autouse=True)
def handoffs(monkeypatch):
    recorded = {"enqueued": [], "events": []}

    async def _enqueue(job_id, user_id):
        recorded["enqueued"].append(str(job_id))
        return True

    async def _publish(user_id, event, payload):
        recorded["events"].append((event, dict(payload)))
        return True

    monkeypatch.setattr(S, "enqueue_training_job", _enqueue)
    monkeypatch.setattr(S, "publish_training_event", _publish)
    return recorded


@pytest.fixture
def db():
    return ModelStore.seeded()


@pytest.fixture
def service(db):
    from backend_app.backend.strategy_service import StrategyService

    with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=db)):
        yield StrategyService()


@pytest.fixture
def store(tmp_path):
    """A real filesystem store under ``tmp_path``, installed for the process."""
    local = MV.LocalArtifactStore(tmp_path / "artifacts")
    MV.register_artifact_store(local)
    return local


def install_production_binder(trainer_factory=None, **trainer_kwargs):
    """Install the PRODUCTION binder through the production seam.

    Task 6.4's suite installs a recording stub here. This file installs
    :func:`model_versioning.bind_trained_model`, which is the whole point: what is under
    test is the artifact and the row a completed run actually produces.
    """
    factory = trainer_factory or (lambda ctx: GradientDescentTrainer(ctx, **trainer_kwargs))
    W.register_training_backend(MV.training_backend(factory))


def a_row(**overrides):
    """A ``model_versions``-shaped mapping for the projection and naming tests."""
    row = {
        "id": "mv_0001",
        "user_id": USER_ID,
        "training_job_id": "job_1",
        "strategy_id": STRATEGY_ID,
        "version_id": "ver_1",
        "node_id": "model_node",
        "block_id": "xgboost",
        "model_version": 3,
        "artifact_uri": "local://models/u/s/v/n/v3-abcdef0123456789.joblib",
        "artifact_checksum": "a" * 64,
        "artifact_bytes": 2048,
        "serialization": MV.SERIALIZATION_JOBLIB,
        "feature_schema": {"feature_names": ["ema_20_lag_1"], "feature_count": 1},
        "hyperparameters": {"epochs_total": 4},
        "train_metrics": {"loss": 0.5},
        "val_metrics": {"loss": 0.6},
        "test_metrics": {"loss": 0.7},
        "is_active": True,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


# ═══════════════════════════════════════════════════════════════════════════
# 1. The vocabulary, and what is reused rather than restated
# ═══════════════════════════════════════════════════════════════════════════


class TestTheVocabularyIsReused:
    def test_the_table_and_the_migration_are_named_once(self):
        assert MV.MODEL_VERSIONS_TABLE == "model_versions"
        # The same migration as ``training_jobs``: one file, one constant, so a warning
        # cannot name a file that does not create the table it is talking about.
        assert MV.MODEL_MIGRATION is S.TRAINING_MIGRATION
        assert MV.MODEL_MIGRATION.endswith("004d_training_and_models.sql")

    def test_the_sanitiser_is_the_ml5_fix_itself_and_not_a_local_copy(self):
        """Requirement 17.8's sanitisation is ``ml_models._safe_filename``, imported.

        Identity, not equivalence: two sanitisers that agree today are one edit away from
        disagreeing, and the ML-5 test suite only covers one of them.
        """
        from backend_app.backend import ml_models

        assert MV._safe_filename is ml_models._safe_filename

    def test_the_lifecycle_target_is_the_platforms_own_constant(self):
        from backend_app.backend.strategy_builder import LIFECYCLE_READY, LIFECYCLE_STATES

        assert LIFECYCLE_READY == "READY"
        assert LIFECYCLE_READY in LIFECYCLE_STATES

    def test_the_serialization_vocabulary_is_closed(self):
        assert set(MV.ARTIFACT_EXTENSIONS) == {
            MV.SERIALIZATION_JOBLIB,
            MV.SERIALIZATION_PICKLE,
        }
        with pytest.raises(MV.ArtifactStoreError):
            MV.artifact_key(
                user_id=USER_ID,
                strategy_id=STRATEGY_ID,
                version_id="ver",
                node_id="node",
                model_version=1,
                checksum="f" * 64,
                serialization="onnx-we-do-not-write",
            )

    def test_the_artifact_ceiling_is_read_from_memory_monitor_live(self):
        """Read, not copied: tightening the cache tightens what may be written."""
        ml_safety.MemoryMonitor.configure(ml_safety.MemoryConfig(max_model_cache_size_mb=7))
        assert MV.max_artifact_bytes() == 7 * 1024 * 1024
        ml_safety.MemoryMonitor.configure(ml_safety.MemoryConfig(max_model_cache_size_mb=64))
        assert MV.max_artifact_bytes() == 64 * 1024 * 1024


# ═══════════════════════════════════════════════════════════════════════════
# 2. Requirement 17.8 - keys and file names cannot carry a path (ML-5)
# ═══════════════════════════════════════════════════════════════════════════


class TestKeysAndFileNamesAreSanitised:
    HOSTILE = "../../../../etc/passwd"

    def _key(self, **overrides):
        arguments = {
            "user_id": USER_ID,
            "strategy_id": STRATEGY_ID,
            "version_id": "ver_1",
            "node_id": "model_node",
            "model_version": 2,
            "checksum": "b" * 64,
            "serialization": MV.SERIALIZATION_JOBLIB,
        }
        arguments.update(overrides)
        return MV.artifact_key(**arguments)

    @pytest.mark.parametrize(
        "field", ["user_id", "strategy_id", "version_id", "node_id"]
    )
    def test_a_traversal_in_any_identity_segment_is_neutralised(self, field):
        key = self._key(**{field: self.HOSTILE})

        assert ".." not in key
        assert "etc/passwd" not in key
        # Exactly six segments: models / user / strategy / version / node / filename. A
        # traversal that survived would show up as a seventh.
        assert len(key.split("/")) == 6

    def test_the_key_carries_the_artifacts_own_checksum(self):
        """Content-addressed: two different artifacts cannot land on one key.

        Half of why retraining cannot overwrite a running deployment's artifact - the
        store's append-only ``put`` is the other half.
        """
        first = self._key(checksum="1" * 64)
        second = self._key(checksum="2" * 64)

        assert first != second
        assert "1" * 16 in first.rsplit("/", 1)[-1]

    def test_the_key_carries_the_model_version_number(self):
        assert "v2-" in self._key(model_version=2).rsplit("/", 1)[-1]
        assert "v9-" in self._key(model_version=9).rsplit("/", 1)[-1]

    def test_a_short_checksum_cannot_address_an_artifact(self):
        with pytest.raises(MV.ArtifactStoreError):
            self._key(checksum="abc")

    def test_the_extension_follows_the_serialization_mode(self):
        assert self._key(serialization=MV.SERIALIZATION_JOBLIB).endswith(".joblib")
        assert self._key(serialization=MV.SERIALIZATION_PICKLE).endswith(".pkl")

    def test_a_served_file_name_carries_no_path_quote_or_newline(self):
        name = MV.artifact_filename(
            a_row(node_id='../../x"; rm -rf /\n', block_id="../../../etc/passwd")
        )

        assert "/" not in name
        assert ".." not in name
        assert '"' not in name
        assert "\n" not in name
        assert name.endswith(".joblib")

    def test_a_file_name_is_built_from_the_row_and_not_from_a_client_string(self):
        name = MV.artifact_filename(a_row(node_id="ml_1", block_id="xgboost", model_version=3))

        assert name == "xgboost-ml_1-v3.joblib"

    def test_a_key_that_escapes_the_root_is_refused_by_the_store_as_well(self, tmp_path):
        """Defence in depth: even a key the sanitiser never saw cannot escape."""
        local = MV.LocalArtifactStore(tmp_path / "artifacts")
        (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)

        with pytest.raises(MV.ArtifactStoreError):
            local.path_of("../../outside.joblib")
        with pytest.raises(MV.ArtifactStoreError):
            local.put("../../outside.joblib", b"x")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Requirement 17.9 / 17.3 - the store is object storage, and append-only
# ═══════════════════════════════════════════════════════════════════════════


class TestTheStoreIsAppendOnly:
    def test_a_repeated_write_of_the_same_bytes_is_reused_not_rewritten(self, store):
        first = store.put("models/u/s/v/n/v1-aaaaaaaaaaaaaaaa.joblib", b"the model")
        second = store.put("models/u/s/v/n/v1-aaaaaaaaaaaaaaaa.joblib", b"the model")

        assert first == second
        assert store.get(first) == b"the model"

    def test_a_key_holding_different_bytes_is_never_overwritten(self, store):
        """Requirement 17.3 at the storage layer. This is the ML-3 class."""
        key = "models/u/s/v/n/v1-aaaaaaaaaaaaaaaa.joblib"
        uri = store.put(key, b"the deployed model")

        with pytest.raises(MV.ArtifactExists):
            store.put(key, b"a retrained model")

        # The bytes a running deployment would be executing are untouched.
        assert store.get(uri) == b"the deployed model"

    def test_the_store_offers_no_delete_at_all(self, store):
        """004d grants DELETE to nobody; the store matches, structurally."""
        assert not hasattr(store, "delete")
        assert not hasattr(store, "remove")
        assert not any(
            name.startswith("delete") or name.startswith("remove")
            for name in dir(MV.ArtifactStore)
        )

    def test_the_checksum_is_computed_by_the_platforms_own_loader_function(self, store):
        from backend_app.core.ml_safety import SafeModelLoader

        uri = store.put("models/a/b/c/d/v1-cccccccccccccccc.joblib", b"bytes to hash")
        path = store.path_for_uri(uri)

        assert store.checksum(uri) == SafeModelLoader.compute_checksum(str(path))
        assert store.checksum(uri) == hashlib.sha256(b"bytes to hash").hexdigest()

    def test_a_missing_artifact_is_an_error_and_not_empty_bytes(self, store):
        with pytest.raises(MV.ArtifactStoreError):
            store.get("local://models/nothing/here.joblib")
        with pytest.raises(MV.ArtifactStoreError):
            store.checksum("local://models/nothing/here.joblib")

    def test_a_reference_from_another_store_is_refused(self, store):
        with pytest.raises(MV.ArtifactStoreError):
            store.get("supabase://bucket/models/x.joblib")

    def test_a_partial_write_leaves_nothing_behind(self, store):
        uri = store.put("models/a/b/c/d/v1-dddddddddddddddd.joblib", b"whole")
        path = store.path_for_uri(uri)

        assert path.exists()
        assert not list(path.parent.glob("*.partial"))

    def test_the_registered_store_wins_and_must_be_a_store(self, tmp_path):
        local = MV.LocalArtifactStore(tmp_path / "elsewhere")
        MV.register_artifact_store(local)
        assert MV.current_artifact_store() is local

        MV.reset_artifact_store()
        assert isinstance(MV.current_artifact_store(), MV.LocalArtifactStore)

        with pytest.raises(TypeError):
            MV.register_artifact_store(object())

    def test_a_configured_bucket_selects_the_object_store_backend(self, monkeypatch):
        """The local store is the DEFAULT, not the only one.

        Asserted with a double of the bucket API rather than against Supabase, because no
        bucket is reachable here - but the selection, the key layout, the reference and the
        append-only refusal are the production ones.
        """

        class FakeBucket:
            def __init__(self):
                self.objects = {}

            def upload(self, key, data, options):
                assert options["upsert"] == "false", "the remote store must not upsert"
                if key in self.objects:
                    raise Exception("Duplicate: the resource already exists (409)")
                self.objects[key] = bytes(data)

            def download(self, key):
                return self.objects[key]

        class FakeStorage:
            def __init__(self):
                self.bucket = FakeBucket()

            def from_(self, name):
                return self.bucket

        class FakeClient:
            def __init__(self):
                self.storage = FakeStorage()

        class FakeConnection:
            def __init__(self, client):
                self._client = client

            def get_client(self):
                return self._client

        client = FakeClient()
        monkeypatch.setenv(MV.ARTIFACT_BUCKET_ENV, "model-artifacts")
        monkeypatch.setattr(
            "backend_app.core.supabase_connection.get_supabase_connection",
            lambda: FakeConnection(client),
        )

        selected = MV.current_artifact_store()
        assert isinstance(selected, MV.SupabaseStorageArtifactStore)

        uri = selected.put("models/u/s/v/n/v1-eeeeeeeeeeeeeeee.joblib", b"remote bytes")
        assert uri.startswith("supabase://model-artifacts/")
        assert selected.get(uri) == b"remote bytes"
        # Same bytes: reused. Different bytes: refused, never overwritten.
        assert selected.put("models/u/s/v/n/v1-eeeeeeeeeeeeeeee.joblib", b"remote bytes") == uri
        with pytest.raises(MV.ArtifactExists):
            selected.put("models/u/s/v/n/v1-eeeeeeeeeeeeeeee.joblib", b"other bytes")

    def test_a_configured_bucket_with_no_storage_api_falls_back_and_says_so(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv(MV.ARTIFACT_BUCKET_ENV, "model-artifacts")
        monkeypatch.setattr(
            "backend_app.core.supabase_connection.get_supabase_connection",
            lambda: None,
        )
        with caplog.at_level("WARNING"):
            selected = MV.current_artifact_store()

        assert isinstance(selected, MV.LocalArtifactStore)
        assert "model-artifacts" in caplog.text


# ═══════════════════════════════════════════════════════════════════════════
# 4. store_artifact - serialize, bound, store, verify by reading back
# ═══════════════════════════════════════════════════════════════════════════


class TestStoringAnArtifact:
    MODEL = {"weights": [[0.1, 0.2], [0.3, 0.4]], "classes": [-1, 0, 1]}

    UNSET = object()

    def _store(self, model=UNSET, **overrides):
        arguments = {
            "user_id": USER_ID,
            "strategy_id": STRATEGY_ID,
            "version_id": "ver_1",
            "node_id": "model_node",
            "model_version": 1,
        }
        arguments.update(overrides)
        return MV.store_artifact(
            self.MODEL if model is self.UNSET else model, **arguments
        )

    def test_the_recorded_figures_describe_the_bytes_that_were_actually_stored(self, store):
        artifact = self._store()

        raw = store.get(artifact.uri)
        assert artifact.bytes == len(raw)
        assert artifact.checksum == hashlib.sha256(raw).hexdigest()
        assert artifact.serialization == MV.SERIALIZATION_JOBLIB
        assert artifact.uri.startswith("local://models/")

    def test_the_artifact_round_trips_through_the_platforms_safe_loader(self, store):
        """A stored artifact is loadable by the code that will load it in production."""
        from backend_app.core.ml_safety import SafeModelLoader

        artifact = self._store()
        path = str(store.path_for_uri(artifact.uri))

        loaded = SafeModelLoader.load_model(
            path, expected_checksum=artifact.checksum, validate_integrity=False
        )
        assert loaded == self.MODEL

    def test_a_checksum_that_does_not_match_is_recorded_as_a_mismatch_not_as_success(
        self, tmp_path
    ):
        """The read-back is the point: an altered write must not produce a row."""

        class LyingStore(MV.LocalArtifactStore):
            def checksum(self, uri):
                return "f" * 64

        MV.register_artifact_store(LyingStore(tmp_path / "artifacts"))
        with pytest.raises(MV.ArtifactChecksumMismatch):
            self._store()

    def test_an_artifact_over_the_ceiling_is_refused_before_it_is_written(self, store):
        ml_safety.MemoryMonitor.configure(ml_safety.MemoryConfig(max_model_cache_size_mb=1))
        big = {"weights": np.zeros(400_000)}

        with pytest.raises(MV.ArtifactTooLarge):
            self._store(big)

        assert not list((store.root).rglob("*.joblib")), "nothing should have been written"

    def test_no_fitted_model_is_a_persistence_failure_not_an_empty_artifact(self, store):
        with pytest.raises(W.ModelPersistenceUnavailable):
            self._store(None)

    def test_two_different_models_for_one_node_get_two_keys_and_two_files(self, store):
        first = self._store({"weights": [1.0]}, model_version=1)
        second = self._store({"weights": [2.0]}, model_version=2)

        assert first.key != second.key
        assert store.get(first.uri) != store.get(second.uri)
        assert len(list(store.root.rglob("*.joblib"))) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 5. Requirements 17.1 / 17.2 / 17.4 - the row, and the binding it IS
# ═══════════════════════════════════════════════════════════════════════════


async def insert_one(db, store, *, version_id="ver_1", node_id="model_node", payload=b"m",
                     model=None, **overrides):
    """Store a real artifact and insert the row it describes."""
    existing = await MV.read_model_versions(db, version_id, node_id)
    number = MV.next_model_version(existing)
    artifact = MV.store_artifact(
        model if model is not None else {"weights": list(payload)},
        user_id=USER_ID,
        strategy_id=STRATEGY_ID,
        version_id=version_id,
        node_id=node_id,
        model_version=number,
        store=store,
    )
    arguments = {
        "user_id": USER_ID,
        "training_job_id": "job_1",
        "strategy_id": STRATEGY_ID,
        "version_id": version_id,
        "node_id": node_id,
        "block_id": "xgboost",
        "artifact": artifact,
        "feature_schema": {"feature_names": ["f1"], "feature_count": 1},
        "hyperparameters": {"epochs_total": 3},
        "train_metrics": {"loss": 0.5},
        "val_metrics": {"loss": 0.6},
        "test_metrics": {"loss": 0.7},
    }
    arguments.update(overrides)
    row = await MV.insert_model_version(db, **arguments)
    return row, artifact


@pytest.mark.asyncio
class TestTheModelVersionRow:
    async def test_the_row_records_everything_requirement_171_lists(self, db, store):
        row, artifact = await insert_one(db, store)

        assert row["artifact_uri"] == artifact.uri
        assert row["artifact_checksum"] == artifact.checksum
        assert row["artifact_bytes"] == artifact.bytes
        assert row["serialization"] == MV.SERIALIZATION_JOBLIB
        assert row["feature_schema"]["feature_names"] == ["f1"]
        assert row["hyperparameters"]["epochs_total"] == 3
        assert row["train_metrics"] == {"loss": 0.5}
        assert row["val_metrics"] == {"loss": 0.6}
        assert row["test_metrics"] == {"loss": 0.7}

    async def test_the_insert_is_the_binding_to_one_version_and_one_node(self, db, store):
        """Requirement 17.2 is the SHAPE of the row, not a second write.

        ``version_id`` and ``node_id`` on the row, and ``uq_mv_active_per_node`` over
        exactly that pair, are what make "bound to one immutable strategy version and one
        model node" impossible to forget.
        """
        row, _ = await insert_one(db, store, version_id="ver_7", node_id="ml_3")

        assert row["version_id"] == "ver_7"
        assert row["node_id"] == "ml_3"
        assert row["is_active"] is True
        assert db.active_models("ver_7", "ml_3") == [row]

    async def test_the_first_version_is_one_and_the_numbers_are_monotonic(self, db, store):
        first, _ = await insert_one(db, store)
        second, _ = await insert_one(db, store, payload=b"mm")
        third, _ = await insert_one(db, store, payload=b"mmm")

        assert [first["model_version"], second["model_version"], third["model_version"]] == [1, 2, 3]

    async def test_the_number_comes_from_the_maximum_and_not_from_the_count(self):
        assert MV.next_model_version([]) == 1
        assert MV.next_model_version([{"model_version": 1}, {"model_version": 4}]) == 5
        # A gap - only a cascade can leave one - must not make two rows collide.
        assert MV.next_model_version([{"model_version": 9}]) == 10
        assert MV.next_model_version([{"model_version": None}, {"model_version": "x"}]) == 1

    async def test_numbering_is_per_node_not_per_version(self, db, store):
        first, _ = await insert_one(db, store, node_id="ml_a")
        second, _ = await insert_one(db, store, node_id="ml_b")

        assert first["model_version"] == 1
        assert second["model_version"] == 1

    async def test_a_row_missing_a_not_null_column_is_refused(self, db, store):
        with pytest.raises(W.ModelPersistenceUnavailable):
            await insert_one(db, store, block_id=None)


@pytest.mark.asyncio
class TestRetrainingAppendsAndOverwritesNothing:
    """Requirement 17.3 and Requirement 17.4 - the ML-3 retrain-overwrite class."""

    async def test_a_second_run_supersedes_the_first_and_leaves_its_artifact_in_place(
        self, db, store
    ):
        first, first_artifact = await insert_one(db, store, model={"weights": [1.0]})
        second, second_artifact = await insert_one(db, store, model={"weights": [2.0]})

        # Two rows, two artifacts, one active.
        assert len(db.models) == 2
        assert first_artifact.uri != second_artifact.uri
        assert [row["id"] for row in db.active_models("ver_1", "model_node")] == [second["id"]]

        superseded = db.models[0]
        assert superseded["is_active"] is False
        # The row a running deployment pinned still points at its own bytes, and those
        # bytes are still there and still match their checksum.
        assert superseded["artifact_uri"] == first_artifact.uri
        assert store.checksum(first_artifact.uri) == first["artifact_checksum"]
        assert store.get(first_artifact.uri) != store.get(second_artifact.uri)

    async def test_the_deactivation_is_a_service_role_update_of_only_the_old_row(
        self, db, store
    ):
        first, _ = await insert_one(db, store, model={"weights": [1.0]})
        db.writes.clear()
        await insert_one(db, store, model={"weights": [2.0]})

        updates = db.updates_to("model_versions")
        assert len(updates) == 1, updates
        assert updates[0]["payload"] == {"is_active": False}
        # Compare-and-set on the row's own id AND its current value, so a row somebody
        # else already deactivated is not written twice.
        assert ("eq", "id", first["id"]) in updates[0]["filters"]
        assert ("eq", "is_active", True) in updates[0]["filters"]

    async def test_two_active_rows_for_one_node_are_unrepresentable(self, db, store):
        """The double enforces ``uq_mv_active_per_node``'s partial predicate.

        Which means the previous test's insert being ACCEPTED is itself the assertion that
        the deactivation happened first.
        """
        await insert_one(db, store, model={"weights": [1.0]})
        row = dict(db.models[0])
        row["id"] = "mv_forced"
        row["model_version"] = 99

        with pytest.raises(UniqueViolation):
            db._assert_model_constraints(row)

    async def test_a_failed_insert_puts_the_previous_model_back(self, db, store):
        """The compensating write. A node must not be left with no active model.

        PostgREST gives this client no multi-statement transaction, so the pair is
        deactivate-then-insert with a restore on failure. What that can leave behind is a
        node with NO active model - which reads as awaiting a model and holds a deployment
        out of running - and never a node whose artifact was swapped underneath it.
        """
        first, first_artifact = await insert_one(db, store, model={"weights": [1.0]})
        db.insert_failure = Exception("connection reset by peer")

        with pytest.raises(W.ModelPersistenceUnavailable):
            await insert_one(db, store, model={"weights": [2.0]})

        assert len(db.models) == 1
        assert db.models[0]["is_active"] is True
        assert db.models[0]["artifact_uri"] == first_artifact.uri

    async def test_a_rejected_duplicate_is_reported_as_a_persistence_failure(self, db, store):
        await insert_one(db, store, model={"weights": [1.0]})
        db.insert_failure = UniqueViolation("uq_mv_active_per_node")

        with pytest.raises(W.ModelPersistenceUnavailable) as caught:
            await insert_one(db, store, model={"weights": [2.0]})

        assert "uq_mv_active_per_node" in str(caught.value)
        assert "Nothing was overwritten" in str(caught.value)

    async def test_the_unique_index_is_recognised_by_name_and_by_sqlstate(self):
        assert MV.is_duplicate_active_model_error(UniqueViolation("uq_mv_active_per_node"))
        assert MV.is_duplicate_active_model_error(UniqueViolation("uq_mv_version_node"))
        assert MV.is_duplicate_active_model_error(Exception("SQLSTATE 23505"))
        assert not MV.is_duplicate_active_model_error(Exception("connection reset"))


@pytest.mark.asyncio
class TestTheUnappliedMigrationDegrades:
    """Never a 500, never a crash, and always a warning naming 004d."""

    async def test_a_read_without_the_table_is_a_classified_persistence_failure(self, caplog):
        db = ModelStore.seeded(with_model_versions=False)

        with caplog.at_level("WARNING"):
            with pytest.raises(W.ModelPersistenceUnavailable) as caught:
                await MV.read_model_versions(db, "ver_1")

        assert "004d_training_and_models.sql" in str(caught.value)
        assert "004d_training_and_models.sql" in caplog.text

    async def test_an_insert_without_the_table_is_the_same_classified_failure(self, store, caplog):
        db = ModelStore.seeded(with_model_versions=False)

        with caplog.at_level("WARNING"):
            with pytest.raises(W.ModelPersistenceUnavailable):
                await insert_one(db, store)

        assert "004d_training_and_models.sql" in caplog.text

    async def test_the_reason_lands_on_the_workers_closed_vocabulary(self):
        reason, _ = W.classify_failure(
            W.ModelPersistenceUnavailable("model_versions does not exist")
        )
        assert reason == W.FAILURE_MODEL_PERSISTENCE_UNAVAILABLE
        assert reason in W.FAILURE_REASONS

    async def test_the_probe_is_narrow_enough_to_let_a_real_error_propagate(self):
        assert MV.is_missing_model_table_error(MissingRelation("model_versions"))
        assert MV.is_missing_model_table_error(
            Exception("Could not find the table 'public.model_versions' in the schema cache")
        )
        assert not MV.is_missing_model_table_error(Exception("permission denied"))
        assert not MV.is_missing_model_table_error(Exception("model_versions is locked"))


# ═══════════════════════════════════════════════════════════════════════════
# 6. Requirement 17.5 - READY needs EVERY node bound
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestTheReadyTransition:
    @staticmethod
    def _version(db, version_id="ver_1", state="TRAINING"):
        db.tables["strategy_versions"].append(
            {"id": version_id, "strategy_id": STRATEGY_ID, "lifecycle_state": state}
        )

    async def test_one_node_bound_out_of_one_is_ready(self, db, store):
        self._version(db)
        await insert_one(db, store)

        report = await MV.promote_version_if_ready(db, "ver_1", ["model_node"])

        assert report["ready"] is True
        assert report["lifecycle_state"] == "READY"
        assert db.rows("strategy_versions")[0]["lifecycle_state"] == "READY"

    async def test_one_node_bound_out_of_two_is_NOT_ready_and_names_the_other(self, db, store):
        """A partially trained strategy is not a strategy."""
        self._version(db)
        await insert_one(db, store, node_id="ml_a")

        report = await MV.promote_version_if_ready(db, "ver_1", ["ml_a", "ml_b"])

        assert report["ready"] is False
        assert report["unbound_nodes"] == ["ml_b"]
        assert report["bound_nodes"] == ["ml_a"]
        assert db.rows("strategy_versions")[0]["lifecycle_state"] == "TRAINING"

    async def test_the_second_node_completing_is_what_makes_it_ready(self, db, store):
        self._version(db)
        await insert_one(db, store, node_id="ml_a")
        assert (await MV.promote_version_if_ready(db, "ver_1", ["ml_a", "ml_b"]))["ready"] is False

        await insert_one(db, store, node_id="ml_b")
        report = await MV.promote_version_if_ready(db, "ver_1", ["ml_a", "ml_b"])

        assert report["ready"] is True
        assert db.rows("strategy_versions")[0]["lifecycle_state"] == "READY"

    async def test_a_deactivated_model_does_not_count_as_bound(self, db, store):
        """Only an ACTIVE model version binds a node."""
        self._version(db)
        await insert_one(db, store, node_id="ml_a")
        db.models[0]["is_active"] = False

        report = await MV.promote_version_if_ready(db, "ver_1", ["ml_a"])

        assert report["ready"] is False
        assert report["unbound_nodes"] == ["ml_a"]
        assert db.rows("strategy_versions")[0]["lifecycle_state"] == "TRAINING"

    async def test_a_version_with_no_model_node_is_not_promoted_here(self, db, caplog):
        """Requirement 14.10's transition is task 6.3's, at compile time, not this one's."""
        self._version(db)
        with caplog.at_level("WARNING"):
            report = await MV.promote_version_if_ready(db, "ver_1", [])

        assert report["ready"] is False
        assert db.rows("strategy_versions")[0]["lifecycle_state"] == "TRAINING"

    async def test_the_write_goes_through_the_workers_validated_setter(self, db, store):
        """``chk_lifecycle_state`` is checked locally, so a typo is not a 23514."""
        self._version(db)
        with pytest.raises(ValueError):
            await W.set_version_lifecycle(db, "ver_1", "REDY")

    async def test_an_absent_version_row_does_not_raise(self, db, store):
        """A version whose row cannot be moved still leaves the model version bound."""
        await insert_one(db, store)

        report = await MV.promote_version_if_ready(db, "ver_1", ["model_node"])

        assert report["ready"] is False
        assert len(db.models) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. A whole run, through task 6.4's worker with THIS module as the binder
#
# This is the gap task 6.5 closes: until a binder is installed no run can reach
# COMPLETED, because a completed job with no bound model is the silent-success shape
# Requirement 15.10 forbids.
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestACompletedRunProducesABoundModel:
    async def test_a_run_reaches_completed_and_binds_a_model_and_makes_the_version_ready(
        self, service, db, reg, window, store, handoffs
    ):
        graph, node_id = model_graph(reg)
        await queue_job(service, reg, graph, epochs=3)
        job_id = db.job()["id"]
        version_id = db.job()["version_id"]
        install_production_binder()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.status == W.STATUS_COMPLETED, result.detail
        assert db.job()["status"] == "COMPLETED"
        assert db.job()["progress"] == 1.0

        assert len(db.models) == 1
        row = db.models[0]
        assert row["version_id"] == version_id
        assert row["node_id"] == node_id
        assert row["model_version"] == 1
        assert row["is_active"] is True
        assert row["training_job_id"] == job_id
        assert row["user_id"] == USER_ID
        assert row["strategy_id"] == STRATEGY_ID

        # The artifact exists, is non-empty, and checksums to what the row records.
        assert row["artifact_bytes"] > 0
        assert store.checksum(row["artifact_uri"]) == row["artifact_checksum"]

        # Requirement 17.5: every ML node of this version is bound, so it is READY.
        version_row = [r for r in db.versions if r["id"] == version_id][0]
        assert version_row["lifecycle_state"] == "READY"
        assert result.model_version["readiness"]["ready"] is True
        assert result.model_version["readiness"]["ml_nodes"] == [node_id]

    async def test_the_row_carries_the_three_metric_sets_the_run_measured(
        self, service, db, reg, window, store
    ):
        """Requirement 17.1's train/val/test metrics, from the real fit."""
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=3)
        install_production_binder()

        await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        row = db.models[0]
        for split in ("train_metrics", "val_metrics", "test_metrics"):
            assert row[split], f"{split} is empty"
            assert "loss" in row[split]
            for key, value in row[split].items():
                assert isinstance(value, (int, float, bool)) or value is None, (key, value)

    async def test_the_row_records_the_feature_schema_the_run_actually_trained_on(
        self, service, db, reg, window, store
    ):
        """Order-significant, and the same names the job row reports."""
        graph, node_id = model_graph(reg)
        await queue_job(service, reg, graph, epochs=2)
        job = db.job()
        install_production_binder()

        await W.run_training_job(job["id"], sb=db, worker_id="worker-a")

        schema = db.models[0]["feature_schema"]
        assert schema["feature_names"] == list(job["feature_names"])
        assert schema["feature_count"] == len(job["feature_names"]) == job["feature_columns"]
        assert schema["node_id"] == node_id
        assert schema["label"]["horizon"] >= 1

    async def test_the_row_records_the_hyperparameters_a_rerun_would_need(
        self, service, db, reg, window, store
    ):
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=3, batch_size=32)
        install_production_binder()

        await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        hyper = db.models[0]["hyperparameters"]
        assert hyper["epochs_total"] == 3
        assert hyper["batch_size"] == 32
        assert isinstance(hyper["seed"], int)
        assert hyper["block_id"] == "xgboost"
        assert set(hyper["split_sizes"]) == {"train", "val", "test"}
        assert hyper["worker_version"] == W.TRAINING_WORKER_VERSION

    async def test_an_unexpected_config_key_is_not_copied_into_the_served_document(
        self, service, db, reg, window, store
    ):
        """Requirement 21.7: the hyperparameters document is a whitelist, not a copy.

        ``config`` is written by the API and could in principle be written past it. If
        this document copied the mapping, a key put there would come straight back out
        through the model endpoint and the realtime frame.
        """
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=2)
        db.job()["config"]["api_key"] = "sk_live_should_never_be_served"
        db.job()["config"]["exchange_account_id"] = "acct_1"
        install_production_binder()

        await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        hyper = db.models[0]["hyperparameters"]
        assert "api_key" not in hyper
        assert "exchange_account_id" not in hyper
        assert "sk_live_should_never_be_served" not in json.dumps(hyper)
        assert set(hyper) <= set(MV.HYPERPARAMETER_CONFIG_KEYS) | {
            "block_id", "model_family", "epoch_unit", "epochs_total", "batch_size",
            "seed", "worker_version", "model_versioning_version", "split_sizes",
            "embargo_bars", "dataset_fingerprint",
        }

    async def test_nothing_the_run_hands_back_carries_the_artifact_reference(
        self, service, db, reg, window, store, handoffs
    ):
        """Requirement 17.8 / design.md: ``artifact_uri`` is server-side only.

        Searched for as a SUBSTRING of the whole serialised payload, not as a key, because
        the failure this guards against is a reference nested somewhere nobody filtered.
        """
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=2)
        install_production_binder()

        result = await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")
        uri = db.models[0]["artifact_uri"]

        assert uri and uri.startswith("local://")
        assert "artifact_uri" not in result.model_version
        assert uri not in json.dumps(result.model_version, default=str)
        assert uri not in json.dumps(result.to_dict(), default=str)

        completed = [payload for event, payload in handoffs["events"] if event == "training.completed"]
        assert completed, handoffs["events"]
        for payload in completed:
            assert uri not in json.dumps(payload, default=str)
            assert "artifact_uri" not in json.dumps(payload, default=str)

        # What IS handed back is enough to verify a download and nothing more.
        assert result.model_version["artifact_available"] is True
        assert result.model_version["artifact_checksum"] == db.models[0]["artifact_checksum"]

    async def test_retraining_the_same_node_through_the_worker_appends(
        self, service, db, reg, window, store
    ):
        """Requirement 17.3, end to end: two rows, one active, both artifacts intact."""
        graph, node_id = model_graph(reg)
        await queue_job(service, reg, graph, epochs=2)
        first_job = db.job()["id"]
        version_id = db.job()["version_id"]
        install_production_binder()
        await W.run_training_job(first_job, sb=db, worker_id="worker-a")
        first_row = dict(db.models[0])

        # A second job for the same (version, node). Permitted now, because the first is
        # no longer live - ``uq_tj_active_per_node`` allows exactly this and only this.
        outcome = await service.create_training_job(
            owner(), version_id, node_id=node_id, registry=reg
        )
        second_job = outcome["jobs"][0]["id"]
        # A different fit, so a different artifact: a fresh seed makes different weights.
        db.job(second_job)["config"]["seed"] = int(db.job(second_job)["config"]["seed"]) + 1
        await W.run_training_job(second_job, sb=db, worker_id="worker-b")

        assert len(db.models) == 2
        active = db.active_models(version_id, node_id)
        assert len(active) == 1
        assert active[0]["model_version"] == 2
        assert active[0]["training_job_id"] == second_job

        # The superseded row is untouched apart from its flag, and its bytes are still
        # there - a deployment pinned to it resolves the same artifact it always did.
        superseded = [r for r in db.models if r["id"] == first_row["id"]][0]
        assert superseded["is_active"] is False
        assert superseded["artifact_uri"] == first_row["artifact_uri"]
        assert superseded["artifact_checksum"] == first_row["artifact_checksum"]
        assert store.checksum(superseded["artifact_uri"]) == first_row["artifact_checksum"]

    async def test_a_cancelled_run_binds_no_model_and_writes_no_artifact(
        self, service, db, reg, window, store
    ):
        """Requirement 15.7 stays structural with the real binder installed."""
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=6)
        job_id = db.job()["id"]

        def cancel_after_two(epoch):
            if epoch == 2:
                db.job(job_id)["cancel_requested"] = True

        install_production_binder(
            lambda ctx: GradientDescentTrainer(ctx, on_epoch=cancel_after_two)
        )

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.status == W.STATUS_CANCELLED
        assert db.models == []
        assert not list(store.root.rglob("*.joblib"))
        version_row = [r for r in db.versions if r["id"] == db.job()["version_id"]][0]
        assert version_row["lifecycle_state"] != "READY"

    async def test_a_failed_persist_leaves_the_job_failed_and_not_completed(
        self, service, db, reg, window, store
    ):
        """The worker writes COMPLETED only after the binder returns."""
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=2)
        job_id = db.job()["id"]
        db.insert_failure = Exception("connection reset by peer")
        install_production_binder()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.status == W.STATUS_FAILED
        assert result.failure_reason == W.FAILURE_MODEL_PERSISTENCE_UNAVAILABLE
        assert db.job()["status"] == "FAILED"
        assert db.models == []
        version_row = [r for r in db.versions if r["id"] == db.job()["version_id"]][0]
        assert version_row["lifecycle_state"] != "READY"

    async def test_the_seam_is_the_production_one_and_the_binder_is_this_module(self):
        backend = MV.training_backend(lambda ctx: None, name="under-test")
        assert isinstance(backend, W.TrainingBackend)
        assert backend.binder is MV.bind_trained_model
        assert backend.name == "under-test"

    async def test_the_split_metrics_reach_the_binder_only_after_every_epoch(
        self, service, db, reg, window, store
    ):
        """``TrainingContext.split_metrics`` is empty until the last epoch has run.

        Which is what makes "a cancelled run records no metrics" and "a cancelled run
        binds no model" the same structural fact rather than two conventions.
        """
        seen = {}

        async def watching_binder(ctx, model):
            seen["at_bind"] = dict(getattr(ctx, "split_metrics", {}) or {})
            return await MV.bind_trained_model(ctx, model)

        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=2)
        W.register_training_backend(
            W.TrainingBackend(
                trainer=lambda ctx: GradientDescentTrainer(ctx),
                binder=watching_binder,
                name="watching",
            )
        )

        await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        assert set(seen["at_bind"]) == {"train", "val", "test"}
        assert W.TrainingContext(
            job={}, config={}, worker_id="", inputs=None, caps=None, admission=None,
            seed=0, epochs_total=0, batch_size=None,
        ).split_metrics == {}


# ═══════════════════════════════════════════════════════════════════════════
# 8. Requirements 17.8 / 21.2 / 21.4 - the client projection and the signed link
# ═══════════════════════════════════════════════════════════════════════════


class TestTheClientProjection:
    def test_the_projection_has_no_artifact_reference_and_cannot_grow_one(self):
        """Built from named keys, so it does not inherit whatever the row carries."""
        public = MV.public_model_version(a_row(artifact_uri="local://secret/place.joblib"))

        assert "artifact_uri" not in public
        assert "local://secret/place.joblib" not in json.dumps(public)
        assert public["artifact_available"] is True

    def test_an_extra_column_on_the_row_is_not_passed_through(self):
        public = MV.public_model_version(
            a_row(internal_note="service-role only", api_key="sk_live_x")
        )

        assert "internal_note" not in public
        assert "api_key" not in public
        assert "sk_live_x" not in json.dumps(public)

    def test_the_projection_carries_what_a_client_needs_to_verify_a_download(self):
        public = MV.public_model_version(a_row())

        assert public["artifact_checksum"] == "a" * 64
        assert public["artifact_bytes"] == 2048
        assert public["serialization"] == MV.SERIALIZATION_JOBLIB
        assert public["artifact_filename"] == "xgboost-model_node-v3.joblib"

    def test_a_row_with_no_artifact_says_so_rather_than_pretending(self):
        assert MV.public_model_version(a_row(artifact_uri=None))["artifact_available"] is False


class TestTheSignedToken:
    def test_a_token_round_trips_and_carries_the_owner_and_the_object(self):
        token, expires_at = MV.sign_artifact_token("mv_1", USER_ID)
        payload = MV.verify_artifact_token(token)

        assert payload["model_version_id"] == "mv_1"
        assert payload["user_id"] == USER_ID
        assert payload["purpose"] == MV.ARTIFACT_TOKEN_PURPOSE
        assert payload["exp"] == expires_at

    def test_an_edited_payload_does_not_verify(self):
        """The signature covers the whole payload, so nothing in it can be swapped."""
        import base64

        token, _ = MV.sign_artifact_token("mv_1", USER_ID)
        body, signature = token.split(".")
        forged_body = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "purpose": MV.ARTIFACT_TOKEN_PURPOSE,
                        "model_version_id": "mv_someone_else",
                        "user_id": USER_ID,
                        "exp": 9_999_999_999,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
            .decode()
            .rstrip("=")
        )

        with pytest.raises(MV.InvalidArtifactToken):
            MV.verify_artifact_token(f"{forged_body}.{signature}")

    def test_an_edited_signature_does_not_verify(self):
        token, _ = MV.sign_artifact_token("mv_1", USER_ID)
        body, signature = token.split(".")

        with pytest.raises(MV.InvalidArtifactToken):
            MV.verify_artifact_token(f"{body}.{signature[:-4]}AAAA")

    def test_an_expired_token_does_not_verify(self):
        token, _ = MV.sign_artifact_token("mv_1", USER_ID, ttl_seconds=1, now=1_000.0)

        MV.verify_artifact_token(token, now=1_000.5)
        with pytest.raises(MV.InvalidArtifactToken):
            MV.verify_artifact_token(token, now=1_002.0)

    def test_a_token_signed_with_another_secret_does_not_verify(self, monkeypatch):
        token, _ = MV.sign_artifact_token("mv_1", USER_ID)
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "a-different-secret-entirely-32chars")

        with pytest.raises(MV.InvalidArtifactToken):
            MV.verify_artifact_token(token)

    def test_a_malformed_token_is_refused_before_anything_is_parsed(self):
        for bad in ("", "no-dot", "a.b.c", "...."):
            with pytest.raises(MV.InvalidArtifactToken):
                MV.verify_artifact_token(bad)

    def test_no_secret_means_no_link_at_all(self, monkeypatch):
        """Fail-closed. An unsigned link would be a public one."""
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        with patch("backend_app.core.config.settings") as settings:
            settings.SUPABASE_JWT_SECRET = ""
            settings.JWT_SECRET = ""
            with pytest.raises(MV.SignedLinkUnavailable):
                MV.sign_artifact_token("mv_1", USER_ID)

    def test_the_default_lifetime_is_short(self):
        assert 0 < MV.DOWNLOAD_TTL_SECONDS <= 900


@pytest.mark.asyncio
class TestTheOwnershipCheckedRead:
    async def test_the_owner_gets_a_link_and_no_artifact_reference(self, db, store):
        row, artifact = await insert_one(db, store)

        with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=db)):
            descriptor = await MV.artifact_download_descriptor(owner(), row["id"])

        assert descriptor["download_url"].startswith(MV.ARTIFACT_DOWNLOAD_PATH + "?token=")
        assert artifact.uri not in json.dumps(descriptor)
        assert "artifact_uri" not in json.dumps(descriptor)
        assert descriptor["artifact_checksum"] == artifact.checksum
        assert descriptor["filename"].endswith(".joblib")

    async def test_the_read_carries_an_explicit_user_filter_as_well_as_rls(self, db, store):
        """Requirement 21.2 asks for the filter in the handler as well as the database."""
        row, _ = await insert_one(db, store)
        captured = []

        class Recording(ModelStore):
            def run(self, query):
                if query.op == "select" and query.table == "model_versions":
                    captured.append(list(query.filters))
                return super().run(query)

        recording = Recording(db.tables)
        with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=recording)):
            await MV.load_owned_model_version(owner(), row["id"])

        assert captured, "no model_versions read was made"
        assert ("eq", "user_id", USER_ID) in captured[-1]
        assert ("eq", "id", row["id"]) in captured[-1]

    async def test_another_users_model_version_is_not_found_rather_than_refused(
        self, db, store
    ):
        """Requirement 21.4: existence does not leak across tenants."""
        row, _ = await insert_one(db, store)
        intruder = {**owner(), "id": "usr_someone_else", "access_token": "token_intruder"}

        with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=db)):
            with pytest.raises(MV.ModelVersionNotFound):
                await MV.load_owned_model_version(intruder, row["id"])
            with pytest.raises(MV.ModelVersionNotFound):
                await MV.artifact_download_descriptor(intruder, row["id"])

    async def test_an_unapplied_migration_is_not_found_and_not_a_500(self, caplog):
        absent = ModelStore.seeded(with_model_versions=False)

        with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=absent)):
            with caplog.at_level("WARNING"):
                with pytest.raises(MV.ModelVersionNotFound):
                    await MV.load_owned_model_version(owner(), "mv_0001")

        assert "004d_training_and_models.sql" in caplog.text

    async def test_a_token_for_one_user_does_not_open_another_users_model(self, db, store):
        row, _ = await insert_one(db, store)
        payload = {
            "model_version_id": row["id"],
            "user_id": "usr_someone_else",
            "purpose": MV.ARTIFACT_TOKEN_PURPOSE,
        }

        with pytest.raises(MV.ModelVersionNotFound):
            await MV.load_model_version_for_token(payload, sb=db)

    async def test_redemption_re_reads_the_row_and_compares_the_owner(self, db, store):
        row, _ = await insert_one(db, store)
        payload = {
            "model_version_id": row["id"],
            "user_id": USER_ID,
            "purpose": MV.ARTIFACT_TOKEN_PURPOSE,
        }

        found = await MV.load_model_version_for_token(payload, sb=db)
        assert found["id"] == row["id"]

        # The same token stops working the moment the row stops being that user's.
        db.models[0]["user_id"] = "usr_someone_else"
        with pytest.raises(MV.ModelVersionNotFound):
            await MV.load_model_version_for_token(payload, sb=db)


class TestServingTheBytes:
    def test_the_bytes_are_returned_only_when_the_checksum_matches(self, store):
        artifact = MV.store_artifact(
            {"weights": [1.0]},
            user_id=USER_ID,
            strategy_id=STRATEGY_ID,
            version_id="ver_1",
            node_id="model_node",
            model_version=1,
            store=store,
        )
        row = a_row(artifact_uri=artifact.uri, artifact_checksum=artifact.checksum)

        assert MV.read_artifact_bytes(row, store=store) == store.get(artifact.uri)

    def test_an_artifact_whose_checksum_moved_is_not_served(self, store):
        """Requirement 17.6's condition, on the serving path."""
        artifact = MV.store_artifact(
            {"weights": [1.0]},
            user_id=USER_ID,
            strategy_id=STRATEGY_ID,
            version_id="ver_1",
            node_id="model_node",
            model_version=1,
            store=store,
        )
        store.path_for_uri(artifact.uri).write_bytes(b"tampered")
        row = a_row(artifact_uri=artifact.uri, artifact_checksum=artifact.checksum)

        with pytest.raises(MV.ArtifactChecksumMismatch):
            MV.read_artifact_bytes(row, store=store)

    def test_a_row_with_no_reference_serves_nothing(self, store):
        with pytest.raises(MV.ArtifactStoreError):
            MV.read_artifact_bytes(a_row(artifact_uri=None), store=store)


# ═══════════════════════════════════════════════════════════════════════════
# 9. The HTTP surface
#
# Two routes, because Requirement 17.8 and design.md's security surface between them ask
# for three things: an ownership-checked endpoint, a signed download, and a sanitised file
# name. The mint is authenticated by the platform's ``get_current_user``; the redeem is
# authenticated by the HMAC signature over (purpose, model_version_id, user_id, exp) and
# re-checks ownership against the row before serving a byte.
# ═══════════════════════════════════════════════════════════════════════════


def _decorators_of(function_name):
    """The decorator source lines on ``function_name``, read from the router file.

    From source rather than from ``__wrapped__`` chains, because source is what a reviewer
    changing this file would delete and that is the change this test exists to catch.
    """
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "backend_app"
        / "routers"
        / "strategy_operations.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for item in ast.walk(tree):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function_name:
            return [ast.unparse(d) for d in item.decorator_list]
    raise AssertionError(f"{function_name} is not defined in {path}")


@pytest.fixture
def client(db, store):
    """A client for the real app, with the limiter suspended for the duration.

    The limits themselves are NOT weakened - they stay exactly as declared, they are
    restored after every test, and :class:`TestTheControlsStayInForce` asserts each
    decorator is still on its endpoint. Suspending them here only stops a 429 earned by
    another file's requests from being read as a statement about this one.
    """
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user, get_request_supabase
    from backend_app.main import app

    limiter = getattr(app.state, "limiter", None)
    was_enabled = getattr(limiter, "enabled", None)
    if limiter is not None:
        limiter.enabled = False

    app.dependency_overrides[get_current_user] = lambda: owner()
    app.dependency_overrides[get_request_supabase] = lambda: db
    try:
        with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=db)):
            with patch.object(W, "_worker_client", AsyncMock(return_value=db)):
                yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled


class TestTheDesignedPathsExist:
    def test_both_routes_are_mounted_as_gets(self):
        from backend_app.main import app

        wanted = {
            "/api/strategy-operations/models/artifact",
            "/api/strategy-operations/models/{model_version_id}/download",
        }
        found = {
            route.path: route.methods
            for route in app.routes
            if getattr(route, "path", None) in wanted
        }
        assert set(found) == wanted, found
        for path, methods in found.items():
            assert "GET" in methods, path

    def test_the_streaming_path_matches_the_constant_the_link_is_built_from(self):
        """The module and the router cannot drift on where the link points."""
        from backend_app.main import app

        paths = {route.path for route in app.routes if hasattr(route, "path")}
        assert MV.ARTIFACT_DOWNLOAD_PATH in paths

    def test_the_literal_segment_is_declared_before_the_parameterised_one(self):
        """Otherwise ``artifact`` would be read as a model version id."""
        from backend_app.main import app

        order = [
            route.path
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/strategy-operations/models/")
            and "registry" not in getattr(route, "path", "")
        ]
        assert order.index("/api/strategy-operations/models/artifact") < order.index(
            "/api/strategy-operations/models/{model_version_id}/download"
        )


class TestTheControlsStayInForce:
    """Requirements 21.1 and 21.8. No control this task adds may be weaker than the rest."""

    @pytest.mark.parametrize(
        "function_name,route",
        [
            ("create_model_artifact_link", "/strategy-operations/models/{model_version_id}/download"),
            ("download_model_artifact", "/strategy-operations/models/artifact"),
        ],
    )
    def test_each_endpoint_keeps_its_route_and_its_rate_limit(self, function_name, route):
        decorators = _decorators_of(function_name)

        assert any(route in d for d in decorators), decorators
        assert any(d.startswith("limiter.limit(") for d in decorators), (
            f"{function_name} has no slowapi limit"
        )

    def test_the_minting_endpoint_requires_an_authenticated_user(self):
        import inspect

        from backend_app.routers import strategy_operations

        signature = inspect.signature(strategy_operations.create_model_artifact_link)
        assert "get_current_user" in repr(signature.parameters["user"].default)

    def test_the_streaming_endpoint_is_authenticated_by_the_signed_token(self):
        """It carries no ``user`` dependency, and that is deliberate - stated here.

        A browser download cannot send a Bearer header, so the credential is the token: an
        HMAC-SHA256 signature over ``(purpose, model_version_id, user_id, exp)`` verified in
        constant time, after which the row is re-read with an explicit ``user_id`` filter.
        This test exists so that "there is no ``get_current_user`` here" is a recorded
        decision rather than something to be discovered.
        """
        import inspect

        from backend_app.routers import strategy_operations

        signature = inspect.signature(strategy_operations.download_model_artifact)
        assert "user" not in signature.parameters
        assert "token" in signature.parameters
        source = inspect.getsource(strategy_operations.download_model_artifact)
        assert "verify_artifact_token" in source
        assert "load_model_version_for_token" in source


@pytest.mark.asyncio
class TestTheHttpExchange:
    @staticmethod
    async def _row(db, store):
        row, artifact = await insert_one(db, store)
        return row, artifact

    async def test_the_owner_mints_a_link_and_the_response_holds_no_reference(
        self, client, db, store
    ):
        row, artifact = await self._row(db, store)

        response = client.get(f"/api/strategy-operations/models/{row['id']}/download")

        assert response.status_code == 200, response.text
        assert artifact.uri not in response.text
        assert "artifact_uri" not in response.text
        body = response.json()
        assert body["status"] == "signed"
        assert body["download_url"].startswith(MV.ARTIFACT_DOWNLOAD_PATH)
        assert body["model_version"]["id"] == row["id"]
        assert "artifact_uri" not in body["model_version"]

    async def test_the_link_streams_the_exact_artifact_with_a_sanitised_name(
        self, client, db, store
    ):
        row, artifact = await self._row(db, store)
        minted = client.get(f"/api/strategy-operations/models/{row['id']}/download").json()

        response = client.get(minted["download_url"])

        assert response.status_code == 200, response.text
        assert response.content == store.get(artifact.uri)
        assert response.headers["content-type"] == "application/octet-stream"
        disposition = response.headers["content-disposition"]
        assert disposition == f'attachment; filename="{minted["filename"]}"'
        assert "/" not in minted["filename"] and ".." not in minted["filename"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-artifact-checksum"] == artifact.checksum

    async def test_a_forged_or_absent_token_gets_the_same_404(self, client, db, store):
        await self._row(db, store)

        assert client.get(f"{MV.ARTIFACT_DOWNLOAD_PATH}?token={'x' * 40}").status_code == 404
        # A missing token is refused by the request model before anything is read.
        assert client.get(MV.ARTIFACT_DOWNLOAD_PATH).status_code == 422

    async def test_an_expired_token_gets_a_404(self, client, db, store):
        row, _ = await self._row(db, store)
        token, _ = MV.sign_artifact_token(row["id"], USER_ID, ttl_seconds=1, now=1_000.0)

        response = client.get(f"{MV.ARTIFACT_DOWNLOAD_PATH}?token={token}")

        assert response.status_code == 404
        assert "MODEL_ARTIFACT_NOT_FOUND" in response.text

    async def test_a_token_naming_another_user_gets_a_404(self, client, db, store):
        row, _ = await self._row(db, store)
        token, _ = MV.sign_artifact_token(row["id"], "usr_someone_else")

        assert client.get(f"{MV.ARTIFACT_DOWNLOAD_PATH}?token={token}").status_code == 404

    async def test_a_tampered_artifact_is_not_served(self, client, db, store):
        row, artifact = await self._row(db, store)
        minted = client.get(f"/api/strategy-operations/models/{row['id']}/download").json()
        store.path_for_uri(artifact.uri).write_bytes(b"tampered")

        response = client.get(minted["download_url"])

        assert response.status_code == 404
        assert b"tampered" not in response.content

    async def test_another_users_model_version_is_a_404_from_the_minting_endpoint(
        self, client, db, store
    ):
        row, _ = await self._row(db, store)
        db.models[0]["user_id"] = "usr_someone_else"

        response = client.get(f"/api/strategy-operations/models/{row['id']}/download")

        assert response.status_code == 404
        assert "MODEL_VERSION_NOT_FOUND" in response.text

    async def test_a_model_version_that_does_not_exist_is_the_same_404(self, client, db, store):
        await self._row(db, store)

        response = client.get("/api/strategy-operations/models/mv_does_not_exist/download")

        assert response.status_code == 404
        assert "MODEL_VERSION_NOT_FOUND" in response.text

    async def test_an_unapplied_migration_is_a_404_and_not_a_500(self, store):
        from fastapi.testclient import TestClient

        from backend_app.core.dependencies import get_current_user, get_request_supabase
        from backend_app.main import app

        absent = ModelStore.seeded(with_model_versions=False)
        limiter = getattr(app.state, "limiter", None)
        was_enabled = getattr(limiter, "enabled", None)
        if limiter is not None:
            limiter.enabled = False
        app.dependency_overrides[get_current_user] = lambda: owner()
        app.dependency_overrides[get_request_supabase] = lambda: absent
        try:
            with patch.object(
                S, "create_request_supabase_async", AsyncMock(return_value=absent)
            ):
                response = TestClient(app).get(
                    "/api/strategy-operations/models/mv_0001/download"
                )
        finally:
            app.dependency_overrides.clear()
            if limiter is not None and was_enabled is not None:
                limiter.enabled = was_enabled

        assert response.status_code == 404
        assert "MODEL_VERSION_NOT_FOUND" in response.text

    async def test_no_signing_secret_is_a_503_and_not_an_unsigned_link(
        self, client, db, store, monkeypatch
    ):
        row, _ = await self._row(db, store)
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)

        with patch("backend_app.core.config.settings") as settings:
            settings.SUPABASE_JWT_SECRET = ""
            settings.JWT_SECRET = ""
            response = client.get(f"/api/strategy-operations/models/{row['id']}/download")

        assert response.status_code == 503
        assert "ARTIFACT_LINK_UNAVAILABLE" in response.text
