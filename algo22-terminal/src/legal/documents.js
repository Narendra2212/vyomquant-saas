/**
 * src/legal/documents.js — LEGAL-01
 *
 * THE SINGLE SOURCE OF TRUTH FOR EVERY LEGAL DOCUMENT ON THIS PLATFORM.
 *
 * WHY THIS FILE EXISTS
 * ====================
 * There were two divergent sets of legal text, both served, and they
 * CONTRADICTED each other on a material commercial term:
 *
 *   components/legal/LegalPage.jsx  (/legal/terms, /legal/privacy, ...)
 *       "we may, at our discretion, issue pro-rated service credits"
 *   pages/LegalPage.jsx             (/legal)
 *       "All purchases are final and non-refundable"
 *
 * Only the first was linked from the footer and the signup checkbox, but both
 * were reachable. That is not a drafting nit: which page a customer happened to
 * read decided whether they were owed money.
 *
 * Both now render FROM HERE, so divergence is unrepresentable rather than merely
 * discouraged, and tests/test_legal01_single_source_of_truth.py fails if a
 * second copy of this prose reappears under algo22-terminal/src.
 *
 * THREE STATEMENTS WERE FALSE. EACH WAS CHECKED AGAINST CODE, NOT INTENT.
 * ======================================================================
 *  1. ToS: "live trading requires explicit activation per strategy."
 *     FALSE. core/live_execution_gate.py::assert_platform_live_trading_enabled
 *     refuses unless VYOMQUANT_MODE=live AND VYOMQUANT_ENABLE_LIVE_TRADING=true
 *     AND the tenant sits in an operator-managed allowlist whose empty default
 *     means nobody; core/safety_config.py:217 holds LIVE_TRADING_ENABLED=False
 *     as a class attribute read from no environment variable. A user cannot
 *     activate live trading. Only a deployment change can.
 *
 *     The owner intends to enable live trading before launch, so section 4 below
 *     is written to be true in BOTH states: it describes availability as
 *     per-account and operator-granted, and never tells the reader the feature
 *     is active for them. That avoids shipping a false claim in the interval
 *     between now and launch, and avoids needing a rewrite afterwards.
 *
 *  2. Privacy: "Read-only API permissions are enforced by default."
 *     FALSE, and backwards. routers/exchange.py:426 refuses a key that can
 *     WITHDRAW; :449 refuses a key whose permissions cannot be verified. TRADE
 *     permission is required — a read-only key cannot place the orders the
 *     product exists to place. Corrected to "trade required, withdrawal
 *     refused", which is what the code does.
 *
 *  3. Privacy: "We do not use third-party tracking or advertising cookies."
 *     FALSE. src/bootstrap.js loads Google Analytics and Microsoft Clarity and
 *     index.html's CSP admits both. They are now disclosed AND gated behind
 *     consent (src/legal/consent.js) rather than loaded unconditionally, which
 *     is also what GDPR/ePrivacy requires once EU/UK users are in scope.
 *
 * VERSIONING AND ACCEPTANCE
 * =========================
 * LEGAL_VERSION identifies this body of text and is recorded against each user
 * when they accept. The platform previously could not answer "which terms did
 * this person agree to": the signup checkbox was React state that gated a form
 * submit and was never sent anywhere.
 *
 * BUMP LEGAL_VERSION WHEN THE SUBSTANCE CHANGES. backend_app/core/
 * legal_documents.py carries the same constant and a test asserts they agree, so
 * the version accepted in the browser is the version written to the database. A
 * typo fix needs no bump; a changed obligation does.
 *
 * WHAT THIS IS NOT
 * ================
 * NOT counsel-reviewed. REVIEW_NOTES lists the clauses a lawyer must settle for
 * the declared jurisdictions. This file makes the documents ACCURATE and
 * COMPLETE; it does not make them adjudicated, and LEGAL-01 stays open until
 * counsel signs off.
 */

/**
 * Version identity of this text. Date-based so it sorts and reads.
 * MUST equal backend_app/core/legal_documents.py::LEGAL_VERSION.
 */
export const LEGAL_VERSION = '2026-10-23';

/** Human-facing effective date for the same body of text. */
export const LEGAL_EFFECTIVE_DATE = '23 October 2026';

/**
 * The operating entity. Published because Razorpay's merchant terms and the
 * Consumer Protection (E-Commerce) Rules 2020 both expect a contactable
 * identity, a reachable address and, for a GST-registered seller, the GSTIN.
 *
 * ALL FOUR IDENTIFIERS WERE VALIDATED BEFORE BEING PUBLISHED, because a wrong
 * statutory identifier in a Terms of Service is worse than a placeholder: a
 * placeholder is obviously unfinished, and a wrong number looks finished.
 *
 *   CIN      21 characters, structure parsed and cross-checked: U (unlisted),
 *            industry 26515, state UP -- which matches the registered address --
 *            year 2024, PTC (Private Limited Company, matching `form` below),
 *            registration 206131.
 *   GSTIN    15 characters, and its CHECK DIGIT WAS COMPUTED INDEPENDENTLY and
 *            agrees ('I'). State code 09 is Uttar Pradesh, matching the address,
 *            and the PAN's 4th character is 'C' (Company), which is what a
 *            Private Limited must be.
 *   PIN      285122. 285001 is Orai town; 2851xx covers surrounding localities
 *            in Jalaun district, so this is consistent with a Mohammdabad
 *            address rather than the town centre.
 *   NAME     Corroborated by src/utils/razorpayCheckout.js, which already
 *            defaults the checkout merchant name to 'Aerora Dynamics'.
 *
 * ONE THING FOR COUNSEL, recorded rather than silently accepted: CIN industry
 * code 26515 sits in the instruments and measuring-devices range, not software
 * or financial services. That is a fact about the incorporation, not an error in
 * these documents, but a reviewer will reasonably ask whether the registered
 * object covers operating a trading platform. See REVIEW_NOTES.
 */
export const ENTITY = {
  name: 'VyomQuant',
  legalName: 'Aerora Dynamics Private Limited',
  form: 'Private Limited Company incorporated in India',
  // PIN included here, in Indian postal order, so the three places that render
  // an address do not have to concatenate it and cannot get the order wrong.
  address: 'Mohammdabad, Orai, Jalaun, Uttar Pradesh 285122, India',
  pinCode: '285122',
  cin: 'U26515UP2024PTC206131',
  gstin: '09ABBCA0242R1ZI',
  // LEGAL-01: GDPR Article 27, and the equivalent UK GDPR provision.
  //
  // THESE ARE REQUIRED AND NOT YET APPOINTED, which is why they render as
  // visible placeholders rather than being quietly omitted.
  //
  // Article 27 obliges a controller established outside the EU to designate a
  // representative in the Union, in writing, when it offers goods or services to
  // people in the EU OR MONITORS THEIR BEHAVIOUR. Both limbs apply here: the
  // product owner's decision is to accept customers globally including the EU
  // and UK, and this platform runs Google Analytics and Microsoft Clarity, which
  // is behaviour monitoring. The Article 27(2) exemptions are narrow -- occasional
  // processing, no large-scale special-category data, unlikely to risk rights and
  // freedoms -- and session analytics across every visitor is not occasional.
  //
  // A representative is an appointment, not code: typically a service in an EU
  // member state (and separately in the UK) that acts as the contact point for
  // data subjects and supervisory authorities. It cannot be written here.
  //
  // tests/test_legal01_documents_and_acceptance.py asserts these placeholders
  // are PRESENT while unappointed, so the gap cannot be lost, and will need
  // updating to assert real values once they are appointed.
  euRepresentative:
    'EU representative: [NOT YET APPOINTED - GDPR Article 27 requires one '
    + 'because we offer this service to people in the EU and use analytics that '
    + 'monitor behaviour. Contact privacy@vyomquant.com in the meantime.]',
  ukRepresentative:
    'UK representative: [NOT YET APPOINTED - the UK GDPR requires one on the '
    + 'same basis. Contact privacy@vyomquant.com in the meantime.]',
  grievanceEmail: 'grievance@vyomquant.com',
  privacyEmail: 'privacy@vyomquant.com',
  billingEmail: 'billing@vyomquant.com',
  supportEmail: 'support@vyomquant.com',
  governingLaw: 'India',
  venue: 'the courts at Orai, Uttar Pradesh',
};

/**
 * Clauses counsel must settle before these documents can be called adequate.
 * Surfaced on the page in non-production builds so they cannot be forgotten,
 * and mirrored in LEGAL-01's ledger entry.
 */
export const REVIEW_NOTES = [
  'OWNER ACTION, BLOCKING FOR EU/UK — ARTICLE 27 REPRESENTATIVES ARE NOT APPOINTED. The product owner has decided to accept customers globally INCLUDING the EU and UK, so GDPR and UK GDPR apply in full. Article 27 obliges a controller established outside the Union to designate a representative in writing where it offers services to people in the EU OR monitors their behaviour; BOTH limbs apply here, since Google Analytics and Microsoft Clarity are behaviour monitoring. The Article 27(2) exemptions are narrow and session analytics across every visitor is not "occasional". A separate UK representative is required on the same basis. These render as VISIBLE PLACEHOLDERS in Privacy section 1 rather than being omitted, so they cannot ship unnoticed. This is an appointment, not code.',
  'OWNER ACTION — PROCESSOR DPAs AND SCCs must be confirmed executed with Supabase, AWS, Google, Microsoft and Sentry. All publish them; Privacy section 7 already states we rely on them, which must be true rather than aspirational. Also: the SUPABASE REGION is not recorded anywhere in this repository (every reference is the placeholder your-project-ref.supabase.co), so docs/legal/RECORDS_OF_PROCESSING.md cannot state where the primary database physically sits -- which is exactly what a ROPA is for.',
  'COUNSEL — DPIA SCREENING. Session replay of every consenting visitor, alongside financial-context data, is the kind of processing an authority may expect a documented screening assessment for. A full DPIA is arguably not mandatory -- there is no Article 9 special-category data and no systematic monitoring of a publicly accessible area -- but that conclusion should be recorded by someone qualified rather than assumed. Records of Processing Activities (Article 30) ARE now maintained at docs/legal/RECORDS_OF_PROCESSING.md.',
  'COUNSEL — PMLA / FIU-IND. The position that VyomQuant falls outside the virtual-digital-asset service-provider definition is stated as a FACTUAL description of what the platform does (no custody, no exchange of assets, no transfer on behalf of another; trades execute on the user\'s own exchange account under their own API keys) and NOT as a legal conclusion. Counsel should confirm against the FIU-IND AML/CFT guidelines as updated 08.01.2026.',
  'COUNSEL — LIABILITY CAP. Terms section 11 is drafted broadly, with a cap at amounts paid in the preceding twelve months or Rs 5,000. Indian consumer law limits how far liability may be excluded against a consumer; the cap and its carve-outs need review.',
  'COUNSEL — REGISTERED OBJECT. The CIN\'s industry code (26515) sits in the instruments and measuring-devices range rather than software or financial services. That is a fact about the incorporation rather than an error in these documents, but a reviewer will reasonably ask whether the registered object covers operating a trading platform.',
  'CONTROL GAP, NARROWED NOT CLOSED — JURISDICTION. The restricted-jurisdiction clause in Terms section 3 is now ENFORCED at signup by backend_app/core/restricted_jurisdictions.py, and the declaration is recorded against the acceptance. But it is a DECLARED-COUNTRY GATE, not sanctions screening: nothing verifies the declaration, no SDN/UN/EU/UK list is consulted, and a country code cannot express region-level measures such as those applying to parts of Ukraine. Counsel must set the definitive list and decide whether a licensed screening provider is required.',
  'CONTROL GAP, NARROWED NOT CLOSED — AGE. The 18+ requirement is now an explicit, recorded attestation at signup rather than a sentence nobody captured. It is still a REPRESENTATION: there is no age or identity verification anywhere in the schema.',
  'RECORDED, REVISIT IF CODE CHANGES — GST. Prices are stated INCLUSIVE of GST because Razorpay Standard Checkout charges the exact server-created order amount and no GST computation exists in subscription_engine.py. If GST-exclusive pricing is ever intended, the code must change BEFORE the documents can say so; a test guards the current wording.',
  'RECORDED, REVISIT IF CODE CHANGES — LIVE TRADING. Live trading is described as per-account and operator-granted, which is true both now (it is hard-gated off) and after it is enabled. If it launches with different mechanics, Terms section 4 and the Risk Disclosure must be revisited and LEGAL_VERSION bumped, which re-prompts every user.',
  'SETTLED — VENUE. Exclusive venue at Orai, Uttar Pradesh, confirmed by the product owner. Drafted as a PREFERENCE and explicitly NOT as a waiver, because the Consumer Protection Act 2019 lets a consumer file where they reside or work regardless of such a clause.',
  'SETTLED — ENTITY DETAILS. Company name, CIN, GSTIN and PIN code supplied and validated: the CIN parses to U / 26515 / UP / 2024 / PTC / 206131 with the state matching the registered address and the ownership code matching the declared company form, and the GSTIN\'s CHECK DIGIT was computed independently and agrees.',
];

export const DOCUMENTS = {
  terms: {
    id: 'terms',
    title: 'Terms of Service',
    sections: [
      {
        heading: '1. Who we are, and what these terms cover',
        body: `${ENTITY.name} is operated by ${ENTITY.legalName}, a ${ENTITY.form}, registered office ${ENTITY.address}. Our Corporate Identity Number is ${ENTITY.cin} and our GSTIN is ${ENTITY.gstin}. "We", "us" and "our" mean that company. "You" means the person using the platform.

These Terms govern your use of the ${ENTITY.name} web platform, desktop application and APIs (the "Platform"). By creating an account or using the Platform you accept these Terms, the Privacy Policy, the Risk Disclosure and the Refund and Cancellation Policy. If you do not accept them, do not use the Platform.

We record which version of these documents you accepted and when. This version is ${LEGAL_VERSION}, effective ${LEGAL_EFFECTIVE_DATE}.`,
      },
      {
        heading: '2. What the Platform is, and what it is not',
        body: `The Platform is software for building, testing and running systematic trading strategies. It provides a strategy builder, historical backtesting, a paper-trading simulator, optional machine-learning components, and the ability to connect your own accounts at third-party cryptocurrency exchanges.

THE PLATFORM IS NOT a broker, an exchange, a custodian, a portfolio manager or an investment adviser. We do not hold, receive, transmit, exchange or safekeep your money or your crypto-assets at any time. We never take custody. When a strategy places an order, that order goes to YOUR OWN account at a third-party exchange using API credentials you supply, and your assets never leave your control or that exchange.

We do not give investment advice or recommendations. Nothing on the Platform — strategy templates, backtest output, machine-learning output, analytics, marketplace listings, or anything the AI assistant says — is advice to buy or sell anything, or a representation that any strategy is suitable for you. See the Risk Disclosure.`,
      },
      {
        heading: '3. Eligibility',
        body: `You must be at least 18 years old, or the age of majority where you live if that is higher, and you must have legal capacity to contract. Under the Indian Contract Act, 1872, a person under 18 cannot.

You must not use the Platform if doing so would break the law where you are. Whether trading crypto-assets, or using automated trading software, is lawful where you live is your responsibility to establish. Some countries restrict or prohibit both.

You must not use the Platform if you are located in, ordinarily resident in, or acting on behalf of a person in a country or territory subject to comprehensive sanctions, or if you are a person named on an applicable sanctions list. We may refuse or close an account on that basis.

WE DO NOT VERIFY your age, identity or location. Everything in this section is a representation you make to us, not a fact we have checked.`,
      },
      {
        heading: '4. Paper trading, and live trading',
        body: `PAPER TRADING is a simulation. Orders fill against a simulated account inside the Platform. No order reaches any exchange and no real money is involved at any point.

LIVE TRADING sends real orders to your own exchange account using your API credentials. Where live trading is offered, IT IS NOT AVAILABLE SIMPLY BECAUSE YOU HOLD A PAID PLAN. It must be enabled by us for your specific account, and we may decline to enable it, or suspend or withdraw it, for any account at any time — including while orders are in flight. Plan descriptions that list live trading describe what a plan may include where live trading has been enabled for you; they are not a commitment that it is enabled.

YOU ARE SOLELY RESPONSIBLE FOR EVERY ORDER placed through your account — whether you placed it, a strategy you configured placed it, or a strategy you obtained from the marketplace placed it. Automated means automated: a strategy you start can place orders without asking you again.

Exchange API credentials you connect must permit trading. Credentials permitting WITHDRAWAL are refused and never stored, because a trading platform never needs the right to move your funds. We also refuse credentials whose permissions we cannot verify.`,
      },
      {
        heading: '5. Your account and your credentials',
        body: `Keep your login credentials confidential. Do not share your account or let anyone else use it. Tell us immediately at ${ENTITY.supportEmail} if you think someone else has.

You are responsible for activity under your account. If you revoke your exchange API credentials at the exchange, or delete them here, strategies that depend on them stop working.

You may delete stored exchange credentials at any time from the Platform.`,
      },
      {
        heading: '6. Plans, payment and taxes',
        body: `Paid plans and their limits are listed on the pricing page. PUBLISHED PRICES ARE IN INDIAN RUPEES AND ARE INCLUSIVE OF GOODS AND SERVICES TAX where GST applies. Prices shown in other currencies are conversions of the Rupee price at the prevailing rate and may move; the Rupee figure is the committed one.

Payments are processed by our payment partners. We do not receive or store your full card details.

A paid plan grants access for a FIXED TERM — one month or one year, as purchased. Plans purchased through our Indian payment partner DO NOT RENEW AUTOMATICALLY: access ends when the paid term ends unless you purchase again, and you will not be charged again without starting a new purchase yourself.

Plan limits — strategies, connected exchanges, backtests, optimisations, model-training runs — are part of what you are buying. Monthly metered allowances reset each billing period and do not carry forward.

You may cancel at any time. Cancelling stops future access at the end of the term you have already paid for; it does not shorten that term. See the Refund and Cancellation Policy.`,
      },
      {
        heading: '7. The strategy marketplace',
        body: `You may publish a strategy for others to subscribe to, and subscribe to strategies others publish.

A strategy you publish remains yours. By publishing it you grant us a licence to host, display and make it available to subscribers, and you grant subscribers the right to run it. Other users can copy its underlying logic ONLY IF YOU SWITCH THAT ON; it is off unless you enable it.

Where a published strategy earns revenue, the creator receives 90% of the amount paid and we retain 10%. Payouts go to the details you provide.

YOU ARE RESPONSIBLE FOR YOUR OWN TAXES on anything you earn, including income tax and GST where applicable. We do not withhold tax on your behalf, we do not act as your agent, and we do not issue invoices for you. Amounts we report are amounts paid, not a tax computation.

If you publish a strategy you must not misrepresent what it does or what it has returned, and you must not present backtested results as though they were achieved with real money.`,
      },
      {
        heading: '8. Things you must not do',
        body: `You must not use the Platform to attempt market manipulation, wash trading, spoofing, layering or front-running, or anything that breaks the rules of an exchange you connect to or the law that applies to you.

You must not: share or resell your account; work around plan limits; attack, overload or probe the Platform; scrape it; reverse-engineer it except where law permits; upload malicious code; impersonate anyone; or use it to launder money or finance anything unlawful.

You must not use the Platform to provide investment advice or portfolio management to other people unless you hold whatever authorisation that requires where you are. That is your responsibility.

We may suspend or terminate an account that breaches this section, without notice where the breach is serious.`,
      },
      {
        heading: '9. Intellectual property',
        body: `The Platform — its software, design, documentation and content — belongs to us or our licensors. These Terms give you permission to use it, not ownership of it.

You keep ownership of strategies you create and data you provide. You grant us the licence we need to operate the Platform for you: to store, process and display your content so the product works.`,
      },
      {
        heading: '10. Availability, and changes to the Platform',
        body: `We aim to keep the Platform available but DO NOT PROMISE UNINTERRUPTED SERVICE. Maintenance, third-party failures, exchange outages and network problems all cause interruptions. We make no uptime commitment in these Terms.

We may change, add or remove features. Where a change materially reduces what a paid plan provides during a term you have already paid for, we will tell you and you may cancel; the Refund and Cancellation Policy says what happens to the amount paid.`,
      },
      {
        heading: '11. Limits on our liability',
        body: `PLEASE READ THIS SECTION CAREFULLY.

The Platform is provided "as is" and "as available". To the extent the law allows, we exclude implied warranties, including fitness for a particular purpose and any warranty that the Platform will be profitable, accurate or uninterrupted.

WE ARE NOT LIABLE FOR TRADING LOSSES. That includes losses from a strategy behaving as configured, a strategy behaving unexpectedly, a backtest that did not predict live results, model output that proved wrong, orders placed or not placed, slippage, latency, an exchange's failure, or market movement.

We are not liable for indirect, incidental, special, consequential or punitive damages, or for lost profits, lost opportunity or lost data.

Where we are liable, our total liability for all claims in any twelve-month period is limited to the amount you actually paid us in the twelve months before the claim arose, or ₹5,000 if you paid us nothing.

NOTHING HERE EXCLUDES LIABILITY THAT CANNOT LAWFULLY BE EXCLUDED, including liability for fraud or wilful misconduct, or any liability you have as a consumer under applicable consumer-protection law.`,
      },
      {
        heading: '12. Indemnity',
        body: `You agree to cover our reasonable losses, including legal costs, arising from your breach of these Terms, your breach of an exchange's rules or of law applying to you, your trading activity, or a strategy you published causing loss to another user.`,
      },
      {
        heading: '13. Suspension and termination',
        body: `You may stop using the Platform and delete your account at any time. If you delete it while a paid term is running, the Refund and Cancellation Policy decides what happens to the amount paid.

We may suspend or terminate your account if you breach these Terms, if law requires it, or if continuing would create a serious risk to the Platform or other users. Where practical and lawful, we will tell you why.

On termination your licence ends, running strategies stop, and we handle your data as set out in the Privacy Policy.`,
      },
      {
        heading: '14. Changes to these Terms',
        body: `We may update these documents. When we do, we publish the new version with a new version identifier and effective date.

For changes that materially affect your rights or obligations, WE WILL ASK YOU TO ACCEPT the new version before you continue using the Platform, and we will record that acceptance. For minor changes we update the published version and date.

If you do not accept a new version you may stop using the Platform and cancel.`,
      },
      {
        heading: '15. Governing law, and disputes',
        body: `These Terms are governed by the laws of ${ENTITY.governingLaw}.

Please contact us first at ${ENTITY.grievanceEmail} so we can try to resolve the problem directly. Most issues are resolved this way.

Subject to the next paragraph, you and we agree that ${ENTITY.venue} are the preferred forum for disputes arising out of these Terms.

IF YOU ARE A CONSUMER, nothing in this section removes your right to bring a complaint before a consumer forum where you reside or work, or any other right you have under the Consumer Protection Act, 2019 or equivalent law where you live. Mandatory consumer rights are unaffected by these Terms.`,
      },
      {
        heading: '16. Grievance contact',
        body: `For complaints, including about content or about how we handle your data, contact our Grievance Officer at ${ENTITY.grievanceEmail}, or write to ${ENTITY.legalName}, ${ENTITY.address}.

We aim to acknowledge within 48 hours and resolve within the period applicable law requires.`,
      },
      {
        heading: '17. General',
        body: `If a provision is unenforceable, the rest continues to apply. If we do not enforce a right immediately, we do not lose it. You may not transfer your rights without our consent; we may transfer ours as part of a reorganisation or sale, on notice to you. These Terms with the Privacy Policy, Risk Disclosure and Refund and Cancellation Policy are the whole agreement between us about the Platform.`,
      },
    ],
  },

  risk: {
    id: 'risk',
    title: 'Risk Disclosure',
    sections: [
      {
        heading: 'Read this before you trade',
        body: `TRADING CRYPTO-ASSETS CARRIES A HIGH RISK OF LOSING MONEY. Using software to trade automatically does not reduce that risk and can increase it, because a program can place many orders quickly without asking you again.

YOU CAN LOSE ALL THE MONEY YOU TRADE WITH. Do not trade with money you cannot afford to lose. Do not trade with borrowed money. Do not trade with money you need for anything else.

Nothing on this Platform is investment advice. ${ENTITY.name} is not a registered investment adviser, broker, portfolio manager or research analyst, and is not registered with SEBI or any equivalent regulator in such a capacity. No one at ${ENTITY.name} is telling you what to buy or sell.`,
      },
      {
        heading: 'Automated trading places real orders without asking you again',
        body: `This is the risk most specific to this Platform and the one most easily underestimated.

Once you start a strategy in live mode it can place orders on your exchange account on its own, as often as its logic dictates, including while you are asleep or offline, and including orders you would not have placed yourself.

A STRATEGY DOES EXACTLY WHAT IT WAS CONFIGURED TO DO, which is not always what you intended. A position size with an extra zero, an inverted condition, a stop that never triggers — each becomes real orders and real losses.

Monitor running strategies. Know how to stop them. Understand that stopping a strategy DOES NOT CLOSE positions it has already opened.`,
      },
      {
        heading: 'Backtests do not predict live results',
        body: `A backtest tells you how a strategy would have behaved on past data under modelling assumptions. It does not tell you what will happen next.

Backtests here, like backtests generally, are subject to overfitting — a strategy tuned until it fits the history it was tuned on — and to look-ahead bias, survivorship bias and unmodelled market impact. Fees, slippage and liquidity are approximations.

A strategy that performed well in a backtest can lose money immediately in live trading. Treat a backtest as a filter for bad ideas, not evidence of a good one.`,
      },
      {
        heading: 'Paper trading is not live trading',
        body: `The paper simulator fills orders against simulated balances. It does not reproduce slippage on a thin order book, partial fills, order rejection, exchange rate limits, exchange downtime, or the price impact of your own order.

PAPER RESULTS ARE SYSTEMATICALLY MORE FAVOURABLE than live results. A strategy profitable on paper may not be profitable with real money.`,
      },
      {
        heading: 'Machine-learning and AI output can be wrong',
        body: `The Platform can train models and use their output inside your strategies, and includes an AI assistant.

Model output is a statistical guess about data it was trained on. It carries no understanding of markets, it degrades when conditions change from the training period, and IT CAN BE CONFIDENTLY WRONG. The AI assistant can also be wrong, including about how the Platform itself works.

If a model's output drives your orders, the losses are still yours. Do not treat model output or AI answers as advice or as a prediction you can rely on.`,
      },
      {
        heading: 'Marketplace strategies are not vetted for profitability',
        body: `A strategy published by another user IS NOT REVIEWED BY US for whether it makes money, and publication is not an endorsement.

Performance figures shown with a listing may be backtested rather than achieved with real money. A creator's past results do not predict yours: you may run the same strategy on different capital, a different exchange, at a different time, and get a materially different outcome.

You are responsible for a strategy you subscribe to exactly as if you had written it yourself.`,
      },
      {
        heading: 'Exchange risk is outside our control',
        body: `Your assets sit at a third-party exchange, not with us. Exchanges have failed, frozen withdrawals, been hacked, suspended trading and shut down, sometimes without warning, and users have lost everything held there.

We have no control over any exchange, no ability to recover assets held at one, and no responsibility for what one does. Your relationship with your exchange is governed by its terms, not ours.

Choose which exchanges you connect to, and how much you keep at them, accordingly.`,
      },
      {
        heading: 'Technology risk',
        body: `Software has bugs. Networks fail. APIs change, rate-limit and go down. Any of these can cause an order to be placed that you did not want, an order you did want not to be placed, a position to go unmanaged, or data on screen to be stale or wrong.

This applies to our software and to every exchange and data provider it talks to. Do not run a strategy you could not tolerate failing at the worst possible moment.`,
      },
      {
        heading: 'Volatility, liquidity and leverage',
        body: `Crypto-asset prices move further and faster than most markets, trade continuously including weekends, and can gap through the price at which you hoped to exit. A STOP-LOSS IS NOT A GUARANTEE of the price you get.

Thin markets can move against you simply because you traded. If you use leverage — at an exchange, not through us — losses are multiplied and you can be liquidated, losing more than you put into a position.`,
      },
      {
        heading: 'Tax and legal responsibility are yours',
        body: `Gains from crypto-assets are taxed, and how depends on where you live. In India, income from transferring virtual digital assets is taxed under a specific regime and tax may be deducted at source on transfers; the rules are specific and change.

WE DO NOT GIVE TAX ADVICE and we do not compute, withhold or report your trading taxes. Keep your own records and take your own professional advice.

Whether you may lawfully trade crypto-assets or use automated trading software is a question about where you are, and it is yours to answer.`,
      },
      {
        heading: 'No guarantee, of anything',
        body: `We do not promise that the Platform will be profitable, that a strategy will work, that a backtest will be reproduced, that an order will be executed, or that data will be accurate.

PAST PERFORMANCE — yours, a creator's, or a backtest's — DOES NOT INDICATE FUTURE RESULTS.`,
      },
    ],
  },

  privacy: {
    id: 'privacy',
    title: 'Privacy Policy',
    sections: [
      {
        heading: '1. Who controls your data',
        body: `${ENTITY.legalName}, ${ENTITY.address}, is the controller of the personal data described here.

For privacy questions or to exercise a right in section 9, contact ${ENTITY.privacyEmail}. For a formal complaint, contact our Grievance Officer at ${ENTITY.grievanceEmail}.

IF YOU ARE IN THE EUROPEAN ECONOMIC AREA OR THE UNITED KINGDOM: we are established in India, not in the EEA or the UK, and we offer this service to people in both. Our representative for the purposes of Article 27 of the GDPR, and of the equivalent provision of the UK GDPR, is:

${ENTITY.euRepresentative}

${ENTITY.ukRepresentative}

You may contact the representative instead of us on any matter relating to our processing of your personal data.

This version is ${LEGAL_VERSION}, effective ${LEGAL_EFFECTIVE_DATE}.`,
      },
      {
        heading: '2. What we collect',
        body: `WHAT YOU GIVE US
• Account: email address, password (stored only as a cryptographic hash), and display name, username, avatar and bio if you set them.
• If you sign in with Google: the email address and basic profile Google returns.
• Billing: your plan, billing period and payment references. Card details go to our payment partner and are never received or stored by us.
• Exchange API credentials: see section 4.
• Content you create: strategies, backtest configurations and results, model-training runs, marketplace listings, support tickets, and messages to the AI assistant.
• If you joined a waiting list: name, email, Telegram handle if given, stated experience level, trader type and monthly volume.
• Payout details if you earn marketplace revenue.

WHAT WE COLLECT AUTOMATICALLY
• IP address, browser and device information, pages visited and actions taken.
• Authentication session cookies.
• Error diagnostics, including a stack trace and the technical context of a failure.
• With your consent only: analytics and session-behaviour data (section 6).

WHAT WE DO NOT COLLECT
We do not operate a KYC process and do not collect identity documents, PAN, Aadhaar, phone number or postal address. We read your exchange balances only to display your own portfolio to you. We never hold your assets or any claim to them.`,
      },
      {
        heading: '3. Why we use it, and our lawful basis',
        body: `• To provide the Platform, run your strategies and show you your own data — necessary to perform our contract with you.
• To take payment and manage your plan — contract.
• To keep the Platform secure, investigate abuse and prevent fraud — our legitimate interest in operating a secure service.
• To fix faults, using error diagnostics — legitimate interest.
• To answer support requests — contract and legitimate interest.
• To send service messages about your account, security or billing — contract. These are not marketing and you cannot opt out while you hold an account.
• To send product updates and marketing — only with your consent, withdrawable at any time.
• For analytics and session recording — only with your consent (section 6).
• To comply with law, including tax, accounting and lawful requests from authorities — legal obligation.

WE DO NOT SELL YOUR PERSONAL DATA. We do not use your data to train machine-learning models that we offer to other users.`,
      },
      {
        heading: '4. Exchange API credentials',
        body: `This is the most sensitive thing you give us, so this section is specific.

Your API key and secret are encrypted before storage using authenticated symmetric encryption, and the encrypting key is held in a managed secrets service separate from the database.

TRADE PERMISSION IS REQUIRED; WITHDRAWAL PERMISSION IS REFUSED. A credential needs trade permission to place the orders you configure. We check a credential's permissions before storing it: IF IT CAN WITHDRAW FUNDS WE REFUSE IT AND DO NOT STORE IT, because a trading platform never needs the ability to move your money and holding such a key would turn any compromise into theft. If we cannot verify a credential's permissions, we refuse it rather than guess.

We recommend you also restrict the credential to our server addresses at the exchange. We warn you when you have not, but we do not currently require it.

You can delete a stored credential at any time. Deletion removes it from our systems; it does not revoke it at the exchange, which you should also do.`,
      },
      {
        heading: '5. Who we share it with',
        body: `We share personal data only with providers who process it for us, under contract, for the purposes above:

• Supabase — authentication and the main database.
• Amazon Web Services — hosting, storage, secrets management and logging.
• Razorpay — payment processing for Indian Rupee payments; an independent controller for its own compliance purposes.
• Stripe — payment processing where used for non-Rupee payments.
• Sentry — error diagnostics.
• Google Analytics — usage analytics. WITH YOUR CONSENT ONLY.
• Microsoft Clarity — session analytics, including recordings of page interactions and heatmaps. WITH YOUR CONSENT ONLY.
• Email delivery, and Telegram, Slack or Discord where you configure an alert to be sent there. Configuring such an alert sends content to that service.
• Cryptocurrency exchanges you choose to connect, which receive the API requests your strategies generate.

We also disclose data where law requires, to enforce our Terms, or to protect the rights and safety of users and the public. If the business is reorganised or sold, data may transfer as part of it and this Policy continues to apply until replaced.`,
      },
      {
        heading: '6. Cookies, analytics and your choice',
        body: `STRICTLY NECESSARY. We set cookies needed to keep you signed in and the Platform secure. These cannot be switched off without breaking the Platform and we do not ask consent for them.

ANALYTICS, ONLY IF YOU AGREE. Google Analytics and Microsoft Clarity LOAD ONLY AFTER YOU CONSENT. Microsoft Clarity records how you interact with pages, including pointer movement, clicks and scrolling. If you decline, these scripts are not loaded at all — not loaded-but-disabled.

You can change or withdraw your choice at any time from the cookie settings link in the footer. Withdrawing stops future collection; it does not retrieve what was already collected.

ERROR DIAGNOSTICS. Sentry captures faults so we can fix them, and is configured not to capture credentials or secret values.

WE DO NOT USE ADVERTISING COOKIES and we do not participate in cross-site advertising networks.`,
      },
      {
        heading: '7. Where your data is processed',
        body: `We operate in Amazon Web Services in the ASIA PACIFIC (SINGAPORE) region. Our database and authentication are provided by Supabase. Rupee payment processing happens in India.

SO IF YOU ARE IN INDIA, YOUR PERSONAL DATA IS PROCESSED OUTSIDE INDIA — in Singapore, and by providers that may process it elsewhere. If you are in the EU or UK, your data is processed outside those areas. Some providers operate globally and may process data in other countries.

Where law requires a safeguard for such a transfer we rely on our contracts with these providers, including standard contractual clauses where applicable. Ask ${ENTITY.privacyEmail} what applies to you.`,
      },
      {
        heading: '8. How long we keep it',
        body: `• Account and content: while your account exists.
• After you ask us to delete your account: we delete or irreversibly anonymise your personal data WITHIN 30 DAYS, except what law requires us to keep — principally billing and tax records for the statutory retention period, and records needed to resolve a dispute or meet a legal obligation.
• Waiting-list entries: up to 24 months.
• Error diagnostics: up to 90 days.
• Analytics data: per the retention configured with each provider.
• Security and audit logs: as long as needed for security and compliance.

Encrypted exchange credentials are deleted when you delete them, or with your account.`,
      },
      {
        heading: '9. Your rights',
        body: `Wherever you live, you may ask us to: give you a copy of your personal data; correct it; delete it; restrict or object to a particular use; send it to you or another provider in a portable, machine-readable form; and withdraw a consent you gave.

You may also object to processing we carry out on the basis of our legitimate interests, which section 3 identifies specifically rather than in general. We do not make decisions about you by purely automated means that have a legal or similarly significant effect on you.

IF YOU ARE IN THE EEA OR THE UK these are your rights under Articles 15 to 22 of the GDPR, and under the UK GDPR. You may lodge a complaint with the supervisory authority where you live or work, or where you believe the problem occurred — in the UK, the Information Commissioner's Office. You may also contact our Article 27 representative, named in section 1, instead of contacting us.

IF YOU ARE IN INDIA you may exercise the equivalent rights under applicable Indian data-protection law and complain to the competent authority.

Write to ${ENTITY.privacyEmail}. WE WILL RESPOND WITHIN ONE MONTH, which we may extend by two further months for a complex request — if we do, we will tell you within the first month and explain why. We may ask you to confirm who you are first, so we do not disclose your data to someone else. We do not charge for this.

Some requests have limits: we cannot delete what we are legally required to keep, chiefly billing and tax records, and deleting your account ends your access to it.`,
      },
      {
        heading: '10. Security, and its limits',
        body: `We encrypt data in transit, encrypt exchange credentials at rest, hold encryption keys in a managed secrets service, apply row-level access controls in the database, restrict administrative access, and log security-relevant events.

NO SYSTEM IS PERFECTLY SECURE AND WE DO NOT CLAIM OURS IS.

IF A BREACH HAPPENS: where it is likely to result in a risk to your rights and freedoms, we will notify the relevant supervisory authority WITHIN 72 HOURS of becoming aware of it. Where it is likely to result in a HIGH risk to you, we will tell you directly, without undue delay, and describe what happened, what data was involved, what we are doing about it and what you can do. We keep a record of every breach we become aware of, whether or not it is notifiable.

Use a strong, unique password. Enable multi-factor authentication where offered.`,
      },
      {
        heading: '11. Children',
        body: `The Platform is not for anyone under 18. We do not knowingly collect data from children. If you believe a child has given us personal data, tell us at ${ENTITY.privacyEmail} and we will delete it.`,
      },
      {
        heading: '12. Changes',
        body: `If we change this Policy we publish a new version with a new version identifier and effective date. Where a change materially affects you we will tell you and, where law requires, ask for your consent.`,
      },
    ],
  },

  refund: {
    id: 'refund',
    title: 'Refund and Cancellation Policy',
    sections: [
      {
        heading: '1. The short version',
        body: `Paid plans are sold for a fixed term and are NOT REFUNDABLE, except where law gives you a right to a refund or in the specific situations in section 4.

You can cancel at any time. Cancelling means you keep access until the end of the term you have already paid for, and are not charged again.

This version is ${LEGAL_VERSION}, effective ${LEGAL_EFFECTIVE_DATE}.`,
      },
      {
        heading: '2. What you are buying',
        body: `A paid plan gives you access to the Platform's features and limits for a FIXED TERM — one month or one year, as purchased.

Plans bought through our Indian payment partner are ONE-TIME PURCHASES FOR THAT TERM. They DO NOT AUTO-RENEW. Access ends when the term ends unless you buy again, and you will not be charged again without starting a new purchase yourself.

Published prices are in Indian Rupees and INCLUDE GST where it applies. Prices displayed in other currencies are conversions and may move with exchange rates.`,
      },
      {
        heading: '3. Cancelling',
        body: `Cancel from your billing settings, or email ${ENTITY.billingEmail}.

When you cancel, access continues to the end of the paid term and then stops. Cancelling does not shorten the term and does not by itself produce a refund of the amount already paid.

Deleting your account has the same effect on the amount paid as cancelling.`,
      },
      {
        heading: '4. When we do refund',
        body: `We will refund you in these cases:

• DUPLICATE CHARGE — you were charged more than once for the same thing. We refund the duplicates in full.
• WRONG AMOUNT — you were charged more than the published price for what you bought. We refund the difference.
• NEVER DELIVERED — your payment succeeded but the plan was never activated and we cannot activate it. We refund in full.
• UNAUTHORISED — a payment was made from your account without your authority and you tell us promptly. We investigate and refund if that is what happened.
• MATERIAL REDUCTION MID-TERM — we materially reduce what your plan provides during a term you have already paid for and you do not want to continue. We refund the unused part of the term, calculated by whole days.
• WHERE THE LAW SAYS SO — any refund your consumer-protection law entitles you to. This policy does not take that away.

Tell us WITHIN 14 DAYS of the charge for a billing error, or within 14 days of the change for a material reduction. Write to ${ENTITY.billingEmail} with the payment reference.`,
      },
      {
        heading: '5. When we do not refund',
        body: `To be straightforward, we do not refund because:

• You did not use the Platform, or used it less than you expected.
• YOU LOST MONEY TRADING, for any reason. Trading losses are never a basis for refunding subscription fees — see the Risk Disclosure.
• A strategy did not perform as you hoped, including one from the marketplace.
• A backtest or a model turned out not to predict live results.
• An exchange you connected failed, froze funds, or acted unexpectedly.
• You changed your mind after the term started, outside any cooling-off right the law gives you.
• You breached the Terms and we terminated your account.
• A service interruption that did not materially reduce the plan over the term.

WE DO NOT OFFER SERVICE CREDITS. An earlier version of this policy said we might issue pro-rated credits for outages beyond 72 hours; nothing ever implemented that, so it has been removed rather than left as a promise we do not keep. Where an interruption is serious enough to materially reduce your plan, section 4 applies instead.`,
      },
      {
        heading: '6. How a refund is made',
        body: `Approved refunds go back to the ORIGINAL PAYMENT METHOD through the payment partner that took the payment. We cannot refund to a different method or to a bank account we were not paid from.

We aim to approve or decline WITHIN 7 WORKING DAYS of receiving what we need. Once issued, the money reaching you depends on your payment provider — usually 5 to 10 working days.

Refunds are made in the currency charged. We do not compensate for exchange-rate movement between payment and refund.`,
      },
      {
        heading: '7. Marketplace purchases',
        body: `Subscribing to another user's strategy is a purchase from us and this policy applies to it.

WE DO NOT REFUND a marketplace subscription because the strategy lost money or underperformed. Creators are not vetted for profitability and publication is not an endorsement.

Where we refund a marketplace purchase, any creator share already allocated is reversed.`,
      },
      {
        heading: '8. Free plans and trials',
        body: `The free plan costs nothing, so there is nothing to refund. Where we offer a trial or free access period, no payment is taken and no refund arises.`,
      },
      {
        heading: '9. Complaints',
        body: `If you disagree with a refund decision, write to our Grievance Officer at ${ENTITY.grievanceEmail} with the payment reference and your reasons. We will look at it again and reply.

This does not affect your right to approach a consumer forum or other body your law provides, wherever you reside or work.`,
      },
    ],
  },
};

/**
 * The one-paragraph risk summary shown in the landing footer.
 *
 * SINGLE-SOURCED FOR THE SAME REASON AS THE DOCUMENTS. This paragraph lived in
 * components/landing/Footer.jsx as literal JSX and had drifted into saying two
 * inaccurate things:
 *
 *   "Paper trading mode is enabled by default" — implies live trading is a
 *   setting the reader can switch on. It is not: it is operator-granted per
 *   account (see Terms section 4). This was the same false implication the
 *   Terms carried, in a second place, which is exactly how the first one
 *   survived so long.
 *
 *   "all execution decisions are made by the user" — not true of an automated
 *   trading platform. The user decides the STRATEGY; once started, the strategy
 *   decides the orders, which is the single most important risk to convey and
 *   the opposite of what that sentence said.
 *
 * It is a SUMMARY and says so, pointing at the Risk Disclosure as authoritative.
 */
export const SUMMARY_DISCLAIMER =
  'Algorithmic trading involves substantial risk of loss and you can lose all '
  + 'the money you trade with. Past performance of backtests does not guarantee '
  + 'future results. VyomQuant provides software infrastructure only and never '
  + 'takes custody of your funds; you choose and configure the strategies, and a '
  + 'strategy you start can place orders on your own exchange account without '
  + 'asking you again. VyomQuant is not a registered investment adviser and '
  + 'nothing here is financial advice. See the Risk Disclosure.';

/** Route-segment order used by the footer and document navigation. */
export const DOCUMENT_ORDER = ['terms', 'privacy', 'risk', 'refund'];

/** Documents a user must accept at signup. */
export const SIGNUP_REQUIRED_DOCUMENTS = ['terms', 'privacy', 'risk', 'refund'];

export default DOCUMENTS;
