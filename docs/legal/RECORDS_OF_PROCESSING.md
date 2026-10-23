# Records of Processing Activities (ROPA)

**Controller:** Aerora Dynamics Private Limited
**Registered office:** Mohammdabad, Orai, Jalaun, Uttar Pradesh 285122, India
**CIN:** U26515UP2024PTC206131 · **GSTIN:** 09ABBCA0242R1ZI
**Trading name:** VyomQuant
**Contact:** privacy@vyomquant.com · **Grievance Officer:** grievance@vyomquant.com

**Document version:** 2026-10-23 · aligned with legal documents version `2026-10-23`

---

## Why this document exists

Article 30 of the GDPR requires a controller to maintain a record of its processing
activities and to make it available to a supervisory authority on request. The product
owner's decision is to **accept customers globally, including the EU and UK**, so this
obligation applies and is not optional.

**This is a factual record, derived from the codebase rather than from intent.** Every
category, recipient and retention period below was read out of migrations, routers and
configuration, and the file paths are cited so a reviewer can verify each line rather than
take it on trust. Where the code and an earlier policy statement disagreed, the code won.

**It is not legal advice and it has not been reviewed by counsel.** Three items in
`REVIEW_NOTES` (`algo22-terminal/src/legal/documents.js`) remain open for a lawyer, and the
Article 27 representatives are **not yet appointed**.

---

## 1. Processing activities

### 1.1 Account creation and authentication

| | |
|---|---|
| **Purpose** | Create and authenticate a user account |
| **Lawful basis** | Art 6(1)(b) — performance of a contract |
| **Data subjects** | Registered users (182 accounts as at 2026-10-23) |
| **Categories** | Email address; password as a cryptographic hash only; display name, username, avatar, bio where set; for Google sign-in, the email and basic profile Google returns |
| **Source** | `public.profiles` (migration `020_declare_pre_existing_tables.sql`), Supabase `auth.users` |
| **Recipients** | Supabase (auth + database), AWS (hosting) |
| **Retention** | Life of the account; deleted or irreversibly anonymised within 30 days of a deletion request, except what law requires us to keep |
| **Transfers** | India → AWS `ap-southeast-1` (Singapore); Supabase |

### 1.2 Legal acceptance, age and jurisdiction declarations

| | |
|---|---|
| **Purpose** | Evidence which version of the Terms, Privacy Policy, Risk Disclosure and Refund Policy a user accepted, and record the representations the Terms require |
| **Lawful basis** | Art 6(1)(c) — legal obligation; Art 6(1)(f) — legitimate interest in being able to evidence agreement |
| **Categories** | User id, document version, documents covered, timestamp, IP address, user agent, age attestation, **declared** country |
| **Source** | `public.legal_acceptances` (migrations `052`, `053`) |
| **Retention** | Retained for the life of the account and for the limitation period thereafter. **Append-only, enforced by `trg_legal_acceptances_append_only`** — a recorded acceptance is a historical fact and cannot be altered or deleted |
| **Note** | The country is a **declaration, not a verified fact**. No IP geolocation, KYC or document check corroborates it. See `backend_app/core/restricted_jurisdictions.py` |

### 1.3 Providing the trading platform

| | |
|---|---|
| **Purpose** | Run strategies, backtests, the paper-trading simulator and model training; show a user their own data |
| **Lawful basis** | Art 6(1)(b) — contract |
| **Categories** | Strategies, backtest configurations and results, model-training runs, paper-trading orders and positions, execution records, signals, trade history |
| **Source** | `strategy_*`, `paper_*`, `execution_records`, `signals`, `trade_history` tables |
| **Recipients** | Supabase, AWS |
| **Retention** | Life of the account |

### 1.4 Exchange API credentials

| | |
|---|---|
| **Purpose** | Place orders on the user's own account at a third-party exchange |
| **Lawful basis** | Art 6(1)(b) — contract |
| **Categories** | API key, secret and passphrase where required — **encrypted at rest** |
| **Source** | `public.exchange_keys` (`encrypted_api_key`, `encrypted_secret_key`, `encrypted_password`) |
| **Security** | Authenticated symmetric encryption (`MultiFernet`, `backend_app/backend/api_key_vault.py`); key held in AWS Secrets Manager, separate from the database. **Credentials permitting withdrawal are refused and never stored** (`routers/exchange.py`); credentials whose permissions cannot be verified are also refused |
| **Recipients** | The exchange the user chooses to connect |
| **Retention** | Until the user deletes them, or with the account |
| **Open issue** | **VAULT-01** — the two credentials currently stored were encrypted under a key that no longer exists and are unreadable. They cannot be decrypted by us or by anyone |

### 1.5 Billing and payments

| | |
|---|---|
| **Purpose** | Take payment, manage plan entitlements, meet tax and accounting obligations |
| **Lawful basis** | Art 6(1)(b) — contract; Art 6(1)(c) — legal obligation for tax records |
| **Categories** | Plan, billing period, payment references, invoices, fee records. **Full card details are never received or stored by us** |
| **Source** | `profiles` billing columns, `billing_invoices`, `payment_methods`, `fee_records`, `plan_usage_ledger` |
| **Recipients** | **Razorpay** (INR payments; an independent controller for its own compliance purposes), **Stripe** (non-INR where used) |
| **Retention** | Statutory retention period for billing and tax records, which survives account deletion |
| **Transfers** | Razorpay processes in India |

### 1.6 Marketplace and referrals

| | |
|---|---|
| **Purpose** | Let users publish and subscribe to strategies; pay creators their 90% share; operate referral commissions |
| **Lawful basis** | Art 6(1)(b) — contract |
| **Categories** | Listings, subscriptions, settlement records, payout details, referral codes and attributions |
| **Source** | `library_strategies`, marketplace settlement tables, referral ledger/payout tables, `profiles.referral_code` / `referred_by_user_id` |
| **Retention** | Life of the account; settlement records for the statutory period |
| **Note** | Creators are responsible for their own taxes. We do not withhold tax, act as agent, or issue invoices on their behalf |

### 1.7 Support and the AI assistant

| | |
|---|---|
| **Purpose** | Answer support requests; provide the in-app assistant |
| **Lawful basis** | Art 6(1)(b) — contract; Art 6(1)(f) — legitimate interest |
| **Categories** | Support tickets and their contents; assistant conversation messages |
| **Source** | `support_tickets`, `copilot_sessions`, `copilot_messages` (migration `018`) |
| **Retention** | Life of the account |
| **Note, recorded honestly** | Migration `018`'s own header records that every copilot write is wrapped in a `try/except` that only logs, so **assistant history is currently never persisted**. The capability exists; the data does not |

### 1.8 Security, abuse prevention and audit

| | |
|---|---|
| **Purpose** | Keep the platform secure, investigate abuse, prevent fraud, maintain an audit trail |
| **Lawful basis** | Art 6(1)(f) — legitimate interest in operating a secure service |
| **Categories** | IP address, user agent, authentication events, order and signal audit logs, safety-control audit, webhook events |
| **Source** | `order_audit_log`, `signal_audit_log`, `safety_control_audit`, `webhook_events` |
| **Retention** | As long as needed for security and compliance |

### 1.9 Error diagnostics

| | |
|---|---|
| **Purpose** | Detect and fix faults |
| **Lawful basis** | Art 6(1)(f) — legitimate interest |
| **Categories** | Stack traces and the technical context of a failure, which may incidentally include an identifier |
| **Recipients** | **Sentry** |
| **Retention** | Up to 90 days |
| **Note** | Configured not to capture credentials or secret values. **Not consent-gated**, and declared as legitimate interest in Privacy §3 and §6 — gating crash reporting behind a banner would lose the crashes of exactly the visitors who never answer one |

### 1.10 Analytics and session behaviour — **consent only**

| | |
|---|---|
| **Purpose** | Understand how the product is used; diagnose usability problems |
| **Lawful basis** | **Art 6(1)(a) — consent**, and consent under the ePrivacy Directive for the storage/access itself |
| **Categories** | Pages visited, actions taken, pointer movement, clicks, scrolling — i.e. **session replay** |
| **Recipients** | **Google Analytics**, **Microsoft Clarity** |
| **Control** | `algo22-terminal/src/legal/consent.js` defaults to **denied** and tests for an explicit grant, not the absence of a refusal. `bootstrap.js` injects nothing until consent, and pre-consent stubs **drop** events rather than queueing them, so a later grant cannot replay pre-consent behaviour. Withdrawable at any time from the footer |
| **Retention** | Per the retention configured with each provider |
| **Honest limit** | Withdrawal stops further sending but cannot unload a tag the browser already executed without a reload. Stated in both the module and the Policy |

### 1.11 Waiting list

| | |
|---|---|
| **Purpose** | Manage pre-launch interest |
| **Lawful basis** | Art 6(1)(a) — consent |
| **Categories** | Name, email, Telegram handle, stated experience level, trader type, monthly volume, IP address, UTM source/medium |
| **Source** | `public.waitlist` (migration `018`) |
| **Retention** | Up to 24 months |
| **Note** | The public form is currently **unmounted** and there is deliberately no anonymous INSERT policy, so nothing is being collected through it today |

### 1.12 Service and marketing messages

| | |
|---|---|
| **Purpose** | Account, security and billing notices; optional product updates; user-configured alerts |
| **Lawful basis** | Art 6(1)(b) for service messages — not opt-outable while an account exists; **Art 6(1)(a) consent** for marketing |
| **Recipients** | SMTP email provider; **Telegram, Slack or Discord where the user configures an alert to go there** |
| **Note** | Configuring an alert to a third-party channel sends content to that service. Plan-gated feature |

---

## 2. Recipients and transfers, consolidated

| Recipient | Role | Purpose | Location |
|---|---|---|---|
| Supabase | Processor | Authentication, primary database | **`ap-southeast-1` (Singapore)** — established from the Supavisor pooler hostname `aws-1-ap-southeast-1.pooler.supabase.com`, which encodes the region. Co-located with our own compute |
| Amazon Web Services | Processor | Hosting, storage, secrets, logging | **`ap-southeast-1` (Singapore)** |
| Razorpay | Independent controller / processor | INR payments | India |
| Stripe | Processor | Non-INR payments where used | global |
| Sentry | Processor | Error diagnostics | global |
| Google Analytics | Processor | Usage analytics — **consent only** | global |
| Microsoft Clarity | Processor | Session analytics — **consent only** | global |
| SMTP provider | Processor | Email delivery | per provider |
| Telegram / Slack / Discord | Processor | User-configured alerts | global |
| Cryptocurrency exchanges | Third party, user-chosen | Receive the API requests a strategy generates | per exchange |

**Transfers out of the EEA/UK.** The controller is in India; primary processing is in
Singapore. Both are outside the EEA and the UK, and neither currently benefits from a UK or
EU adequacy decision covering this processing. Privacy §7 discloses the transfer and states
we rely on our contracts with these providers, including standard contractual clauses where
applicable. **The DPAs and SCCs must actually be executed — see §4.**

---

## 3. What we do *not* process

Stated because its absence is load-bearing for the risk assessment:

- **No custody of funds or assets.** The platform holds exchange API credentials and trades
  on the user's own exchange account. It never receives, holds, transmits, exchanges or
  safekeeps money or crypto-assets. This is also the factual basis for the position that it
  falls outside the PMLA virtual-digital-asset service-provider definition.
- **No KYC.** No identity documents, PAN, Aadhaar, phone number or postal address anywhere
  in the schema.
- **No special-category data** within Art 9 — no health, biometric, racial or ethnic origin,
  political opinion, religious belief, trade union membership, sex life or sexual
  orientation data.
- **No children's data knowingly.** 18+ only, attested at signup.
- **No automated decision-making** with legal or similarly significant effect under Art 22.
  Trading decisions are made by strategies the **user** configures and starts.
- **We do not sell personal data**, and we do not use user data to train models offered to
  other users.

---

## 4. Known gaps — the honest part

| # | Gap | Who closes it | Status |
|---|---|---|---|
| 1 | **Article 27 representatives not appointed**, for either the EU or the UK. Required because we offer the service to people in the EU/UK *and* run behaviour-monitoring analytics. The Art 27(2) exemptions are narrow and session analytics across every visitor is not "occasional". **This also blocks the breach-notification route** — see `BREACH_RESPONSE_RUNBOOK.md` §4 | **Owner** — an appointment, typically a paid service per jurisdiction | **OPEN** |
| 2 | **DPAs / SCCs with processors not confirmed executed.** Supabase, AWS, Google, Microsoft and Sentry all publish them; Privacy §7 states we rely on them, which must be true rather than aspirational | **Owner** — mostly click-through or already within provider terms | **OPEN** |
| 3 | Supabase region | — | **CLOSED.** `ap-southeast-1` (Singapore), established from the Supavisor pooler hostname rather than asked for. Co-located with our own compute, so there is no additional hop between application and database |
| 4 | DPIA screening | — | **CLOSED.** `docs/legal/DPIA_SCREENING.md`. Concludes a full DPIA is **not mandatory** — none of the three Art 35(3) triggers is met — while recommending a focused one on the session-replay processing anyway, and recording that the conclusion is borderline rather than comfortable. Counsel to confirm or overturn |
| 5 | Breach-response runbook | — | **CLOSED.** `docs/legal/BREACH_RESPONSE_RUNBOOK.md`, with the 72-hour clock, the containment tooling that already exists, the Art 33(3)/34(2) content requirements, and its own standing gaps |
| 6 | Three substantive clauses still await counsel: the PMLA/FIU-IND position, the liability cap, and whether the CIN's registered object (industry code 26515 — instruments and measuring devices) covers operating a trading platform | **Counsel** | **OPEN** |
| 7 | **Clarity session-replay masking** | — | **CLOSED.** Found by writing the DPIA screening: Clarity's default masking covers form inputs but not page text, and on a trading platform the text is the sensitive part. `data-clarity-mask="true"` now applies to the single `<main>` landmark wrapping every authenticated route, so balances, positions and strategy contents do not reach a replay. Public marketing pages render outside it and stay unmasked, which is where the analytics are actually useful |

---

## 5. Maintenance

Review this document when any of the following changes: the processing activities above, the
recipients, the retention periods, the transfer arrangements, or `LEGAL_VERSION` in
`algo22-terminal/src/legal/documents.js`.

Last reviewed: **2026-10-23.**
