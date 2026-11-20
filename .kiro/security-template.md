# Security Review Template

Kiro uses this file as the ruleset when you ask for a security review or the daily Red/Amber/Green review.
It layers **this project's own rules** on top of a well-architected security baseline for the platform below.

> **You don't edit the tables by hand.** Say `add a rule: <plain language>`, `list our rules`,
> `change SC-4 to a BLOCKER`, or `remove SC-7` and Kiro will handle IDs, categories, and severities.
> You can also jot rough rules in the **Quick Add** box and say "process quick add".

---

## Baseline Rules Source (governed externally)

<!--
Baseline rules are owned by a central security team and fetched fresh at review time so you always review
against the latest org standard. Point this at their file or repo. Left blank = use the power's bundled
AWS guidance + the packs merged below.
-->

- **baseline_source:** <not configured — using the power's bundled AWS guidance and starter packs>
- **Per-platform sources (optional):** n/a
- **offline_cache:** `.kiro/baseline-cache.md`

---

## Project Context

- **Application name:** ALGO22 Quant Platform
- **Platform(s):** aws
- **Environment(s):** prod, staging
- **Tech stack:** Python / FastAPI backend (`backend_app/`), React + Vite frontend (`algo22-terminal/`),
  PostgreSQL via Supabase, Redis (ElastiCache), Terraform IaC (ECS + ALB + CloudFront + WAF), GitHub Actions CI/CD
- **Data sensitivity:** Trader PII and account data, broker API credentials, subscription billing.
  Card data is handled by hosted gateways (Stripe Checkout, Razorpay Checkout) — **no PAN stored in this system**.
- **Compliance targets:** <TBD — confirm whether SOC 2 / DPDP Act / SEBI obligations apply>

---

## Design Principles

| ID  | Principle | Notes |
|-----|-----------|-------|
| DP-1 | Enforce least privilege for every human and machine identity. | |
| DP-2 | Encrypt all data at rest and in transit (TLS 1.2+). | |
| DP-3 | Use no long-lived static credentials; prefer short-lived tokens / IAM roles. | |
| DP-4 | Apply defense in depth — never rely on a single control. | |
| DP-5 | Maintain traceability — log and monitor security-relevant actions. | |
| DP-6 | Treat all client input as untrusted. | Web App pack |
| DP-7 | Authenticate and authorize at the edge of the application. | Web App pack |
| DP-8 | Collect and retain the minimum personal data necessary. | Data minimization |
| DP-9 | Keep people away from raw personal data; access is exceptional, logged, and time-boxed. | |
| DP-10 | Security controls are declared in code and enforced in the pipeline, not applied by hand. | IaC pack |
| DP-11 | Default to closed — resources are private and encrypted unless explicitly opened. | IaC pack |

---

## Quick Add — just write your rules here

<!--
Type rules in plain language, one per line. No ID, category, or severity needed. Then say
"process quick add" and Kiro formalizes each line into the tables below and clears this list.
-->

```
# e.g.
# broker API credentials must never be returned in an API response
# every payment webhook must verify the provider signature before acting
```

---

## Security Constraints

### Identity & Access
| ID  | Constraint | Severity |
|-----|-----------|----------|
| SC-1 | No identity policy grants unrestricted (wildcard) actions on all resources. | BLOCKER |
| SC-2 | No long-lived static credential keys in code or config. | BLOCKER |
| SC-3 | MFA required for privileged human access. | HIGH |
| SC-4 | IAM roles/policies in IaC avoid wildcard actions and resources. | HIGH |

### Data Protection
| ID  | Constraint | Severity |
|-----|-----------|----------|
| SC-5 | Data stores encrypt at rest. | HIGH |
| SC-6 | Secrets come from a secrets manager/vault at runtime, never hardcoded. | BLOCKER |
| SC-7 | TLS 1.2+ enforced for data in transit; plaintext transport denied. | HIGH |
| SC-8 | PII is never written to application logs, traces, or error messages. | BLOCKER |
| SC-9 | Sensitive data is classified/tagged and encrypted with a customer-managed KMS key. | HIGH |
| SC-10 | PII fields are masked or tokenized in non-production environments. | HIGH |
| SC-11 | Data retention and deletion policy is enforced (no indefinite PII storage). | MEDIUM |
| SC-12 | Access to personal data is logged and auditable. | HIGH |
| SC-13 | Personal data is not sent to third parties/analytics without a documented lawful basis. | HIGH |
| SC-14 | S3 buckets set Block Public Access and default encryption. | BLOCKER |
| SC-15 | Databases, volumes, and snapshots declare encryption at rest. | HIGH |

### Network & Infrastructure
| ID  | Constraint | Severity |
|-----|-----------|----------|
| SC-16 | Public web endpoints sit behind WAF with managed rule groups. | HIGH |
| SC-17 | No security group allows `0.0.0.0/0` inbound except intended public ports (80/443). | HIGH |
| SC-18 | No security group rule allows `0.0.0.0/0` to sensitive ports (22, 3389, 3306, 5432). | BLOCKER |
| SC-19 | Compute runs in private subnets unless explicitly public-facing. | MEDIUM |

### Detection
| ID  | Constraint | Severity |
|-----|-----------|----------|
| SC-20 | Audit logging is enabled and cannot be disabled by app code/roles. | HIGH |

### Application
| ID  | Constraint | Severity |
|-----|-----------|----------|
| SC-21 | No secrets, tokens, or keys committed to the repository. | BLOCKER |
| SC-22 | Dependencies are pinned and free of known critical vulnerabilities. | HIGH |
| SC-23 | All user input is validated; database access uses parameterized queries. | BLOCKER |
| SC-24 | Output is encoded to prevent XSS. | HIGH |
| SC-25 | AuthN/AuthZ enforced on every non-public endpoint. | BLOCKER |
| SC-26 | Security headers set (HSTS, CSP, X-Content-Type-Options). | MEDIUM |
| SC-27 | Errors do not leak stack traces, secrets, or internal details to clients. | HIGH |
| SC-28 | Rate limiting / throttling applied to public endpoints. | MEDIUM |
| SC-29 | IaC is scanned (e.g., checkov / cfn-guard) and no secrets are hardcoded in templates or tfvars. | HIGH |
| SC-30 | Remote state is encrypted and access-controlled; state is never committed to the repo. | HIGH |

---

## Exceptions / Waivers (controlled locally)

<!--
Approved, time-boxed exceptions to specific GOVERNED BASELINE rules, for this project only. Waivers appear in
every report (as WAIVED, with reason and expiry) — they don't hide risk. Expired waivers re-enable the rule.

Add one by saying: "waive <baseline-rule-id> for <scope> until <date> because <reason>, approved by <name>".
-->

| Baseline Rule ID | Scope (paths) | Reason | Approved by | Expires (YYYY-MM-DD) |
|------------------|---------------|--------|-------------|----------------------|
| _none_ | | | | |

---

## Scope

- **Include:** `backend_app/**`, `algo22-terminal/src/**`, `terraform/**`,
  `aerora_quant_platform/infrastructure/**`, `.github/workflows/**`
- **Exclude:** `tests/**`, `**/node_modules/**`, `.codex_verify_venv/**`, `.hypothesis/**`,
  `**/*.tf.disabled`, `**/dist/**`

---

## Review Output Preferences

- **Format:** both
- **Fail the review if:** any BLOCKER or HIGH
- **Include remediation suggestions:** yes

---

## RAG Rating Rules

- 🔴 **Red** — one or more BLOCKER or HIGH constraints violated. Should not merge without action.
- 🟠 **Amber** — MEDIUM/LOW violation, a PARTIAL pass, or anything marked NEEDS REVIEW. Reviewer judgment required.
- 🟢 **Green** — no constraint violations found in the change.

> The reviewer may still accept an Amber or Red item — but the risk is called out and recorded in the log.

---

## Daily Review Settings

- **Base for comparison:** commits in the last 24h
- **Branches to include:** all active branches
- **Group summary by:** author, then file area

### Daily Review Log (required)

- **Report directory:** `docs/security-reviews/`
  <!--
  NOT the power's default `reports/`: that path is gitignored in this repo (.gitignore:336)
  because the CI security workflows write scan artifacts there. A review log written to an
  ignored directory is never committed, which defeats the audit trail. `docs/` is tracked.
  -->
- **Report filename pattern:** `ALGO22_Quant_Platform_Review_MMDDYYYY.md`
- **If a report for today already exists:** append a new run section with a timestamp

---

## Custom Tracking / Watchlist

| ID  | Track this | Why it matters |
|-----|-----------|----------------|
| TR-1 | New or changed IAM roles, policies, and permissions. | High-impact access changes. |
| TR-2 | New third-party dependencies added. | Supply-chain risk. |
| TR-3 | Changes to authentication, 2FA, or authorization code. | Access-control regressions. |
| TR-4 | Changes to payment webhook handling or signature verification. | Money-moving path; a silent break is costly. |
| TR-5 | Changes to broker API credential storage, encryption, or retrieval. | Compromise means third-party account takeover. |
| TR-6 | Changes to order placement, risk limits, or kill-switch logic. | Faulty guards can move real money. |
