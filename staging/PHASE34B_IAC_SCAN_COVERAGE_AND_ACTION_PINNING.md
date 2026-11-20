# Phase 34B — IaC Scan Coverage Resolved, and a Supply-Chain Exposure in Workflow Pinning

**Destination verified ABSENT before writing.** Supplement to
`staging/PHASE34_CONTINUOUS_BLUE_GREEN_AND_RELEASE_RECOVERY.md`, which remains final and
unmodified. **0 production files · 0 workflows · 0 tests · 0 Terraform · 0 governance records ·
0 approvals · 0 CI dispatched · 0 cloud mutations.** New files are `staging/phase34b_*`.

> ## Why this exists
>
> I set out to close one loose end I created in Phase 34 §5 — whether `terraform/redis-only` is
> actually scanned. It resolves cleanly (§1). But verifying it surfaced two things I was not
> looking for, one of which is the most directly actionable security finding in several phases
> and is **not blocked by the authorization deadlock**:
>
> **1. IaC coverage is intact but rests on an undeclared default.** All three Terraform roots fall
> inside the scanned trees. However the steps use `scan-type: fs` with **no `scanners:` input**,
> and Trivy's own documentation states misconfiguration detection is **not enabled by default** for
> `fs`. Coverage therefore depends on the *action's* default contradicting the *CLI's* default.
>
> **2. Every workflow action reference is tag-pinned — 52 of 52, zero SHA-pinned** — including
> **4 × `aquasecurity/trivy-action@v0.36.0`** and **2 × `dtolnay/rust-toolchain@stable`** (a
> branch). Public reporting describes a **March 2026 compromise of `aquasecurity/trivy-action` in
> which version tags were force-pushed** to inject a credential stealer into pipelines that
> referenced it by tag.
>
> **No evidence suggests this repository was compromised**, and I am not claiming it was. The
> finding is about posture: tag pins offer no integrity guarantee against tag mutation.

---

## 1. Phase 34 §5 UNKNOWN → RESOLVED: all three roots are scanned

### 1.1 The roots, measured

Directories containing `*.tf`, enumerated:

| Root | Nesting |
|---|---|
| `terraform` | top level |
| `terraform/redis-only` | **inside `terraform/`** |
| `aerora_quant_platform/infrastructure/terraform` | **inside `aerora_quant_platform/infrastructure/`** |

Exactly three, matching the historical inventory.

### 1.2 The scan steps, read from `05-security.yml`

| Step | scan-type | scan-ref | severity | exit-code |
|---|---|---|---|---|
| 2 — `terraform/` (blocking) | `fs` | `terraform` | CRITICAL | **1** |
| 3 — `aerora_quant_platform/infrastructure/` (blocking) | `fs` | `aerora_quant_platform/infrastructure` | CRITICAL | **1** |
| 4 — report (non-blocking) | `fs` | `terraform` | CRITICAL,HIGH,MEDIUM | 0 |

No `skip-dirs` or `skip-files` input on any step.

### 1.3 Recursion

Trivy's Terraform coverage documentation states that it recursively searches directories and scans
all Terraform files it finds, and its file-selection documentation describes traversing all
directories in a target by default
([Terraform coverage](https://trivy.dev/docs/latest/guide/coverage/iac/terraform/),
[selecting files](https://trivy.dev/docs/dev/docs/configuration/skipping/)).
*Content was rephrased for compliance with licensing restrictions.*

Therefore `scan-ref: terraform` covers **`terraform/` and `terraform/redis-only/`**, and
`scan-ref: aerora_quant_platform/infrastructure` covers the nested `terraform/` beneath it.

**Phase 34 §5's "`terraform/redis-only` scan coverage UNKNOWN" is RESOLVED → COVERED**, and any
earlier suggestion that a third root goes unscanned should be retired. Two blocking steps with
`exit-code: 1` cover all three roots, and because they are separate steps a failure in the first
does not prevent the second from running — the independence requirement is already satisfied.

### 1.4 But the misconfiguration scanner is not declared

Trivy's documentation is explicit that misconfiguration detection is **not** enabled by default in
the `image`, `fs` and `repo` subcommands, and must be requested via `--scanners misconfig`
([misconfiguration overview](https://trivy.dev/docs/latest/guide/scanner/misconfiguration/)).
*Rephrased for compliance.*

These steps use `scan-type: fs` and declare **no `scanners:` input**. Misconfiguration scanning is
nonetheless clearly running — prior phases recorded 5 CRITICAL `AWS-0054`/`AWS-0104` findings from
this job, which are misconfiguration rule IDs. The only explanation is that
`aquasecurity/trivy-action@v0.36.0` defaults its `scanners` input to include `misconfig`, i.e. the
action's default contradicts the CLI's default.

**What that means, stated conservatively.** This is **not a present coverage gap** — misconfig
demonstrably ran. It is a **latent silent-coverage-loss risk**: a job named *"Trivy IaC
Misconfiguration & Secret Scan"* obtains its headline capability from an undeclared third-party
default. If that default changes, or the pin moves, misconfiguration scanning stops and **the job
still passes green**, because `exit-code: 1` only fires on findings and there would be none.

**I could not verify the action's default directly** — Trivy is not installed locally, and I did
not fetch the action's source. The inference rests on the documented CLI default plus the observed
findings. Classified **source-verified inference, not executed**.

Smallest fix, when `05-security.yml` is authorized: add `scanners: 'misconfig,secret'` explicitly to
the three IaC steps. One line each; it makes the job's name true by declaration rather than by
inheritance.

---

## 2. Workflow action pinning — 52 of 52 mutable

`staging/phase34b_action_pinning.py` → `staging/phase34b_action_pinning.txt`. Parses every `uses:`
across `.github/workflows/` and classifies the ref.

```
total references: 52
  TAG/BRANCH   52
  SHA           0
mutable refs: 52   of which THIRD-PARTY: 13
```

### 2.1 Third-party, mutable — 13 references

| Action | Refs | Where | Ref |
|---|---|---|---|
| `aws-actions/configure-aws-credentials` | **4** | `02-build.yml:52`, `03-deploy.yml:113,152`, `06-frontend-deploy.yml:255` | `v4` |
| **`aquasecurity/trivy-action`** | **4** | `05-security.yml:78,121,132,145` | `v0.36.0` |
| `aws-actions/amazon-ecr-login` | 2 | `02-build.yml:60`, `03-deploy.yml:160` | `v2` |
| **`dtolnay/rust-toolchain`** | 2 | `04-nightly-audit.yml:73`, `07-desktop-build.yml:173` | **`stable`** — a *branch* |
| `docker/setup-buildx-action` | 1 | `02-build.yml:63` | `v3` |

First-party, also tag-pinned: `actions/checkout@v4` ×17, `actions/setup-python@v5` ×12,
`actions/upload-artifact@v4` ×5, `actions/setup-node@v4` ×4, `actions/cache@v4` ×1.

### 2.2 Why this is worth raising now

A tag is a mutable pointer. Public reporting describes a **March 2026 supply-chain compromise of
`aquasecurity/trivy-action`** in which an attacker force-pushed the large majority of the action's
version tags to inject a multi-stage credential stealer, affecting a very large number of workflows
that referenced the action by tag; the initial foothold is reported to have come from a
`pull_request_target` workflow leaking a privileged token
([Endor Labs analysis](https://www.endorlabs.com/learn/github-actions-security-lessons-from-trivy),
[Phoenix Security write-up](https://phoenix.security/trivy-supply-chain-compromise-teampcp-weaponised-scanner-ongoing-attack/)).
*Content was rephrased for compliance with licensing restrictions; figures are as reported by those
sources and I have not independently verified them.*

This repository references that exact action **by tag, four times**, in the job that gates the
backend deploy.

**What I am NOT claiming.** I have no evidence that this repository was compromised. The CI runs
examined in earlier phases (10–11 October 2026) completed with ordinary outcomes, and I did not
inspect runner logs for indicators, which would in any case be outside read-only analysis. **This is
a posture finding, not an incident report.**

**Where the blast radius would be largest.** `aws-actions/configure-aws-credentials@v4` appears at
four sites, each immediately preceding steps that use real AWS credentials — ECR push in
`02-build.yml`, ECS deploy in `03-deploy.yml`, S3/CloudFront publication in `06-frontend-deploy.yml`.
`dtolnay/rust-toolchain@stable` is the most mutable form present: a branch reference, which can
change on any push to that branch.

### 2.3 Smallest mitigation

Replace each mutable ref with a full 40-character commit SHA, retaining the human-readable version
in a trailing comment:

```yaml
- uses: aquasecurity/trivy-action@<40-hex-sha>   # v0.36.0
```

Prioritise, in order: the 4 `configure-aws-credentials` sites (credential adjacency), the 4
`trivy-action` sites (reported tag-force-push incident), then `amazon-ecr-login`,
`rust-toolchain@stable`, `setup-buildx-action`, then the first-party `actions/*` refs.

**Not implemented.** `.github/workflows/**` is governed and unauthorized — the authoritative ledger
(blob `7e238620bc22f0ad246f0ebc373b7032c6b28ef0`) remains **0 `GRANTED` / 5 `NOT GRANTED`**, now
corroborated by `PHASE38` (0 bare `GRANTED`, 6 `NOT GRANTED`) as the ninth concurring session.
No workflow file was edited.

**This differs from every other open lane in one respect worth the owner's attention: it needs no
database, no Redis, no Trivy, no Terraform, no cluster, and no architectural decision. It is a
mechanical, reviewable, individually revertible edit.**

---

## 3. Integrity

| Check | Result |
|---|---|
| `HEAD` / tree / branch | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` / `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` / `main` — unchanged |
| Staged / unmerged | 0 / 0 |
| Governed files modified | **0** — `.github/`, `scripts/`, `terraform/`, `tests/`, `backend_app/`, `infra/` untouched except the other workstream's pre-existing `tests/test_drift_classification.py` |
| Ledger blob | `7e238620…` unchanged, not modified |
| Stashes / worktrees | 2 / 1 — intact |
| Phase 34 report | unmodified; this is a separate file |
| Secrets | none printed; `exchange_keys.json` **not read**; no AWS call made (`aws` CLI present, never invoked) |

**Files created — 3:** `phase34b_action_pinning.py`, `phase34b_action_pinning.txt`, this report.

**Classifications:** §1.1–1.3 **RESOLVED by documentation + measured layout**. §1.4
**source-verified inference, NOT executed** (Trivy absent). §2 **measured** (52/52 census) plus
**externally reported context** for the trivy-action incident, not independently verified.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**
