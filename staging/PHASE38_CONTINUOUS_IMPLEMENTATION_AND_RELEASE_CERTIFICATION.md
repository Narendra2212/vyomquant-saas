# Phase 38 — Continuous Implementation Campaign: Release-Gate Repair Specification, Image-Provenance Closure, Security Recovery, and Engineering Certification

**Destination verified ABSENT before writing.** `PHASE37_…`, `PHASE37B_…`, `PHASE36_…` and all
earlier artifacts preserved, not overwritten.

**0 production files · 0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 tests ·
0 governance records changed. 1 artifact written (this file).**

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. What this phase delivers, and why it is not another audit

Every implementation lane is **NOT AUTHORIZED** (§3). Rather than restate that, this phase uses the
allowance in §14 — *"produce a patch specification or unified diff in an explicitly non-production
artifact"* — to convert the accumulated findings into **complete, applyable diffs pinned to exact
blob hashes**, plus the full source of the real-workflow contract test that replaces the masking
fixtures.

**Nothing here has been applied.** Each diff is annotated with the grant it needs. A diff in this
document is a specification, not an implementation, and must not be read as one.

### Entry / exit integrity

| Measurement | Entry | Exit |
|---|---|---|
| branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **identical** |
| `HEAD^{tree}` parent chain | parent `074b772a…` | identical |
| upstream divergence | 0 ahead / 0 behind | identical |
| staged / unmerged | **0 / 0** | 0 / 0 |
| stashes / worktrees | **2 / 1** — recorded, untouched | 2 / 1 |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **identical** |
| porcelain | 177 | 178 (+1 = this report) |
| concurrent modification | `tests/test_drift_classification.py` ` M` | **untouched** |

**Tools:** `python` 3.12.10 · `pytest` · `bandit` 1.9.4 · PyYAML 6.0.3 · `gh` · `node`/`npm` —
present. **`docker` ✘ · `trivy` ✘ · `terraform` ✘.** PostgreSQL `:5432`, Redis `:6379` closed.

---

## 2. Source baseline — diffs below are pinned to these blobs

| Blob | Path |
|---|---|
| `5c70dbbfb882036badade2ff01b4bf687595ff4e` | `staging/phase18_release_gate.py` |
| `3dd575192edbb8df26f3bcee0ba6fef7992e0483` | `staging/phase18_test_release_gate.py` |
| `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` | `.github/workflows/05-security.yml` |
| `42c7696eb7cb993b8942b7f0053bf7490e3e5edd` | `.github/workflows/02-build.yml` |
| `eb8078eac06268447d60f26ef7a97a537ceed988` | `.github/workflows/03-deploy.yml` |
| `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` | `.github/workflows/06-frontend-deploy.yml` |
| `cec975b267e844e6c770a865258bae0f1e9edfc7` | `scripts/ecs_deploy_and_diagnose.py` |
| `e06423f2b3b075add617778c662146c0c4754657` | `scripts/deploy.sh` |

**If a blob hash differs when a grant arrives, re-derive the diff — do not apply it blind.**

---

## 3. Authorization — rechecked this phase

`CURRENT SOURCE` `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`, parsed field-by-field:

| Row | Owner (role label only) | Status |
|---|---|---|
| `AP-W1D-1` | `DB owner (read access)` | **NOT GRANTED** |
| `AP-W1D-2` | `DB owner` | **NOT GRANTED** |
| `AP-W1D-3` | `Code owner` | **NOT GRANTED** |
| `AP-W1D-4` | `DB owner + code owner` | **NOT GRANTED** |
| `AP-W1D-5` | `Coordinator + DB owner` | **NOT GRANTED** |

`A23E6` → **0 ticked / 12 unticked**. No `CODEOWNERS` / `OWNERS` / `MAINTAINERS`. **No row names an
individual** — every owner is a role label, which is why no grant can be exercised even in
principle. **Every Phase 38 target is NOT AUTHORIZED.**

---

## 4. Lane R — release-gate repair (P18-1 + P18-2 + P18-3)

**Grant needed:** `staging/phase18_release_gate.py` + `staging/phase18_test_release_gate.py`
(prototype repair). Promotion to `scripts/`/`tests/` is a **separate** grant.
**Blocked additionally by the §5 owner decision.**

### 4.1 Bidirectional inventory

`CURRENT SOURCE` `phase18_release_gate.py:61-66` declares **9 mandatory** jobs; `:74` declares 2
advisory. Reconciled against real YAML — no dangling policy IDs, no unclassified required job:

| Workflow | Policy ID | Real `name:` | Class | Match |
|---|---|---|---|---|
| `05 Security` | `sast-scan` | `Bandit SAST & Dependency Security Scan` | mandatory | ✘ |
| `05 Security` | `container-security` | `Trivy Container Vulnerability Scanner` | mandatory | ✘ |
| `05 Security` | `iac-scan` | `Trivy IaC Misconfiguration & Secret Scan` | mandatory | ✘ |
| `01 PR Check` | `static-audit` | `Code Quality & Static Audit` | mandatory | ✘ |
| `01 PR Check` | `unit-tests` | `Unit & Integration Tests` | mandatory | ✘ |
| `01 PR Check` | `paper-mode-tests` | `Execution Tests (production posture)` | mandatory | ✘ |
| `01 PR Check` | `database-tests` | `Database Tests (PostgreSQL)` | mandatory | ✘ |
| `01 PR Check` | `docker-check` | `Docker Build Validation` | mandatory | ✘ |
| `01 PR Check` | `frontend-tests` | `Frontend Build & Unit Tests` | mandatory | ✘ |
| `01 PR Check` | `format-check` | `Format Backlog (report only)` | advisory | ✘ |
| `01 PR Check` | `provisioning-and-isolation` | `Provisioning, Privilege Hardening & Tenant Isolation` | advisory | ✘ |

**0 of 11 match.** The module's own comment at `:59-60` states the correct rule —
*"Job names must match the API's `name`, i.e. the `name:` field if present, otherwise the job id"* —
and the table at `:62-66` violates it. A spec/implementation divergence.

**REUSED EVIDENCE (Phase 37B, same blobs):** a real-YAML probe admitted ID-named jobs and
**refused** real-named jobs with *"contains no job named 'sast-scan' … The run concluded success"*,
listing the real names it found. Not re-run — blobs identical, so a rerun adds nothing.

### 4.2 Proposed diff — YAML-derived identity (P18-3)

```diff
--- a/staging/phase18_release_gate.py
+++ b/staging/phase18_release_gate.py
@@
-#: Workflows whose success is mandatory, and the jobs within each that must individually have
-#: concluded `success`. An empty job tuple means "run-level only" and is recorded explicitly
-#: rather than left implicit. Job names must match the API's `name`, i.e. the `name:` field if
-#: present, otherwise the job id.
-REQUIRED_WORKFLOWS: dict = {
-    "05 Security": ("sast-scan", "container-security", "iac-scan"),
-    "01 PR Check": ("static-audit", "unit-tests", "paper-mode-tests", "database-tests",
-                    "docker-check", "frontend-tests"),
-}
+#: Workflows whose success is mandatory, keyed by workflow FILE, with the mandatory YAML job
+#: IDs. The expected API display name is resolved from the workflow YAML AT THE CANDIDATE SHA
+#: (`job_spec.get("name", job_id)`) — never assumed equal to the id, and never taken from the
+#: current default branch. P18-3: every job in this repository declares an explicit `name:`,
+#: so comparing ids against API names refuses every successful run.
+REQUIRED_WORKFLOWS: dict = {
+    ".github/workflows/05-security.yml": (
+        "sast-scan", "container-security", "iac-scan"),
+    ".github/workflows/01-pr-check.yml": (
+        "static-audit", "unit-tests", "paper-mode-tests", "database-tests",
+        "docker-check", "frontend-tests"),
+}
+
+#: Display name of each workflow, for diagnostics and run matching.
+WORKFLOW_DISPLAY_NAMES: dict = {
+    ".github/workflows/05-security.yml": "05 Security",
+    ".github/workflows/01-pr-check.yml": "01 PR Check",
+}
```

and the new resolver plus a distinct refusal class, so identity failure is never confused with a
failed job:

```diff
+class IdentityUnresolved(GateRefusal):
+    """The expected job identity could not be established. Distinct from a failed job."""
+
+
+def _workflow_yaml_at(repo, path, sha, token):
+    """Workflow YAML AT THE CANDIDATE SHA. Fail closed on any ambiguity."""
+    import base64
+    try:
+        payload = _api_get("/repos/%s/contents/%s?ref=%s" % (repo, path, sha), token)
+    except urllib.error.HTTPError as exc:
+        raise IdentityUnresolved(
+            "Could not fetch %s at %s (HTTP %s). Refusing: without the definition the "
+            "expected job identities are unknown." % (path, sha[:8], exc.code)) from exc
+    if payload.get("encoding") != "base64" or not payload.get("content"):
+        raise IdentityUnresolved("Unexpected contents payload for %s at %s." % (path, sha[:8]))
+    try:
+        doc = yaml.safe_load(base64.b64decode(payload["content"]).decode("utf-8"))
+    except Exception as exc:
+        raise IdentityUnresolved("Could not parse %s at %s: %s" % (path, sha[:8], exc)) from exc
+    jobs = (doc or {}).get("jobs")
+    if not isinstance(jobs, dict) or not jobs:
+        raise IdentityUnresolved("%s at %s declares no jobs." % (path, sha[:8]))
+    return jobs
+
+
+def _expected_names(jobs_yaml, mandatory, path, sha):
+    """job_id -> expected API display name, with duplicate detection."""
+    expected, seen = {}, {}
+    for jid in mandatory:
+        if jid not in jobs_yaml:
+            raise IdentityUnresolved(
+                "Mandatory job id %r is not defined in %s at %s. The policy and the workflow "
+                "have drifted. Refusing. (defined: %s)"
+                % (jid, path, sha[:8], sorted(jobs_yaml)))
+        name = (jobs_yaml[jid] or {}).get("name", jid)
+        if name in seen:
+            raise IdentityUnresolved(
+                "Jobs %r and %r in %s both display as %r; the API response would be "
+                "ambiguous. Refusing." % (seen[name], jid, path, name))
+        seen[name] = jid
+        expected[jid] = name
+    return expected
```

`_assert_mandatory_jobs_passed()` then takes `expected` and looks up `expected[jid]`, reporting both
identifiers: `"mandatory job %r (expected API name %r)"`. **All existing refusal paths are
preserved** — exact-SHA binding, branch allow-list, bounded wait, API-error refusal, empty-policy
refusal, no override.

### 4.3 Proposed diff — real pagination (P18-2)

Current `_get_list()` refuses when a listing fills a page. Fail-closed but brittle. Replacement
follows pages within a bound and refuses only when the bound is exceeded:

```diff
-    if len(items) >= _PER_PAGE:
-        raise GateRefusal(
-            "Pagination is ambiguous for %s: %d entries fills the page, so more may exist "
-            "on a page that was not fetched. Refusing." % (what, len(items)))
-    return items
+    page, collected, seen_pages = 1, list(items), {1: _fingerprint(items)}
+    while len(items) >= _PER_PAGE:
+        page += 1
+        if page > _MAX_PAGES:
+            raise GateRefusal(
+                "%s exceeded %d pages; refusing rather than reasoning from a truncated "
+                "listing." % (what, _MAX_PAGES))
+        payload = _api_get(_with_page(path, page), token)
+        items = payload.get(key) or []
+        fp = _fingerprint(items)
+        if fp in seen_pages.values():
+            raise GateRefusal("%s returned a repeated page at %d; cursor loop. Refusing."
+                              % (what, page))
+        seen_pages[page] = fp
+        collected.extend(items)
+    return collected
```

### 4.4 The contract test that replaces the masking fixtures

The existing fixture derives the API name from the policy (`phase18_test_release_gate.py:49-63`),
which is why 28 tests pass against a broken contract. **Renaming fixtures to the new expected
strings would reproduce the blind spot.** The replacement derives expectations from the YAML:

```python
# tests/test_release_gate_identity_contract.py   (NEW — needs its own grant)
"""The expected API job names come from the workflow YAML, never from the gate's policy."""
import pathlib
import pytest
import yaml

from scripts import release_gate as gate   # post-promotion import path

WF = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _jobs(path):
    return (yaml.safe_load((WF.parent.parent / path).read_text(encoding="utf-8")) or {})["jobs"]


@pytest.mark.parametrize("wf_path", sorted(gate.REQUIRED_WORKFLOWS))
def test_every_mandatory_job_id_exists_in_the_real_workflow(wf_path):
    jobs = _jobs(wf_path)
    missing = [j for j in gate.REQUIRED_WORKFLOWS[wf_path] if j not in jobs]
    assert not missing, f"{wf_path}: policy names jobs absent from the YAML: {missing}"


@pytest.mark.parametrize("wf_path", sorted(gate.REQUIRED_WORKFLOWS))
def test_expected_api_names_are_derived_from_yaml_not_from_policy(wf_path):
    jobs = _jobs(wf_path)
    expected = gate._expected_names(jobs, gate.REQUIRED_WORKFLOWS[wf_path], wf_path, "0" * 40)
    for jid, name in expected.items():
        assert name == jobs[jid].get("name", jid)
    # The P18-3 regression: in THIS repository every job renames itself, so an
    # implementation that compared ids with API names would resolve zero of them.
    assert all(n != j for j, n in expected.items()), (
        "A job id now equals its display name; this test no longer proves the mapping. "
        "Add a job that renames itself, or assert the mapping another way.")


def test_a_renamed_job_is_detected(tmp_path):
    """Changing a YAML `name:` must change the expected identity, not silently pass."""
    wf = sorted(gate.REQUIRED_WORKFLOWS)[0]
    jobs = _jobs(wf)
    jid = gate.REQUIRED_WORKFLOWS[wf][0]
    mutated = {**jobs, jid: {**(jobs[jid] or {}), "name": "RENAMED SENTINEL"}}
    assert gate._expected_names(mutated, (jid,), wf, "0" * 40)[jid] == "RENAMED SENTINEL"


def test_duplicate_display_names_are_refused():
    wf = sorted(gate.REQUIRED_WORKFLOWS)[0]
    ids = gate.REQUIRED_WORKFLOWS[wf][:2]
    clash = {i: {"name": "SAME"} for i in ids}
    with pytest.raises(gate.IdentityUnresolved):
        gate._expected_names(clash, ids, wf, "0" * 40)


def test_policy_drift_against_the_real_workflow_is_refused():
    wf = sorted(gate.REQUIRED_WORKFLOWS)[0]
    with pytest.raises(gate.IdentityUnresolved):
        gate._expected_names(_jobs(wf), ("job-that-does-not-exist",), wf, "0" * 40)


def test_right_name_wrong_conclusion_is_still_refused():
    """A matching display name must not rescue a skipped job."""
    wf = sorted(gate.REQUIRED_WORKFLOWS)[0]
    jid = gate.REQUIRED_WORKFLOWS[wf][0]
    expected = gate._expected_names(_jobs(wf), (jid,), wf, "0" * 40)
    api_jobs = [{"name": expected[jid], "conclusion": "skipped", "status": "completed"}]
    with pytest.raises(gate.GateRefusal) as exc:
        gate._assert_mandatory_jobs_passed(api_jobs, wf, (jid,), expected, 1)
    assert "skip is not a pass" in str(exc.value)
```

Plus the §4.3 pagination cases (empty, exactly one full page, multiple pages, missing next page,
API error on a later page, duplicate run IDs, bound exceeded) and a test asserting
`ADVISORY_JOBS ∩ mandatory = ∅` so a new job cannot silently become either.

---

## 5. Lane C — candidate-SHA architecture: OWNER DECISION REQUIRED

`CURRENT SOURCE` event-to-SHA matrix:

| Event | Workflow | SHA recorded against | Usable for the deployed SHA? |
|---|---|---|---|
| `pull_request` | `01 PR Check` | **PR head** | **No** for a post-merge `main` SHA |
| `push` → `main` | `02 Build`, `05 Security` | the pushed SHA | yes |
| `workflow_run` | `03 Deploy` | `workflow_run.head_sha` (pinned) | yes |
| `workflow_dispatch` | `03 Deploy` | `github.sha` of the ref | yes |
| `workflow_call` | `02 Build` declares it | — | **no caller exists** |

| Option | Exact-SHA | Cost |
|---|---|---|
| **A** PR / merge-queue | **weak as-is** — head ≠ merge SHA | needs merge queue or squash/FF so head ≡ deploy SHA |
| **B** dedicated push correctness workflow | **strong** | must enumerate sufficient jobs; two pipelines to keep aligned |
| **C** extend `01 PR Check` to `push` | **strong** | runs **`provisioning-and-isolation`** (provisions a DB) on every push; duplicate runs |
| **D** other | — | none found |

**Not selected** — it changes release policy, required checks and infrastructure cost. No trigger
broadened; no permissive fallback added. **P18-1 and P18-3 must be fixed together**: identity alone
leaves the trigger deadlock; trigger alone leaves a gate that refuses every successful run.

---

## 6. Lane P — image provenance: proposed diffs

**Grant needed:** `02-build.yml`, `03-deploy.yml`, `ecs_deploy_and_diagnose.py`, `terraform/ecs.tf`,
`ecs-task-definition-full.json`.

### 6.1 Chain status (REUSED EVIDENCE, blobs identical)

| Edge | Status |
|---|---|
| SHA → build | present, **ungated** |
| build → push | pushes SHA tag **and `:latest`** |
| push → digest | **LATENT/ORPHANED** — `02-build.yml:78` resolves, `:81` exports; **no caller** for the reusable workflow and `workflow_run` carries no job outputs |
| digest → scan of that digest | **BROKEN** — `05 Security` scans a local `trading-platform:scan` build |
| scan → SHA+digest binding | **ABSENT** |
| evidence → deploy input | **verified then discarded** — `03-deploy.yml:162-174` genuinely **fail-closed**; `$DIGEST` step-local |
| deploy input → task definition | **BROKEN** — `ecs_deploy_and_diagnose.py:94` default `latest` |
| Terraform declared state | **BROKEN** — 6 definitions on `:latest` |
| root JSON | **BROKEN, reachable** — `:18` `:latest`; runbook registers it |
| Kubernetes | **BROKEN**, status **UNKNOWN** |
| ECR immutability | **UNKNOWN** — 0 `image_tag_mutability` matches; registry default is not proof |
| runtime identity | **UNKNOWN** — needs authorized cloud evidence |

### 6.2 Diff — build digest fail-closed

```diff
--- a/.github/workflows/02-build.yml
+++ b/.github/workflows/02-build.yml
@@
-          DIGEST=$(aws ecr describe-images --repository-name $ECR_REPOSITORY --image-ids imageTag=$IMAGE_TAG --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || echo "$IMAGE_TAG")
-          
-          echo "image_tag=$IMAGE_TAG" >> $GITHUB_OUTPUT
-          echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
+          # A tag is NOT a digest. Never substitute one for the other: a consumer that
+          # trusts `image_digest` would be comparing a mutable alias.
+          DIGEST=$(aws ecr describe-images \
+            --repository-name "$ECR_REPOSITORY" \
+            --image-ids imageTag="$IMAGE_TAG" \
+            --query 'imageDetails[0].imageDigest' --output text)
+          if [ -z "$DIGEST" ] || [ "$DIGEST" = "None" ]; then
+            echo "::error::No digest returned for $ECR_REPOSITORY:$IMAGE_TAG"; exit 1
+          fi
+          case "$DIGEST" in
+            sha256:*) : ;;
+            *) echo "::error::Not a digest: $DIGEST"; exit 1 ;;
+          esac
+          echo "image_tag=$IMAGE_TAG" >> $GITHUB_OUTPUT
+          echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
```

Note the `2>/dev/null` is removed so the AWS error surfaces, and `set -e` is no longer relied on for
a command inside `$( )`.

### 6.3 Diff — deploy exports the digest it already proved

```diff
--- a/.github/workflows/03-deploy.yml
+++ b/.github/workflows/03-deploy.yml
@@
       - name: Verify ECR Immutable Artifact Exists for Commit SHA
+        id: verify-artifact
         env:
@@
           echo "✅ Verified ECR Image Artifact: $ECR_REPOSITORY:$IMAGE_TAG (Digest: $DIGEST)"
+          echo "image_digest=$DIGEST" >> "$GITHUB_OUTPUT"
 
       - name: Execute Automated Deployment & Self-Healing Diagnostic Engine
         env:
           ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
-          IMAGE_TAG: ${{ github.event.workflow_run.head_sha || github.sha }}
+          IMAGE_DIGEST: ${{ steps.verify-artifact.outputs.image_digest }}
         run: python scripts/ecs_deploy_and_diagnose.py
```

### 6.4 Diff — deploy helper requires a digest

```diff
--- a/scripts/ecs_deploy_and_diagnose.py
+++ b/scripts/ecs_deploy_and_diagnose.py
@@
-IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")
+#: No default. A missing identity must refuse, not silently deploy whatever `latest`
+#: currently resolves to.
+IMAGE_DIGEST = (os.getenv("IMAGE_DIGEST") or "").strip()
@@
 def main() -> None:
-    image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"
+    if not IMAGE_DIGEST.startswith("sha256:"):
+        print("DEPLOY REFUSED: IMAGE_DIGEST must be a sha256 digest; got %r." % IMAGE_DIGEST)
+        sys.exit(1)
+    image_uri = f"{ECR_REGISTRY}/vyomquant-api@{IMAGE_DIGEST}"
     success = execute_deployment(image_uri)
```

**Caller sweep required with the grant:** `03-deploy.yml` (above) and any runbook passing
`IMAGE_TAG`. Tests: unset, `""`, `"  "`, `"latest"`, a SHA-shaped tag, and a valid digest ⇒ only the
last proceeds; the rendered URI must be `repository@sha256:…`.

---

## 7. Lane S — security workflow: proposed diffs

**Grant needed:** `05-security.yml`. **Safety's blocking/advisory policy is an owner decision** and
is deliberately *not* decided here — the diff below fixes **reachability and evidence only**, and
preserves the current declared non-blocking behaviour.

```diff
--- a/.github/workflows/05-security.yml
+++ b/.github/workflows/05-security.yml
@@
       - name: Run Bandit Scan
+        id: bandit
         run: bandit -r backend_app -ll -f json -o reports/bandit-report.json
 
       - name: Run Safety Check
+        # Independent of Bandit's result: previously, a Bandit failure skipped this step
+        # entirely, so the dependency audit never ran at all.
+        if: always()
         run: safety check --json --output reports/safety-report.json || echo "Safety advisory logged"
 
       - name: Upload Security Reports
+        # Evidence is most needed when a scan fails. This does NOT make the job succeed.
+        if: always()
         uses: actions/upload-artifact@v4
         with:
           name: security-scan-reports
           path: reports/
+          if-no-files-found: warn
@@
       - name: Trivy IaC scan — terraform/ (blocking, CRITICAL)
+        id: iac-terraform
         uses: aquasecurity/trivy-action@v0.36.0
@@
       - name: Trivy IaC scan — aerora_quant_platform/infrastructure/ (blocking, CRITICAL)
+        # Must run even when the first root fails, or its findings are invisible and
+        # "zero findings" is indistinguishable from "never scanned".
+        if: always()
         uses: aquasecurity/trivy-action@v0.36.0
```

`if: always()` on a *scan* step preserves its non-zero exit, so the job still fails — it only stops
a failure in root 1 from hiding root 2. The second root
(`aerora_quant_platform/infrastructure/terraform/main.tf`, 178 lines) declares an **S3 backend** and
an **`aws_secretsmanager_secret`**, and the step specifies `scanners: 'misconfig,secret'`.

**Left for the owner:** whether `|| echo "Safety advisory logged"` should become blocking. Both
execution defects above need fixing either way; the policy only decides the failure semantics.

---

## 8. Lanes F, K, D7, W-6, T, Redis, Terraform

| Lane | Status | Position / proposed change |
|---|---|---|
| **F** frontend gate | **NOT AUTHORIZED** | `06-frontend-deploy.yml` is `on: push` with **no `workflow_run`, no `needs:`**, publishing via 3 × `s3 sync --delete` + `/*` invalidation. Fix: a gate job calling the verifier with `ref:` pinned to the candidate SHA, every publish step behind `needs:`, credentials acquired after the gate. `tests/test_release_artifacts.py` (**8 passed**, REUSED) already proves gate-before-sync ordering and is the pattern to extend — it is **not** release authorization. Edge bytes for `ecd23a9e`: **UNKNOWN** |
| **K** `scripts/deploy.sh` | **NOT AUTHORIZED**, status **UNKNOWN** | `IMAGE_TAG=${2:-latest}`; `kubectl set image … backend=$IMAGE_TAG` passes a **bare tag** with no registry/repository; the health loop `break`s on success but **cannot fail** after five failures; unquoted `export $(grep … \| xargs)`. Fix: require a digest, build the full URI, `exit 1` after exhausted retries, quote the env load. **`blue-green-deploy.sh` rollback/promotion NOT RE-AUDITED** |
| **D7-1** | **NOT AUTHORIZED** + policy | Three `access_token=` callers vs `load_decrypted_keys(self, user_id, exchange_id)`; `exchange.py:993` correct. Market route's broad `except` can turn a vault/auth failure into silent anonymous access. **Keys-absent policy not invented** |
| **D6-D / D7-2** | **NOT AUTHORIZED** + evidence | `portfolio_cache_updater.py:151` imports non-existent `backend_app.backend.key_manager` **outside** the inner `try`, failing deterministically every cycle while health reports `ACTIVE`. No speculative class; no `APIKeyVault()` with guessed config. Needs **redacted boot evidence** |
| **W-6** | **CONFIRMED classification-only** | `except <Type>` → 0; `error_code ==` → 0; `.retryable` read in exactly one log f-string; `replay_safe_transaction_guard` re-derives independently. **No retries wired to financial side effects; `UNKNOWN` semantics untouched** |
| **T** tenant | **BLOCKED · NOT CERTIFIED** | PostgreSQL closed. Nine RLS policies on `app.current_tenant_id`; **`FORCE ROW LEVEL SECURITY` only in a SQL comment**; `dependencies.py` has **0** tenant-context references; two GUC setters both off the request path; fallback role owns the tables ⇒ policies **inert** on the ORM path; `tenant_db_context.py` correct and **unwired**. **FORCE RLS not enabled; no matrix cell, assertion or anti-vacuity check altered** |
| **Redis / workers** | **BLOCKED** | Redis closed; **skips are not passes**. `ExchangeReconciliationService` and `MarketplaceExpiryWorker`: **0 references in `main.py`**. No worker activated |
| **Bandit** | **REUSED EVIDENCE** | Blobs, `bandit` 1.9.4, command and config unchanged ⇒ not re-run: **9 × B608 MEDIUM / exit 1** tree-wide across three Alembic migrations vs **0 / exit 0** single-module; negative control **PASS**; 2 stale `# nosec B608` in `backend_app/test_perf.py`. No suppression added or removed |
| **Terraform** | **BLOCKED** | `trivy`/`docker`/`terraform` absent — no scan, `fmt -check`, `validate`, `plan`; **no result claimed**; no `init` against the S3 backend. **AWS-0054 false-positive analysis and the withdrawn VPC-collision finding preserved** |

---

## 9. Commands executed

| Command | Class | Result |
|---|---|---|
| `git rev-parse` / `rev-list` / `status --porcelain` / `diff --cached` / `stash list` / `worktree list` / `reflog -1` | TESTED NOW | **PASS** — §1 |
| `git hash-object` ×8 | TESTED NOW | **PASS** — §2 baseline |
| ledger field parse (`AP-W1D-1…5`) | TESTED NOW | **PASS** — all NOT GRANTED |
| `Test-Path` P38 destination | TESTED NOW | **PASS** — absent |
| targeted reads: `phase18_release_gate.py:57-80`, `ecs_deploy_and_diagnose.py:88-99` | TESTED NOW | **PASS** — exact patch context |
| Phase 37B real-YAML probe; suites 28/42/8; Bandit 9×B608 | **REUSED EVIDENCE** | identical blobs, tool versions, commands, configuration |

**Tests executed against repository targets: none.** No lane is authorized, so there was no change
to validate; running unchanged suites would not be evidence about unchanged source. Trivy / Docker /
Terraform / PostgreSQL / Redis: **BLOCKED**. CI dispatch: **NOT RUN**. No skipped, blocked or unrun
check is reported as a pass; no mocked data is presented as GitHub API evidence; no diff in this
document has been applied.

---

## 10. Minimum remaining grants and owner decisions

| # | Need | Owner | Unblocks |
|---|---|---|---|
| **1** | **Candidate-SHA architecture A/B/C/D** (§5) | release owner | Lane R end to end |
| **2** | Grant `staging/phase18_release_gate.py` + its tests for the **three-part** repair (§4) | release owner | §4.2–4.4 are written and applyable |
| **3** | Grant `02-build.yml`, `03-deploy.yml`, `ecs_deploy_and_diagnose.py` (§6.2–6.4) | release owner | Binds the digest end to end |
| **4** | Grant `05-security.yml` (§7) + **Safety policy** | release owner | Restores three checks; policy decides semantics |
| **5** | ECR `image_tag_mutability`, or the live setting | infrastructure owner | The one unknown that provenance cannot close without |
| **6** | Is the Kubernetes / blue-green path live? | platform owner | Whether §8 Lane K is live exposure |
| **7** | Grant `terraform/ecs.tf` + `ecs-task-definition-full.json` | infrastructure owner | 7 remaining `latest` consumers |
| **8** | Grant `06-frontend-deploy.yml` | release owner | Frontend gate |
| **9** | Keys-absent policy; vault boot evidence | product/security; platform owner | D7-1 / D7-2 |
| **10** | Disposable PostgreSQL + Redis; Trivy/Docker | CI / platform owners | Every certification lane |
| **11** | **An individual named against at least one owner role** | project decision-maker | **Prerequisite for 1–9** — every ledger row names a role, never a person (§3) |

---

## 11. Operations explicitly not performed

stage · commit · push · reset · stash/pop/drop · clean · revert · merge · rebase · branch switch ·
history rewrite · worktree mutation · CI dispatch · PR · repository-settings or branch-protection
change · asset publication · CloudFront invalidation · DNS change · ECR mutation ·
task-definition registration · deploy · rollback · Terraform apply/init · infrastructure
provisioning · production-database mutation · `kubectl` · exchange order · live-trading enablement ·
worker activation · secret/token/DSN exposure · threshold lowering · suppression addition ·
`continue-on-error` or `|| true` insertion · fixture renaming to mask a contract.

---

## 12. Status matrix

| Dimension | Status |
|---|---|
| Source modifications | **NOT AUTHORIZED** (0 files) |
| Release-gate repair | **NOT AUTHORIZED** — diffs written, pinned, unapplied |
| Candidate-SHA architecture | **OWNER DECISION REQUIRED** |
| Verifier identity contract | **FAIL** (P18-3, demonstrated Phase 37B) — repair specified |
| Verifier pagination | **FAIL** (P18-2) — repair specified |
| Verifier test contract | **FAIL** — fixtures mask it; replacement test written |
| Image provenance | **NOT ESTABLISHED** — 5 broken, 1 latent, 2 unknown |
| Security workflow completeness | **FAIL** — diffs written for reachability + evidence; Safety policy **OWNER DECISION REQUIRED** |
| Frontend publication safety | **FAIL** — edge bytes **UNKNOWN** |
| Kubernetes deployment | **UNKNOWN** |
| ECS deployment | **PARTIAL** — digest-existence fail-closed; deploys by tag |
| Tenant isolation | **BLOCKED / NOT CERTIFIED** |
| Live trading | **NOT CERTIFIED** |
| Test coverage / execution | **PARTIAL** — 70 maintained tests outside CI; 28 validate a masked contract |
| Infrastructure validation | **BLOCKED** |
| Bandit | **REUSED EVIDENCE** — 9 findings stand |

**A unified diff in this document is a specification, not an implementation. A mocked pass is not
deployment proof. A static RLS review is not tenant-isolation certification.**
