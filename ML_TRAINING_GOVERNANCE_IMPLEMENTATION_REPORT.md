# ML/DL Training Governance — Implementation Report

Phase 34 deliverable. Every number in the "Test results" section was produced by a command that
actually ran in this workspace; where something could not be executed here, it is listed as not
executed rather than assumed.

---

## 1. Systems discovered

The investigation phase found that this repository **already had an authoritative training
governance layer**, and also a complete route around it. That shaped the whole implementation: the
work was to extend the existing layer and close the bypass, not to build a second one.

### What already existed and was reused

| Component | Role it already performed |
|---|---|
| `backend_app/backend/ml_training_policy.py` | The minimum-data gate (`check_ml_data_requirements`) and the backend-enforced per-tier caps (`resolve_caps`, `enforce_caps`, `ML_TIER_CAPS`) |
| `backend_app/backend/strategy_service.py` | `prepare_training` — the admission path; `insert_training_job` — the queue row |
| `backend_app/backend/training_worker.py` | The epoch loop, `recheck_caps`, `MemoryMonitor` integration, failure classification |
| `backend_app/backend/ml_dataset.py` | Dataset assembly, chronological splits, the embargo |
| `backend_app/core/subscription_engine.py`, `subscription_dependencies.py` | `require_ml_training`, `check_ml_quota`, `check_optimization_quota`, `check_ml_model_slot` |
| `backend_app/backend/ml_safety.py`, `market_data_validation.py`, `feature_validator.py` | Runtime memory bound, window quality grading, feature schema checks |
| `backend_app/migrations/004d_training_and_models.sql` | `training_jobs`, including the `config` column enforcement now rides in |

### The two training architectures that coexisted

1. **Governed.** `POST /api/strategy-operations/training/jobs` → `prepare_training` → a
   `training_jobs` row → claimed by a worker via a conditional UPDATE. Caps enforced at admission
   and re-checked at claim time.
2. **Ungoverned.** `POST /api/strategies/{strategy_id}/train` ran training inside FastAPI
   `BackgroundTasks` with a hardcoded `limit=10000`, called
   `XGBoostStrategyBlock.train_custom_strategy` directly, and **created no job row** — so no cap,
   no quota, no data gate, no queue, no record. A third surface,
   `connection_layer/routers/strategies.py` `POST /train-ml`, did the same on a separately
   mountable app.

Also found: `ml_models.py:753` contained `model.fit(..., epochs=10, batch_size=64,
validation_split=0.1, ...)` — epoch count, batch size and validation fraction all hardcoded,
reachable without passing any policy; and no worker process was deployed anywhere in
`docker-compose.yml`, so the governed path's queue had no consumer.

### Gaps that drove the work

No model/task awareness in the gate (one row-count rule for every family); no adaptive epoch
budget (caps were static ceilings); no early stopping; no dataset *quality* measurement (only
shape); no record of refusals (a refused request creates no row, so refusals were invisible); no
search/HPO budget; no artifact storage accounting; and the bypass above.

---

## 2. Files changed

18 files: 15 modified, 3 added. `git diff --stat` over the 14 *tracked* modified files reports
**5,696 insertions, 250 deletions**; the 15th (`connection_layer/routers/strategies.py`) is not
tracked by git in this repository. The three new files add 2,438 lines (627 + 673 + 1,138).

The figures above exclude a concurrent billing/entitlements workstream that was editing this tree
during the work (`backend_app/core/subscription_dependencies.py`, `backend_app/routers/billing.py`,
`algo22-terminal/src/design/errorCopy.js`) — those files are not part of this change.

### Backend — governance core

| File | Change |
|---|---|
| `backend_app/backend/ml_training_policy.py` | `POLICY_VERSION` 1.0.0 → 2.0.0. Task vocabulary (`resolve_task`, `SUPERVISED_TASKS`). Finding codes 5 → 30 (10 new blocking, 15 new warning). `SufficiencyThresholds` (+`from_env`), `MeasurementsView`, `GateOutcome`, `SplitPlan.compute`, `rows_for_feasible_split`. `EarlyStoppingPolicy`, `TrainingBudget`, `resolve_budget`, `estimate_seconds_per_epoch`, `FAMILY_COST_FACTOR`. `TierSearchCaps`, `ML_TIER_SEARCH_CAPS`, `SearchBudget`, `resolve_search_budget`, `enforce_search_budget`. `MLTrainingCaps.recommended_epochs`. |
| `backend_app/backend/ml_dataset.py` | New measurement layer: `measure_dataset`, `DatasetMeasurements`, `SplitMeasurements`, `MeasurementThresholds`. Never raises — a measurement failure degrades to "not measured". |
| `backend_app/backend/ml_models.py` | `ModelTask`, `FAMILY_SUPPORTED_TASKS`, `ModelSpec.supported_tasks/default_task/supports_task`. `assert_governed_training` + a `ContextVar` + the `ML_ALLOW_UNGOVERNED_TRAINING` escape, wired into all **8** concrete `train_custom_strategy` methods. |
| `backend_app/backend/strategy_service.py` | `try_split_training_dataset` (carries a block instead of raising, so the gate can report it). `prepare_training` reordered so splits are known before the gate runs. `build_training_config(budget=, verdict=)`. `insert_training_job(governance=)`. `record_governance_decision` + `training_governance_decisions`. Graceful degradation when the 019 columns are absent. |
| `backend_app/backend/training_worker.py` | `EarlyStoppingMonitor`, best-weight snapshot/restore, `STOP_REASON_*`, `run_isolated(stateful=)`, `_actuals_row`, runtime installation in `_main`. |
| `backend_app/backend/training_runtime.py` **(new, 627 lines)** | Real incremental epoch trainers for the tree family: `_XGBoostAdapter`, `_LightGBMAdapter`, `_RandomForestAdapter`, `_CatBoostAdapter`, `TreeEpochTrainer`, `build_trainer`, `install_training_runtime`. |
| `backend_app/backend/model_versioning.py` | `effective_artifact_ceiling`, `user_artifact_bytes`, `artifact_storage_allowance_bytes`, `assert_artifact_storage_available`, `prune_superseded_artifacts`, `store_artifact(max_bytes=)`. |
| `backend_app/backend/metrics.py` | 4 new series and their recorders, in their own `TRAINING_GOVERNANCE_METRIC_ATTRIBUTES` tuple folded into `get_prometheus_metrics`. |

### Backend — routes and schema

| File | Change |
|---|---|
| `backend_app/routers/strategies.py` | `train_ml_strategy` rewritten from an in-process trainer into a **governed adapter**: it admits a job and returns its id. Server-resolved fields are refused with `TRAINING_FIELD_SERVER_RESOLVED`, not ignored. `@limiter.limit("20/minute")`. |
| `backend_app/routers/strategy_operations.py` | `run_optimization` gained a resolved search budget and reports it as `search_budget`. |
| `connection_layer/routers/strategies.py` | `POST /train-ml` → **410** `TRAINING_ROUTE_RETIRED`, naming the governed endpoint. |
| `backend_app/migrations/019_training_governance.sql` **(new, 673 lines)** | 20 nullable columns on `training_jobs`, 5 guarded CHECKs, 2 partial indexes; new append-only `training_governance_decisions` table with owner-scoped RLS (SELECT + INSERT only, no UPDATE/DELETE policy), grants to `authenticated`/`service_role`, `REVOKE ALL` from `anon`. |
| `docker-compose.yml` | New `training-worker` service (2 replicas, 4G/2cpu), shared `model_artifacts` volume, governance env wired into both backend and worker. |

### Frontend and tests

`algo22-terminal/src/lib/graphValidation.js` — appended `deriveTrainingReadiness`.
`tests/test_ml_training_governance.py` **(new, 1,138 lines)**, plus updates to
`tests/test_ml_deployment_guards.py`, `tests/test_ml_training_policy.py`,
`tests/test_sb06_exchange_agnostic_save.py` (see §8 for why two pre-existing tests were rewritten).

---

## 3. Governance architecture

**One layer, one decision point.** `ml_training_policy` decides; everything else asks it.

```
request (any surface)
   │
   ├─ entitlement:  require_ml_training / check_ml_quota / check_ml_model_slot   (existing)
   │
   ├─ dataset:      ml_dataset.assemble → try_split_training_dataset
   │                ml_dataset.measure_dataset  ──► DatasetMeasurements
   │
   ├─ GATE:         ml_training_policy.check_ml_data_requirements
   │                   (task-aware, measurement-aware)  ──► GateVerdict
   │                   outcome ∈ {VALID, WARNING, BLOCKED}
   │
   ├─ BUDGET:       ml_training_policy.resolve_budget
   │                   caps ∩ model ∩ dataset ∩ duration, floored  ──► TrainingBudget
   │                                                                   + EarlyStoppingPolicy
   │
   ├─ RECORD:       training_jobs row (config carries the budget and the stopping terms)
   │                training_governance_decisions row (ADMITTED | DEFERRED | REFUSED)
   │
   └─ worker:       claim → recheck_caps → epoch loop honouring config["early_stopping"]
```

Three properties were treated as non-negotiable:

**No bypass.** The legacy route is now an adapter that admits a job; the `connection_layer` route
answers 410; and the trainer itself is guarded. `ml_models.assert_governed_training(block_id)` sits
at all 8 concrete `train_custom_strategy` methods, so a *new* caller that forgets to go through
admission fails loudly instead of training silently. The guard reads a `ContextVar` set by the
governed path, and an `ML_ALLOW_UNGOVERNED_TRAINING` environment escape exists for offline work
only — it cannot be set by a request. The guard is on the trainer rather than a parameter on it,
because a parameter is supplied by the caller and so proves nothing about the caller.

**Refusal, not silent correction.** A request for 5,000 epochs on a plan permitting 100 is refused
naming requested, recommended and approved — it is not clamped. Server-resolved fields
(`symbol`, `timeframe`, `exchange_id`, `data_source`, `indicators`, …) are refused with
`TRAINING_FIELD_SERVER_RESOLVED` rather than dropped, because dropping them would train on a
different market than the caller asked for and report success.

**Refusals are recorded.** A refused request creates no `training_jobs` row, so refusals used to be
invisible. `training_governance_decisions` is a separate append-only table for exactly that reason.

---

## 4. Data sufficiency

**No universal minimum.** The requirement is computed per model family and per task.
`FAMILY_SUPPORTED_TASKS` stamps `supported_tasks`/`default_task` onto every `ModelSpec`, and the
gate branches on the resolved task. A classifier is judged on class structure; a regressor on target
variance and outliers; a sequence model on window length and horizon; all of them on whether a
chronologically valid split with the embargo is even feasible at that row count.

Measuring and deciding are deliberately separated: `ml_dataset.measure_dataset` (which may use
numpy) produces a `DatasetMeasurements`, and `ml_training_policy` reads it through a plain
`MeasurementsView`. That keeps the policy module import-light, which
`tests/test_strategy_dag_architecture.py` enforces in a fresh interpreter.

**Three outcomes, not two.** `GateOutcome` is `VALID`, `WARNING` or `BLOCKED`, and `warnings` is a
separate tuple from `issues` — every existing caller treats a non-empty `issues` as a refusal, so
folding advisories into it would have turned advice into rejection.

10 new blocking conditions: unsupported task, single-class target, a class absent from train, a
single-class validation split, a class too rare to learn, no target variance, unusable labels, no
feature variance, an infeasible split, a non-chronological index.

15 new advisory conditions: class imbalance, a small minority class, a class absent from
validation, limited samples, high dimensionality, constant and near-constant feature columns,
duplicate feature rows, unlabelled rows, target outliers, low target cardinality, irregular
intervals, duplicate timestamps, unknown task support, and sufficiency not measured.

Every finding is a structured contract entry — a code a client can branch on, the field, the
measured quantity and a fix hint — and the ordering is deterministic, so two processes evaluating
one dataset produce one verdict in one order. 11 `ML_SUFFICIENCY_*` environment variables tune the
advisory thresholds, each clamped to a sane range on read.

When a split is infeasible the gate reports `SPLIT_INFEASIBLE` **with the row count that would make
it feasible**, which required computing splits before the gate rather than after. The existing
`split_training_dataset` is untouched and still authoritative; `try_split_training_dataset` carries
its refusal instead of raising it.

---

## 5. Resource governance

**The budget is an intersection, with a floor.** `resolve_budget` intersects the tier cap, the
model's own safe maximum, what the dataset can support, and a wall-clock estimate
(`estimate_seconds_per_epoch` × `FAMILY_COST_FACTOR`, with 80% planning headroom). The result is
then floored at `min_meaningful_epochs = min(spec.recommended_epochs, caps.max_epochs)`. A
pessimistic duration estimate can lower the ceiling but **never below that floor** — it warns
instead. This is the rule that stops the system from quietly handing someone a 1-epoch run and
calling it training.

**Saturation defers; it never degrades.** Platform load is recorded on the decision but is not an
input to `max_epochs`. When the shared pool is full a job waits (`DEFERRED`). Letting load reduce
epochs would make model quality depend on what time of day the user clicked, which is not a
trade-off a user can see or consent to.

**Search/HPO has its own table.** `ML_TIER_SEARCH_CAPS` is deliberately *not* derived from
`ML_TIER_CAPS`, because the plans genuinely differ: BASIC grants 0 ML trainings but 25
optimizations a month.

| Plan | max trials | concurrent | total seconds |
|---|---|---|---|
| FREE | 0 | 0 | 0 |
| BASIC | 50 | 1 | 1,800 |
| PROFESSIONAL | 200 | 2 | 3,600 |
| ENTERPRISE | 500 | 4 | 10,800 |

The same floor principle applies: `MIN_MEANINGFUL_SEARCH_TRIALS` (10) protects an entitled account
from an estimate so pessimistic it reduces a paid feature to a single backtest.

**Artifact storage is derived, not a third table.** The allowance is the user's `ml_models` quota ×
`max_model_size_mb`. `assert_artifact_storage_available` runs before a write.
`DEFAULT_MAX_CHECKPOINTS` is 1, because writing several checkpoints per run would consume a user's
storage quota for rows nothing can resolve.

---

## 6. Training quality

**Early stopping terms are decided at admission and recorded.** `EarlyStoppingPolicy.for_run`
picks the monitored metric, patience, min-delta and divergence factor; they are written into
`training_jobs.config["early_stopping"]`; the worker reads them. A worker that re-derived them could
stop a run on different terms than the author was told.

**A missing monitored metric disables early stopping rather than silently substituting training
loss.** Training loss cannot detect overfitting, so falling back to it would keep the feature's name
while removing the thing it exists to do.

**Best weights are restored.** The worker snapshots the best epoch and restores it when it stops, so
the artifact is the best model rather than the last one. `stopped_reason`, `epochs_completed`,
`best_epoch` and `best_metric` are recorded on the job.

**Real incremental trainers.** `training_runtime.py` provides genuine per-epoch incremental fitting
for `xgboost`, `lightgbm`, `random_forest` and `catboost`, both classification and regression, with
probability handling, log-loss and macro-F1. `run_isolated(fn, stateful=True)` runs stateful
trainers in-process, because a `spawn` child would fit a copy and silently discard the incremental
state.

`training_epochs_used_ratio` records how much of the approved budget was actually spent, which is
the figure that tells you whether the budget policy is well calibrated rather than merely safe.

---

## 7. Security

- **Tenancy.** `training_governance_decisions` carries owner-scoped RLS with **SELECT and INSERT
  policies only** — no UPDATE or DELETE policy and no such grant, so the decision log is
  append-only by construction. `REVOKE ALL FROM anon`.
- **No credential or venue in the training request.** The rewritten route names no exchange and
  holds no credential; venue and data source are resolved server-side. The pre-existing test that
  asserted one resolved value fed both the vault and the connection was rewritten to assert the
  opposite, because the route no longer touches either (`get_vault` and `increment_usage` imports
  were removed).
- **Rate limiting.** `@limiter.limit("20/minute")` on the training route.
- **Enforcement cannot be bypassed by schema drift.** The governance *enforcement* data lives in
  `training_jobs.config`, which migration 004d already created. The 019 columns are reporting
  columns: if they are absent, `is_missing_governance_column_error` drops them with a warning naming
  `019_training_governance.sql` and the job still runs governed.
- **The environment escape is environment-only.** `ML_ALLOW_UNGOVERNED_TRAINING` is read from the
  process environment at call time, so no request body or header can set it.
- **Metric labels.** The 4 new series label on code, reason and `block_id` — no user id, no
  credential. `tests/test_task_9_1_builder_metrics.py` asserts the forbidden-label rule for the
  builder tuple; the new series carry no identity labels either.
- **CI lint gate.** `flake8 backend_app --count --select=E9,F63,F7,F82` → **0**.

---

## 8. Test results

All figures below were produced by commands executed in this workspace.

### The governance work itself

`tests/test_ml_training_governance.py` is new: **101 tests** (96 behavioural + 5 pinning the new
metric tuple). Run together with the 14 existing training/ML suites it touches:

```
tests/test_ml_training_governance.py  tests/test_ml_training_policy.py
tests/test_ml_dataset.py              tests/test_ml_deployment_guards.py
tests/test_ml_strategy_id_linking.py  tests/test_sb06_exchange_agnostic_save.py
tests/test_strategy_dag_architecture.py  tests/test_training_service_admission.py
tests/test_training_worker.py         tests/test_training_status.py
tests/test_model_versioning.py        tests/test_training_endpoints.py
tests/test_training_realtime_channels.py
tests/test_training_and_model_tables_migration.py
tests/test_task_9_1_builder_metrics.py

→ 1005 passed, 2 skipped, 0 failed  (788 s)
```

Frontend, for the `graphValidation.js` change:
`graphValidation.test.js`, `strategyBuilder.trainingBlocks.test.jsx`,
`builder.architecture.test.js` → **3 files, 90 tests passed**. eslint on the changed file: exit 0.

### Whole suite

A single monolithic `pytest tests` run died mid-file twice in this environment without printing a
summary, so the suite was run in **9 chunks** covering every test directory and all 312 top-level
test files (`tests/perf` is excluded by `pytest.ini`):

| Chunk | Scope | Result | Time |
|---|---|---|---|
| F0 | 10 test subdirectories | 4 failed, 717 passed, 1 xfailed, 3 xpassed | 20:20 |
| F1 | files 1–39 | 6 failed, 884 passed | 13:16 |
| F2 | files 40–78 | 730 passed | 2:59 |
| F3 | files 79–117 | 1,238 passed, 13 skipped | 3:47 |
| F4 | files 118–156 | 1,877 passed, 4 skipped | 32:55 |
| 0 | files 157–195 | 1 failed, 1,252 passed | 2:22 |
| 1 | files 196–234 | 784 passed, 2 skipped | 3:59 |
| 2 | files 235–273 | 2 failed, 1,887 passed, 4 xfailed | 8:28 |
| 3 | files 274–312 | 8 failed, 2,306 passed, 11 skipped | 4:51 |
| **Total** | | **21 failed, 11,675 passed, 30 skipped, 5 xfailed, 3 xpassed** | |

For reference, this repository's own recorded baseline (`production-launch-hardening` task 13.13) is
*85 failed, 11,334 passed* for a single monolithic run. Chunking removes most cross-file pollution,
which is why the failure count is lower.

### Attribution of all 21 failures

**2 were caused by this work. Both are fixed.**

`tests/test_task_9_1_builder_metrics.py::TestTheMetricVocabularyIsTheSpecs` —
`test_there_is_no_nineteenth_and_no_seventeenth` and
`test_the_attribute_list_and_the_metric_objects_agree`. I had appended the 4 new governance metrics
to `MetricsCollector.STRATEGY_BUILDER_METRIC_ATTRIBUTES`, which is asserted element-for-element
against Requirements 24.1–24.3. That file's own comment warns against this and establishes the
pattern — `PAPER_FEED_METRIC_ATTRIBUTES` and `MARKETPLACE_PAPER_METRIC_ATTRIBUTES` are separate
tuples for the same reason. Fixed by giving the governance series a fourth tuple,
`TRAINING_GOVERNANCE_METRIC_ATTRIBUTES`, folded into `get_prometheus_metrics`.

Proven mine by differential measurement, not inference: the same two tests pass at `HEAD` and failed
in my tree (`1 failed, 163 passed` vs `3 failed, 161 passed`). After the fix,
`tests/test_task_9_1_builder_metrics.py` → **160 passed**, and the four series were confirmed
present in the scrape with a recorded sample of each
(`training_epochs_used_ratio_sum{block_id="xgboost"} 0.3` for 12 of 40 approved epochs). 5 tests
were added to pin this so it cannot regress silently.

**19 are pre-existing.** Each was attributed by running it at `HEAD` in a clean `git worktree`, or
against the repository's recorded inventory — not assumed:

| Failures | Tests | Evidence |
|---|---|---|
| 3 | `test_transaction_isolation_serializable` | Recorded environment gap: needs a real PostgreSQL. Inventory records exactly 3 |
| 4 | `test_atomic_order_cancellation_fix` | Recorded environment gap: needs a real PostgreSQL. Inventory records exactly 4 |
| 1 | `test_position_delta_race_condition_fix::test_position_update_row_level_locking` | Fails identically at `HEAD` |
| 2 | `test_strategy_lifecycle_concurrency` | Recorded as the deliberate P0 proofs of task 12.6 |
| 2 | `test_tenant_isolation_matrix` | Fails at `HEAD` (3 in isolation, 3 in the 9-directory run). Library checkout/clone tier gating |
| 2 | `test_tenant_isolation_library_paper` | The same library checkout/clone cells |
| 1 | `test_validation_sweep[library.clone_strategy]` | `git diff HEAD -- backend_app/routers/library.py` is empty; untouched by this work |
| 2 | `test_billing_e2e::TestBillingJsxUI` | Identical failure set at `HEAD` (`3 failed, 87 passed` both trees) |
| 2 | `test_paper_persistence_roundtrip` | Passes alone in both trees (6 passed); reproduces at `HEAD` in the same 9-directory run |

Two further checks worth recording:

- **`test_baseline_unchanged` showed 10 failures in the monolithic run and 0 in the chunked run.**
  Run alone it is **60 passed in my tree and 60 passed at `HEAD`** — identical. So my changes to two
  router surfaces violate no pinned API capture, and those 10 were order-dependent pollution, as the
  repository's inventory already recorded.
- **Two pre-existing tests were rewritten rather than made to pass**, because they pinned the bypass
  that was the point of this work: `test_ml_training_auto_persists_model_path` →
  `test_ml_training_route_admits_rather_than_trains`, and
  `test_one_resolved_value_feeds_both_the_vault_and_the_connection` →
  `test_the_training_route_names_no_venue_and_holds_no_credential`. Two of my own new tests were
  also corrected when they turned out to encode wrong premises (4,000 rows × 60 columns is genuinely
  `VALID`, and `rows_for_feasible_split` must be compared against the same `minimum_per_split`).

### Not executed here

`backend_app/migrations/019_training_governance.sql` was **not applied to a live PostgreSQL** in
this environment — there is no local Postgres available, and migration 004d is itself unapplied
here. Its syntax and idempotency follow 004d's house style and the SQL guard scans pass, but the
statement that it applies cleanly to production is unverified.

---

## 9. Remaining risks

1. **Sequence and autoencoder families have no governed trainer.** `lstm`, `gru`, `transformer` and
   `autoencoder` raise `TrainerUnavailable`. Keras models are not reliably joblib-serialisable and
   `model_versioning.serialize_model` uses `joblib.dump`, so producing an artifact the platform's
   own loader cannot read would be worse than refusing. This is the same practical outcome as before
   the change, with a clearer message. `can_train` was deliberately **not** flipped to `False`,
   which would remove those blocks from the palette.
2. **`019_training_governance.sql` is hand-applied.** The repository applies numbered SQL manually
   via `scripts/apply_migrations.py`; alembic is vestigial here. If 019 is not applied, governance
   still enforces (enforcement rides in `config`, created by 004d) but the reporting columns and the
   decision log are absent, and the code degrades with a warning naming the file.
3. **`assert_artifact_storage_available` fails open.** If the quota read itself fails, the write
   proceeds with a warning. Every epoch has already run at that point; discarding a finished model
   because of a failed quota read destroys real work. The consequence is that a storage overage is
   possible during a database incident.
4. **`prune_superseded_artifacts` defaults to `dry_run=True` and is wired to nothing.** No scheduler
   calls it. Enabling it would make downloads of superseded model versions 404, so it is deliberately
   inert until someone decides that trade-off.
5. **`ML_TRAINING_MAX_CONCURRENT_GLOBAL` must be kept in step with the worker `replicas` count by
   hand.** Nothing derives one from the other; compose sets 2 and 2, and the policy default is 8.
6. **The epoch-duration estimate is a constant, not learned.**
   `ML_TRAINING_EPOCH_CELLS_PER_SECOND` (4e6) and `FAMILY_COST_FACTOR` are deployment-independent
   guesses. They can only lower the ceiling, never below the meaningful floor, so a bad estimate
   degrades into warnings rather than bad runs — but `training_epochs_used_ratio` should be watched
   and the constant corrected from it.
7. **The pre-existing `library.clone_strategy` 500** (`'NoneType' object has no attribute 'table'`
   at `backend_app/routers/library.py:2247`) and the library checkout/clone tenant-isolation cells
   remain open. They are not in this work's scope but they are real.
8. **A parallel workstream was editing this tree during verification** (billing/entitlements:
   `subscription_dependencies.py`, `billing.py`, `errorCopy.js`, and a new
   `tests/test_billing_entitlements_endpoint.py`). Their `PlanVerificationUnavailable` change was
   checked and is compatible — it subclasses `RuntimeError` and my quota read catches `Exception`.
   But billing/marketplace failure counts in §8 measure a moving tree and should be re-measured once
   their work lands.

---

## 10. Deployment requirements

**Schema.** Apply `backend_app/migrations/019_training_governance.sql` (via
`scripts/apply_migrations.py`, the path this repository actually uses). Without it the service still
enforces governance but records none of it.

**New process.** Deploy the `training-worker` service from `docker-compose.yml`
(`python -m backend_app.backend.training_worker`). **This is required for training to happen at
all** on the governed path — before this change the queue had no consumer. It needs
`SUPABASE_SERVICE_ROLE_KEY` and the `model_artifacts` volume shared with the backend.

**Required environment.**

| Variable | Default | Note |
|---|---|---|
| `ML_TRAINING_MAX_CONCURRENT_GLOBAL` | 8 (compose sets 2) | Keep equal to worker replicas × per-worker concurrency |
| `STRATEGY_MODEL_ARTIFACT_DIR` | — | Must be the shared volume on both backend and worker |
| `SUPABASE_SERVICE_ROLE_KEY` | — | Worker only |

**Optional tuning.** 11 × `ML_SUFFICIENCY_*` (advisory thresholds),
`ML_TRAINING_EPOCH_CELLS_PER_SECOND`, `ML_TRAINING_MAX_CHECKPOINTS`,
`ML_SEARCH_SECONDS_PER_TRIAL`, `ML_SEARCH_MIN_TRIALS`. Each is clamped on read.

**Must stay unset in production.** `ML_ALLOW_UNGOVERNED_TRAINING` — offline use only.

**New Prometheus series** on the existing `/metrics` endpoint:
`training_dataset_warnings{code}`, `training_dataset_blocks{code}`,
`training_jobs_early_stopped{reason}`, `training_epochs_used_ratio{block_id}`.
Suggested first alerts: a rising `training_dataset_blocks{code="SPLIT_INFEASIBLE"}` means users are
being refused for a window the platform could widen; a `training_epochs_used_ratio` concentrated at
1.0 means budgets are binding and the ceiling, not convergence, is ending runs.

**Client-visible contract changes.** `POST /api/strategies/{strategy_id}/train` no longer trains
in-process — it returns a job id, and refuses server-resolved fields with 422
`TRAINING_FIELD_SERVER_RESOLVED`. `connection_layer` `POST /train-ml` now answers **410**
`TRAINING_ROUTE_RETIRED`. `POST /api/strategy-operations/optimization` gained a `search_budget` in
its response.
