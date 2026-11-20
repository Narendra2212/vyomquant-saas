# DPIA Screening Assessment

**Controller:** Aerora Dynamics Private Limited (trading as VyomQuant)
**Version:** 2026-10-23 · aligned with legal documents version `2026-10-23`

> **This is a SCREENING, not a DPIA.** Its only job is to answer one question — *is a full
> Data Protection Impact Assessment required under Article 35?* — and to record the reasoning
> either way.
>
> **It is not legal advice and it has not been reviewed by counsel.** It is written so that a
> lawyer can confirm or overturn the conclusion in minutes instead of starting from nothing.
> Article 35(1) requires the *controller* to carry out a DPIA where one is needed; this
> records the controller's screening.

---

## Why screen at all

The product owner accepts customers globally, **including the EU and UK**, so GDPR applies in
full. The platform does two things that sit near Article 35's triggers:

- **session replay** of every consenting visitor, via Microsoft Clarity — pointer movement,
  clicks, scrolling
- processing in a **financial context** — strategies, trades, exchange credentials, billing

Neither is obviously inside Article 35 and neither is obviously outside it. That is exactly
when a screening should be written down rather than a conclusion assumed.

---

## 1. The Article 35(3) mandatory triggers

Article 35(3) lists three cases where a DPIA is **required**.

### (a) Systematic and extensive evaluation of personal aspects based on automated processing, including profiling, on which decisions are based that produce legal effects or similarly significantly affect the person

**Not met.** And this is worth being careful about, because on a platform where software
places orders the intuitive answer is the wrong one.

The automated processing here evaluates **market data**, not people. A strategy's inputs are
prices, volumes and indicators. Its output is an order. At no point does the platform score,
rank, categorise or predict anything about the *user*.

The decisions that affect the user financially are **the user's own**: they build the
strategy, configure it, and start it. The Risk Disclosure is explicit that a started strategy
places orders without asking again — but it is still the user's instruction being executed,
not a decision the platform made *about* them.

There is no credit scoring, no eligibility decision, no pricing personalisation, no automated
account restriction. Plan tiers are purchased, not assigned.

Evidence: `backend_app/core/live_execution_gate.py`, `backend_app/backend/strategy_service.py`,
and `docs/legal/RECORDS_OF_PROCESSING.md` §3, which records "No automated decision-making"
as a non-processing fact.

### (b) Processing on a large scale of special categories of data (Art 9) or criminal-offence data (Art 10)

**Not met.** No special-category data is processed at all — no health, biometric, genetic,
racial or ethnic origin, political opinion, religious or philosophical belief, trade union
membership, sex life or sexual orientation data. No criminal-offence data.

**Financial data is not a special category under Article 9.** It is sensitive in the ordinary
sense and attracts higher risk in a DPIA's *assessment*, but it does not trigger 35(3)(b).

There is also **no KYC**: no identity documents, PAN, Aadhaar, phone number or postal address
anywhere in the schema. Verified by searching the migrations and models.

### (c) Systematic monitoring of a publicly accessible area on a large scale

**Not met.** This limb is about physical or quasi-physical public spaces — CCTV, and by
extension systematic monitoring of public areas. A visitor's own session inside an
authenticated product is not a "publicly accessible area".

Session replay is behaviour monitoring, and it matters for Article 27 (it is why a
representative is required) and for the lawful basis (it is why consent is required). It is
not 35(3)(c).

---

## 2. The supervisory-authority criteria

Authorities publish criteria lists — the EDPB's nine, and national lists such as the ICO's.
Meeting **two or more** is generally treated as indicating a DPIA. Assessed honestly:

| Criterion | Met? | Reasoning |
|---|---|---|
| Evaluation or scoring | **No** | Nothing scores the user; see 1(a) |
| Automated decision-making with legal or significant effect | **No** | See 1(a) |
| Systematic monitoring | **YES** | Session replay of every consenting visitor is systematic monitoring of behaviour |
| Sensitive data or data of a highly personal nature | **ARGUABLY** | Not Art 9 data, but trading activity and financial position are of a highly personal nature |
| Data processed on a large scale | **No** | 182 accounts. Small by any reading |
| Matching or combining datasets | **No** | No enrichment, no third-party data purchase, no cross-service matching |
| Data concerning vulnerable data subjects | **No** | 18+ only; no employee, child or patient relationship |
| Innovative use or new technology | **No** | Session analytics and ML are both ordinary commercial technology |
| Preventing data subjects from exercising a right or using a service | **No** | No right or service is contingent on the processing. Analytics consent is genuinely optional and refusing it does not restrict the product |

**Score: one clearly met, one arguable.**

---

## 3. Conclusion

**A full DPIA is assessed as NOT MANDATORY, and is RECOMMENDED anyway for the analytics
processing specifically.**

Reasoning:

- None of the three Article 35(3) mandatory triggers is met.
- Against the authority criteria, one is clearly met (systematic monitoring) and one is
  arguable (highly personal data). That is at or just below the usual "two or more"
  threshold, and the scale factor points firmly the other way at 182 accounts.
- **The honest reading is that this is borderline, not comfortable.** The combination of
  session replay with financial-context data is the kind of pairing an authority could
  reasonably want to see assessed, and "we scored one and a half out of nine" is a weaker
  defence than "we did the assessment".

### Recommendation

1. **Counsel to confirm or overturn this conclusion.** It is a screening by the controller,
   not an opinion.
2. **Do a focused DPIA on the Clarity session-replay processing** regardless of whether one
   is mandatory. It is the single highest-risk processing activity, the assessment is narrow,
   and it would be cheap. Specific questions it should answer:
   - Is session replay **necessary and proportionate** to the purpose, or would page-level
     analytics achieve it?
   - Can recordings be **masked** so account identifiers, balances and strategy contents are
     not captured? Clarity supports masking; **it is not configured here.**
   - What is the **retention** on the Clarity side? Currently "per the retention configured
     with each provider", which is not a number.
3. **Re-screen if any of these change:** scale grows materially beyond a few thousand
   accounts; any profiling or scoring of users is introduced; any automated decision
   affecting a user is introduced; KYC or identity verification is added; or special-category
   data enters the system.

---

## 4. The risk this screening did surface

Independent of whether a DPIA is mandatory, the screening turned up one concrete,
unaddressed thing:

> **Microsoft Clarity session recordings were not configured to mask sensitive content.**
> On a trading platform, a recording of an authenticated session can capture account
> balances, position sizes, strategy logic and exchange-connection screens. That is a real
> data-minimisation problem under Article 5(1)(c) regardless of Article 35, and it is
> fixable with configuration rather than with legal argument.

**FIXED IN THE SAME CHANGE AS THIS DOCUMENT.** `data-clarity-mask="true"` now applies to
the single `<main>` landmark that wraps every authenticated route (`algo22-terminal/src/App.jsx`),
so Clarity masks the content of every signed-in page by default rather than relying on each
new page remembering to. The public marketing pages render outside that element and stay
unmasked — which is deliberate: they carry no personal data and are where the analytics are
actually useful.

Two things this does **not** do, stated so the fix is not over-claimed:

- It does not change the **account-wide masking mode** in the Clarity dashboard, which is an
  owner-side setting. The attribute does not depend on it, but tightening it as well would
  be defence in depth.
- It does not retroactively mask **recordings already captured**. If Clarity has been live
  against authenticated sessions, those recordings exist and should be purged on the Clarity
  side.

Recorded here rather than left for a DPIA that may never be commissioned. It was engineering
work, and it is done.

---

*Screened: 2026-10-23. Not reviewed by counsel.*
