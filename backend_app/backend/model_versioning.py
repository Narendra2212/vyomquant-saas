"""
backend_app/backend/model_versioning.py

The other half of a training run: the artifact, the ``model_versions`` row, the
binding, and the ``READY`` transition.

Spec: strategy-builder task 6.5. Requirements 17.1, 17.2, 17.3, 17.5, 17.8, 17.9,
21.2, 21.7. ``design.md`` -> Training workflow (``persist_artifact`` ->
``insert_model_version`` -> ``bind_model_to_version_node`` -> ``all_ml_nodes_bound``),
-> Database (``-- 3. Model versions``), -> Security surface ("Model version + artifact").

WHERE THIS MODULE SITS
----------------------
Task 6.4's worker owns the control plane of a run - the claim, the epochs, the
cancellation boundary, the wall clock, the heartbeat, the terminal status. It
deliberately owns no artifact and writes no ``model_versions`` row, and reaches this
module through **one named seam**: ``training_worker.TrainingBackend(trainer=...,
binder=...)``. :func:`bind_trained_model` is that ``binder``::

    from backend_app.backend.model_versioning import training_backend
    from backend_app.backend.training_worker import register_training_backend

    register_training_backend(training_backend(my_trainer_factory))

Until a binder is installed the worker records a run that fitted every epoch as
``FAILED`` / ``MODEL_PERSISTENCE_UNAVAILABLE``, because a completed job with no bound
model is the silent-success shape Requirement 15.10 forbids. Installing this one is
what lets a run reach ``COMPLETED``.

WHAT IS STRUCTURAL HERE RATHER THAN PROMISED
--------------------------------------------
1. **Retraining cannot overwrite the artifact a running deployment is executing**
   (Requirement 17.3, the ML-3 class). Two independent reasons, either of which alone
   would be enough:

   * the object key is **content-addressed** - it carries the artifact's own SHA-256 -
     so two different models cannot land on one key, and
   * :meth:`ArtifactStore.put` **never overwrites**. A key already holding the same
     bytes is reused; a key holding different bytes is an error, not a write. Nothing
     here deletes an artifact, and no path mutates ``artifact_uri`` on an existing row.

   Retraining therefore appends: a new key, a new row, a new ``model_version`` integer.
   The superseded row keeps its own reference and its own bytes.

2. **At most one active model version per (version, node)** (Requirement 17.4).
   ``uq_mv_active_per_node`` is the guarantee; this module's job is to not fight it.
   Deactivating the superseded row is an UPDATE, and 004d grants UPDATE on
   ``model_versions`` **only to service_role** and creates no ``mv_owner_update``
   policy - deliberately, so a user's own client cannot rewrite which artifact a
   running deployment resolves. So the deactivate-then-insert pair runs on the worker's
   service-role client, and a failed insert puts the previously active row back (see
   :func:`insert_model_version` for why a compensating write rather than a transaction).

3. **``artifact_uri`` never reaches a client.** :func:`public_model_version` is the only
   projection anything outside this module returns, and it has no such key - the
   binder's return value, which the worker publishes on ``training.completed``, is that
   projection. The download is a two-step, ownership-checked, signed exchange
   (:func:`artifact_download_descriptor` / :func:`verify_artifact_token`), and every
   file name and path segment it produces goes through ``ml_models._safe_filename`` -
   the existing ML-5 path-traversal fix, reused rather than reimplemented.

4. **A version reaches ``READY`` only when EVERY ML node is bound** (Requirement 17.5).
   :func:`promote_version_if_ready` compares the version's own plan's ``ml_nodes``
   against the nodes that actually have an active row and refuses a partial binding,
   because a partially trained strategy is not a strategy.

WHAT THIS MODULE REUSES RATHER THAN RESTATES
--------------------------------------------
* ``ml_models._safe_filename`` - the ML-5 sanitiser, for every path segment and every
  served file name. No second sanitiser exists.
* ``core.ml_safety.SafeModelLoader.compute_checksum`` - the checksum recorded on the row
  is computed by the same function that verifies it at load time, over the bytes as they
  were actually stored rather than over the bytes we meant to store.
* ``core.ml_safety.MemoryMonitor`` - its model-cache ceiling is the artifact ceiling. An
  artifact too large to be cached is too large to serve for inference, so it is refused
  at write time instead of at deploy time.
* ``strategy_builder.LIFECYCLE_STATES`` / ``training_worker.set_version_lifecycle`` -
  the ``READY`` write, with ``chk_lifecycle_state`` validated locally.
* ``strategy_service.is_missing_training_table_error`` / ``_execute`` /
  ``_result_error_text`` - one degradation vocabulary for the whole Strategy Builder.

THE UNAPPLIED MIGRATION
-----------------------
``backend_app/migrations/004d_training_and_models.sql`` creates ``model_versions`` and
is applied by hand. Every path here that touches the table degrades with a warning
**naming that file**: on the worker the run ends ``FAILED`` /
``MODEL_PERSISTENCE_UNAVAILABLE`` (a classified reason, not a 500), and on the API the
answer is "no such model version" - the same answer another tenant's id gets, so a
missing table neither becomes a 500 nor leaks.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend import strategy_service as S
from backend_app.backend.ml_models import _safe_filename

logger = logging.getLogger("ModelVersioning")

#: Bumped when the row's shape, the key layout or the token format changes.
MODEL_VERSIONING_VERSION = "1.0.0"

__all__ = [
    "MODEL_VERSIONING_VERSION",
    "MODEL_VERSIONS_TABLE",
    "MODEL_MIGRATION",
    "SERIALIZATION_JOBLIB",
    "SERIALIZATION_PICKLE",
    "ARTIFACT_EXTENSIONS",
    "ARTIFACT_DOWNLOAD_PATH",
    "DOWNLOAD_TTL_SECONDS",
    "ARTIFACT_TOKEN_PURPOSE",
    "HYPERPARAMETER_CONFIG_KEYS",
    # errors
    "ModelVersioningError",
    "ArtifactStoreError",
    "ArtifactExists",
    "ArtifactTooLarge",
    "ArtifactChecksumMismatch",
    "ModelVersionNotFound",
    "SignedLinkUnavailable",
    "InvalidArtifactToken",
    # the store
    "StoredArtifact",
    "ArtifactStore",
    "LocalArtifactStore",
    "SupabaseStorageArtifactStore",
    "register_artifact_store",
    "current_artifact_store",
    "reset_artifact_store",
    "artifact_root",
    "artifact_bucket",
    "max_artifact_bytes",
    # documents
    "serialize_model",
    "artifact_key",
    "artifact_filename",
    "feature_schema_document",
    "hyperparameters_document",
    "split_metrics_document",
    "public_model_version",
    # persistence
    "is_missing_model_table_error",
    "is_duplicate_active_model_error",
    "read_model_versions",
    "next_model_version",
    "insert_model_version",
    "active_model_versions",
    "promote_version_if_ready",
    "store_artifact",
    "bind_trained_model",
    "training_backend",
    # the download exchange
    "sign_artifact_token",
    "verify_artifact_token",
    "load_owned_model_version",
    "artifact_download_descriptor",
    "read_artifact_bytes",
]


# ══════════════════════════════════════════════════════════════════════════
#  1. VOCABULARY
# ══════════════════════════════════════════════════════════════════════════

#: The table, spelled once.
MODEL_VERSIONS_TABLE = "model_versions"

#: The migration that creates it, named in every degradation warning so an operator is
#: never left guessing which file to apply. Same file as ``training_jobs``.
MODEL_MIGRATION = S.TRAINING_MIGRATION

#: ``model_versions.serialization``. ``joblib`` is what ``SafeModelLoader.load_model``
#: reads, so it is the platform's format; ``pickle`` is recorded honestly for an
#: environment where joblib is absent (``ml_models`` itself falls back the same way).
SERIALIZATION_JOBLIB = "joblib"
SERIALIZATION_PICKLE = "pickle"

#: The closed set of extensions an artifact key may carry. A serialization mode outside
#: this mapping cannot produce a file name at all, which is deliberate: the extension is
#: the one part of a key not passed through ``_safe_filename`` (a dot would not survive
#: it), so it comes from here and from nowhere else.
ARTIFACT_EXTENSIONS: Dict[str, str] = {
    SERIALIZATION_JOBLIB: "joblib",
    SERIALIZATION_PICKLE: "pkl",
}

#: Where the local store puts artifacts when nothing else is configured.
ARTIFACT_ROOT_ENV = "STRATEGY_MODEL_ARTIFACT_DIR"
DEFAULT_ARTIFACT_ROOT = os.path.join("user_strategies", "model_artifacts")

#: The object-storage bucket. Set it and the Supabase storage backend is used; leave it
#: unset and artifacts stay on the worker's own volume.
ARTIFACT_BUCKET_ENV = "STRATEGY_MODEL_ARTIFACT_BUCKET"

#: The mounted path of the streaming half of the download exchange. Asserted against
#: the router's own route in the tests, so the two cannot drift.
ARTIFACT_DOWNLOAD_PATH = "/api/strategy-operations/models/artifact"

#: How long a signed download link lives. Short, because the link is the credential.
DOWNLOAD_TTL_SECONDS = 300

#: The token's audience. A token minted for anything else does not open an artifact.
ARTIFACT_TOKEN_PURPOSE = "model_artifact"

#: The ONLY keys copied out of ``training_jobs.config`` into ``hyperparameters``. A
#: whitelist rather than a copy of the mapping: Requirement 21.7 keeps credentials out
#: of every response, this document IS served to the client, and the way to keep an
#: unexpected key out of it is to have no path that copies unknown keys.
HYPERPARAMETER_CONFIG_KEYS: Tuple[str, ...] = (
    "symbol",
    "timeframe",
    "epochs",
    "batch_size",
    "seed",
    "val_fraction",
    "test_fraction",
    "embargo_bars",
    "label_horizon",
    "label_mode",
    "label_threshold",
)


class ModelVersioningError(Exception):
    """Base class for every refusal in this module."""


class ArtifactStoreError(ModelVersioningError):
    """The object store could not hold, or could not return, an artifact."""


class ArtifactExists(ArtifactStoreError):
    """A key already holds DIFFERENT bytes.

    Never an overwrite. This is the ML-3 guard at the storage layer: an artifact a
    running deployment may be executing is not replaced, whatever the caller intended.
    """


class ArtifactTooLarge(ArtifactStoreError):
    """The serialized model exceeds the ceiling ``MemoryMonitor`` enforces on its cache.

    Refused at write time rather than at deploy time: an artifact that cannot be held in
    the model cache cannot be used for inference, so storing it would produce a row that
    looks deployable and is not.
    """


class ArtifactChecksumMismatch(ArtifactStoreError):
    """What was read back is not what was written, or not what the row records.

    Requirement 17.6's condition. At write time it aborts the row; at read time it
    refuses to serve the bytes.
    """


class ModelVersionNotFound(ModelVersioningError):
    """No such model version belongs to this user.

    Deliberately the same answer for "does not exist", "belongs to another tenant" and
    "``model_versions`` has not been created yet" (Requirement 21.4): existence must not
    leak, and an unapplied migration must not become a 500.
    """


class SignedLinkUnavailable(ModelVersioningError):
    """No signing secret is configured, so no download link can be minted.

    Fail-closed on purpose. An unsigned link would be a public one.
    """


class InvalidArtifactToken(ModelVersioningError):
    """The token is malformed, expired, for another purpose, or not ours."""


def _persistence_unavailable(message: str) -> Exception:
    """``training_worker.ModelPersistenceUnavailable``, imported lazily.

    Raising the worker's own type is what lands a failure here on the closed vocabulary
    as ``MODEL_PERSISTENCE_UNAVAILABLE`` - "every epoch ran, but no artifact was stored
    and no ``model_versions`` row was written" - rather than on the classified catch-all.
    Lazy so this module can be used, and tested, without loading the worker.
    """
    from backend_app.backend.training_worker import ModelPersistenceUnavailable

    return ModelPersistenceUnavailable(message)


# ══════════════════════════════════════════════════════════════════════════
#  2. THE OBJECT STORE
# ══════════════════════════════════════════════════════════════════════════


def artifact_root() -> Path:
    """The local store's root directory, from the environment or the default."""
    return Path(os.getenv(ARTIFACT_ROOT_ENV) or DEFAULT_ARTIFACT_ROOT)


def artifact_bucket() -> str:
    """The configured object-storage bucket, or ``""`` when there is none."""
    return str(os.getenv(ARTIFACT_BUCKET_ENV) or "").strip()


def max_artifact_bytes() -> int:
    """The artifact ceiling, read from ``MemoryMonitor``'s LIVE configuration.

    Read rather than copied, so an operator who tightens the model cache tightens what
    may be written. ``get_cache_stats()`` is the public accessor. ``0`` means "no
    configured ceiling", and then nothing is refused on size.
    """
    try:
        from backend_app.core.ml_safety import MemoryMonitor

        megabytes = int(MemoryMonitor.get_cache_stats().get("max_size_mb") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("The artifact ceiling could not be read from MemoryMonitor: %s", exc)
        return 0
    return max(0, megabytes) * 1024 * 1024


@dataclass(frozen=True)
class StoredArtifact:
    """What one artifact write produced: the row's four artifact columns, measured."""

    uri: str
    key: str
    checksum: str
    bytes: int
    serialization: str
    reused: bool = False

    def to_row(self) -> Dict[str, Any]:
        return {
            "artifact_uri": self.uri,
            "artifact_checksum": self.checksum,
            "artifact_bytes": int(self.bytes),
            "serialization": self.serialization,
        }


class ArtifactStore:
    """Content-addressed, append-only object storage for model artifacts.

    Three rules, and they are why this is an interface rather than two calls to
    ``open()``:

    * ``put`` is **append-only**. An existing key holding the same bytes is reused; an
      existing key holding different bytes raises :class:`ArtifactExists`. There is no
      overwrite and there is no delete, so Requirement 17.3 ("leave in place the
      artifact referenced by any running deployment") does not depend on anybody
      remembering it.
    * ``get`` verifies nothing. Verification is the caller's, against the checksum the
      ``model_versions`` row records, because the row is the authority and the store is
      not.
    * a ``uri`` is opaque to everything outside this module and never leaves the server
      (Requirement 17.8).
    """

    scheme = ""

    def put(self, key: str, data: bytes) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def get(self, uri: str) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def checksum(self, uri: str) -> str:  # pragma: no cover - interface
        """The SHA-256 of what is actually stored."""
        raise NotImplementedError

    def key_of(self, uri: str) -> str:
        """The key inside ``uri``, or :class:`ArtifactStoreError` if it is not ours."""
        prefix = f"{self.scheme}://"
        text = str(uri or "")
        if not text.startswith(prefix):
            raise ArtifactStoreError(
                f"artifact reference {text!r} is not a {self.scheme} reference"
            )
        return text[len(prefix):]


class LocalArtifactStore(ArtifactStore):
    """The default store: a directory on the worker's own volume.

    Why this is the default, stated rather than left to be discovered: the design says
    artifacts go to object storage addressed by reference and checksum, and this
    environment has no object-store credentials. The **addressing** is what the row
    depends on and it is honoured exactly - a key built from sanitised identity segments
    plus the artifact's own checksum, an opaque ``local://`` reference on the row,
    append-only writes - so swapping in :class:`SupabaseStorageArtifactStore` (or an S3
    one) changes where the bytes live and changes nothing about the row, the binding or
    the download exchange.

    Path traversal is closed twice: every segment of a key is built by
    ``ml_models._safe_filename`` (the ML-5 fix), and the resolved path is then asserted
    to be inside the root - so even a key assembled by future code that forgot the
    sanitiser cannot escape.
    """

    scheme = "local"

    def __init__(self, root: Optional[Any] = None):
        self.root = Path(root) if root is not None else artifact_root()

    def path_of(self, key: str) -> Path:
        """``root/key``, asserted to be inside ``root``."""
        root = self.root.resolve()
        candidate = (root / str(key)).resolve()
        if candidate != root and root not in candidate.parents:
            raise ArtifactStoreError(
                f"artifact key {key!r} resolves outside the artifact root; refusing to "
                f"touch {candidate}"
            )
        return candidate

    def path_for_uri(self, uri: str) -> Path:
        return self.path_of(self.key_of(uri))

    def uri_for(self, key: str) -> str:
        return f"{self.scheme}://{key}"

    def put(self, key: str, data: bytes) -> str:
        path = self.path_of(key)
        payload = bytes(data)
        if path.exists():
            existing = hashlib.sha256(path.read_bytes()).hexdigest()
            if existing == hashlib.sha256(payload).hexdigest():
                logger.info(
                    "Artifact %s already holds these exact bytes; reusing it rather "
                    "than rewriting it.",
                    key,
                )
                return self.uri_for(key)
            raise ArtifactExists(
                f"artifact key {key!r} already holds different bytes (stored checksum "
                f"{existing[:16]}...). Artifacts are append-only: a running deployment "
                f"may be executing this one."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling and move, so a crash mid-write cannot leave a truncated
        # artifact behind a checksum that says it is whole.
        staging = path.with_name(path.name + ".partial")
        staging.write_bytes(payload)
        staging.replace(path)
        return self.uri_for(key)

    def get(self, uri: str) -> bytes:
        path = self.path_for_uri(uri)
        if not path.exists():
            raise ArtifactStoreError(f"artifact {uri} is not present in the store")
        return path.read_bytes()

    def checksum(self, uri: str) -> str:
        from backend_app.core.ml_safety import SafeModelLoader

        path = self.path_for_uri(uri)
        if not path.exists():
            raise ArtifactStoreError(f"artifact {uri} is not present in the store")
        # The platform's own function, so the checksum recorded on the row is computed by
        # the code that will later verify it.
        return SafeModelLoader.compute_checksum(str(path))


class SupabaseStorageArtifactStore(ArtifactStore):
    """The same contract over a Supabase storage bucket.

    Used when :data:`ARTIFACT_BUCKET_ENV` names a bucket and the platform's existing
    service-role client exposes ``.storage``. It adds no dependency: the client is the
    one ``core.supabase_connection`` already builds.

    ``upsert`` is explicitly ``"false"`` - :class:`ArtifactStore`'s append-only rule,
    stated in the one place a remote store could otherwise silently overwrite.
    """

    scheme = "supabase"

    def __init__(self, bucket: str, client: Any):
        self.bucket = str(bucket)
        self.client = client

    def _bucket(self) -> Any:
        storage = getattr(self.client, "storage", None)
        if storage is None:
            raise ArtifactStoreError(
                f"the configured Supabase client exposes no storage API, so no artifact "
                f"can be stored in bucket {self.bucket!r}"
            )
        return storage.from_(self.bucket)

    def uri_for(self, key: str) -> str:
        return f"{self.scheme}://{self.bucket}/{key}"

    def key_of(self, uri: str) -> str:
        remainder = super().key_of(uri)
        prefix = f"{self.bucket}/"
        if not remainder.startswith(prefix):
            raise ArtifactStoreError(
                f"artifact reference {uri!r} names another bucket than {self.bucket!r}"
            )
        return remainder[len(prefix):]

    def put(self, key: str, data: bytes) -> str:
        payload = bytes(data)
        try:
            self._bucket().upload(
                key,
                payload,
                {"content-type": "application/octet-stream", "upsert": "false"},
            )
        except ArtifactStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if "exist" in text or "duplicate" in text or "409" in text:
                # Append-only: reuse when the bytes match, refuse when they do not.
                try:
                    stored = self.get(self.uri_for(key))
                except Exception:  # noqa: BLE001 - cannot compare, so cannot reuse
                    raise ArtifactExists(
                        f"artifact key {key!r} already exists in bucket {self.bucket!r} "
                        f"and could not be read back for comparison"
                    ) from exc
                if hashlib.sha256(stored).hexdigest() == hashlib.sha256(payload).hexdigest():
                    return self.uri_for(key)
                raise ArtifactExists(
                    f"artifact key {key!r} already holds different bytes in bucket "
                    f"{self.bucket!r}"
                ) from exc
            raise ArtifactStoreError(
                f"artifact {key!r} could not be stored in bucket {self.bucket!r}: {exc}"
            ) from exc
        return self.uri_for(key)

    def get(self, uri: str) -> bytes:
        key = self.key_of(uri)
        try:
            payload = self._bucket().download(key)
        except ArtifactStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArtifactStoreError(f"artifact {uri} could not be read: {exc}") from exc
        return bytes(payload)

    def checksum(self, uri: str) -> str:
        return hashlib.sha256(self.get(uri)).hexdigest()


_ARTIFACT_STORE: Optional[ArtifactStore] = None


def register_artifact_store(store: Optional[ArtifactStore]) -> None:
    """Install (or, with ``None``, forget) the process's artifact store."""
    global _ARTIFACT_STORE
    if store is not None and not isinstance(store, ArtifactStore):
        raise TypeError(
            f"register_artifact_store expects an ArtifactStore, got "
            f"{type(store).__name__}"
        )
    _ARTIFACT_STORE = store


def reset_artifact_store() -> None:
    """Forget the installed store. For tests, and for a reconfigured worker."""
    register_artifact_store(None)


def current_artifact_store() -> ArtifactStore:
    """The installed store, or one built from the environment.

    A configured bucket wins when the platform's service-role client can actually reach
    it; otherwise the local store is used and **says so**, because "your artifacts are
    somewhere other than where you configured them" is exactly the thing an operator
    must not have to infer.
    """
    if _ARTIFACT_STORE is not None:
        return _ARTIFACT_STORE

    bucket = artifact_bucket()
    if bucket:
        client = None
        try:
            from backend_app.core.supabase_connection import get_supabase_connection

            connection = get_supabase_connection()
            client = connection.get_client() if connection is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Artifact bucket %r is configured but no Supabase client could be "
                "obtained (%s).",
                bucket,
                exc,
            )
        if client is not None and getattr(client, "storage", None) is not None:
            return SupabaseStorageArtifactStore(bucket, client)
        logger.warning(
            "Artifact bucket %r is configured but the Supabase client exposes no "
            "storage API, so artifacts are being written to the local store at %s. "
            "An artifact written on one worker will not be readable from another until "
            "object storage is reachable.",
            bucket,
            artifact_root(),
        )
    return LocalArtifactStore()


# ══════════════════════════════════════════════════════════════════════════
#  3. THE ARTIFACT: BYTES, KEY, NAME
# ══════════════════════════════════════════════════════════════════════════


def serialize_model(model: Any) -> Tuple[bytes, str]:
    """``(bytes, serialization)`` for one fitted model.

    ``joblib`` first, because ``SafeModelLoader.load_model`` reads joblib and a stored
    artifact has to be loadable by the platform's own loader. ``pickle`` only when joblib
    is absent, which is the same fallback ``ml_models`` itself makes - and the mode is
    **recorded on the row** either way, so a reader never has to guess.

    A model that cannot be serialized is a failed persist, not a fallback: silently
    trying another format would produce an artifact the loader cannot read.
    """
    if model is None:
        raise _persistence_unavailable(
            "the trainer exposed no fitted model, so there is nothing to store and no "
            "model_versions row can be written"
        )

    try:
        import joblib
    except ImportError:
        import pickle

        try:
            return (
                pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL),
                SERIALIZATION_PICKLE,
            )
        except Exception as exc:  # noqa: BLE001
            raise _persistence_unavailable(
                f"the fitted model could not be serialized with pickle: {exc}"
            ) from exc

    buffer = io.BytesIO()
    try:
        joblib.dump(model, buffer)
    except Exception as exc:  # noqa: BLE001
        raise _persistence_unavailable(
            f"the fitted model could not be serialized with joblib: {exc}"
        ) from exc
    return buffer.getvalue(), SERIALIZATION_JOBLIB


def _sanitised_pairs(*values: str) -> List[str]:
    """Sanitise identity strings into path segments with the ML-5 fix, in pairs.

    ``ml_models._safe_filename`` takes two strings and returns two sanitised ones. It is
    reused exactly as it is - including its pairwise shape and its 64-character
    truncation - rather than wrapped in a second sanitiser, because one sanitiser the
    ML-5 tests already cover is worth more than two that agree today.
    """
    items = [str(value or "") for value in values]
    if len(items) % 2:
        items.append("")
    out: List[str] = []
    for index in range(0, len(items), 2):
        left, right = _safe_filename(items[index], items[index + 1])
        out.extend([left, right])
    return out


def artifact_key(
    *,
    user_id: str,
    strategy_id: str,
    version_id: str,
    node_id: str,
    model_version: int,
    checksum: str,
    serialization: str,
) -> str:
    """The object key: sanitised identity, then the artifact's own checksum.

    ``models/<user>/<strategy>/<version>/<node>/v<n>-<checksum[:16]>.<ext>``

    Content-addressed on purpose. Two different artifacts cannot collide on one key,
    which is half of why retraining cannot overwrite what a running deployment is
    executing (:class:`ArtifactStore`'s append-only ``put`` is the other half). Every
    segment comes from ``_safe_filename``; the extension is chosen from
    :data:`ARTIFACT_EXTENSIONS` and is the only part not passed through it, because a dot
    would not survive it.
    """
    extension = ARTIFACT_EXTENSIONS.get(str(serialization))
    if not extension:
        raise ArtifactStoreError(
            f"serialization {serialization!r} has no registered artifact extension; "
            f"known modes are {sorted(ARTIFACT_EXTENSIONS)}"
        )
    digest = str(checksum or "")
    if len(digest) < 16:
        raise ArtifactStoreError(
            f"artifact checksum {digest!r} is too short to address an artifact"
        )
    safe_user, safe_strategy, safe_version, safe_node = _sanitised_pairs(
        user_id, strategy_id, version_id, node_id
    )
    stem, _ = _safe_filename(f"v{int(model_version)}-{digest[:16]}", "")
    return "/".join(
        [
            "models",
            safe_user,
            safe_strategy,
            safe_version,
            safe_node,
            f"{stem}.{extension}",
        ]
    )


def artifact_filename(row: Mapping[str, Any]) -> str:
    """The file name a download is served under. Sanitised, and never the key.

    Requirement 17.8's "sanitize artifact file names before use". The name is BUILT from
    the row's own identifiers rather than taken from anything a client sent, and then
    sanitised anyway - so a ``Content-Disposition`` header cannot carry a path, a quote
    or a newline whatever ends up in the database.
    """
    serialization = str(row.get("serialization") or SERIALIZATION_JOBLIB)
    extension = ARTIFACT_EXTENSIONS.get(serialization, "bin")
    node, block = _safe_filename(
        row.get("node_id") or "node", row.get("block_id") or "model"
    )
    stem, _ = _safe_filename(f"{block}-{node}-v{int(row.get('model_version') or 0)}", "")
    return f"{stem}.{extension}"


# ══════════════════════════════════════════════════════════════════════════
#  4. THE ROW'S JSONB DOCUMENTS
# ══════════════════════════════════════════════════════════════════════════


def _scaler_document(schema: Any, model: Any) -> Optional[Dict[str, Any]]:
    """Scaler parameters, if the schema or the model exposes real ones.

    ``feature_schema`` is specified as "names + order + scaler params". A trainer that
    standardises internally - which the honest ones do, on train-split statistics only -
    holds those statistics, so it is asked for them through ``model.scaler_params``; a
    ``FeatureSchema`` carrying an sklearn scaler is read directly. Absent both this is
    ``None`` rather than an invented identity transform, because a deployment that
    standardises with the wrong numbers is worse than one that knows it was not told.
    """
    scaler = getattr(schema, "scaler", None)
    mean = getattr(scaler, "mean_", None)
    scale = getattr(scaler, "scale_", None)
    if mean is not None and scale is not None:
        return {
            "kind": type(scaler).__name__,
            "mean": [float(v) for v in list(mean)],
            "scale": [float(v) for v in list(scale)],
        }

    params = getattr(model, "scaler_params", None)
    if isinstance(params, Mapping) and params:
        document: Dict[str, Any] = {"kind": str(params.get("kind") or "trainer")}
        for key in ("mean", "scale", "std"):
            values = params.get(key)
            if values is None:
                continue
            try:
                document[key] = [float(v) for v in list(values)]
            except Exception:  # noqa: BLE001 - a scaler we cannot read is not recorded
                logger.warning(
                    "Scaler parameter %r could not be recorded as scalars.", key
                )
        return document
    return None


def feature_schema_document(ctx: Any, model: Any = None) -> Dict[str, Any]:
    """The ``feature_schema`` JSONB: names, order, count and scaler parameters.

    This is the document Requirement 17.7 compares against the version's current
    canonical graph at deploy time, so ``feature_names`` is **order-significant** and is
    taken from the schema ``strategy_service.check_feature_schema`` produced from the
    matrix this run actually trained on - not from the block descriptor's idea of what
    the columns ought to be.
    """
    schema = getattr(ctx, "feature_schema", None)
    names = [str(name) for name in (getattr(schema, "expected_features", None) or [])]
    if not names:
        dataset = getattr(ctx, "dataset", None)
        names = [str(name) for name in (getattr(dataset, "columns", None) or [])]

    config = dict(getattr(ctx, "config", None) or {})
    plan = getattr(getattr(ctx, "inputs", None), "plan", None)
    document: Dict[str, Any] = {
        "feature_names": names,
        "feature_count": len(names),
        "node_id": ctx.node_id,
        "block_id": ctx.block_id,
        "warmup_bars": int(getattr(plan, "warmup_bars", 0) or 0),
        "label": {
            "mode": str(config.get("label_mode") or S.DEFAULT_LABEL_MODE),
            "horizon": int(config.get("label_horizon") or S.DEFAULT_LABEL_HORIZON),
        },
        "scaler": _scaler_document(schema, model),
    }
    dtypes = getattr(schema, "feature_dtypes", None)
    if isinstance(dtypes, Mapping) and dtypes:
        document["feature_dtypes"] = {str(k): str(v) for k, v in dtypes.items()}
    return document


def _worker_version() -> str:
    try:
        from backend_app.backend.training_worker import TRAINING_WORKER_VERSION

        return TRAINING_WORKER_VERSION
    except Exception:  # noqa: BLE001
        return ""


def hyperparameters_document(ctx: Any) -> Dict[str, Any]:
    """The ``hyperparameters`` JSONB: what would have to be repeated to repeat this run.

    Built from a **whitelist** of ``training_jobs.config`` keys
    (:data:`HYPERPARAMETER_CONFIG_KEYS`) plus the figures the worker measured, never from
    a copy of the config mapping - see that constant for why.
    """
    config = dict(getattr(ctx, "config", None) or {})
    spec = getattr(ctx, "spec", None)
    document: Dict[str, Any] = {
        "block_id": ctx.block_id,
        "model_family": str(getattr(spec, "model_family", "") or ""),
        "epoch_unit": str(getattr(spec, "epoch_unit", "") or "epochs"),
        "epochs_total": int(getattr(ctx, "epochs_total", 0) or 0),
        "batch_size": getattr(ctx, "batch_size", None),
        "seed": int(getattr(ctx, "seed", 0) or 0),
        "worker_version": _worker_version(),
        "model_versioning_version": MODEL_VERSIONING_VERSION,
    }
    for key in HYPERPARAMETER_CONFIG_KEYS:
        if key in config and key not in document:
            document[key] = config[key]

    splits = getattr(ctx, "splits", None)
    if splits is not None:
        try:
            document["split_sizes"] = dict(splits.sizes)
            document["embargo_bars"] = int(getattr(splits, "embargo_bars", 0) or 0)
        except Exception:  # noqa: BLE001 - a split we cannot read is not recorded
            pass
    fingerprint = str((getattr(ctx, "job", None) or {}).get("dataset_fingerprint") or "")
    if fingerprint:
        # Provenance, not a precondition: task 6.4 records why the recorded fingerprint
        # is compared and reported rather than enforced.
        document["dataset_fingerprint"] = fingerprint
    return document


def split_metrics_document(ctx: Any) -> Dict[str, Dict[str, Any]]:
    """``{"train": {...}, "val": {...}, "test": {...}}``, scalars only.

    The worker scores the three splits and puts them on the context
    (``TrainingContext.split_metrics``) precisely so this row can record them:
    Requirement 17.1 asks for training, validation **and** test metrics on the model
    version, and the design's own note says they belong here rather than duplicated onto
    the job. Each set goes through ``sanitize_epoch_metrics`` again - it is cheap, and a
    direct caller of this function has not been through the worker's scorer.
    """
    from backend_app.backend.training_worker import sanitize_epoch_metrics

    raw = dict(getattr(ctx, "split_metrics", None) or {})
    if not raw:
        # A run with no scored splits is not a failed run - the design's scorer treats a
        # missing score as a warning - but it must not look like a scored one.
        logger.warning(
            "Training job %s produced no split metrics, so train/val/test metrics are "
            "recorded as empty rather than invented.",
            getattr(ctx, "job_id", ""),
        )
    return {
        split: sanitize_epoch_metrics(raw.get(split) or {})
        for split in ("train", "val", "test")
    }


def public_model_version(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The ONLY projection of a ``model_versions`` row that anything returns to a client.

    ``artifact_uri`` is absent **by construction** - this builds a new mapping from named
    keys rather than copying the row and deleting one, because a projection that removes
    a key is one refactor away from not removing it. The checksum, the size and the
    serialization mode ARE returned: they are what a client needs to verify a download it
    has a signed link for, and none of them says where the bytes live.

    ``artifact_available`` replaces the reference: a client learns that an artifact
    exists, and gets at it only through :func:`artifact_download_descriptor`.
    """
    return {
        "id": str(row.get("id") or ""),
        "model_version": int(row.get("model_version") or 0),
        "training_job_id": str(row.get("training_job_id") or ""),
        "strategy_id": str(row.get("strategy_id") or ""),
        "version_id": str(row.get("version_id") or ""),
        "node_id": str(row.get("node_id") or ""),
        "block_id": str(row.get("block_id") or ""),
        "artifact_checksum": str(row.get("artifact_checksum") or ""),
        "artifact_bytes": int(row.get("artifact_bytes") or 0),
        "serialization": str(row.get("serialization") or ""),
        "artifact_available": bool(row.get("artifact_uri")),
        "artifact_filename": artifact_filename(row),
        "feature_schema": row.get("feature_schema") or {},
        "hyperparameters": row.get("hyperparameters") or {},
        "train_metrics": row.get("train_metrics") or {},
        "val_metrics": row.get("val_metrics") or {},
        "test_metrics": row.get("test_metrics") or {},
        "is_active": bool(row.get("is_active")),
        "created_at": row.get("created_at"),
    }


# ══════════════════════════════════════════════════════════════════════════
#  5. PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════


def is_missing_model_table_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says ``model_versions`` does not exist.

    Narrow, for the reason ``strategy_service`` gives for the same split: anything this
    returns ``False`` for propagates, because a write that fails loudly beats a model
    that silently was never recorded. The generic missing-relation codes are recognised by
    ``strategy_service.is_missing_training_table_error`` - both tables come from the same
    migration - and the table-named phrasing is added here.
    """
    if S.is_missing_training_table_error(exc):
        return True
    text = str(exc).lower()
    if MODEL_VERSIONS_TABLE not in text:
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown table")
    )


def is_duplicate_active_model_error(exc: BaseException) -> bool:
    """True when ``exc`` is ``uq_mv_active_per_node`` or ``uq_mv_version_node`` rejecting.

    Recognised by index name as well as by SQLSTATE, so a client library that reformats
    the message still lands here.
    """
    text = str(exc).lower()
    if "uq_mv_active_per_node" in text or "uq_mv_version_node" in text:
        return True
    return any(code in text for code in ("23505", "unique_violation", "duplicate key"))


def _degrade(exc: BaseException, what: str) -> bool:
    """Log a missing-``model_versions`` error naming 004d. ``True`` when it was one."""
    if is_missing_model_table_error(exc):
        logger.warning(
            "Model versioning could not %s because %s does not exist. Apply %s. "
            "Detail: %s",
            what,
            MODEL_VERSIONS_TABLE,
            MODEL_MIGRATION,
            exc,
        )
        return True
    return False


def _unavailable_because_absent() -> Exception:
    return _persistence_unavailable(
        f"{MODEL_VERSIONS_TABLE} does not exist, so no artifact reference could be "
        f"recorded and no model version could be bound. Apply {MODEL_MIGRATION}."
    )


async def read_model_versions(
    sb: Any, version_id: str, node_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every ``model_versions`` row for one version, optionally narrowed to one node.

    Raises ``ModelPersistenceUnavailable`` when the table is absent, so a caller on the
    worker records a classified failure rather than a 500 - and the warning names 004d.
    """
    if sb is None:
        raise _persistence_unavailable(
            "no database client is available, so no model version could be read"
        )
    try:
        query = (
            sb.table(MODEL_VERSIONS_TABLE)
            .select("*")
            .eq("version_id", str(version_id))
        )
        if node_id is not None:
            query = query.eq("node_id", str(node_id))
        result = await S._execute(query.execute())
    except Exception as exc:  # noqa: BLE001
        if _degrade(exc, "read the model versions of a strategy version"):
            raise _unavailable_because_absent() from exc
        raise

    error_text = S._result_error_text(result)
    if error_text:
        if _degrade(Exception(error_text), "read the model versions of a strategy version"):
            raise _unavailable_because_absent()
        raise _persistence_unavailable(
            f"the model versions of {version_id} could not be read: {error_text}"
        )
    rows = (getattr(result, "data", None) or []) if result else []
    return [dict(row or {}) for row in rows]


def next_model_version(rows: Sequence[Mapping[str, Any]]) -> int:
    """The next ``model_version`` integer for a node. Monotonic, never reused.

    ``uq_mv_version_node`` makes ``(version_id, node_id, model_version)`` unique, so a
    reused integer is a rejected insert rather than an ambiguous history. Derived from the
    maximum rather than from the count, so a gap - only a cascade can leave one, since
    004d grants DELETE to nobody - cannot make two rows collide.
    """
    highest = 0
    for row in rows or ():
        try:
            highest = max(highest, int(row.get("model_version") or 0))
        except (TypeError, ValueError):
            continue
    return highest + 1


async def _set_active(sb: Any, model_version_id: str, active: bool) -> bool:
    """Flip ``is_active`` on one row as a compare-and-set on its current value.

    A **service-role** write. 004d creates no ``mv_owner_update`` policy and grants UPDATE
    on ``model_versions`` to ``service_role`` only, deliberately: deactivating a superseded
    model version decides which artifact a running deployment resolves, and that is a
    backend decision, never a client one.
    """
    query = (
        sb.table(MODEL_VERSIONS_TABLE)
        .update({"is_active": bool(active)})
        .eq("id", str(model_version_id))
        .eq("is_active", not bool(active))
        .execute()
    )
    result = await S._execute(query)
    error_text = S._result_error_text(result)
    if error_text:
        raise _persistence_unavailable(
            f"model version {model_version_id} could not be "
            f"{'activated' if active else 'deactivated'}: {error_text}"
        )
    return bool(getattr(result, "data", None))


async def insert_model_version(
    sb: Any,
    *,
    user_id: str,
    training_job_id: str,
    strategy_id: str,
    version_id: str,
    node_id: str,
    block_id: str,
    artifact: StoredArtifact,
    feature_schema: Mapping[str, Any],
    hyperparameters: Mapping[str, Any],
    train_metrics: Optional[Mapping[str, Any]] = None,
    val_metrics: Optional[Mapping[str, Any]] = None,
    test_metrics: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert one ``model_versions`` row and make it the active one for its node.

    **The insert IS the binding** (Requirement 17.2). The row carries ``version_id`` and
    ``node_id``, and ``uq_mv_active_per_node`` is a partial unique index over exactly that
    pair, so "bound to one immutable strategy version and one node" is the shape of the
    row rather than a second write that could be forgotten. There is no separate
    ``bind_model_to_version_node`` step; the design's pseudocode names one, and it is this
    insert.

    Ordering, and why it is deactivate-then-insert
    ----------------------------------------------
    A new row is inserted ``is_active = TRUE``. If the node already has an active row,
    inserting first would be rejected by ``uq_mv_active_per_node`` - so the superseded row
    is deactivated first, in the SAME logical operation.

    004d's header asks for both writes "in the same transaction as the new INSERT". The
    platform reaches PostgreSQL through PostgREST, which exposes no multi-statement
    transaction to this client, so the pair is a **compensating sequence** instead:
    deactivate, insert, and on a failed insert re-activate what was deactivated. The
    invariant that arrangement protects - never two active rows for one node - holds
    throughout, because the window between the two writes has *fewer* active rows, not
    more. What the sequence can leave behind on a crash between them is a node with **no**
    active model version, which reads as "awaiting a model" and holds a deployment out of
    running (Requirement 17.6's disposition) rather than silently swapping an artifact
    underneath a running one. That is the safe direction to fail in, and it is why this is
    acceptable rather than merely convenient.

    The retrained node's predecessor keeps its ``artifact_uri``, its checksum and its
    bytes. Nothing here deletes or rewrites an artifact (Requirement 17.3).
    """
    if sb is None:
        raise _persistence_unavailable(
            "no database client is available, so no model version could be recorded"
        )

    # 004d declares these NOT NULL. Refused here rather than coerced, because
    # ``str(None)`` is the string ``"None"`` and a row carrying that satisfies the
    # constraint while naming a node, a block or an owner that does not exist.
    identity = {
        "user_id": user_id,
        "training_job_id": training_job_id,
        "strategy_id": strategy_id,
        "version_id": version_id,
        "node_id": node_id,
        "block_id": block_id,
    }
    absent = sorted(name for name, value in identity.items() if not str(value or "").strip())
    if absent:
        raise _persistence_unavailable(
            f"a model version cannot be recorded without {', '.join(absent)}: "
            f"{MODEL_VERSIONS_TABLE} declares those columns NOT NULL and a placeholder "
            f"would name a resource that does not exist"
        )

    existing = await read_model_versions(sb, version_id, node_id)
    version_number = next_model_version(existing)
    superseded = [
        str(row.get("id"))
        for row in existing
        if row.get("is_active") and row.get("id")
    ]

    row: Dict[str, Any] = {
        "user_id": str(user_id),
        "training_job_id": str(training_job_id),
        "strategy_id": str(strategy_id),
        "version_id": str(version_id),
        "node_id": str(node_id),
        "block_id": str(block_id),
        "model_version": int(version_number),
        "feature_schema": dict(feature_schema or {}),
        "hyperparameters": dict(hyperparameters or {}),
        "train_metrics": dict(train_metrics or {}),
        "val_metrics": dict(val_metrics or {}),
        "test_metrics": dict(test_metrics or {}),
        "is_active": True,
    }
    row.update(artifact.to_row())

    deactivated: List[str] = []
    for model_version_id in superseded:
        if await _set_active(sb, model_version_id, False):
            deactivated.append(model_version_id)
            logger.info(
                "Model version %s for node %s of version %s was deactivated; its "
                "artifact is left in place and a running deployment pinned to it keeps "
                "resolving the same bytes.",
                model_version_id,
                node_id,
                version_id,
            )

    try:
        result = await S._execute(sb.table(MODEL_VERSIONS_TABLE).insert(row).execute())
        error_text = S._result_error_text(result)
        if error_text:
            raise Exception(error_text)
    except Exception as exc:  # noqa: BLE001
        # Compensate before reporting, so a failed insert does not leave the node with no
        # active model at all.
        for model_version_id in deactivated:
            try:
                await _set_active(sb, model_version_id, True)
                logger.warning(
                    "Model version %s was re-activated after the replacement insert "
                    "failed, so node %s keeps the model it had.",
                    model_version_id,
                    node_id,
                )
            except Exception as restore_error:  # noqa: BLE001
                logger.error(
                    "Model version %s could not be re-activated after a failed insert "
                    "(%s). Node %s of version %s now has NO active model version and "
                    "reads as awaiting a model, which holds any deployment out of "
                    "running rather than letting it resolve an unchosen artifact.",
                    model_version_id,
                    restore_error,
                    node_id,
                    version_id,
                )
        if _degrade(exc, "record a model version"):
            raise _unavailable_because_absent() from exc
        if is_duplicate_active_model_error(exc):
            raise _persistence_unavailable(
                f"a model version for node {node_id!r} of strategy version {version_id} "
                f"was rejected by the database's one-active-model-per-node rule "
                f"(uq_mv_active_per_node / uq_mv_version_node). Nothing was overwritten. "
                f"Detail: {exc}"
            ) from exc
        raise _persistence_unavailable(
            f"the model version for node {node_id!r} of strategy version {version_id} "
            f"could not be recorded: {exc}"
        ) from exc

    written = (getattr(result, "data", None) or []) if result else []
    stored = dict(written[0]) if written else dict(row)
    logger.info(
        "Model version %s (v%d) bound to node %s of strategy version %s: %d bytes, "
        "checksum %s..., %s.",
        stored.get("id") or "(id assigned by the database)",
        version_number,
        node_id,
        version_id,
        artifact.bytes,
        artifact.checksum[:16],
        artifact.serialization,
    )
    return stored


async def active_model_versions(sb: Any, version_id: str) -> Dict[str, Dict[str, Any]]:
    """``{node_id: row}`` for the active model version of each bound node.

    Two active rows for one node would be a broken ``uq_mv_active_per_node``, so this
    warns rather than picking one silently: with the migration applied it is unreachable,
    and against anything else it must be visible.
    """
    found: Dict[str, Dict[str, Any]] = {}
    for row in await read_model_versions(sb, version_id):
        if not row.get("is_active"):
            continue
        node_id = str(row.get("node_id") or "")
        if node_id in found:
            logger.error(
                "Strategy version %s has more than one ACTIVE model version for node "
                "%s. uq_mv_active_per_node should make that unrepresentable - check "
                "that %s has been applied.",
                version_id,
                node_id,
                MODEL_MIGRATION,
            )
            continue
        found[node_id] = row
    return found


async def promote_version_if_ready(
    sb: Any, version_id: str, ml_nodes: Sequence[str]
) -> Dict[str, Any]:
    """Move the version to ``READY`` when EVERY ML node has an active model version.

    Requirement 17.5, and the loop invariant the design states for the training gate: a
    partially trained strategy is not a strategy, so a partial binding is not ``READY``.
    The unbound nodes are named in the report and in the log, because "not ready" without
    "waiting for which node" is not a truthful status.

    The write goes through ``training_worker.set_version_lifecycle``, which validates the
    target against ``strategy_builder.LIFECYCLE_STATES`` (``chk_lifecycle_state``
    verbatim) before touching the row - reused rather than restated, so this module cannot
    disagree with the worker about the vocabulary.
    """
    from backend_app.backend.strategy_builder import LIFECYCLE_READY
    from backend_app.backend.training_worker import set_version_lifecycle

    wanted = [str(node) for node in (ml_nodes or ()) if str(node)]
    bound = await active_model_versions(sb, version_id)
    missing = [node for node in wanted if node not in bound]

    report: Dict[str, Any] = {
        "version_id": str(version_id),
        "ml_nodes": wanted,
        "bound_nodes": sorted(bound),
        "unbound_nodes": missing,
        "ready": False,
        "lifecycle_state": None,
    }

    if not wanted:
        # A graph with no model node is READY the moment it compiles (Requirement 14.10)
        # and that transition is task 6.3's. Reaching here with no ML nodes means a job
        # trained a node the plan does not declare, which is not a state to paper over.
        logger.warning(
            "Strategy version %s declares no model node, so nothing here promotes it to "
            "%s.",
            version_id,
            LIFECYCLE_READY,
        )
        return report

    if missing:
        logger.info(
            "Strategy version %s is not %s yet: %d of %d model node(s) bound, still "
            "waiting for %s.",
            version_id,
            LIFECYCLE_READY,
            len(wanted) - len(missing),
            len(wanted),
            ", ".join(missing),
        )
        return report

    written = await set_version_lifecycle(sb, str(version_id), LIFECYCLE_READY)
    report["ready"] = bool(written)
    report["lifecycle_state"] = LIFECYCLE_READY if written else None
    if written:
        logger.info(
            "Strategy version %s is %s: every one of its %d model node(s) is bound to an "
            "active model version.",
            version_id,
            LIFECYCLE_READY,
            len(wanted),
        )
    return report


# ══════════════════════════════════════════════════════════════════════════
#  6. STORING THE ARTIFACT
# ══════════════════════════════════════════════════════════════════════════


def store_artifact(
    model: Any,
    *,
    user_id: str,
    strategy_id: str,
    version_id: str,
    node_id: str,
    model_version: int,
    store: Optional[ArtifactStore] = None,
) -> StoredArtifact:
    """Serialize, size-check, store, and verify one artifact by reading it back.

    The order is the point:

    1. serialize, so the bytes exist before anything is addressed;
    2. hash them, so the key is content-addressed and cannot collide with another model;
    3. refuse anything over :func:`max_artifact_bytes` **before** writing it;
    4. store append-only - never overwriting whatever a running deployment may be using;
    5. read the stored artifact's checksum back through
       ``SafeModelLoader.compute_checksum`` and compare.

    Step 5 is what makes the recorded checksum a fact rather than an intention. A
    truncated or altered write is caught here, no ``model_versions`` row is inserted, and
    the run is reported as a persistence failure - instead of producing a row whose
    checksum will fail at deploy time and leave the node awaiting a model with no
    explanation.
    """
    payload, serialization = serialize_model(model)
    checksum = hashlib.sha256(payload).hexdigest()
    size = len(payload)

    ceiling = max_artifact_bytes()
    if ceiling and size > ceiling:
        raise ArtifactTooLarge(
            f"the serialized model is {size} bytes, over the {ceiling}-byte artifact "
            f"ceiling MemoryMonitor's model cache allows. An artifact that cannot be "
            f"cached cannot serve inference, so it is refused here rather than at deploy "
            f"time."
        )

    target = store if store is not None else current_artifact_store()
    key = artifact_key(
        user_id=user_id,
        strategy_id=strategy_id,
        version_id=version_id,
        node_id=node_id,
        model_version=int(model_version),
        checksum=checksum,
        serialization=serialization,
    )
    uri = target.put(key, payload)

    stored_checksum = target.checksum(uri)
    if stored_checksum != checksum:
        raise ArtifactChecksumMismatch(
            f"the artifact stored at {key!r} checksums to {stored_checksum[:16]}... but "
            f"the bytes written checksum to {checksum[:16]}.... No model version was "
            f"recorded."
        )

    return StoredArtifact(
        uri=uri,
        key=key,
        checksum=checksum,
        bytes=size,
        serialization=serialization,
    )


# ══════════════════════════════════════════════════════════════════════════
#  7. THE SEAM: THE BINDER
# ══════════════════════════════════════════════════════════════════════════


async def bind_trained_model(ctx: Any, model: Any, *, sb: Any = None, store: Any = None) -> Dict[str, Any]:
    """``TrainingBackend.binder``: persist the artifact, write the row, bind, promote.

    The design's four steps, in the design's order::

        persist_artifact -> insert_model_version -> bind_model_to_version_node
                         -> IF all_ml_nodes_bound THEN set_state(version, READY)

    The worker writes ``COMPLETED`` only after this returns something truthy, so every
    failure below leaves the job ``FAILED`` with a classified reason and **no bound
    model** - which is the honest report of a run that produced nothing deployable.

    The client-safe projection is what is returned, not the row. The worker publishes this
    mapping on ``training.completed`` and carries it on ``TrainingRunResult``, so
    :func:`public_model_version` is applied HERE rather than at the API boundary: a value
    that never holds ``artifact_uri`` cannot leak it through a channel nobody remembered
    to filter.

    The client is **the one the run is already being written with** (``ctx.sb``), falling
    back to ``training_worker._worker_client`` - the **service-role** singleton, which is
    what 004d's header says this step needs: there is no ``mv_owner_update`` policy, so
    deactivating a superseded row cannot be done with a caller's RLS-scoped client. Tenant
    scoping is therefore explicit rather than free - every write here is keyed on the
    ``version_id`` and ``node_id`` of the job the worker claimed, and ``user_id`` is copied
    from that job row.
    """
    from backend_app.backend.training_worker import _worker_client

    client = sb if sb is not None else getattr(ctx, "sb", None)
    if client is None:
        client = await _worker_client()
    if client is None:
        raise _persistence_unavailable(
            "no database client is available, so the fitted model could not be recorded "
            "and no model version was bound"
        )

    version_id = ctx.version_id
    node_id = ctx.node_id
    job = dict(getattr(ctx, "job", None) or {})
    strategy_id = str(job.get("strategy_id") or "")
    if not (version_id and node_id and strategy_id and ctx.user_id):
        raise _persistence_unavailable(
            f"the training job is missing the identity a model version is bound to "
            f"(version_id={version_id!r}, node_id={node_id!r}, "
            f"strategy_id={strategy_id!r}), so nothing could be bound"
        )

    # The next number for THIS node, read before the artifact is addressed because the key
    # carries it. A racing second writer loses at uq_mv_version_node, not here.
    existing = await read_model_versions(client, version_id, node_id)
    number = next_model_version(existing)

    artifact = store_artifact(
        model,
        user_id=ctx.user_id,
        strategy_id=strategy_id,
        version_id=version_id,
        node_id=node_id,
        model_version=number,
        store=store,
    )

    metrics = split_metrics_document(ctx)
    row = await insert_model_version(
        client,
        user_id=ctx.user_id,
        training_job_id=ctx.job_id,
        strategy_id=strategy_id,
        version_id=version_id,
        node_id=node_id,
        block_id=ctx.block_id,
        artifact=artifact,
        feature_schema=feature_schema_document(ctx, model),
        hyperparameters=hyperparameters_document(ctx),
        train_metrics=metrics.get("train"),
        val_metrics=metrics.get("val"),
        test_metrics=metrics.get("test"),
    )

    plan = getattr(getattr(ctx, "inputs", None), "plan", None)
    ml_nodes = [str(node) for node in (getattr(plan, "ml_nodes", None) or ())]
    readiness = await promote_version_if_ready(client, version_id, ml_nodes)

    public = public_model_version(row)
    public["readiness"] = readiness
    public["artifact_reused"] = bool(artifact.reused)
    return public


def training_backend(trainer: Any, *, name: str = "phase6-model-versioning") -> Any:
    """A ``TrainingBackend`` whose ``binder`` is :func:`bind_trained_model`.

    The one line that closes task 6.4's gap: with this installed a run that fits every
    epoch reaches ``COMPLETED``, because the artifact, the row and the binding exist.
    ``trainer`` is the caller's - this module owns the persistence half and nothing else.
    """
    from backend_app.backend.training_worker import TrainingBackend

    return TrainingBackend(trainer=trainer, binder=bind_trained_model, name=name)


# ══════════════════════════════════════════════════════════════════════════
#  8. THE DOWNLOAD EXCHANGE
#
#  Requirement 17.8: "serve model artifacts through an ownership-checked endpoint and
#  sanitize artifact file names before use", and design.md's security surface:
#  "artifact_uri is server-side only and never returned raw to the client; download goes
#  through a signed, ownership-checked endpoint".
#
#  Two steps, because those two sentences ask for two different things:
#
#    1. MINT. The caller's JWT is checked, the row is read under the caller's own
#       RLS-scoped client AND an explicit user_id filter, and a short-lived token bound
#       to (model_version_id, user_id) is signed. No artifact_uri is returned.
#    2. REDEEM. The token is verified, and the row is read AGAIN and its user_id compared
#       to the token's before a single byte is served. Ownership is therefore checked at
#       both ends, not carried across on trust.
#
#  Why a signed token at all: an artifact download is a browser navigation or a file
#  save, and this platform authenticates with a Bearer JWT that such a request cannot
#  carry. The token is the credential for that one object, for five minutes. It is signed
#  with the platform's existing secret and it is fail-closed - no secret, no link.
# ══════════════════════════════════════════════════════════════════════════


def _signing_secret() -> bytes:
    """The platform's existing HMAC secret. Fail-closed: no secret, no signed link.

    ``SUPABASE_JWT_SECRET`` first and ``JWT_SECRET`` second, which is the order
    ``core.auth_middleware`` already resolves them in - one secret for the process, not a
    new one to rotate.
    """
    secret = ""
    try:
        from backend_app.core.config import settings

        secret = str(
            os.getenv("SUPABASE_JWT_SECRET")
            or getattr(settings, "SUPABASE_JWT_SECRET", "")
            or os.getenv("JWT_SECRET")
            or getattr(settings, "JWT_SECRET", "")
            or ""
        )
    except Exception:  # noqa: BLE001 - settings absent is the same as no secret
        secret = str(os.getenv("SUPABASE_JWT_SECRET") or os.getenv("JWT_SECRET") or "")
    if not secret:
        raise SignedLinkUnavailable(
            "no signing secret is configured (SUPABASE_JWT_SECRET or JWT_SECRET), so no "
            "signed artifact link can be issued. An unsigned link would be a public one."
        )
    return secret.encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign_artifact_token(
    model_version_id: str,
    user_id: str,
    *,
    ttl_seconds: int = DOWNLOAD_TTL_SECONDS,
    now: Optional[float] = None,
) -> Tuple[str, int]:
    """``(token, expires_at)`` for one artifact and one user.

    The signature covers the whole payload, so neither the model version id, the user id,
    the purpose nor the expiry can be edited. ``purpose`` is in the payload so a token
    minted for something else cannot open an artifact, and ``user_id`` is in the payload so
    redemption can compare it against the row's owner rather than trusting the caller.
    """
    issued = float(now if now is not None else time.time())
    expires_at = int(issued + max(1, int(ttl_seconds)))
    payload = {
        "purpose": ARTIFACT_TOKEN_PURPOSE,
        "model_version_id": str(model_version_id),
        "user_id": str(user_id),
        "exp": expires_at,
        "v": MODEL_VERSIONING_VERSION,
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64(hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}", expires_at


def verify_artifact_token(token: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """The token's payload, or :class:`InvalidArtifactToken`.

    The signature is compared with :func:`hmac.compare_digest` - constant time, so a
    forged token cannot be refined byte by byte - and it is compared BEFORE the payload is
    interpreted, so an attacker-controlled document is never parsed as trusted.
    """
    text = str(token or "")
    if text.count(".") != 1:
        raise InvalidArtifactToken("the artifact token is malformed")
    body, signature = text.split(".", 1)

    expected = _b64(hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise InvalidArtifactToken("the artifact token's signature does not verify")

    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise InvalidArtifactToken("the artifact token's payload is unreadable") from exc
    if not isinstance(payload, Mapping):
        raise InvalidArtifactToken("the artifact token's payload is not an object")

    if str(payload.get("purpose") or "") != ARTIFACT_TOKEN_PURPOSE:
        raise InvalidArtifactToken("the artifact token was not minted for an artifact")
    moment = float(now if now is not None else time.time())
    if float(payload.get("exp") or 0) <= moment:
        raise InvalidArtifactToken("the artifact token has expired")
    if not str(payload.get("model_version_id") or "") or not str(payload.get("user_id") or ""):
        raise InvalidArtifactToken("the artifact token names no model version or no user")
    return dict(payload)


async def _client_for(user: Mapping[str, Any], sb: Any = None) -> Any:
    """The caller's own RLS-scoped client, through the seam the platform already uses.

    ``strategy_service.create_request_supabase_async`` rather than a direct import of
    ``core.dependencies``, so this path is the same one every other Strategy Builder read
    goes through - and the same one the tests substitute.
    """
    if sb is not None:
        return sb
    result = S.create_request_supabase_async(dict(user or {}).get("access_token"))
    if hasattr(result, "__await__"):
        return await result
    return result


async def load_owned_model_version(
    user: Mapping[str, Any], model_version_id: str, *, sb: Any = None
) -> Dict[str, Any]:
    """One ``model_versions`` row this user owns, or :class:`ModelVersionNotFound`.

    Two filters, deliberately, exactly as ``strategy_service._load_owned_version`` does it:
    the request-scoped client applies ``mv_owner_select`` (``user_id = auth.uid()``), and
    the query then carries an explicit ``.eq("user_id", ...)``. Requirement 21.2 asks for
    the filter in the handler as well as in the database, and a row that matches neither is
    reported as **not found** - the same answer a non-existent id gets, so existence does
    not leak across tenants (Requirement 21.4).

    An absent ``model_versions`` table is also "not found", with a warning naming 004d: a
    client asking for a model in an environment where the migration has not been applied
    gets the honest "there is no such model version", never a 500.
    """
    client = await _client_for(user, sb)
    user_id = str(dict(user or {}).get("id") or "")
    if client is None or not user_id or not str(model_version_id or ""):
        raise ModelVersionNotFound(f"Model version {model_version_id} not found")

    try:
        result = await S._execute(
            client.table(MODEL_VERSIONS_TABLE)
            .select("*")
            .eq("id", str(model_version_id))
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if _degrade(exc, "read a model version for a download"):
            raise ModelVersionNotFound(
                f"Model version {model_version_id} not found"
            ) from exc
        raise

    error_text = S._result_error_text(result)
    if error_text:
        if _degrade(Exception(error_text), "read a model version for a download"):
            raise ModelVersionNotFound(f"Model version {model_version_id} not found")
        raise ModelVersioningError(
            f"model version {model_version_id} could not be read: {error_text}"
        )

    rows = (getattr(result, "data", None) or []) if result else []
    if not rows:
        raise ModelVersionNotFound(f"Model version {model_version_id} not found")
    row = dict(rows[0])
    if str(row.get("user_id") or "") != user_id:
        # Unreachable with the filter above and with RLS in force. Asserted anyway,
        # because this is the last line before an artifact is handed out.
        raise ModelVersionNotFound(f"Model version {model_version_id} not found")
    return row


async def artifact_download_descriptor(
    user: Mapping[str, Any],
    model_version_id: str,
    *,
    sb: Any = None,
    ttl_seconds: int = DOWNLOAD_TTL_SECONDS,
) -> Dict[str, Any]:
    """A signed, short-lived link to one artifact, plus what a client needs to verify it.

    Never ``artifact_uri``: the descriptor's ``download_url`` is a path plus a token, and
    :func:`public_model_version` supplies the rest. The client gets the checksum and the
    byte count so it can check what it downloaded, and the sanitised file name so it can
    save it without inventing one.
    """
    row = await load_owned_model_version(user, model_version_id, sb=sb)
    if not row.get("artifact_uri"):
        raise ModelVersionNotFound(f"Model version {model_version_id} not found")

    token, expires_at = sign_artifact_token(
        str(row.get("id") or ""),
        str(dict(user or {}).get("id") or ""),
        ttl_seconds=ttl_seconds,
    )
    return {
        "model_version": public_model_version(row),
        "download_url": f"{ARTIFACT_DOWNLOAD_PATH}?token={token}",
        "expires_at": expires_at,
        "expires_in_seconds": int(max(1, int(ttl_seconds))),
        "filename": artifact_filename(row),
        "artifact_checksum": str(row.get("artifact_checksum") or ""),
        "artifact_bytes": int(row.get("artifact_bytes") or 0),
        "serialization": str(row.get("serialization") or ""),
    }


def read_artifact_bytes(row: Mapping[str, Any], *, store: Optional[ArtifactStore] = None) -> bytes:
    """The artifact's bytes, verified against the checksum the row records.

    Requirement 17.6's condition, applied on the serving path: bytes whose checksum does
    not match the row are **not returned**. A caller that cannot get the artifact is a
    caller that has been told the truth; one that gets a silently different artifact is
    the failure this refuses.
    """
    uri = str(row.get("artifact_uri") or "")
    if not uri:
        raise ArtifactStoreError("this model version records no artifact reference")

    target = store if store is not None else current_artifact_store()
    payload = target.get(uri)
    recorded = str(row.get("artifact_checksum") or "")
    actual = hashlib.sha256(payload).hexdigest()
    if recorded and actual != recorded:
        raise ArtifactChecksumMismatch(
            f"model version {row.get('id')} records checksum {recorded[:16]}... but its "
            f"stored artifact checksums to {actual[:16]}.... The artifact is not served."
        )
    return payload


async def load_model_version_for_token(
    payload: Mapping[str, Any], *, sb: Any = None
) -> Dict[str, Any]:
    """The row a verified token names, re-checked against the token's owner.

    The redeeming request carries no JWT - that is the whole reason the token exists - so
    this read cannot go through a caller-scoped client. It uses the backend's own
    service-role client and makes the ownership filter **explicit**: the query carries both
    ``id`` and the ``user_id`` the token was signed for, and the row's ``user_id`` is
    compared again afterwards. Ownership is therefore checked at redemption too, not
    carried across from the mint on trust.

    A row that does not match, an absent table and an artifact-less row are all
    :class:`ModelVersionNotFound`, so a token for a deleted or unmigrated model tells the
    holder nothing beyond "no".
    """
    from backend_app.backend.training_worker import _worker_client

    client = sb if sb is not None else await _worker_client()
    model_version_id = str(dict(payload or {}).get("model_version_id") or "")
    user_id = str(dict(payload or {}).get("user_id") or "")
    if client is None or not model_version_id or not user_id:
        raise ModelVersionNotFound(f"Model version {model_version_id} not found")

    try:
        result = await S._execute(
            client.table(MODEL_VERSIONS_TABLE)
            .select("*")
            .eq("id", model_version_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if _degrade(exc, "read a model version for a signed download"):
            raise ModelVersionNotFound(
                f"Model version {model_version_id} not found"
            ) from exc
        raise

    error_text = S._result_error_text(result)
    if error_text:
        if _degrade(Exception(error_text), "read a model version for a signed download"):
            raise ModelVersionNotFound(f"Model version {model_version_id} not found")
        raise ModelVersioningError(
            f"model version {model_version_id} could not be read: {error_text}"
        )

    rows = (getattr(result, "data", None) or []) if result else []
    if not rows:
        raise ModelVersionNotFound(f"Model version {model_version_id} not found")
    row = dict(rows[0])
    if str(row.get("user_id") or "") != user_id or not row.get("artifact_uri"):
        raise ModelVersionNotFound(f"Model version {model_version_id} not found")
    return row


__all__.append("load_model_version_for_token")
