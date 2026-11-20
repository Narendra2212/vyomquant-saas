# ALGO22 Quant Platform — Security Review

- **Date:** 2026-10-03
- **Ruleset:** `.kiro/security-template.md` (11 principles, 30 constraints)
- **Baseline source:** not configured — bundled AWS guidance + starter packs used
- **Platform:** AWS
- **Scope reviewed:** `backend_app/**`, `algo22-terminal/src/**`, `terraform/**`,
  `aerora_quant_platform/infrastructure/**`, `.github/workflows/**`
  (excluded: `tests/**`, `node_modules/**`, venvs, `.hypothesis/**`, `*.tf.disabled`)
- **Fail condition:** any BLOCKER or HIGH
- **Verdict:** 🔴 **FAIL** — 9 FAIL, 6 PARTIAL, 1 NEEDS REVIEW, 14 PASS

---

## Findings

### Identity & Access

| ID | Constraint | Sev | Status | Evidence | Remediation |
|---|---|---|---|---|---|
| SC-1 | No wildcard identity policy | BLOCKER | PASS | `terraform/iam.tf` — both role policies scoped to `secretsmanager:GetSecretValue` on `vyomquant/*` and `ssm:GetParameters` on `/vyomquant/*`; no `Action:"*"`+`Resource:"*"` | — |
| SC-2 | No long-lived static credentials | BLOCKER | PASS | ECS task/execution roles assumed by `ecs-tasks.amazonaws.com`; no `AKIA…` or private key in tracked source (only `tests/` fixtures, out of scope) | — |
| SC-3 | MFA required for privileged human access | HIGH | **FAIL** | `backend_app/core/dependencies.py:422` defines `require_aal2`, and its own docstring says it "MUST be applied to sensitive endpoints". It is applied to **zero** endpoints. Admin routes use `get_operator_user` (role check only) | Apply `require_aal2` to admin routes, credential storage, order placement, kill-switch, billing changes |
| SC-4 | IaC IAM avoids wildcard actions/resources | HIGH | PARTIAL | `terraform/iam.tf:44-47` grants `kms:Decrypt` on `arn:aws:kms:<region>:<acct>:key/*` — every key in the account | Scope to the specific key ARNs backing the secrets, or use a key policy |

### Data Protection

| ID | Constraint | Sev | Status | Evidence | Remediation |
|---|---|---|---|---|---|
| SC-5 | Data stores encrypt at rest | HIGH | PASS | `terraform/redis.tf:22`; `terraform/cloudfront.tf:28` (S3 SSE); Supabase is managed-encrypted | — |
| SC-6 | Secrets from a vault at runtime | BLOCKER | PASS | `terraform/ecs.tf:40-70` — 7 secrets injected via `valueFrom` Secrets Manager ARNs. `backend_app/backend/api_key_vault.py:77-85` fails fast when `MASTER_ENCRYPTION_KEYS` is absent | — |
| SC-7 | TLS 1.2+ enforced, plaintext denied | HIGH | **FAIL** | `terraform/alb.tf:123` uses `ELBSecurityPolicy-2016-08`, which still negotiates TLS 1.0/1.1. `alb.tf:55-72` forwards plaintext `:80` straight to the app whenever `enable_custom_domain` is false — the default at `variables.tf:28` | Use `ELBSecurityPolicy-TLS13-1-2-2021-06`; make the 80→443 redirect unconditional |
| SC-8 | PII never in logs | BLOCKER | **FAIL** | `backend_app/routers/auth.py:129` logs the user's email on every `/api/auth/me`. `routers/admin.py:46-60` writes operator email to logs and to Redis list `audit:admin_actions` with no TTL or trim. `backend/alert_engine.py:267,422` log recipient addresses. `routers/billing.py:312` logs client IP. No global redaction filter — `backend/logging_config.py:427-448` passes `details` through verbatim; the Sentry scrubber at `backend/observability/sentry_config.py:27-47` covers Sentry events only | Remove the email from `auth.py:129`; install a `logging.Filter` that redacts email/IP/token keys; add a TTL + `ltrim` to the admin audit list |
| SC-9 | CMK encryption for sensitive data | HIGH | **FAIL** | No `aws_kms_key` resource in `terraform/**` or `aerora_quant_platform/infrastructure/**`. All encryption uses AWS-managed keys, so there is no key policy, rotation control, or usage audit you own | Create CMKs for the Redis, S3 and Secrets Manager material; reference them explicitly |
| SC-10 | PII masked in non-production | HIGH | NEEDS REVIEW | No non-prod data seeding or refresh pipeline found in the repo. Cannot determine how staging gets its data | Confirm how staging data is produced; if it is a prod copy, add masking |
| SC-11 | Retention/deletion policy enforced | MEDIUM | **FAIL** | No account-deletion or PII-retention endpoint in `backend_app/routers/**`. Only incidental TTLs exist (strategy audit 30 days, signal trace 1 hour). Profiles, exchange key rows and billing history have no expiry | Add an account-deletion path that purges `profiles`, `exchange_keys` and derived data; document retention |
| SC-12 | Access to personal data logged | HIGH | PARTIAL | Writers exist — `core/audit_trail.py:785+` (`StrategyAuditLogger`), `routers/admin.py:33-62`, `core/tenant_middleware.py:373-377`, `backend/execution_guard.py:1602+` — but they record **mutations and refusals only**; reads of personal data are unaudited (`routers/auth.py:123-135` writes no record). Storage is Redis lists + stdout, capped at 500 entries per resource and expired at 30 days (`audit_trail.py:789-793`); no durable table located | Persist audit records to an append-only table; add read-access records for personal data |
| SC-13 | No third-party PII egress without lawful basis | HIGH | PARTIAL | No user identifier is handed to analytics: nothing in `algo22-terminal/src/**` references `gtag`/`clarity`/`dataLayer`; both SDKs are loaded only by `algo22-terminal/index.html:100-141` and gated by an `isDummy` check. **But** Microsoft Clarity records sessions by default and there is no masking config (`data-clarity-mask`) anywhere, so on-screen PII — profile email, balances, ticket text — is within its default capture scope | Add Clarity masking on PII-bearing elements; confirm GA/Clarity are covered by the privacy policy |
| SC-14 | S3 Block Public Access + default encryption | BLOCKER | PASS | `terraform/cloudfront.tf:19-27` (all four BPA flags true), `:28-37` (SSE), `:58-75` (bucket policy restricted to the CloudFront OAC) | — |
| SC-15 | DB/volume/snapshot encryption declared | HIGH | PASS | `terraform/redis.tf:22-23` — at-rest and in-transit both true; same in `terraform/redis-only/main.tf:90-91,115-116` | — |

### Network & Infrastructure

| ID | Constraint | Sev | Status | Evidence | Remediation |
|---|---|---|---|---|---|
| SC-16 | WAF with managed rules on public endpoints | HIGH | PASS | `terraform/waf.tf:1` web ACL with `AWSManagedRulesCommonRuleSet`, `:39-41` associated to the ALB | — |
| SC-17 | No `0.0.0.0/0` except intended public ports | HIGH | PASS | `terraform/security_groups.tf:11,19` — ALB ingress is only 80/443; every other wide rule is egress | — |
| SC-18 | No `0.0.0.0/0` to sensitive ports | BLOCKER | PASS | `terraform/security_groups.tf` — ECS ingress restricted to the ALB SG; Redis 6379 restricted to the ECS SG. No 22/3389/3306/5432 exposure | — |
| SC-19 | Compute in private subnets | MEDIUM | PASS | `terraform/ecs.tf` — every service sets `assign_public_ip = false` and `subnets = aws_subnet.private[*].id` | — |

### Detection

| ID | Constraint | Sev | Status | Evidence | Remediation |
|---|---|---|---|---|---|
| SC-20 | Audit logging enabled, not disableable by app | HIGH | **FAIL** | No `aws_cloudtrail`, `aws_guardduty`, `aws_config` or `aws_securityhub` resource in any IaC directory. There is no control-plane audit trail: an IAM or security-group change leaves no record you own | Add an org/account CloudTrail to a dedicated bucket with log-file validation; enable GuardDuty |

### Application

| ID | Constraint | Sev | Status | Evidence | Remediation |
|---|---|---|---|---|---|
| SC-21 | No secrets committed | BLOCKER | PASS | `.env` is ignored (`.gitignore:18`) and untracked; `terraform.tfstate` ignored and untracked. Only `tests/` fixtures match key patterns. **Git history not scanned** | Add a history scan (gitleaks/trufflehog) to CI to close the gap |
| SC-22 | Dependencies pinned, no known criticals | HIGH | PASS | `requirements-base.txt` pins every package with `==` and carries CVE triage notes (PyJWT 2.14.0, cryptography 43.0.0, aiohttp 3.14.3, python-jose 3.4.0). CI runs bandit + safety (`05-security.yml:30-39`), Trivy gated at CRITICAL (`03-deploy.yml:50-58`), pip-audit + npm audit nightly (`04-nightly-audit.yml:52-69`) | — |
| SC-23 | Input validated, parameterized queries | BLOCKER | PARTIAL | Supabase PostgREST and SQLAlchemy paths use binding throughout. QuestDB's REST `/exec` has no parameter binding, so five sites interpolate — four are guarded by the `_safe_uid` allowlist (`backend/dashboard_aggregation_service.py:604-644`). **`:1563` interpolates `limit` with no `int()` cast or clamp**, unlike the equivalent at `:1619`. Separately, many endpoints accept raw `Dict[str, Any]` (`routers/strategies.py:1083,2473,3234,3303,3362`). No `eval`/`exec`/`pickle`/`yaml.load` anywhere; the strategy builder interprets a tree against a fixed operator whitelist (`backend/strategy_builder.py:29-35`) | Cast and clamp `limit` at `:1563`; add Pydantic models to the `Dict[str, Any]` endpoints |
| SC-24 | Output encoded to prevent XSS | HIGH | PARTIAL | One sink: `algo22-terminal/src/pages/TwoFA.jsx:449` `dangerouslySetInnerHTML` renders the Supabase MFA QR SVG. Server-controlled, not user-authored, but unsanitised — no DOMPurify in the frontend | Render the SVG via a blob URL in `<img>`, or sanitise |
| SC-25 | AuthN/AuthZ on every non-public endpoint | BLOCKER | PASS | 266 endpoints enumerated; 24 lack an auth dependency and each was verified intentional: health probes, login/register/google, signature-verified payment webhooks, public marketplace reads, FAQ/categories/plans/blocks, and three `*_blocked` 403 stubs (`routers/orders.py:726,739,757`). `strategy_operations.py:5964` authenticates via a signed ownership-bound token; `dag_tasks.py:297` fails closed and checks tenant. `/metrics` is unauthenticated but not internet-reachable — `terraform/alb.tf:132-178` routes only `/api/*` and `/ws/*`, so it never reaches the API target group | Consider an in-VPC control on `/metrics` for defence in depth |
| SC-26 | Security headers set | MEDIUM | PASS | `backend_app/main.py:175-188` — CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS `max-age=31536000; includeSubDomains` | — |
| SC-27 | Errors don't leak internals to clients | HIGH | **FAIL** | The global handler forwards `exc.detail` unchanged (`main.py:773-796` → `core/schemas.py:149-170`), so every `HTTPException(…, f"…{e}")` is echoed verbatim. Worst case `routers/exchange.py:213-215`: a ccxt error from a connection attempt made with the user's **just-submitted credentials** is reflected to the caller. Also `exchange.py:231-235` (Supabase/PostgREST error text), `auth.py:115,237,259`, `billing.py:2128,2451,2698,2746,2791`, `dag_tasks.py:465,503,585`, `paper_trading.py:236,269`, `backend/execution_router.py:49-50`. The catch-all itself is clean (`main.py:814-829`) but uses `print()`, bypassing formatters and the Sentry scrubber | Log `e` internally, return a static client message — copy the pattern already used at `routers/exchange.py:327-335` |
| SC-28 | Rate limiting on public endpoints | MEDIUM | PARTIAL | slowapi, Redis-backed, correctly fail-closed in production (`core/rate_limit.py:27-51`). Auth routes covered 5-30/min. **But** no `default_limits` is set, so any undecorated route is unlimited; `routers/strategies.py` `/optimize` `:3232`, `/monte-carlo` `:3301`, `/walk-forward` `:3360`, `/validate` `:2471` each run a full backtest with no per-minute ceiling (quota metering is not burst control); `routers/referral.py:41` builds a second in-memory `Limiter` that bypasses the Redis requirement | Set `default_limits`; decorate the four compute endpoints; delete the shadow limiter |
| SC-29 | IaC scanned, no secrets in templates | HIGH | **FAIL** | No `checkov`, `tfsec` or `terrascan` step in any of the six workflows in `.github/workflows/`. bandit/safety cover Python, Trivy covers the container image — nothing inspects Terraform. Every IaC finding in this report would have been caught by a scanner in CI | Add a checkov or tfsec job to `01-pr-check.yml` |
| SC-30 | Remote state encrypted and access-controlled | HIGH | **FAIL** | `terraform/providers.tf:11-16` — the S3 backend block is commented out, so state is a local file. `terraform.tfstate` is correctly gitignored and untracked, but local prod state has no encryption, no locking, no versioning, and no access control beyond the workstation's filesystem | Enable the backend with `encrypt = true` + DynamoDB locking; migrate with `terraform init -migrate-state` |

---

## Summary counts

| Status | Count |
|---|---|
| PASS | 14 |
| FAIL | 9 |
| PARTIAL | 6 |
| NEEDS REVIEW | 1 |
| **Total** | **30** |

By severity, among non-PASS: 2 BLOCKER (SC-8 FAIL, SC-23 PARTIAL), 12 HIGH, 1 MEDIUM, 1 HIGH-NEEDS-REVIEW.

---

## Priorities

1. **SC-8 — email in logs on every profile fetch** (BLOCKER). `routers/auth.py:129` is one line, and it runs on every `/api/auth/me`. Highest value per unit of effort in this report. The deeper issue is that redaction is per-site rather than a filter on the root logger.
2. **SC-27 — credential-bearing error reflected to the client** (HIGH). `routers/exchange.py:213-215` is the sharp edge: the exception comes from a live call made with the credentials the user just submitted, and third-party SDK errors often embed request context. The codebase already has the correct pattern 110 lines further down.
3. **SC-7 — TLS** (HIGH). Weak policy is unconditional in code; the plaintext listener depends on a variable that defaults to false.
4. **SC-20 / SC-29 — no control-plane audit trail, no IaC scanning** (HIGH, HIGH). These are the two that would have caught the rest of this list automatically. Fixing them changes the slope, not just the intercept.
5. **SC-3 — MFA enforced nowhere** (HIGH). The dependency is written and documented; it is simply not wired to anything.
6. **SC-30 — local Terraform state** (HIGH). Prod state on a workstation with no lock means two operators can corrupt it.
7. **SC-23 — `limit` interpolation** (BLOCKER, partial). One cast and clamp at `dashboard_aggregation_service.py:1563`.

## Watchlist notes (from the template)

- **TR-5 broker credentials:** encryption design is sound — `MultiFernet` with rotation, private `_encrypt`/`_decrypt`, ciphertext-only columns, and `list_exchanges` narrowed to `exchange_id, created_at` so ciphertext is never even selected. Two notes: the file documents itself as "AES-256" but Fernet is AES-128-CBC + HMAC-SHA256 (sound, mislabelled), and the `masked_key` at `routers/exchange.py:372` is synthesized from the exchange id, not the key — cosmetic, but it looks like a masked credential and is not one.
- **TR-6 order placement:** manual order endpoints are deliberately 403'd in ALGO-ONLY mode (`routers/orders.py:726-770`).

## Unverified

- Git history was not scanned for previously committed secrets (SC-21).
- Live value of `enable_custom_domain` is unknown; no `.tfvars` exists in the repo and `terraform.tfstate` was deliberately not read, as state files can contain secret material. Production may differ from the committed default (SC-7).
- No durable audit table was located, but `backend_app/migrations/**` was not read exhaustively (SC-12).
- Whether the frontend Sentry SDK sets a user context was not traced (SC-13).

---

# Run 2 — 2026-10-03, remediation applied

Findings above are left as originally recorded. This section states what changed since.

## Status changes

| ID | Sev | Was | Now | What changed |
|---|---|---|---|---|
| SC-7 | HIGH | FAIL | PASS | `terraform/alb.tf` — `ssl_policy` moved to `ELBSecurityPolicy-TLS13-1-2-2021-06` (TLS 1.3/1.2 only). A `lifecycle.precondition` on `aws_lb_listener.http` now refuses any apply where `environment` is prod/production and `enable_custom_domain` is false, which was the combination that served the whole API in clear text |
| SC-30 | HIGH | FAIL | PARTIAL | `terraform/providers.tf` — the S3 backend is declared with `encrypt = true` and DynamoDB locking. **Still PARTIAL: inert until the bucket and lock table exist and `terraform init -migrate-state` is run.** Prerequisites are documented in the file |
| SC-8 | BLOCKER | FAIL | PARTIAL | `backend_app/routers/auth.py` — the email address is gone from the `/api/auth/me` log line, replaced by the subject id with lazy `%s` formatting. **Still PARTIAL:** `routers/admin.py:46-60` (operator email, no-TTL Redis list), `backend/alert_engine.py:267,422` (recipient addresses) and `routers/billing.py:312` (client IP) are untouched, and there is still no root-logger redaction filter |
| SC-27 | HIGH | FAIL | PARTIAL | `backend_app/routers/exchange.py` — no response in this router interpolates an exception any more; see below. **Still PARTIAL:** `auth.py:115,237,259`, `billing.py` (5 sites), `dag_tasks.py` (3 sites), `paper_trading.py:236,269` and `backend/execution_router.py:49` continue to echo `str(e)` |

## SC-27: how the exchange router was changed

The router gained a classification layer rather than a flat message, because "verification failed"
cannot distinguish a wrong key from a key missing a permission from an IP that is not allowlisted
at the venue from a venue in maintenance — and those need four different actions from the trader.

Seven stable codes (`EXCHANGE_VERIFICATION_REJECTED`, `_FORBIDDEN`, `_THROTTLED`, `_UNREACHABLE`,
`_FAILED`, `EXCHANGE_UNSUPPORTED`, `EXCHANGE_KEY_STORAGE_FAILED`, plus
`EXCHANGE_NO_STORED_CREDENTIALS`) each map to a sentence this repository owns.
`_verification_refusal()` classifies against ccxt's exception hierarchy using an explicit ordered
table rather than an `except` chain, so the ordering is readable and checkable. The ordering was
verified empirically against the pinned ccxt 4.3.92:

```
PermissionDenied  < AuthenticationError  < ExchangeError   < BaseError
AccountNotEnabled < PermissionDenied
RateLimitExceeded < NetworkError         < OperationFailed < BaseError
OnMaintenance     < ExchangeNotAvailable < NetworkError
```

`RateLimitExceeded` descends directly from `NetworkError` in this version, not from
`DDoSProtection`, so it must be tested first. Narrow classes precede broad ones throughout.
Unclassified failures answer 502, not 400: a 400 would assert the caller's credentials were at
fault, and nothing established that.

Six raise sites changed. The provider and database text now goes to the operator log with
`exc_info=True`, and every refusal chains its cause with `from e`:

- `store_keys` — the connect/verify failure (was reflecting a ccxt message produced from the
  credentials in the request body) and the Supabase storage failure (was reflecting PostgREST
  text naming `exchange_keys` and its columns).
- `test_connection` — same exposure as `store_keys`, same fix.
- `test_stored_connection` — the vault-lookup 404 (status preserved; the frontend branches on it)
  and the connect failure.
- `delete_connection` and `get_exchange_auth_schema` — no credential in play, but both were
  echoing internal error text.

## A regression this introduced, and its fix

Making `detail` a structured object broke the client. `algo22-terminal/src/pages/ExchangeManager.jsx`
`sanitizeErrorMessage` only handled `typeof detail === "string"`, and `core/schemas.py:149`
passes a dict `detail` through unchanged, so every new refusal fell past the string branch into the
generic `"An error occurred. Please try again."` — the trader would have lost the actionable
sentence the server had just been taught to write.

An object branch was added ahead of the string branch, returning `detail.message` after applying
the same internal-leak guard (Traceback / SELECT / TypeError / 500 Internal) for defence in depth.
The string branch is byte-identical, because `exchange_manager_phase6.test.jsx` case 7 pins its
behaviour with a string input and never reaches the new branch.

Worth noting: that file's header already documented this exact server-side leak as a known finding
it had deliberately declined to patch client-side, on the grounds that the fix belonged in
`backend_app/` and was out of its spec's scope. The header now records the resolution instead.

## Verification

- `python -m py_compile` on both changed routers — clean. IDE diagnostics clean on all four files.
- Backend: 49 passed (`test_exchange_connection_security`, `_lifecycle`, `_connections_read_regression`,
  `crash_recovery/test_exchange_connection_disconnect`, `_vault_contract`, `test_exchange_security`);
  6 passed (`test_auth_logging_sanitization`, `test_exchange_credential_log_redaction`);
  389 passed (`test_phase7b_auth_remediation`, `test_bearer_auth_no_cookies`, `test_admin_auth`,
  `test_exchange_multitenancy`, `test_exchange_vault_singleton`, `test_log_redaction`).
- Frontend: `exchange_manager_phase6.test.jsx` 9/9. Guards plus `degradedSurfaces` 238/238 across
  13 files. The 6 unhandled async errors in `degradedSurfaces.test.jsx` were confirmed pre-existing
  by re-running that suite against the unmodified HEAD copy of `ExchangeManager.jsx` — same 6.
  They originate in a `useEntitlements` mock, not in this change.
- No test or frontend branch depended on any message string that was replaced (checked by grep
  across `tests/**` and `algo22-terminal/src/**`).

## Not verified

- `terraform validate` was not run: no terraform binary on this machine. Both IaC changes are
  reviewed by inspection only.
- The precondition's `error_message` is a literal rather than interpolated, because interpolation
  there requires Terraform 1.6 and `providers.tf` declares `>= 1.5.0`.

---

# Run 3 — 2026-10-03, detection and pipeline

## Status changes

| ID | Sev | Was | Now | What changed |
|---|---|---|---|---|
| SC-29 | HIGH | FAIL | PASS | `.github/workflows/05-security.yml` — new `iac-scan` job runs Trivy over `terraform/` and `aerora_quant_platform/infrastructure/` with the `misconfig` and `secret` scanners. CRITICAL blocks; a second non-blocking pass reports to MEDIUM and uploads the artifact |
| SC-20 | HIGH | FAIL | PASS | `terraform/cloudtrail.tf` — multi-region CloudTrail with `enable_log_file_validation`, `include_global_service_events`, a dedicated encrypted and versioned log bucket with Block Public Access, a deny-non-TLS bucket policy and lifecycle expiry. GuardDuty included behind `enable_guardduty`, default false |
| SC-3 | HIGH | FAIL | PARTIAL | `backend_app/core/dependencies.py` — `get_current_user` now propagates the `aal` claim, without which MFA enforcement is impossible. **Still PARTIAL: `require_aal2` remains applied to zero endpoints.** Wiring it is an access-control change with a lockout blast radius and is held for a decision |

## SC-3: the dependency was not merely unwired, it was non-functional

Run 1 recorded SC-3 as "`require_aal2` is defined and applied nowhere". That understated it.

`get_current_user` assembled a six-key dict — `id`, `email`, `tenant_id`, `access_token`,
`role`, `app_metadata` — and `aal` was not among them. `decode_token_local` returns the full
verified payload, so the claim arrived and was then discarded. `require_aal2` reads
`app_metadata.get("aal") or user.get("aal", "aal1")`:

- `app_metadata` is the wrong location. Supabase publishes `aal` as a **required top-level
  claim**; `app_metadata` carries provider data such as `{"provider": "email"}`
  ([Supabase JWT claims reference](https://supabase.com/docs/guides/auth/jwt-fields)).
- the top-level lookup found nothing, because the key was never copied onto the dict.

Both arms therefore missed and the function fell to its `"aal1"` default. Applied to any
endpoint, it would have returned 403 to **every** caller — including a user who had just
completed a TOTP challenge. The remediation for SC-3 as originally written would have been an
outage.

**Why three test suites missed it.** `test_phase7b_auth_remediation.py`,
`test_phase12a_production_security_validation.py` and `tests/regression/capture_baseline.py`
all invoke `require_aal2` with a hand-constructed user dict, so they exercise the gate's
decision table and never the path the claim travels. `create_test_jwt` in the phase12a suite
compounds it by writing `aal` into `app_metadata` as well as the top level — the fixture
diverges from a real Supabase token in precisely the field under test, and in the direction
that makes the gate look functional.

`tests/test_sc3_aal_claim_propagation.py` (8 tests) closes this by asserting through
`get_current_user` itself. One case, `test_the_refusal_is_not_universal`, exists specifically
to catch the "rejects everyone" mode that a negative-only suite reads as healthy. The tests
were confirmed non-vacuous: with the propagation line reverted, 6 of 8 fail, and the 2 that
still pass are the fail-closed cases — correctly, since without propagation everything
resolves to `aal1`.

Propagation changes no decision today. Nothing reads `aal`, because `require_aal2` is still
applied to zero endpoints. It is the prerequisite that makes enforcement possible rather than
an outage.

## Pipeline supply chain — found while fixing SC-29

`05-security.yml` ran `aquasecurity/trivy-action@master`: a mutable ref, so whatever that
branch pointed at executed in the job. In February 2026 the Trivy project's own repository was
compromised via a `pull_request_target` workflow and a privileged token extracted
([Endor Labs analysis](https://www.endorlabs.com/learn/github-actions-security-lessons-from-trivy)),
which makes "the upstream of my CI scanner is a trusted input" an assumption worth dropping.
Pinned to `v0.36.0`, as are the three new IaC steps. A tag can still be moved, but it is not
rewritten on every upstream merge. The workflow now has no action on `@master` or `@main`.

Note for the first run: this pins a Trivy version where the job previously tracked master, so
the vulnerability database and check set may differ from the last green run. The gate is
unchanged (CRITICAL, exit-code 1).

## Two deliberate choices worth challenging

**The IaC gate is CRITICAL, not HIGH**, even though this report's fail condition is
BLOCKER-or-HIGH. `03-deploy.yml:50-58` already documents CRITICAL-blocks/HIGH-reports for the
container scan, and a new scanner gated on HIGH would fail the pipeline on its first run over
pre-existing findings — which teaches people to bypass it. The non-blocking MEDIUM report
exists so the backlog is visible and can be ratcheted.

**The IaC scan is scoped to the IaC directories, not the repository.** A repo-wide secret scan
trips immediately on deliberate fixtures (`tests/test_credential_vault_strength.py` carries a
fake EC private key, `test_log_redaction.py` a PEM header). Repo-wide and git-history secret
scanning is still worth adding for SC-21, but it needs an ignore file written first.

## Baseline re-record

`tests/regression/test_baseline_unchanged.py::surface_exchange_connection` failed after the
Run 2 exchange changes — correctly. A field-level diff confirmed the only member that moved
was `raises`, on five handlers: the handler set (8), every handler's parameters, and every
response key set and type map were unchanged.

Re-recorded per the module's documented procedure, with a `_baseline_note_sc27_error_detail_shape`
member stating the authorisation. The pre-existing `_baseline_note_plan_ladder` was recovered
byte-exact from git and re-attached, since `capture_baseline.main` writes only what a capture
emits and a capture never emits a note. 60/60 baseline tests pass.

## Verification

- 442 tests pass across eleven suites (the SC-3 propagation suite, both auth-remediation
  suites, six exchange suites, two log-redaction suites). 60/60 regression baseline.
- The new workflow YAML parses, the `iac-scan` job has the two expected blocking steps, and
  no action in the file is on a mutable ref — asserted programmatically.
- `terraform/cloudtrail.tf`: braces balanced, every `var.*` and `local.*` reference declared,
  and 100 terraform addresses across `terraform/*.tf` with no duplicate — asserted
  programmatically. `data.aws_caller_identity.current` is reused from `iam.tf`, not redeclared.

## Not verified

- `terraform validate` still cannot run here (no terraform binary). The CloudTrail bucket
  policy follows AWS's documented two-statement form with `aws:SourceArn`; the trail ARN is
  composed as a string rather than referenced, which is what avoids the
  policy-needs-trail/trail-needs-policy cycle and the InsufficientS3BucketPolicyException.
  First apply is the real test.
- The Trivy IaC scan has not been run against this repository, so the size of the initial
  MEDIUM/HIGH backlog is unknown.

## Open decision — SC-3 enforcement

`require_aal2` now works but guards nothing. Wiring it is a product decision, not a code one:
every user without an enrolled factor gets 403 on whatever it protects. Its own docstring
nominates exchange API key mutation, admin endpoints, password change and billing mutations.
Held pending a decision on scope and rollout.
