# PHASE 38 — Continuous Implementation and Release Recovery

**Release posture, unchanged and not weakened by anything in this phase:**

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

All work products are untracked probes under `staging/` and are **PROTOTYPE ONLY**.

---

## A. Executive outcome

**What changed in the repository: nothing.** 0 governed files created, modified, renamed or deleted. No git state-mutating command ran. `git write-tree` is byte-identical to Phases 17–35 at `4b45a7633f76cd8ab5be023c1e45af7f1a63c398`.

**Why no fix was implemented:** not "no grant found", which is what earlier phases reported. There is exactly **one** live grant, and it contains an explicit exclusion clause that names everything Phase 38 targets. §B.2.

**What this phase added that prior phases did not have:**

1. **The deploy-script defect is now reproduced by execution**, not inferred from source. `scripts/deploy.sh` exits **0** and prints its success banner with all five health checks failing. Five positive controls returned nonzero, so this is a property of the script, not the harness. §D.1
2. **The invariant checker itself is largely inert.** 13 of its behaviours diverge from its own specification. It detects approximately the exact defect shapes currently in the repository and little else — and it would **reject** one of the four candidate digest architectures as a violation. §C.2
3. **The Bandit gate/CI parity failure is now measured on both sides simultaneously.** Gate test: 3 passed, exit 0. CI-equivalent scan: exit 1, 9 findings. **9 of 9** findings lie outside the paths the gate test names. §D.2
4. **Every `# nosec` directive in the codebase suppresses nothing.** Bandit's own `metrics._totals.nosec` is **0** at both the CI threshold and at all severities, against 56 directives. This refines, and partly reverses, how earlier phases characterised the suppression inventory. §D.3
5. **Two previously untracked image-identity surfaces**, bringing the count from 4 to 6, plus a **seventh live workflow** nobody had inventoried. §D.4
6. **A complete, dormant, stricter duplicate of the pipeline exists in the repository** and already implements the digest plumbing the live pipeline lacks. §D.5

**Release readiness did not materially improve.** It is better *characterised*: three things previously believed to be remedies (promote the Phase 18 verifier, trust the invariant checker, trust the Bandit gate) are now measured to be non-remedies.

---

## B. Integrity and authorization

### B.1 Entry and exit state

| Property | Entry | Exit | Match |
|---|---|---|---|
| HEAD | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | same | ✓ |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | same | ✓ |
| Branch / upstream | `main` / `origin/main`, 0 ahead 0 behind | same | ✓ |
| Staged paths | 0 | 0 | ✓ |
| Unmerged paths | 0 | 0 | ✓ |
| Tracked files differing from HEAD | 2 | 2 | ✓ |
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` | same | ✓ |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` | same | ✓ |
| Stash entries | 2 | 2 | ✓ |
| Worktrees | 1 | 1 | ✓ |
| Latest reflog | `ecd23a9e … commit: launch phase 1` | same | ✓ |

Concurrent sessions also hold `staging/PHASE38_CONTINUOUS_IMPLEMENTATION_AND_RELEASE_CERTIFICATION.md`, `staging/PHASE38_FULL_REPOSITORY_IMPLEMENTATION_AND_RELEASE_RECOVERY.md`, `staging/phase38_tenant_matrix.txt` and `staging/phase38_deploy_suite.txt`. None was read for evidence, modified or overwritten. This report's destination was verified absent immediately before writing.

Stashes untouched: `stash@{0}` is a `filter-branch: rewrite` backup in the stash reflog, not a real stash. `stash@{1}` is orphaned WIP whose base `c673409` is not an ancestor of HEAD, and is the only place a `core/credential_vault.py` and `core/tenant_middleware.py` pair exists.

### B.2 Authorization — a correction to every prior phase

Prior phases reported "0 grants exist on any governed target." That is imprecise. The accurate position:

**The authoritative request register** — `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`, blob `7e238620bc22f0ad246f0ebc373b7032c6b28ef0`, lines 800–804 — records all five approval requests as **NOT GRANTED**:

| ID | Request | Owner | Status |
|---|---|---|---|
| AP-W1D-1 | Per-statement completeness predicates for 022–025 under a read-only session | DB owner | **NOT GRANTED** |
| AP-W1D-2 | Insert 4 ledger rows for migrations verified FULLY APPLIED | DB owner | **NOT GRANTED** |
| AP-W1D-3 | Implement the 4-way drift classification + 10 regression tests | Code owner | **NOT GRANTED** |
| AP-W1D-4 | Add the `_provenance` envelope and recapture the snapshot | DB + code owner | **NOT GRANTED** |
| AP-W1D-5 | Install `pgserver`; create the isolated cluster and `aerora_app` role | Coordinator + DB owner | **NOT GRANTED** |

**The decision record** — `staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` §1, blob `1402bdc5a1b030430d6e192989a851b4a21ce9a8` — records `AP-W1D-3` as **GRANTED** by the session Code owner with no conditions, scoped in §1.1 to exactly four files:

1. `scripts/verify_provisioning.py`
2. `scripts/verify_schema_drift.py`
3. `tests/test_drift_classification.py`
4. `tests/test_snapshot_dimension_invariants.py`

and closing with: *"Explicitly NOT authorized: `scripts/verify_production_schema.py`, `scripts/provision_database.py`, `staging/apply_migration.py`, migrations, snapshots, shared configuration, **and any other workstream's files**."*

**Three consequences.**

First, every Phase 38 target — `.github/workflows/**`, `scripts/deploy.sh`, `scripts/blue-green-deploy.sh`, `infra/deploy.sh`, `.gitattributes`, `tests/test_bandit_sast_gate_is_green.py`, `backend_app/core/state.py`, `backend_app/main.py`, the Terraform roots, the tenant matrix — falls under "any other workstream's files". The blocker is an **affirmative exclusion**, not an absence of permission. That is a stronger and more durable finding than "no grant found", which invites the reading that a grant merely has not been located yet.

Second, the one authorized file of Phase-38 adjacency, `tests/test_drift_classification.py`, is one of the two files a concurrent session is actively modifying. Even within the grant, editing it would collide with live work.

Third, **the register and the decision record disagree and were never reconciled.** The register still reads `NOT GRANTED` at `:802` for an item the decision record grants. Nobody is recorded as the writer for the register — `PHASE11B_A27_RECONCILIATION_AND_READINESS_REPORT.md:119` records editing it as `NOT GRANTED — NOT ESTABLISHED`. So the register cannot be corrected by the party who knows it is stale. That is a governance defect in its own right, and it is why "re-read the ledger" produces a different answer depending on which file you read. Decision **D38-A** in §G.

No permission was inferred, broadened, manufactured or edited. The prompt was not treated as a grant.

### B.3 Environment, and a material regression

| Resource | State | Consequence |
|---|---|---|
| **Disk `C:`** | **0–25 MB free of 111 GB** | Test execution unreliable; see below |
| **WSL2** | **`Catastrophic failure / E_UNEXPECTED`** | `bash` unusable; all shell-script lanes dead |
| **PostgreSQL** | 51815, 51805, 5432 all **CLOSED** | Tenant DB evidence impossible |
| Redis | 6379 **CLOSED**, `REDIS_URL` unset | Redis lanes blocked |
| Python | 3.12.10 | working |
| pytest | 9.1.1 | working |
| bandit | 1.9.4 | working |
| PyYAML | 6.0.3 | working |
| `gh` | 2.100.0, authenticated | working |
| `docker`, `trivy`, `terraform`, `kubectl`, `safety`, `pip-audit` | **ABSENT** | reproduction blocked |

**PostgreSQL was listening on 51815 during Phase 35 and is now down.** That is an environment regression, not a code change, and it is consistent with the full disk. `bash` worked earlier in this phase and degraded mid-phase from "distribution failed to start" to "catastrophic failure".

The workspace holds ~9 GB of the 111 GB, so the bulk is elsewhere on the volume. Largest workspace directories: `.venv` 2.9 GB, `algo22-terminal` 2.4 GB, `terraform` 1.4 GB (provider cache), `.git` 493 MB, `staging` 387 MB. **No reclamation was attempted**: deleting any of these is destructive, would disrupt concurrent sessions, and `.git` holds the irreplaceable orphaned `stash@{1}`. Decision **D38-B** in §G.

**A hazard specific to this host, worth recording:** with the volume full, a file write can report success and produce a **0-byte file**. This happened once (`staging/p38_nested_pipeline.py`). Every subsequent artifact was size-verified before use. Any phase running under these conditions should do the same.

---

## C. Workflow invariants

### C.1 Current status — all five still violated

`python staging/phase31_workflow_invariants.py` → **exit 1, 5 violations**. Byte-identical to Phase 35, which is expected: the tree hash is unchanged.

| ID | Invariant | Status | Location |
|---|---|---|---|
| INV-1 | evidence-retention | **FAIL** | `05-security.yml :: sast-scan / Upload Security Reports` — no `if: always()`, 1 earlier failing step (`Run Bandit Scan`) |
| INV-2 | mandatory-scan-not-swallowed | **FAIL** | `05-security.yml :: sast-scan / Run Safety Check` — `safety check … \|\| echo "Safety advisory logged"` |
| INV-3 | no-tag-as-digest | **FAIL** | `02-build.yml :: build-push` — `DIGEST` falls back to `$IMAGE_TAG` |
| INV-4 | no-ungated-mutable-tag-push | **FAIL** | `02-build.yml :: build-push` — pushes `latest`, no gate dependency |
| INV-5 | digest-is-consumed | **FAIL** | `02-build.yml :: workflow outputs` — no workflow reads `needs.<job>.outputs.image_digest` |

**The build-to-deploy digest chain is NOT validated.** Independently confirmed three ways this phase: the checker's INV-5; a repository-wide search finding **0** expressions matching `needs\.[\w-]+\.outputs\.image_digest` and **0** `uses:` references to `02-build.yml`; and the architectural fact that `03-deploy.yml` consumes via `workflow_run`, which structurally cannot carry job outputs.

### C.2 The checker's own fidelity — 13 divergences

Phase 35 recorded that the checker's 21-test suite passes, and inferred reliability from that. **That inference does not hold.** `staging/p38_checker_fidelity.py` drives each `check_*` function with synthetic workflows and compares the result against the outcome the invariant's own docstring requires.

| Invariant | Kind | Case |
|---|---|---|
| INV-5 | **FALSE POSITIVE** | Option D — single workflow, same-workflow `needs:` |
| INV-5 | **FALSE NEGATIVE** | exporter detector is case-sensitive; `IMAGE_DIGEST=` unseen |
| INV-4 | **FALSE NEGATIVE** | pushes `:latest` in a job with no `needs:`, while `needs:` exists in an unrelated job |
| INV-2 | FALSE NEGATIVE | `bandit … \|\| exit 0` |
| INV-2 | FALSE NEGATIVE | `bandit … ; true` |
| INV-2 | FALSE NEGATIVE | `set +e` then `bandit` |
| INV-2 | FALSE NEGATIVE | `if ! bandit; then echo soft; fi` |
| INV-2 | FALSE NEGATIVE | `bandit … \|\| /bin/true` |
| INV-2 | FALSE NEGATIVE | line continuation before `\|\|` |
| INV-3 | FALSE NEGATIVE | `DIGEST=$(… \|\| echo "latest")` — literal fallback |
| INV-3 | FALSE NEGATIVE | `DIGEST=${DIGEST:-$IMAGE_TAG}` — parameter expansion |
| INV-3 | FALSE NEGATIVE | `IMAGE_DIGEST=$IMAGE_TAG` — direct assignment |
| INV-1 | FALSE POSITIVE | job-level `continue-on-error: true` |

All positive controls behaved correctly, so these are detector gaps, not probe errors.

**The two that change decisions:**

**INV-5 rejects Option D.** A single workflow that builds and deploys, consuming its own digest through `needs:`, is reported as a violation. `check_digest_is_consumed` requires the consumer to be a *different* workflow — deliberately, per its own comment, to stop `02-build.yml` re-exporting its own digest from counting as consumption. The side effect is that a correct combined pipeline is scored as broken. If the owner selects Option D, this checker must change first, or it will report the fix as the defect.

**INV-5's exporter detector is case-sensitive.** Measured directly: `IMAGE_DIGEST=abc` → export **not detected**; `image_digest=abc` → detected; `image_digest:` as a YAML key → detected. `02-build.yml:78` assigns the uppercase shell form. INV-5 fires on the live pipeline only because `02-build.yml` *also* declares a lowercase YAML output key. A pipeline carrying a digest purely in shell — which is what Option B looks like — would not be analysed at all.

I initially recorded Options B and C as "correctly accepted". They were not judged compliant; they were **never examined**. Corrected in the artifact.

**INV-4's gate test is workflow-wide, not job-wide.** `re.search('workflow_run|require_security_scan|needs:', raw)` over the whole dumped document means any `needs:` anywhere marks the workflow gated. A workflow pushing `:latest` from an ungated job passes if some unrelated job has a dependency.

**Net reading.** The checker reliably detects approximately the exact shapes present in the repository today. Of nine INV-2 swallow idioms, it catches two. Of five INV-3 fallback forms, one. Changing `|| echo` to `|| exit 0` would turn it green with the defect fully intact. **It is a description of today's defects, not an invariant that prevents their recurrence.** Promoting it into a blocking gate would install false assurance.

No invariant was weakened, and the checker's fail-closed `exit 1` was preserved. Repair requires authorization on `tests/**`: **BLOCKED — NO GRANT**.

---

## D. Implementation record

No governed file was changed, so this records what was **measured** and what the precise proposed change would be.

### D.1 `scripts/deploy.sh` failure semantics — CONFIRMED BY EXECUTED REPRODUCTION

Harness `staging/p38_deploy_harness.py`. It reads the script from the **committed blob** (`git show HEAD:scripts/deploy.sh`, 2,489 bytes, **0** CR characters) so the local `core.autocrlf=true` conversion cannot influence the result. `kubectl`, `curl` and `sleep` are stubs; sandbox in WSL `/tmp`; no cluster, network or AWS contact.

| id | scenario | exit | printed "complete!" | printed "health check passed" |
|---|---|---|---|---|
| A | all 5 health checks succeed | 0 | yes | yes |
| **B** | **all 5 health checks fail** | **0** | **yes** | **no** |
| C | 4 fail, 5th succeeds | 0 | yes | yes |
| **D** | **`curl` absent from PATH** | **0** | **yes** | **no** |
| **E** | **`.env.staging` missing** | **0** | **yes** | yes |
| F | `.env` with comments/blanks/quotes/metachars | 1 | no | no |
| G | `kubectl cluster-info` fails | 1 | no | no |
| H | namespace missing | 1 | no | no |
| I | `kubectl set image` fails | 1 | no | no |
| J | `kubectl rollout status` fails | 1 | no | no |
| K | no image tag argument | 0 | yes | yes |
| M | invalid environment name | 1 | no | no |

**5 of 5 positive controls (G, H, I, J, M) returned nonzero**, establishing the harness can observe failure.

**D32-1 CONFIRMED.** Scenario B. Source basis, `:61-68`:

```bash
for i in {1..5}; do
    if curl -sf $HEALTH_URL > /dev/null; then
        echo "   ✓ Health check passed"; break
    fi
    echo "   ⏳ Health check attempt $i/5..."; sleep 10
done
```

No post-exhaustion branch. `set -e` cannot fire because curl's nonzero status is consumed by the `if` condition. Execution falls through to `kubectl get pods` and the success banner.

**D32-2 CONFIRMED.** Scenario E. `:21` `export $(grep -v '^#' .env.$ENVIRONMENT | xargs)` — grep's failure is confined to a command substitution, and `export` with no operands returns 0, so `set -e` observes success. A missing environment file is indistinguishable from a present one.

**D38-1 NEW.** Scenario D. A missing health-check client and a failing health check traverse the same unguarded loop and are indistinguishable.

**D38-2 NEW.** Captured from the stub: with no tag argument the command issued is
`kubectl set image deployment/trading-backend backend=latest -n staging`. The default deployment identity is the most mutable tag available, and `:46-47` passes it unquoted with no digest validation.

**D38-3, restated accurately.** Scenario F exits **1**. The cause is `export` rejecting malformed identifiers produced by word-splitting the substitution result — so an ordinary quoted value such as `QUOTED="a value with spaces"` *fails the deployment*. This is an **availability** defect on well-formed configuration, not a security one. See §D.7 for the injection reading I withdrew.

**Proposed change, not applied** (`scripts/deploy.sh`, **BLOCKED — NO GRANT**): give the retry loop an exhaustion branch that exits nonzero; make a missing or unreadable `.env.$ENVIRONMENT` an explicit failure; parse configuration without `export $(… | xargs)`; reject an image identity that is empty or not a digest; emit the success banner only after every required check has actually passed.

### D.2 Bandit CI parity — CONFIRMED BY EXECUTED REPRODUCTION

Both sides measured on the same source, same Bandit 1.9.4, same machine.

| | command | exit | result |
|---|---|---|---|
| CI equivalent | `bandit -r backend_app -ll` | **1** | 9 findings, all `B608`, all MEDIUM; `errors: 0`; 219,319 LOC |
| Repository gate | `pytest tests/test_bandit_sast_gate_is_green.py` | **0** | **3 passed** in 125.56 s |

Findings:

| file | n | lines |
|---|---|---|
| `backend_app/alembic/versions/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 2 | 212, 222 |
| `backend_app/alembic/versions/b2c3d4e5f6a7_positions_canonical_shape.py` | 2 | 224, 262 |
| `backend_app/alembic/versions/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 5 | 193, 198, 209, 467, 475 |

The gate test names exactly one `backend_app` path, `backend_app/backend/transactional_execution_manager.py`. **9 of 9** current findings lie outside it. The gate is green and the pipeline is red, and both are correct about their own scope. A green gate is not evidence of a green pipeline.

`05-security.yml:36` carries no `continue-on-error`, so this step fails the job.

### D.3 Security evidence chain, and the suppression inventory re-measured

**The evidence chain collapses from a single failure.** `sast-scan` steps run in order: `Run Bandit Scan` → `Run Safety Check` → `Upload Security Reports`. Bandit fails (§D.2) and has no `continue-on-error`, so GitHub skips both remaining steps. Therefore, on current source:

- the dependency scan **never executes** (independently of INV-2's `|| echo`, which would have hidden its verdict anyway), and
- the artifact that would explain the failure is **never uploaded** (INV-1).

One scanner's findings disable the other scanner and destroy the evidence. Both Phase 38 §5 claims — Safety skipped after Bandit fails, and the second IaC scan skipped after the first fails — are **CONFIRMED on current source**. For IaC: `Trivy IaC scan — terraform/` has `exit-code: '1'` and no `continue-on-error`, so `Trivy IaC scan — aerora_quant_platform/infrastructure/` runs only when the first root is clean. The nested root is scanned **conditionally on an unrelated root passing**. `Upload IaC Scan Report` does carry `if: always()` and is correct.

**The suppression inventory suppresses nothing.** `staging/p38_nosec_effect.py`:

| measurement | `-ll` (CI threshold) | all severities |
|---|---|---|
| exit code | 1 | 1 |
| findings reported | 9 | 169 |
| **`metrics._totals.nosec`** | **0** | **0** |
| "nosec encountered … but no failed test" | 59 | 59 |

Textual inventory: **56 directives across 17 files — 54 colon-form `# nosec: Bxxx`, 2 space-form, 0 bare.** This independently confirms Phase 36's correction and confirms that the earlier "54 bare" figure was wrong.

`metrics._totals.nosec` is Bandit's count of findings it withheld because a directive told it to. It is **0 at both thresholds**. Against 56 directives and 59 (rule, location) pairs where the named rule does not fire, the conclusion is that **the entire suppression inventory is inert.** The stale directives span `dashboard_aggregation_service.py`, `metrics_service.py`, `telemetry_engine.py`, `transactional_execution_manager.py` (10 lines), `portfolio.py`, `database_scaling.py`, `execution_record.py:1385`, `order_watchdog.py:116`, `risk.py:460`, `ws_server.py:72` (`B104`) and `test_perf.py`.

This **refines and partly reverses** the earlier framing. Prior phases treated the inventory as a risk surface concealing findings. It conceals nothing today. The actual defects are different and smaller: directives asserting an assessment that is no longer needed, creating false confidence that someone reviewed those lines. **It does not follow that the SQL is safe** — it follows that neither the scan nor the directive currently tells you anything about those lines.

**A latent widening risk.** Bandit parses everything after `# nosec` as a list of test ids, so prose rationale is parsed as test ids and rejected with a warning: **37 rejections over 26 distinct tokens** (`container`, `binding`, `interpolated`, `metrics`, `exporter`, `microservice`, `internal`, `health`, `endpoint`, …). Checked against Bandit's registry of **84** known ids and names: **0 collisions today.** That is luck, not design. A rationale containing a real test name or a `B`-number would silently widen the suppression with no diagnostic.

No suppression was added, removed, moved or edited. No severity threshold was changed. No finding was reclassified.

### D.4 Image provenance — now six surfaces

`staging/p38_image_consumers.py` inventoried every file producing or consuming an image identity and classified reachability conservatively, per the instruction that a bounded search cannot prove a manually operated script unused.

Machine-checked totals:

- workflow expressions reading `needs.<job>.outputs.image_digest`: **0**
- `uses:` references to `02-build.yml`: **0**
- `aws_ecr_repository` resources declared in Terraform: **0** → tag immutability is not declared as code anywhere; its live value is **UNKNOWN** from this repository

Surfaces 1–4 were already on record (`02-build.yml`, `03-deploy.yml`, `ecs_deploy_and_diagnose.py`, `scripts/deploy.sh` → `kubectl`, with `blue-green-deploy.sh` promotion re-reading the live cluster). Two are new:

**Surface 5 — `terraform/ecs.tf` pins `:latest` in six task definitions.**

```
 :28  image = "vyomquant-api:latest"        # ECR URL goes here
:126  image = "vyomquant-tee:latest"
:198  image = "vyomquant-mds:latest"        # ECR URL goes here
:284  image = "vyomquant-websocket:latest"  # ECR URL goes here
:378  image = "vyomquant-simulator:latest"  # ECR URL goes here
:478  image = "vyomquant-api:latest"        # Workers use the backend image
```

These are not merely mutable tags, they are **bare image names with no registry prefix**, and four carry the comment "ECR URL goes here". Infrastructure-as-code declares the deployed image identity as an unqualified mutable placeholder. No prior phase recorded this.

**Surface 6 — a dormant duplicate pipeline.** §D.5.

Also inventoried with no workflow reference, recorded as **NEEDS OWNER CONFIRMATION** rather than dead: `scripts/deploy_to_ecs.py` (task-definition registration), `scripts/aws_validation.py` (task-definition image inspection), `scripts/blue-green-deploy.sh`, `scripts/deploy.sh`.

### D.5 The dormant pipeline — and a working reference implementation

`aerora_quant_platform/deployment/.github/workflows/` contains a complete parallel CI/CD definition: `ci-cd.yml`, `blue-green-deploy.yml`, `pr-checks.yml`. GitHub executes only the repository-root `.github/workflows/`, so none of it runs.

Checker run against both directories:

| invariant | live pipeline | dormant pipeline |
|---|---|---|
| INV-1 evidence-retention | **VIOLATED** | holds |
| INV-2 scan-not-swallowed | **VIOLATED** | **VIOLATED** |
| INV-3 no-tag-as-digest | **VIOLATED** | holds |
| INV-4 no-ungated-mutable-tag-push | **VIOLATED** | holds |
| INV-5 digest-is-consumed | **VIOLATED** | **VIOLATED** |
| | **5 violations** | **2 violations** |

Mechanism comparison:

| control | live | dormant |
|---|---|---|
| digest source | shells out: `DIGEST=$(… describe-images … \|\| echo "$IMAGE_TAG")` | `image_digest: ${{ steps.build.outputs.digest }}` — native, no fallback |
| build job gate | `needs:` count **0** | `needs: [test, security]` |
| Safety exit status | swallowed by `\|\| echo` | preserved |
| Trivy action ref | pinned to release tags (4 occurrences) | **`trivy-action@master`** — mutable |
| deploys using | tag | tag (**6** occurrences of `image_tag`, **0** of `image_digest`) |

Three things follow.

**The mechanism Option A needs already exists and works in this repository.** `ci-cd.yml:135` takes the digest from `docker/build-push-action`'s own `steps.build.outputs.digest` — no shell, no registry round-trip, no tag fallback — and publishes it as `needs.build.outputs.image_digest`. That is exactly the shape the live pipeline lacks. It is a concrete reference, not a design sketch.

**It nonetheless reproduces the same end-state defect by a different route.** It declares a correct digest and then deploys with `needs.build.outputs.image_tag` at `:214`, `:306` and `:325`. Two independent pipelines, two different digest mechanisms, same outcome: a correct digest, computed and unused. That strengthens the case that the gap is a consumption decision rather than a plumbing difficulty.

**It is looser than the live pipeline on exactly one control, and it is the one the live pipeline deliberately hardened.** `05-security.yml` pins `aquasecurity/trivy-action@v0.36.0` and documents why, citing the February 2026 compromise of the Trivy project's own repository. The dormant pipeline tracks `@master`. If anyone promotes it by copying, that regression travels with it.

Two divergent definitions of one pipeline, one unexecuted, is a drift hazard: nothing in the files states which is authoritative. Decision **D38-C** in §G.

### D.6 Seventh live workflow, and the publication-gate census

The live directory holds **seven** workflows, not the six prior phases tracked. `07-desktop-build.yml` was uninventoried.

**It is benign, and I checked rather than assumed.** It documents at `:44` and `:84` that it publishes nothing and creates no release; it has no S3, CloudFront or release step, only `actions/upload-artifact@v4` at `:308`. It is **not** a second ungated publication path. Its header does record that `algo22-terminal/releases/` contains placeholder artifacts masquerading as installers — mac `.dmg` 69 bytes, linux `.AppImage` 64 bytes, `.deb` 67 bytes, against a genuine 112 MB Windows installer.

`needs:` census across all seven:

| workflow | `needs:` | publishes to shared infrastructure? |
|---|---|---|
| `01-pr-check.yml` | 5 | no |
| **`02-build.yml`** | **0** | **yes — ECR** |
| `03-deploy.yml` | 1 | yes — ECS |
| `04-nightly-audit.yml` | 0 | no |
| `05-security.yml` | 0 | no (3 independent scan jobs; correct for parallelism) |
| **`06-frontend-deploy.yml`** | **0** | **yes — S3 + CloudFront** |
| `07-desktop-build.yml` | 1 | no |

**Exactly two workflows publish to shared infrastructure with zero gate dependencies: `02-build.yml` to ECR and `06-frontend-deploy.yml` to S3/CloudFront.** That is the precise, complete statement of the publication-gate exposure, and it is the mechanism behind the recorded incident in which the frontend published from a commit whose security workflow was red.

### D.7 `require_security_scan.py` — the gate is sound, its explanation is not

The Phase 38 misdiagnosis claim is **CONFIRMED on current source**. The closing branch:

```python
print("  REFUSING: %s concluded %r for %s." % (WORKFLOW_NAME, conclusion, sha[:8]))
print("  05-security.yml runs Trivy at severity CRITICAL with exit-code 1, so this means a")
print("  CRITICAL container vulnerability is present. Fix the pin and push again.")
```

`05 Security` has **three independent jobs** — `sast-scan`, `container-security`, `iac-scan`. Any one failing makes the workflow conclusion non-success. The job actually failing today is `sast-scan`, on Bandit's 9 `B608` findings (§D.2), **not** the container scan. So the gate tells the operator to "fix the pin" when the real defect is SQL string construction in three migrations.

The module docstring compounds it: *"`05-security.yml` already runs Trivy with `severity: CRITICAL` and `exit-code: 1`, so the workflow is red only for CRITICAL findings."* That is false. The workflow is also red for Bandit findings at MEDIUM and above, and for IaC CRITICAL findings.

**The decision logic is correct and fail-closed and must be preserved**: it refuses on missing runs, on non-completion, on `cancelled`/`skipped`, and on any non-success conclusion. Only the attribution is wrong. The remedy is diagnostic accuracy — report which job failed — not a change to the gate.

### D.8 Residual confirmations

`staging/p38_reuse_and_confirm.py`, all read-only.

**Workers — both unregistered.** `backend_app/main.py` contains **0** occurrences of `ExchangeReconciliationService` and **0** of `MarketplaceExpiryWorker`. Exchange reconciliation detects divergence between local order state and venue state; marketplace expiry retires expired listings. Both are inert in the deployed application and nothing logs their absence.

**Vault — confirmed.** `backend_app/core/state.py:67` `APIKeyVault = safe_import("APIKeyVault", "api_key_vault")` uses a bare module name, alongside three siblings at `:68`, `:75`, `:76` with the same shape. `backend_app/backend/portfolio_cache_updater.py:151` imports `from backend_app.backend.key_manager import KeyVault`; `backend_app/backend/key_manager.py` **does not exist** (verified).

**Terraform — three roots, three postures** (machine-extracted):

| root | backend | bucket | key | region | `encrypt` | `dynamodb_table` |
|---|---|---|---|---|---|---|
| `terraform/` | s3 | `vyomquant-terraform-state` | `prod/terraform.tfstate` | `ap-southeast-1` | present | present |
| `aerora_quant_platform/infrastructure/terraform/` | s3 | `aerora-terraform-state` | `production/terraform.tfstate` | `us-east-1` | **ABSENT** | **ABSENT** |
| `terraform/redis-only/` | **none** | — | — | — | — | — (local state) |

Different buckets, different regions, different keys: these are two unrelated state locations, not two configurations of one. `terraform/providers.tf:16-19` documents that `encrypt` provides SSE and `dynamodb_table` provides the concurrent-apply lock — so the project has written down why both are required, and the second root satisfies neither. `terraform validate`/`plan`: **BLOCKED — TOOL ABSENT**.

The withdrawn Redis security-group collision hypothesis stays **WITHDRAWN**; `AWS-0054` stays a confirmed **false positive** with its `lifecycle.precondition` preserved. No new evidence bears on either.

**Tenant isolation — BLOCKED.** `tests/property/test_tenant_isolation_matrix.py` is **UNCHANGED** at `a60cd00c4558b386de61e045b08bf9b5ebfaf69d`. All PostgreSQL ports are closed, so the historical 4-of-150 result was neither reproduced nor refuted for a second consecutive phase. Recorded as **BLOCKED — TOOL/SERVICE ABSENT**, explicitly **not** as passing. Tenant isolation remains **NOT CERTIFIED**.

**Release verifier — NOT RUN, reuse licensed.** `staging/phase18_release_gate.py` is **UNCHANGED** at `5c70dbbfb882036badade2ff01b4bf687595ff4e`, as is every workflow input. The 9-of-9 job-id vs display-name mismatch is carried forward on that hash basis per §14. The Phase 28–31 recommendation to promote it stays **withdrawn**: a gate that compares the wrong identity field refuses every release, which is fail-closed for the wrong reason and trains operators to bypass it.

---

## E. Test ledger

Tool versions: Python 3.12.10 · pytest 9.1.1 · bandit 1.9.4 · PyYAML 6.0.3 · bash 5.2.21 (WSL2, **failed mid-phase**) · gh 2.100.0.

| # | command | exit | result |
|---|---|---|---|
| 1 | `python staging/phase31_workflow_invariants.py` | **1** | 5 violations (fail-closed, expected) |
| 2 | `python staging/p38_checker_fidelity.py` | 0 | 13 divergences; all positive controls correct |
| 3 | `python staging/p38_deploy_harness.py` (WSL-native revision) | 0 | 13 scenarios; 5/5 positive controls nonzero |
| 4 | `python -m bandit -r backend_app -ll -f json` | **1** | 9 findings, `errors: 0`, 219,319 LOC |
| 5 | `python -m pytest tests/test_bandit_sast_gate_is_green.py -p no:cacheprovider -q` | 0 | **3 passed** in 125.56 s |
| 6 | `python staging/p38_bandit_parity.py` | 0 | scope gap 9/9 outside gated paths |
| 7 | `python staging/p38_nosec_effect.py` | 0 | `nosec` suppression count **0** at both thresholds |
| 8 | `python staging/p38_image_consumers.py` | 0 | 6 surfaces; 0 digest consumers; 0 `aws_ecr_repository` |
| 9 | `python staging/p38_nested_pipeline.py` | 0 | live 5 violations vs dormant 2 |
| 10 | `python staging/p38_reuse_and_confirm.py` | 0 | 4/4 carried-forward hashes UNCHANGED |
| 11 | `git rev-parse` / `write-tree` / `status` / `stash list` / `worktree list` / `hash-object` | 0 | §B.1 |

**Reuse licensed by hash** (§14): Phase 18 verifier identity mismatch, tenant 4-of-150, frontend `needs:=0`, ledger contents. All four source blobs verified **UNCHANGED**.

**NOT RUN, with reasons:**

| item | reason |
|---|---|
| Phase 18 release verifier execution | inputs hash-verified unchanged; no new information |
| Tenant isolation matrix | **PostgreSQL down** |
| Redis-dependent paths | **Redis down**, `REDIS_URL` unset |
| `trivy`, `terraform validate`, `docker build`, `kubectl` | tools **ABSENT** |
| `safety`, `pip-audit` | **ABSENT**, and no Safety policy exists to encode |
| `blue-green-deploy.sh` / `infra/deploy.sh` harnesses | **WSL2 catastrophic failure** |
| Corrected deploy-harness scenarios (genuinely-empty tag; `$(…)` marker probe; quoted-values-only `.env`) | harness corrected, **WSL died before re-run** |
| `staging/` prototypes under default discovery | `pytest.ini` sets `testpaths = tests`; **not broadened**, per instruction |

No skipped, xfailed, blocked, uncollected or unavailable test is counted as passed.

---

## F. Release certification matrix

| Gate | Status |
|---|---|
| Correctness gate (`01 PR Check` at candidate SHA) | **FAIL** — 6/6 recent runs, one job, one test |
| Workflow invariants INV-1…INV-5 | **FAIL** — 5/5 violated |
| Invariant checker trustworthy as a gate | **FAIL** — 13 divergences; rejects Option D |
| Security scan reachability | **FAIL** — Bandit failure skips Safety **and** the upload |
| Security evidence retention | **FAIL** — INV-1 |
| Safety policy | **BLOCKED — OWNER DECISION**; scan also **BLOCKED — TOOL ABSENT** |
| IaC scan coverage | **FAIL** — nested root conditional on an unrelated root passing |
| Bandit gate parity | **FAIL** — gate green (3 passed) while CI red (9 findings) |
| Suppression hygiene | **FAIL** — 56 directives, 0 suppressing anything, 59 stale |
| Image provenance | **NOT ESTABLISHED** — 6 surfaces, 0 digest consumers |
| ECR tag immutability | **UNKNOWN** — no `aws_ecr_repository` in any root |
| Frontend publication gate | **FAIL** — `needs: = 0` |
| ECR publication gate (`02-build.yml`) | **FAIL** — `needs: = 0` |
| Deployment-script failure semantics | **FAIL** — reproduced: exit 0 on total health-check failure |
| Deployment-script config handling | **FAIL** — missing file tolerated; quoted values fail the deploy |
| Tenant isolation | **NOT CERTIFIED** — **BLOCKED**, PostgreSQL down |
| Worker lifecycle | **FAIL** — 2 services defined, 0 registered |
| Credential vault | **FAIL** — bare-name import; `key_manager` module absent |
| Redis | **BLOCKED — SERVICE ABSENT** |
| Terraform backend posture | **FAIL** on 1 of 3 roots; 1 root on local state |
| Pipeline definition uniqueness | **FAIL** — two divergent definitions, one dormant |
| Live trading | **OFF / NOT CERTIFIED** |

Nothing was weakened to reach a green result. No test, threshold, required job, fail-closed behaviour, tenant control, anti-vacuity assertion, strict XFAIL or provenance requirement was relaxed. Fail-closed behaviours explicitly preserved: `03-deploy.yml:168-173`, the invariant checker's `exit 1`, and `require_security_scan.py`'s refusal logic.

---

## G. Owner decisions

Only decisions that genuinely block the next safe step.

**D38-A — Reconcile the authorization register.** `…W1D_LEDGER…:802` says `AP-W1D-3` is `NOT GRANTED`; `…W1D12_CLASSIFIER_DECISION_RECORD…` §1 says `GRANTED`. No writer is authorized for the register. Either authorize someone to correct it or designate the decision record as authoritative. Until then "re-read the ledger" yields different answers depending on the file read. *New this phase.*

**D38-B — Disk exhaustion.** `C:` is full; WSL2 and PostgreSQL are both down as a result. Test execution is unreliable and writes can silently produce 0-byte files. Reclamation requires deleting files, which I will not do unattended: `.git` holds the orphaned `stash@{1}`, and `.venv` / `algo22-terminal` are in use by concurrent sessions. *New this phase.*

**D38-C — The dormant pipeline.** Adopt `aerora_quant_platform/deployment/.github/workflows/` as the basis for the digest fix, retire it, or keep both and record which is authoritative. Note it already implements Option A's digest plumbing correctly and carries one regression — `trivy-action@master` — that must not travel with it. *New this phase.*

**D38-D — Digest architecture.** Options A–D as previously framed. Two constraints are now measured: Option A's mechanism already exists and works in the dormant pipeline; and INV-5 as written would **report Option D as a violation**, so selecting D requires fixing the checker first.

**D38-E — Safety status and severity policy.** No authoritative policy exists; none was invented. Whatever is chosen, the Safety job must execute independently of Bandit's outcome — currently it cannot run at all when Bandit fails.

**D38-F — Suppression disposition.** 56 directives suppress nothing and 59 sit where their named rule does not fire. Remove them, or re-scope them to lines that do fire. A rationale containing a real Bandit test name or `B`-number would silently widen a suppression; there are 0 such collisions today.

**D38-G — Required-job identity source.** Static list versus derived-from-candidate-SHA YAML, given that the expected job set is SHA-dependent and that all 9 configured identifiers are job ids where the API returns display names.

**D38-H — Frontend publication containment.** Unchanged, and still constrained: rollback to the known-green SHA ships bytes identical to a known-red SHA.

**D38-I — Canonical tenant identity, and quota/ownership ordering.** Three identity expressions exist; `apply_tenant_context` has 0 external consumers. Any reordering must preserve the write-bypass closure documented at `library.py:2352`.

**D38-J — Credential-vault lifecycle**, including whether the orphaned `stash@{1}` implementation supersedes `api_key_vault`.

**D38-K — Nested Terraform root**: adopt `encrypt` + `dynamodb_table`, or retire it. It is a separate bucket in a separate region, not a variant of the main root.

**D38-L — Worker production lifecycle** for `ExchangeReconciliationService` and `MarketplaceExpiryWorker`.

**D38-M — Supported manual deployment entry points**: `scripts/deploy.sh`, `scripts/blue-green-deploy.sh`, `scripts/deploy_to_ecs.py`, `scripts/aws_validation.py`, `scripts/ecs_deploy_and_diagnose.py`, and the six `:latest` placeholders in `terraform/ecs.tf`.

---

## H. Workstream disposition

| Workstream | State |
|---|---|
| Workflow-invariant status | **IMPLEMENTED AND VALIDATED** (measurement; 5/5 violated) |
| Invariant-checker fidelity audit | **IMPLEMENTED AND VALIDATED** (13 divergences) |
| Invariant-checker repair | **BLOCKED — NO GRANT** (`tests/**`) |
| Deploy-script failure semantics | **DISPROVEN-FREE: CONFIRMED BY REPRODUCTION**; fix **BLOCKED — NO GRANT** |
| Deploy-script corrected scenarios | **BLOCKED — TOOL/SERVICE ABSENT** (WSL dead) |
| Bandit parity | **IMPLEMENTED AND VALIDATED** (both sides measured) |
| Bandit gate-test widening | **BLOCKED — NO GRANT** |
| Suppression census | **IMPLEMENTED AND VALIDATED** (0 suppressing) |
| Suppression cleanup | **BLOCKED — OWNER DECISION** (D38-F) |
| Security-scan reachability | **CONFIRMED — CURRENT SOURCE**; fix **BLOCKED — NO GRANT** |
| `require_security_scan.py` diagnostics | **CONFIRMED — CURRENT SOURCE**; fix **BLOCKED — NO GRANT** |
| Image-consumer inventory | **IMPLEMENTED AND VALIDATED** (6 surfaces) |
| Digest architecture | **BLOCKED — OWNER DECISION** (D38-C, D38-D) |
| Release-verifier identity | **NOT RUN** — reuse licensed by hash; promotion stays withdrawn |
| Frontend gate | **CONFIRMED — CURRENT SOURCE**; containment **BLOCKED — OWNER DECISION** |
| Tenant isolation | **BLOCKED — TOOL/SERVICE ABSENT** (PostgreSQL down) |
| Worker lifecycle | **CONFIRMED — CURRENT SOURCE** (0 registered); fix **BLOCKED — NO GRANT** |
| Vault / D7-1 | **CONFIRMED — CURRENT SOURCE**; fix **BLOCKED — NO GRANT** |
| W-6 classification | **CONFIRMED** — flag is log-only (§I) |
| Redis | **BLOCKED — SERVICE ABSENT** |
| Terraform roots | **CONFIRMED — CURRENT SOURCE**; `validate` **BLOCKED — TOOL ABSENT** |
| `.gitattributes` | **BLOCKED — NO GRANT** |

---

## I. Claims corrected, withdrawn or refined by this phase

Recorded so no reader acts on a superseded conclusion.

| Claim | Disposition |
|---|---|
| "0 grants exist on any governed target" (Phases 12–35) | **CORRECTED** — exactly one live grant, `AP-W1D-3`, 4 files, with an explicit exclusion clause covering all Phase 38 targets (§B.2) |
| Suppression inventory conceals findings; "54 bare" | **REFINED / CORRECTED** — 0 bare, and **0 findings suppressed** at any severity; the defect is inert directives and false confidence, not concealment (§D.3) |
| Four image-identity surfaces | **CORRECTED to six** — `terraform/ecs.tf` ×6 `:latest` placeholders, and a dormant duplicate pipeline (§D.4–D.5) |
| Six live workflows | **CORRECTED to seven** — `07-desktop-build.yml`; checked and benign (§D.6) |
| Checker reliability inferred from its 21 passing tests (Phase 35) | **CORRECTED** — 13 divergences; detects roughly today's exact shapes only (§C.2) |
| Options B and C "correctly accepted" by INV-5 | **CORRECTED mid-phase** — never analysed; exporter detector is case-sensitive |
| `retryable` has 0 control-flow consumers | **CONFIRMED, refined.** My own crude count of 6 collapsed to **1** on inspection: 3 were comments and 1 a docstring. The single real use is `self._is_retryable_error(e)` at `replay_safe_transaction_guard.py:304` — an independent classifier, not a consumer of the W-6 flag. Prior phases were right; I caught my error before publishing it. A second, unused notion of retryability does exist. |
| CRLF defect in committed shell scripts | stays **WITHDRAWN** — re-verified: `scripts/deploy.sh` blob has **0** CR bytes. Residual gap: `.gitattributes` still absent |
| Redis security-group collision | stays **WITHDRAWN** |
| `AWS-0054` | stays a confirmed **false positive** |
| Promote the Phase 18 verifier (Phases 28–31) | stays **WITHDRAWN** |
| Digest "echoed and discarded" (Phase 33) | stays **SUPERSEDED** — unreachable by event type |

### Harness errors I discarded rather than reported

Recorded because each would have produced a confident, wrong finding.

1. **First deploy-harness revision: all 13 scenarios exit 1, no output.** Read naively, "the script fails everything." Cause: `bash` here is WSL2, and the harness drove it with Windows paths and `chmod` on a drvfs mount, so the interpreter never started. Rewritten to run inside WSL `/tmp`. Result discarded.
2. **A command-injection finding in the `.env` parser.** The marker string did appear in stdout — inside `export`'s own error message (`export: 'INJECTEDSUBST)': not a valid identifier`), echoing the token back rather than executing it. Replaced the stdout grep with a marker-file existence check. **No injection exists**; the real defect is availability (§D.1, D38-3).
3. **"Empty image tag" scenario.** Unquoted argument expansion passed the literal two-character token `""`, not an empty string. Corrected via `eval "ARGS=(…)"`; the corrected run is **NOT RUN** (WSL died).
4. **"Bandit exits 1 with 0 findings" — i.e. a scanner error.** `bandit -f json` writes a progress bar to stdout ahead of the JSON, so `json.loads(stdout)` failed and looked like an error rather than a verdict. Stripped the prefix; the real result is 9 findings with `errors: 0`. In CI this never arises because `-o` writes to a file.
5. **A 0-byte script that ran and exited 0.** `fs_write` reported success while the full disk wrote nothing. All later artifacts were size-verified.

---

## J. Exit integrity

Re-verified after writing this report: HEAD `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` · `write-tree` `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` · branch `main` · 0 staged · 0 unmerged · 2 files differing from HEAD at their original blob hashes · 2 stashes · 1 worktree.

**Governed files changed: 0.** No stage, commit, push, reset, stash, pop, drop, clean, revert, merge, rebase, branch switch, history rewrite or worktree mutation. No CI dispatch, no PR, no branch-protection or repository-settings change, no deploy, no rollback. No S3, ECR, CloudFront or DNS mutation. No Terraform apply, destroy, import or state operation. No production database mutation. No live Kubernetes operation. No real order. Live trading not enabled. No credential, token, DSN or secret value printed.

All prior reports, prototypes, untracked artifacts, stashes, worktrees and concurrent changes intact.

---

*Every `staging/` artifact referenced here is **PROTOTYPE ONLY** and is not part of the governed codebase.*
