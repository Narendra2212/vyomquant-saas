# Personal Data Breach Response Runbook

**Controller:** Aerora Dynamics Private Limited (trading as VyomQuant)
**Version:** 2026-10-23 · aligned with legal documents version `2026-10-23`
**Owner of this procedure:** the Grievance Officer, grievance@vyomquant.com

---

## Why this exists

Privacy Policy §10 now commits to notifying the relevant supervisory authority **within
72 hours** of becoming aware of a breach likely to risk rights and freedoms, and to telling
affected people directly where the risk is high.

**A commitment without a procedure behind it is the exact pattern LEGAL-01 was opened
about.** 72 hours is not long enough to invent a process in. This is the process.

It covers GDPR Articles 33 and 34, the UK GDPR equivalents, and the breach-reporting
expectations of applicable Indian law.

---

## 0. What counts as a breach

A personal data breach is **any** security incident leading to accidental or unlawful
destruction, loss, alteration, unauthorised disclosure of, or access to personal data.

It is **not** limited to an attacker getting in. These all count:

- An exchange API credential, database credential or encryption key becoming readable by
  someone who should not have it — **including by being committed to a public repository**
- A misconfigured database privilege or RLS policy exposing one user's rows to another
- Sending an email containing one user's data to a different user
- Losing data irrecoverably, including by losing the key that decrypts it
- A processor (Supabase, AWS, Razorpay, Google, Microsoft, Sentry) notifying us of a breach
  on their side

Two real examples from this platform's own history, recorded because a runbook written from
imagination is worse than one written from events that happened:

- **SEC-01.** Three live production secrets sat in a public repository for 70–93 days. Had
  any of them been used against customer data, that would have been a notifiable breach.
- **VAULT-01.** Two customers' exchange credentials were encrypted under a key that no
  longer exists. **Irrecoverable loss of personal data is a breach**, even though nobody
  gained access to anything.

---

## 1. The clock

> **The 72 hours starts when we become AWARE, not when we finish investigating.**

"Aware" means a reasonable degree of certainty that a security incident has occurred leading
to personal data being compromised. It does not require knowing the full scope.

Article 33(4) permits notifying in phases. **A partial notification inside 72 hours beats a
complete one at 96.** Do not let investigation run the clock down.

| | |
|---|---|
| T+0 | Someone becomes aware. **Record the timestamp immediately** — it is the only fact the whole timeline rests on, and it is unrecoverable once memory blurs |
| T+1h | Containment started; Grievance Officer informed |
| T+24h | Assessment complete enough to decide notifiability |
| T+72h | **Supervisory authority notified** if notifiable |
| without undue delay | **Data subjects notified** if high risk |

---

## 2. Step 1 — Contain, and record while you do

Containment comes first. **But write down what you do as you do it**, because the
notification needs a timeline and reconstructing one afterwards is how timelines become
wrong.

Open an incident note immediately with: the time of awareness, who noticed, how, and every
action taken with its timestamp.

Containment actions available on this platform, with the tooling that already exists:

| Incident | Action |
|---|---|
| Leaked database credential | `scripts/rotate_database_password.py --stage / --redeploy / --verify` |
| Leaked JWT secret | `scripts/rotate_jwt_secret.py --stage / --redeploy / --verify` |
| Leaked encryption key | `scripts/rotate_master_encryption_keys.py` — **prepend, redeploy, re-encrypt, verify, then retire.** Retiring before re-encryption is permanent data loss; the script refuses it |
| Secret in git history | `scripts/audit_secrets_in_git_history.py` to establish scope; `scripts/locate_leaked_secret.py` for commits and files |
| Orphaned secret holding live material | `scripts/delete_orphaned_secrets.py --apply` |
| Compromised user account | Suspend it; the user must rotate their exchange API credentials **at the exchange**, which we cannot do for them |
| Database privilege exposure | `scripts/check_production_privileges.py`; apply a corrective migration via `scripts/apply_migration_to_production.py --trial` first |

**Do not destroy evidence while containing.** Preserve logs before rotating or redeploying.
A rotation that erases the trail of what the old credential was used for makes the scope
assessment impossible.

---

## 3. Step 2 — Assess

Answer these, in writing, with evidence rather than impression:

1. **What data?** Which categories, from which tables? `docs/legal/RECORDS_OF_PROCESSING.md`
   lists what exists where, which is most of the answer.
2. **Whose?** How many people, and can they be identified? The platform has 182 accounts as
   at 2026-10-23.
3. **Any EEA/UK data subjects?** This decides whether GDPR notification applies at all.
   The `declared_country` column on `legal_acceptances` is the best signal available — and
   it is a **declaration, not a verified fact**, so treat it as indicative and lean toward
   assuming EEA/UK subjects are affected if the data is silent.
4. **Was it actually accessed, or only exposed?** Exposure without access is still a breach
   but materially lower risk. Say which you can prove and which you are inferring.
5. **Is the data usable?** Encrypted-at-rest credentials whose key was not also compromised
   are a different risk from plaintext. **Be honest about which.** SEC-01's leaked
   `master_encryption_keys` turned out to decrypt nothing stored, and that mattered — but it
   was established by testing 265 candidate keys against the actual ciphertext, not assumed.
6. **What is the likely consequence?** For this platform the serious ones are: exchange API
   credentials enabling trades on a customer's own funds, and authentication material
   enabling account takeover. Financial loss, not just inconvenience.

### Notifiability

| | |
|---|---|
| **Notify the authority** (Art 33) | Unless the breach is **unlikely** to result in a risk to rights and freedoms. The default is to notify; not notifying is the exception and must be justified in writing |
| **Notify the data subjects** (Art 34) | Where it is likely to result in a **HIGH** risk — e.g. exchange credentials or authentication material exposed |
| **Either way** | Record it. Article 33(5) requires a record of **every** breach, notifiable or not, with the facts, effects and remedial action |

---

## 4. Step 3 — Notify

### Supervisory authority, within 72 hours

Submit to the authority that supervises us. **We have no EU/UK establishment, so this goes
through our Article 27 representatives** — see Privacy §1.

> **GAP, AND IT IS BLOCKING FOR THIS STEP:** the Article 27 representatives are **not yet
> appointed**. Until they are, there is no designated route to an EEA or UK supervisory
> authority. If a notifiable breach affecting EEA/UK subjects occurred today, this step
> would have to be improvised under a 72-hour clock. Appointing them is the single
> highest-value outstanding action on LEGAL-01.

Include, per Article 33(3):

- the nature of the breach, the categories and approximate number of data subjects, and the
  categories and approximate number of records
- the Grievance Officer's contact details
- the likely consequences
- the measures taken or proposed, including mitigation

Phase it if you must. State plainly what is not yet known.

### Data subjects, without undue delay where risk is high

Plain language, no hedging, Article 34(2) content:

- what happened and when
- what data of theirs was involved
- what we have done
- **what they should do** — for this platform, usually: revoke and re-issue exchange API
  credentials at the exchange, change their password, review recent account activity
- who to contact: grievance@vyomquant.com

Send by email to the address on the account. If a notification to each person would be
disproportionate, make a public statement that reaches them as effectively — and record why
individual notice was disproportionate.

### Also consider

- **Razorpay / Stripe** if payment data is implicated — their merchant terms impose their own
  obligations on us
- **Indian authorities** per applicable law and timelines
- The affected **exchanges**, if their API credentials were exposed

---

## 5. Step 4 — Record, and then fix the class

Every breach goes in the register, notifiable or not: what happened, when we became aware,
what data, how many people, the assessment and its reasoning, whether we notified and when,
and the remedial action.

Then do the part that is usually skipped: **fix the class of defect, not the instance.**
SEC-01's real finding was never one careless file — it was that *nothing was watching*,
which is why `.github/workflows/08-secret-history-audit.yml` now runs nightly. Ask what
would have caught this earlier, and build that.

---

## 6. Standing gaps in this procedure

Stated here rather than discovered during an incident:

1. **Article 27 representatives are not appointed.** There is no designated route to an
   EEA/UK supervisory authority. **Blocking for §4.**
2. **No 24/7 rota.** A breach discovered on a Friday night has no defined responder, and the
   72 hours runs through the weekend.
3. **`declared_country` is unverified**, so "are EEA/UK subjects affected" cannot be
   answered definitively from our own data.
4. **No tested notification template** for either authority or data subjects. Writing one
   now, calmly, is much cheaper than writing it at T+60h.
5. **Processor breach terms not confirmed.** Whether Supabase, AWS and the rest are
   contractually bound to tell us without undue delay depends on DPAs that have not been
   confirmed executed.

---

*Review this runbook whenever the processing activities, the processors, or the legal
documents change. Last reviewed: 2026-10-23.*
