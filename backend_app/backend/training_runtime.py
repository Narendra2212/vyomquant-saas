"""
╔══════════════════════════════════════════════════════════════════════════╗
║  THE GOVERNED TRAINING RUNTIME — the fit that executes an approved plan  ║
╚══════════════════════════════════════════════════════════════════════════╝

WHAT THIS CLOSES
================
``training_worker`` has always had a named seam for the fit - ``TrainingBackend``,
installed with ``register_training_backend`` - and until now **nothing installed one in
production**. A repository-wide search found ``register_training_backend`` called only
from tests. The consequence was precise and invisible: every job admitted through the
governed path reached the worker, claimed, re-checked its caps, and then ended
``FAILED`` / ``TRAINER_UNAVAILABLE`` because there was no trainer. The only training
that actually ran was the ungoverned in-process path in ``routers/strategies.py``, which
is now closed. This module is what makes the governed path able to train.

THE THREE RULES THIS RUNTIME EXISTS TO HOLD
===========================================
1. **It executes the APPROVED configuration, never a request.** Epochs, batch size,
   task, seed and the early-stopping terms are read from ``training_jobs.config``, which
   the policy engine wrote at admission. There is no path from an HTTP body to a
   hyperparameter here.

2. **Validation is the EMBARGOED split, never a framework's own.** The legacy trainers
   called ``model.fit(..., validation_split=0.1)``, which carves a validation set out of
   the training rows with no embargo between them - so a training row's forward-looking
   label window overlapped the validation rows it was being judged on. Every validation
   number produced that way was optimistic by construction. Here the splits come from
   ``ml_dataset.TemporalSplits``, which is chronological, disjoint and separated by at
   least ``feature_lookback + label_horizon`` bars, and they arrive as ranges that
   cannot be shuffled.

3. **One epoch means one epoch.** ``train_epoch(n)`` adds exactly one boosting round,
   one iteration or one estimator to a model that persists between calls. That is what
   lets the worker decide, at every boundary, whether to continue - which is what makes
   validation-aware early stopping possible at all. A trainer that refitted from scratch
   each call would make the epoch count meaningless and the per-epoch loss curve a lie.

WHAT IS AND IS NOT IMPLEMENTED, PLAINLY
=======================================
Implemented: the **TREE** family - ``xgboost``, ``lightgbm``, ``random_forest``,
``catboost`` - for both ``CLASSIFICATION`` and ``REGRESSION``. Four libraries, four
genuinely incremental APIs, all joblib-serialisable so ``model_versioning.serialize_model``
can store them and ``SafeModelLoader`` can read them back.

NOT implemented: the **SEQUENCE** family (``lstm``, ``gru``, ``transformer``) and
``AUTOENCODER``. Those need Keras, and a Keras model is not reliably joblib-serialisable,
which is the format the existing artifact store and loader are built around. Rather than
produce an artifact the platform's own loader cannot read, :func:`build_trainer` raises
``TrainerUnavailable`` naming the family - so such a job ends ``FAILED`` with a
classified reason and a message that says what is missing, which is exactly what it does
today and no worse. This is recorded as a known gap rather than papered over.

NOTHING HERE DECIDES A LIMIT
============================
No threshold, no cap, no patience and no epoch count is declared in this file.
``ml_training_policy`` owns all of them, ``strategy_service.prepare_training`` applies
them, ``training_worker.recheck_caps`` re-applies them before the first epoch and
``EarlyStoppingMonitor`` acts on them. This module fits, scores and exposes a model.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Tuple

import numpy as np

logger = logging.getLogger("TrainingRuntime")

__all__ = [
    "TrainedModelBundle",
    "TreeEpochTrainer",
    "build_trainer",
    "install_training_runtime",
    "training_runtime_installed",
    "SUPPORTED_BLOCK_IDS",
]

#: Blocks this runtime can fit. Derived from the adapters below rather than hand-listed,
#: so a block cannot appear here without an implementation behind it.
SUPPORTED_BLOCK_IDS: Tuple[str, ...] = (
    "xgboost",
    "lightgbm",
    "random_forest",
    "catboost",
)


# ══════════════════════════════════════════════════════════════════════════
#  THE ARTIFACT
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class TrainedModelBundle:
    """What gets persisted: the estimator plus everything needed to use it correctly.

    The estimator alone is not enough and storing it alone is how a model becomes
    unusable six months later. Three things travel with it:

    * ``feature_names`` in **order**, so the deployment-time
      ``FeatureValidator.check_model_compatibility`` has something real to compare the
      graph's current feature output against. A column order that silently changed
      produces a model that predicts confidently and wrongly.
    * ``classes`` - the ORIGINAL label values, in the order the estimator's columns are
      in. The dataset labels are ``down=0, flat=1, up=2``, but a train split need not
      contain all three, and every one of these libraries requires contiguous
      ``0..k-1`` targets. So labels are remapped for fitting and this is the inverse map.
      Without it a two-class model's "class 1" is indistinguishable from "flat".
    * ``task``, because the same estimator type means different things for
      classification and regression and a caller must not have to guess.

    Plain dataclass with no library types of its own, so ``joblib.dump`` handles it.
    """

    estimator: Any
    task: str
    feature_names: Tuple[str, ...] = ()
    #: Original label values, index-aligned to the estimator's class columns.
    #: Empty for regression.
    classes: Tuple[Any, ...] = ()
    block_id: str = ""
    epochs_fitted: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Provenance, for a reader that has the bundle but not this module."""
        return {
            "task": self.task,
            "block_id": self.block_id,
            "feature_names": list(self.feature_names),
            "classes": [
                int(value) if isinstance(value, (int, np.integer)) else value
                for value in self.classes
            ],
            "epochs_fitted": self.epochs_fitted,
        }


# ══════════════════════════════════════════════════════════════════════════
#  THE INCREMENTAL ADAPTERS
#
#  Each adapter adds ONE unit of work to a model that already exists. The unit is the
#  model spec's own (`ml_models.EpochUnit`): a boosting round for xgboost and lightgbm,
#  an iteration for catboost, an estimator for a random forest. The worker counts those
#  units, the caps bound them, and the cap message names them honestly - which is the
#  whole reason `EpochUnit` exists.
# ══════════════════════════════════════════════════════════════════════════


class _Adapter:
    """One library's incremental fit. Stateless; the state is the model it returns."""

    #: Set by subclasses; used only in messages.
    name = ""

    def fit_one(
        self,
        previous: Any,
        X: np.ndarray,
        y: np.ndarray,
        *,
        task: str,
        n_classes: int,
        seed: int,
        epoch: int,
    ) -> Any:
        raise NotImplementedError

    @staticmethod
    def predict_proba(model: Any, X: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    @staticmethod
    def predict(model: Any, X: np.ndarray) -> np.ndarray:
        raise NotImplementedError


class _XGBoostAdapter(_Adapter):
    name = "xgboost"

    def fit_one(self, previous, X, y, *, task, n_classes, seed, epoch):
        import xgboost as xgb

        params: Dict[str, Any] = {
            "max_depth": 4,
            "eta": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "seed": int(seed),
            # One thread per two cores' worth of work: this runs in a multi-tenant
            # worker and an unbounded thread pool is how one job starves the fleet.
            "nthread": 2,
            "verbosity": 0,
        }
        if task == "regression":
            params["objective"] = "reg:squarederror"
        else:
            params["objective"] = "multi:softprob"
            params["num_class"] = int(n_classes)
        matrix = xgb.DMatrix(X, label=y)
        # `xgb_model=previous` is what makes this ONE round rather than a refit: the
        # returned booster is the previous one plus a tree.
        return xgb.train(params, matrix, num_boost_round=1, xgb_model=previous)

    @staticmethod
    def predict_proba(model, X):
        import xgboost as xgb

        return np.asarray(model.predict(xgb.DMatrix(X)), dtype=float)

    @staticmethod
    def predict(model, X):
        import xgboost as xgb

        return np.asarray(model.predict(xgb.DMatrix(X)), dtype=float)


class _LightGBMAdapter(_Adapter):
    name = "lightgbm"

    def fit_one(self, previous, X, y, *, task, n_classes, seed, epoch):
        import lightgbm as lgb

        params: Dict[str, Any] = {
            "learning_rate": 0.05,
            "max_depth": 5,
            "num_leaves": 31,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "seed": int(seed),
            "num_threads": 2,
            "verbose": -1,
        }
        if task == "regression":
            params["objective"] = "regression"
        else:
            params["objective"] = "multiclass"
            params["num_class"] = int(n_classes)
        dataset = lgb.Dataset(X, label=y, free_raw_data=False)
        return lgb.train(params, dataset, num_boost_round=1, init_model=previous)

    @staticmethod
    def predict_proba(model, X):
        return np.asarray(model.predict(X), dtype=float)

    @staticmethod
    def predict(model, X):
        return np.asarray(model.predict(X), dtype=float)


class _RandomForestAdapter(_Adapter):
    name = "random_forest"

    def fit_one(self, previous, X, y, *, task, n_classes, seed, epoch):
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

        if previous is None:
            cls = RandomForestRegressor if task == "regression" else RandomForestClassifier
            model = cls(
                n_estimators=1,
                max_depth=6,
                min_samples_split=5,
                max_features="sqrt",
                random_state=int(seed),
                n_jobs=2,
                # The forest's own incremental mechanism: raising `n_estimators` and
                # refitting grows the existing ensemble instead of rebuilding it.
                warm_start=True,
            )
        else:
            model = previous
            model.set_params(n_estimators=int(model.n_estimators) + 1)
        model.fit(X, y)
        return model

    @staticmethod
    def predict_proba(model, X):
        return np.asarray(model.predict_proba(X), dtype=float)

    @staticmethod
    def predict(model, X):
        return np.asarray(model.predict(X), dtype=float)


class _CatBoostAdapter(_Adapter):
    name = "catboost"

    def fit_one(self, previous, X, y, *, task, n_classes, seed, epoch):
        from catboost import CatBoostClassifier, CatBoostRegressor

        cls = CatBoostRegressor if task == "regression" else CatBoostClassifier
        model = cls(
            iterations=1,
            learning_rate=0.05,
            depth=5,
            random_seed=int(seed),
            thread_count=2,
            verbose=False,
            allow_writing_files=False,
        )
        # `init_model` continues from the previous model rather than starting over, and
        # the returned model carries the whole ensemble.
        model.fit(X, y, init_model=previous)
        return model

    @staticmethod
    def predict_proba(model, X):
        return np.asarray(model.predict_proba(X), dtype=float)

    @staticmethod
    def predict(model, X):
        return np.asarray(model.predict(X), dtype=float).reshape(-1)


_ADAPTERS: Mapping[str, _Adapter] = {
    "xgboost": _XGBoostAdapter(),
    "lightgbm": _LightGBMAdapter(),
    "random_forest": _RandomForestAdapter(),
    "catboost": _CatBoostAdapter(),
}


# ══════════════════════════════════════════════════════════════════════════
#  THE TRAINER
# ══════════════════════════════════════════════════════════════════════════


def _as_probabilities(raw: np.ndarray, n_classes: int) -> np.ndarray:
    """Normalise a model's classification output to ``(rows, n_classes)``.

    The four libraries disagree about the shape of a binary prediction: some return one
    column of P(class 1), some return two. Normalising here means the metric code below
    has one shape to reason about, rather than four branches that each have to be right.
    """
    array = np.asarray(raw, dtype=float)
    if array.ndim == 1:
        array = array.reshape(-1, 1)
    if array.shape[1] == 1 and n_classes == 2:
        other = 1.0 - array[:, 0]
        array = np.column_stack([other, array[:, 0]])
    if array.shape[1] != n_classes:
        # Honest rather than reshaped: a mismatch here means the adapter and the label
        # remap disagree, which is a defect to surface and not to paper over.
        raise ValueError(
            f"the model produced {array.shape[1]} class column(s) for {n_classes} "
            f"classes; the prediction cannot be scored"
        )
    return array


def _log_loss(probabilities: np.ndarray, y_index: np.ndarray) -> float:
    """Multiclass cross-entropy, clipped so a confident wrong answer is finite.

    Hand-written rather than ``sklearn.metrics.log_loss`` for one reason: sklearn's
    version infers the label set from the data it is given, so a validation split
    missing a class raises or silently rescores against a different label set. Here the
    class columns are fixed by the bundle's remap and a split that happens to lack a
    class still scores against the same model columns.
    """
    rows = probabilities.shape[0]
    if rows == 0:
        return float("nan")
    picked = probabilities[np.arange(rows), y_index]
    return float(-np.mean(np.log(np.clip(picked, 1e-12, 1.0))))


def _f1_macro(y_true_index: np.ndarray, y_pred_index: np.ndarray, n_classes: int) -> float:
    """Macro-averaged F1 over the model's own class columns.

    Macro, not weighted, because the whole reason the sufficiency engine warns about
    class imbalance is that a weighted score hides a class the model never predicts.
    A class with no true instances AND no predictions contributes nothing rather than a
    zero, so an absent class does not silently drag the score down.
    """
    scores: List[float] = []
    for index in range(n_classes):
        true_positive = float(np.sum((y_pred_index == index) & (y_true_index == index)))
        predicted = float(np.sum(y_pred_index == index))
        actual = float(np.sum(y_true_index == index))
        if predicted == 0 and actual == 0:
            continue
        precision = true_positive / predicted if predicted else 0.0
        recall = true_positive / actual if actual else 0.0
        scores.append(
            0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
        )
    return float(np.mean(scores)) if scores else float("nan")


@dataclass
class TreeEpochTrainer:
    """One tree-family model, fitted one unit of work at a time.

    Satisfies ``training_worker.EpochTrainer``: ``train_epoch(n)``, ``evaluate(split)``
    and ``model``. Adds two things the worker uses when they are present -
    ``stateful``, so the worker does not try to fit an epoch in a child process and lose
    the model, and ``restore_best()``, so the artifact holds the best epoch's weights
    rather than the last epoch's.
    """

    block_id: str
    task: str
    seed: int
    adapter: _Adapter

    X_train: np.ndarray
    y_train: np.ndarray
    X_val: np.ndarray
    y_val: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray

    feature_names: Tuple[str, ...] = ()
    #: Original label values in class-column order. Empty for regression.
    classes: Tuple[Any, ...] = ()

    _estimator: Any = None
    _best_estimator: Any = None
    _best_epoch: int = 0
    _epochs_fitted: int = 0

    #: Read by the worker. The fitted state lives on this object, so an epoch run in a
    #: child process would be discarded. See ``training_worker.run_isolated``.
    stateful: bool = True

    # -- labels ----------------------------------------------------------
    @property
    def n_classes(self) -> int:
        return len(self.classes)

    def _index_of(self, labels: np.ndarray) -> np.ndarray:
        """Original labels -> contiguous class indices, using the bundle's own map."""
        lookup = {value: index for index, value in enumerate(self.classes)}
        return np.asarray([lookup.get(value, 0) for value in labels.tolist()], dtype=int)

    # -- the fit ---------------------------------------------------------
    def train_epoch(self, epoch: int) -> Mapping[str, Any]:
        """Add one unit of work and report this epoch's scalars.

        ``val_loss`` is computed on the EMBARGOED validation split, which is what makes
        the worker's early stopping mean anything. A trainer that reported a validation
        loss computed on a slice of its own training rows would produce a curve that
        falls forever and a stop decision that never fires.

        Returns scalars only - never a prediction vector and never a feature value.
        ``training_jobs.metrics_history`` is served over the API and pushed over
        realtime frames, and ``004d_training_and_models.sql``'s header makes the
        data-minimisation rule for that column explicit.
        """
        y_fit = self.y_train if self.task == "regression" else self._index_of(self.y_train)
        self._estimator = self.adapter.fit_one(
            self._estimator,
            self.X_train,
            y_fit,
            task=self.task,
            n_classes=max(1, self.n_classes),
            seed=self.seed,
            epoch=int(epoch),
        )
        self._epochs_fitted = int(epoch)

        metrics: Dict[str, Any] = {
            "loss": self._loss(self.X_train, self.y_train),
            "val_loss": self._loss(self.X_val, self.y_val),
        }
        if self.task != "regression":
            metrics["accuracy"] = self._accuracy(self.X_train, self.y_train)
            metrics["val_accuracy"] = self._accuracy(self.X_val, self.y_val)
        return metrics

    def _loss(self, X: np.ndarray, y: np.ndarray) -> float:
        if X.shape[0] == 0:
            return float("nan")
        if self.task == "regression":
            predicted = np.asarray(self.adapter.predict(self._estimator, X), dtype=float)
            return float(np.mean((predicted.reshape(-1) - np.asarray(y, dtype=float)) ** 2))
        probabilities = _as_probabilities(
            self.adapter.predict_proba(self._estimator, X), self.n_classes
        )
        return _log_loss(probabilities, self._index_of(y))

    def _accuracy(self, X: np.ndarray, y: np.ndarray) -> float:
        if X.shape[0] == 0:
            return float("nan")
        probabilities = _as_probabilities(
            self.adapter.predict_proba(self._estimator, X), self.n_classes
        )
        predicted = np.argmax(probabilities, axis=1)
        return float(np.mean(predicted == self._index_of(y)))

    # -- the quality controls the worker drives --------------------------
    def note_best(self, epoch: int) -> None:
        """Snapshot the estimator as the best seen. Called by :meth:`train_epoch`'s caller.

        Not called from ``train_epoch`` itself, because *which* epoch is best is the
        worker's decision - it holds the approved monitor, the mode and the min-delta -
        and a trainer that decided for itself would be a second early-stopping rule.
        """
        self._best_estimator = copy.deepcopy(self._estimator)
        self._best_epoch = int(epoch)

    def restore_best(self) -> None:
        """Put the best epoch's estimator back before the artifact is written."""
        if self._best_estimator is None:
            logger.info(
                "No better epoch than the last was recorded, so the last epoch's model "
                "is already the best one."
            )
            return
        self._estimator = self._best_estimator
        self._epochs_fitted = self._best_epoch
        logger.info("Restored the estimator fitted at epoch %d.", self._best_epoch)

    # -- scoring and the artifact ---------------------------------------
    def evaluate(self, split: str) -> Mapping[str, Any]:
        """Score one of ``"train"``, ``"val"``, ``"test"``. Scalars only."""
        X, y = {
            "train": (self.X_train, self.y_train),
            "val": (self.X_val, self.y_val),
            "test": (self.X_test, self.y_test),
        }.get(split, (np.empty((0, 0)), np.empty((0,))))

        if X.shape[0] == 0 or self._estimator is None:
            return {"rows": int(X.shape[0])}

        if self.task == "regression":
            predicted = np.asarray(
                self.adapter.predict(self._estimator, X), dtype=float
            ).reshape(-1)
            actual = np.asarray(y, dtype=float)
            residual = predicted - actual
            variance = float(np.var(actual))
            return {
                "rows": int(X.shape[0]),
                "mse": float(np.mean(residual**2)),
                "mae": float(np.mean(np.abs(residual))),
                # Guarded: a zero-variance target makes R² undefined, and the
                # sufficiency gate blocks that case - but a split can still be flat on
                # its own, and reporting an infinity would poison the metrics document.
                "r2": (
                    float(1.0 - float(np.mean(residual**2)) / variance)
                    if variance > 0
                    else float("nan")
                ),
            }

        probabilities = _as_probabilities(
            self.adapter.predict_proba(self._estimator, X), self.n_classes
        )
        true_index = self._index_of(y)
        predicted_index = np.argmax(probabilities, axis=1)
        return {
            "rows": int(X.shape[0]),
            "loss": _log_loss(probabilities, true_index),
            "accuracy": float(np.mean(predicted_index == true_index)),
            "f1_macro": _f1_macro(true_index, predicted_index, self.n_classes),
        }

    @property
    def model(self) -> Any:
        """The bundle the binder persists. ``None`` before the first epoch."""
        if self._estimator is None:
            return None
        return TrainedModelBundle(
            estimator=self._estimator,
            task=self.task,
            feature_names=tuple(self.feature_names),
            classes=tuple(self.classes),
            block_id=self.block_id,
            epochs_fitted=self._epochs_fitted,
        )


# ══════════════════════════════════════════════════════════════════════════
#  CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════


def _split_arrays(context: Any) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """``{"train": (X, y), "val": ..., "test": ...}`` from the EMBARGOED ranges.

    ``SplitRange.take`` returns a contiguous slice in chronological order and offers no
    way to gather by index list, so there is no shuffled variant of this to get wrong.
    """
    dataset = context.dataset
    splits = context.splits
    X = np.asarray(dataset.X)
    y = np.asarray(dataset.y)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    return {
        "train": (splits.train.take(X), splits.train.take(y)),
        "val": (splits.val.take(X), splits.val.take(y)),
        "test": (splits.test.take(X), splits.test.take(y)),
    }


def build_trainer(context: Any) -> TreeEpochTrainer:
    """The ``TrainingBackend.trainer`` factory: one trainer for one approved job.

    Raises
        ``training_worker.TrainerUnavailable`` when this runtime cannot fit the job's
        block. That is a classified failure reason the worker already understands, so
        the job ends ``FAILED`` / ``TRAINER_UNAVAILABLE`` with a message naming what is
        missing - rather than producing an artifact the platform's loader cannot read,
        or a model fitted for a task the graph did not ask for.
    """
    from backend_app.backend.training_worker import TrainerUnavailable

    block_id = str(context.block_id or "")
    adapter = _ADAPTERS.get(block_id)
    if adapter is None:
        raise TrainerUnavailable(
            f"The governed training runtime has no trainer for {block_id!r}. It "
            f"implements the tree family ({', '.join(SUPPORTED_BLOCK_IDS)}); the "
            f"sequence and autoencoder families need a Keras artifact format the model "
            f"store does not yet handle, so no artifact is produced rather than one "
            f"that cannot be loaded back."
        )

    config = dict(context.config or {})
    # The APPROVED task, read from the row the policy engine wrote. Never re-derived
    # from a label mode, which a later edit could change.
    task = str(config.get("task") or "").lower()
    if task not in ("classification", "regression"):
        # An older row carries no task. Fall back to the label mode it DOES carry, and
        # say so - guessing silently is how a classifier gets fitted on a continuous
        # target.
        task = str(config.get("label_mode") or "classification").lower()
        logger.info(
            "Job %s records no approved task; using its label mode %r.",
            context.job_id,
            task,
        )
    if task not in ("classification", "regression"):
        raise TrainerUnavailable(
            f"{task!r} is not a task this runtime can fit; it fits classification and "
            f"regression."
        )

    arrays = _split_arrays(context)
    X_train, y_train = arrays["train"]

    classes: Tuple[Any, ...] = ()
    if task == "classification":
        # The class columns are fixed by the TRAIN split, because those are the classes
        # the model can actually learn. A class present only in validation or test is
        # not a column the estimator has, and pretending otherwise would make every
        # score for it meaningless. The sufficiency gate blocks the case that matters -
        # a class absent from train - before a job ever gets here.
        present = np.unique(np.asarray(y_train))
        classes = tuple(
            int(value) if isinstance(value, (int, np.integer)) else value
            for value in present.tolist()
        )
        if len(classes) < 2:
            raise TrainerUnavailable(
                f"The training split carries {len(classes)} class(es), so there is "
                f"nothing to discriminate. The data-sufficiency gate normally refuses "
                f"this before a job is queued."
            )

    trainer = TreeEpochTrainer(
        block_id=block_id,
        task=task,
        seed=int(context.seed),
        adapter=adapter,
        X_train=np.ascontiguousarray(X_train, dtype=float),
        y_train=np.asarray(y_train),
        X_val=np.ascontiguousarray(arrays["val"][0], dtype=float),
        y_val=np.asarray(arrays["val"][1]),
        X_test=np.ascontiguousarray(arrays["test"][0], dtype=float),
        y_test=np.asarray(arrays["test"][1]),
        feature_names=tuple(str(name) for name in (context.dataset.columns or ())),
        classes=classes,
    )
    logger.info(
        "Trainer built for job %s: %s / %s, train=%d val=%d test=%d, %d feature "
        "column(s)%s.",
        context.job_id,
        block_id,
        task,
        trainer.X_train.shape[0],
        trainer.X_val.shape[0],
        trainer.X_test.shape[0],
        trainer.X_train.shape[1] if trainer.X_train.ndim > 1 else 1,
        f", {len(classes)} class(es)" if classes else "",
    )
    return trainer


def install_training_runtime() -> bool:
    """Install this runtime as the worker's ``TrainingBackend``. Idempotent.

    Called by the worker process at start-up. Deliberately NOT called at import time:
    installing a global backend as a side effect of importing a module would make the
    behaviour of any process that happens to import it depend on import order, and the
    worker's own tests replace the backend per test.

    The binder is ``model_versioning.bind_trained_model``, reached through that module's
    own ``training_backend`` helper - so the artifact, the ``model_versions`` row, the
    binding and the version promotion all stay where they already are. This module adds
    the fit and nothing else.
    """
    try:
        from backend_app.backend import model_versioning as MV
        from backend_app.backend.training_worker import (
            current_training_backend,
            register_training_backend,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("The training runtime could not be installed: %s", exc)
        return False

    existing = current_training_backend()
    if existing is not None and getattr(existing, "name", "") == "governed-tree-runtime":
        return True
    register_training_backend(
        MV.training_backend(build_trainer, name="governed-tree-runtime")
    )
    logger.info(
        "Governed training runtime installed for %s. Epochs, batch size, task, seed and "
        "the early-stopping terms all come from the approved training_jobs.config; "
        "validation uses the embargoed temporal split.",
        ", ".join(SUPPORTED_BLOCK_IDS),
    )
    return True


def training_runtime_installed() -> bool:
    """Whether THIS runtime is the installed backend."""
    try:
        from backend_app.backend.training_worker import current_training_backend
    except Exception:  # noqa: BLE001
        return False
    backend = current_training_backend()
    return backend is not None and getattr(backend, "name", "") == "governed-tree-runtime"
