/**
 * pages/LegalPage.jsx — the four legal documents behind `/legal`.
 *
 * retail-ui-simplification task 7.7 (commit 13). Requirements 3.1, 3.3, 4.1, 4.3, 4.4,
 * 5.1, 5.2, 6.2, 19.6. design.md §2.4, §2.5.
 *
 * 15 absolute pixel sizes to 0 with the entry DELETED (Requirement 1.5), and 3 colour
 * literals to 0 with the entry moved out of the deferred block into the in-scope group.
 * Both budgets move in this commit.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE LEGAL TEXT IS UNCHANGED, WORD FOR WORD (Requirement 19.6)
 * ═══════════════════════════════════════════════════════════════════════════
 * Every sentence of the terms, the privacy policy, the risk disclosure and the refund
 * policy is carried across byte for byte, including the quoted `"AS IS"` / `"AS AVAILABLE"`
 * pair, the `⚠️` on the high-risk warning, and every numbered heading's own wording. Nothing
 * is reworded, shortened, summarised or "simplified". All four documents stay, in the same
 * order, under the same titles. This commit changes type step, colour, weight, case and
 * layout, and nothing else.
 *
 * Two consequences of that, stated because they look like omissions:
 *
 *   * **A heading whose wording is long stays long.** *1. Beta Service & Paper Trading
 *     Only* is six words and *2. Simulated / Paper Trading Limitations* is five. Requirement
 *     3.3 takes the ALL-CAPS off them; it does not authorise rewriting them.
 *   * **The prose gets no size class at all.** See THE 15 PIXEL SIZES below — this is the
 *     one page in the group where the right answer to §2.4 is to declare nothing.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE ONE PAGE THAT EXERCISES `FONT_SIZE_QUOTED` END TO END
 * ═══════════════════════════════════════════════════════════════════════════
 * Every one of the 15 was the quoted `fontSize: 'Npx'` syntax — `14px` ×12, `10px` ×2,
 * `16px` ×1 — and this is the only file in the tree of which that is true. So this commit
 * is also the proof that task 1.1's second pattern works on real bytes: had
 * `FONT_SIZE_QUOTED` been broken, this entry would have seeded at 0 and this commit would
 * measure no change at all.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * TWO OF TASK 7'S FIVE STEPS HAVE NOTHING TO DO HERE, AND THAT IS STATED
 * RATHER THAN SKIPPED — the shape task 7.5 set
 * ═══════════════════════════════════════════════════════════════════════════
 * **Step 1, `usePanelState`: THERE IS NO READ ON THIS PAGE.** It issues no request of any
 * kind — no `api.*` call, no supabase call, not even a mutation. All four documents are
 * authored copy held in this file. There is no `loading` flag and no `error` flag to
 * delete, because there was never anything to load. Requirement 4.1 is satisfied
 * vacuously, and pointing a reader hook at a constant would be a hook that can re-fetch
 * nothing.
 *
 * **Step 2, `pageFields.js`: THERE IS NO FIGURE ON THIS PAGE.** No count, no balance, no
 * timestamp, no percentage — nothing with a source path and nothing with a not-available
 * arm. Declaring an entry would put a page in `PAGE_FIELDS_BY_PAGE` with nothing to
 * declare, which makes the audit look complete where it is empty. Same argument as
 * `pages/UpdatePasswordPage.jsx` and `pages/TwoFA.jsx`.
 *
 * Steps 3, 4 and 5 are the whole of this commit.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE PRIMITIVES (Requirement 4.3)
 * ═══════════════════════════════════════════════════════════════════════════
 *   the title row + Back control  → `ds/PageHeader` with `ds/CommandButton` in `actions`
 *   the four-tab strip and panels → `ds/Tabs`
 *
 * **`ds/Tabs` is the tab strip's exact shape, so there is no Requirement 4.4 finding to
 * raise for it** — and it is a strict accessibility gain rather than a like-for-like swap.
 * The strip was four bare `<button>`s: reachable, but only by accident, because every
 * button is its own tab stop and nothing tied a button to the region it switched. The
 * primitive brings the APG tabs pattern — `role="tablist"`, `role="tab"` with
 * `aria-selected`, `role="tabpanel"` with `aria-labelledby`, a roving tabindex so the strip
 * costs ONE tab stop instead of four, and Home / End / ArrowLeft / ArrowRight. Enter and
 * Space still select, because these are still real buttons. So every control keeps the
 * keyboard path it had (Requirements 20.1–20.3) and the panel gains one.
 *
 * `onBack` is untouched: the same optional prop, the same handler on the same control, still
 * rendered only when a caller passes one. `App.jsx:593` passes none, so on `/legal` the
 * control is absent exactly as it is today.
 *
 * TWO THINGS THAT DID NOT SURVIVE, NEITHER OF THEM INFORMATION:
 *
 *   * **The `<h1>`'s `Shield` glyph.** `ds/PageHeader` has no icon slot, and a shield beside
 *     a heading reading *Legal & Risk Center* is the second statement of one fact. Same call
 *     as task 7.5's padlock. The heading text is unchanged.
 *   * **The Back button's two hover handlers.** They were dead in one respect already:
 *     `onMouseEnter` and `onMouseLeave` both assigned `token.surface.inset` to
 *     `background`, so the only thing the pair really changed was the label colour.
 *     `ds/CommandButton` carries hover as a class, which is also why the handlers could not
 *     come with it.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 15 PIXEL SIZES, BY §2.4'S NINE QUESTIONS (Requirement 2.4)
 * ═══════════════════════════════════════════════════════════════════════════
 *   14 ×12  the section headings   Q8 heading      → `--text-section` (+29%)
 *   16 ×1   the page `<h1>`        Q8 heading      → `ds/PageHeader`'s `--text-page`; removed
 *   10 ×1   the four tab buttons   Q8's region names → `ds/Tabs`' own step; removed
 *   10 ×1   the Back control       a command       → `ds/CommandButton`; removed
 *
 * **THE HEADINGS WERE SMALLER THAN THE TEXT THEY HEADED.** This is the measurement that
 * settles Q8's three-way choice, and it is worth stating plainly: the twelve `<h2>`s were
 * declared at 14px, while the paragraphs and list items beneath them declare NO size at all
 * and therefore render at the browser default — 16px. Tailwind's preflight resets a
 * heading's font-weight to `inherit`, so they were not bold either. A 14px normal-weight
 * heading over 16px prose is not a hierarchy; what made those lines read as headings was
 * the ALL-CAPS and the brand hue, and Requirement 3.3 takes the first of those away.
 *
 * So `--text-title` (14px) is refused here even though it is numerically exact: it would
 * leave every section heading in a legal document smaller than its own body text with the
 * caps gone as well. `--text-section` (18px) is the first step above the prose, it is what
 * Q8 gives a heading one level below the page `<h1>` in the document outline, and it is what
 * `pages/Wizard.jsx` resolved its own `<h2>`s to one commit ago. The weight moves with it:
 * `font-semibold` and the step together carry what the capitals were carrying.
 *
 * **THE PROSE KEEPS NO SIZE DECLARATION, DELIBERATELY.** Q1 sends a sentence to
 * `--text-body` **minimum**, and `--text-body` is 13px. The sentences on this page are
 * already at 16px and already scale with the reader's own browser setting, so "resolving"
 * them onto the step would be a **19% shrink of a legal document** — with no overflow to
 * yield to and therefore not even §2.5's illegal-shrink tell to argue about. The floor is a
 * floor. Requirement 2.1's clause is that a size must honour the reader's preference, and
 * text with no declaration honours it completely; there is nothing here for the ratchet to
 * count and nothing for this commit to add.
 *
 * The one thing that does move on the prose is the local `lineHeight: '1.6'` on all four
 * documents, which becomes `leading-relaxed` (1.625). Same leading to the naked eye, and it
 * is a declared utility rather than a hand-written number.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 12 `uppercase` USAGES, AGAINST REQUIREMENT 3.3'S THREE-WORD RULE
 * ═══════════════════════════════════════════════════════════════════════════
 * 12 `textTransform: 'uppercase'` in 146 lines — the highest density in the tree outside
 * `StrategyMarketplace.jsx`. All twelve are the section headings, and **all twelve go.**
 *
 * Seven are plainly over the line (*1. Beta Service & Paper Trading Only*, *3. Subscription
 * Tiers & Billing*, *2. How We Use Information*, *3. Data Storage & Security*, *1.
 * Algorithmic & Quantitative Trading Risk*, *2. Simulated / Paper Trading Limitations*, *1.
 * Information We Collect*). The other five sit on the boundary — *4. Prohibited Uses*, *2.
 * Eligibility & Accounts*, *1. Digital Subscription Tiers*, *2. Cancellation and Renewal*
 * and *⚠️ High-Risk Investment Warning* are three words plus an ordinal or a glyph — and
 * they go with the rest, because **twelve sibling headings in one document set are one
 * decision, not twelve.** A set where nine read as sentences and three shout is worse than
 * either uniform answer, and 3.3's mechanism is skim: a reader scanning a legal document for
 * the clause that applies to them is doing exactly the word-shape recognition all-caps
 * suppresses.
 *
 * TWO CANDIDATES OUTSIDE THE TWELVE, RESOLVED IN OPPOSITE DIRECTIONS:
 *
 *   * **`LEGAL & RISK CENTER` → `Legal & Risk Center`.** Three words and an ampersand, so
 *     the rule does not compel it — but it is now the page's `<h1>` at `--text-page`, and
 *     24px of capitals is where 3.3's mechanism costs the most. `ds/PageHeader` renders the
 *     title as given and applies no transform, so this is a change to the string's CASE and
 *     to nothing else: the same four words, the same accessible name for the route.
 *   * **The four tab labels KEEP their capitals.** `Terms of Service` is three words,
 *     `Privacy Policy`, `Risk Disclosure` and `Refund Policy` are two, so every one of them
 *     passes the three-word rule, and the `.toUpperCase()` call is left exactly where it
 *     was. Requirement 3.3 is a ceiling on length and not a ban, and a tab is the one
 *     construct on this page where a short all-caps label still buys a distinction. They do
 *     grow from 10px to `ds/Tabs`' own step; the strip carries `overflow-x-auto`, so the
 *     extra width scrolls rather than clipping — §2.5's question answered by the primitive.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * MONOSPACE: BOTH PAGE DECLARATIONS GO (Requirement 3.1)
 * ═══════════════════════════════════════════════════════════════════════════
 * `fontFamily: 'monospace'` was set on the whole card and then again on the tab buttons, so
 * **four legal documents rendered in a monospace stack.** Requirement 3.1 reserves monospace
 * for values whose character alignment or literalness carries meaning — a figure in a
 * column, an identifier, a symbol, a code. A refund policy is prose; it goes to the sans
 * stack, which is the app default and therefore needs no declaration.
 *
 * One monospace treatment remains on the page and it is not this page's: `ds/CommandButton`
 * renders through `components/ui/Button`, which applies `font-mono` to every label in the
 * app. That is task 6.24's file in the other spec, it is budgeted there, and restating it
 * here per page is how a primitive's decision becomes twenty pages' decisions.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 3 COLOUR LITERALS, AND FOUR OFF-PALETTE CONSTRUCTS NO GUARD CAN SEE
 * ═══════════════════════════════════════════════════════════════════════════
 *   rgba(14, 19, 25, 0.75)     the card's glassmorphic wash → `bg-surface-raised`
 *   0 20px 40px rgba(0,0,0,0.5) a hand-mixed elevation      → `shadow-raised`
 *   rgba(0, 0, 0, 0.2)         the content well             → `bg-surface-inset`
 *
 * The first two were a single visual idea between them — a translucent dark pane lifted off
 * the page by a shadow twice the depth of anything the token layer declares. `tokens.css`
 * declares three elevations and no translucent surface, and its own annotation for them is
 * "no coloured glows. Calm by default", so both resolve to the declared pair: the raised
 * surface and the raised shadow.
 *
 * The third is the one that needed a decision rather than a lookup, and it is recorded
 * because the VALUE moves even though the NAME is exact. `rgba(0,0,0,0.2)` is a black wash
 * that made the reading pane *darker* than the card; `--color-surface-inset` (`#1A202C`) is
 * marginally *lighter* than `--color-surface-raised` (`#151821`). The token layer's own
 * comment names `inset` for "inputs, wells", which is precisely what this element is, so the
 * declared name is followed rather than the old value matched — Requirement 5.3's rule
 * pointed at a surface instead of a hue. The well keeps its border, its radius and its
 * `minHeight`, so the pane is still a pane.
 *
 * FOUR CONSTRUCTS NEITHER GUARD COUNTS WENT WITH THEM, recorded because a scan will not find
 * them for the next reader:
 *
 *   * `backdropFilter: 'blur(20px)'` on the card — a glassmorphic wash the token layer does
 *     not declare anywhere. It blurred the background the first literal above supplied;
 *     with the surface opaque there is nothing left for it to do, and it is not reproduced.
 *   * `` `${token.brand.base}15` `` on the active tab's fill and `` `${token.line.default}80` ``
 *     on the well's border — tokens with hex-alpha digits concatenated onto them, i.e.
 *     hand-mixed 8% and 50% alphas wearing a token's name. **No `#` appears in either, so
 *     `no-colour-literals` never counted them.** The first goes with the tab strip, whose
 *     selected state `ds/Tabs` owns (a 2px `--color-brand` underline plus the label in
 *     `--color-brand`, marked on `aria-selected` as well as in hue); the second becomes a
 *     plain `border-line-default`. This is the same blind spot `pages/SecurityLogs.jsx`
 *     recorded at task 7.4 for `` `${token.line.default}15` `` and
 *     `pages/UpdatePasswordPage.jsx` at 7.5 for `` `${token.status.profit.fg}12` ``.
 *
 * ONE HUE IS DELIBERATELY NOT RE-DECIDED. The `⚠️ High-Risk Investment Warning` heading
 * reads `token.status.loss.fg` today and becomes `text-status-loss` — the same `#EF5350`,
 * through the same token, expressed as the utility class step 5 asks for. It is arguably a
 * *warning* rather than a *loss*, and `--color-status-warning` is amber, so renaming it
 * would change what a trader sees on a risk disclosure. Requirement 5.3 says to raise that
 * rather than pick, and a typography commit is not where an existing risk hue gets swapped:
 * recorded here, unchanged in the diff.
 */

import React, { useState } from "react";
import { ArrowLeft } from "lucide-react";

import { CommandButton } from "../components/ds/CommandButton";
import { PageHeader } from "../components/ds/PageHeader";
import { Tabs } from "../components/ds/Tabs";

/**
 * One section heading inside a document.
 *
 * `fontSize: '14px'` ×12 → `--text-section` (heading — Q8, one level below the page
 * `<h1>` in the outline), and `textTransform: 'uppercase'` ×12 removed per Requirement
 * 3.3. The step and `font-semibold` carry what the capitals carried; see the header for
 * why `--text-title` is refused despite being the exact number.
 *
 * `tone` exists for exactly one heading — the risk disclosure's warning, which reads the
 * loss hue today and keeps it. Every other heading is the brand hue, as it is now.
 */
const SectionHeading = ({ tone = "brand", children }) => (
  <h2
    className={`mb-3 text-section font-semibold ${
      tone === "loss" ? "text-status-loss" : "text-brand"
    }`}
  >
    {children}
  </h2>
);

/**
 * The four documents, in the order the tab strip has always shown them.
 *
 * `title` is the tab's label and is unchanged; `body` is the document, verbatim. The
 * paragraphs and list items carry NO size class — see the header: they are at the reader's
 * own default and `--text-body` would shrink them.
 */
const DOCUMENTS = Object.freeze([
  Object.freeze({
    id: "tos",
    title: "Terms of Service",
    body: (
      <>
        <SectionHeading>1. Beta Service & Paper Trading Only</SectionHeading>
        <p className="mb-3">The Beta Service is provided solely for testing, evaluation, and paper trading purposes.</p>
        {/* `listStyleType: 'square'` is kept as the bullet glyph these two clauses have
            always had; it is neither a colour nor a size, and Tailwind declares no square
            marker utility. `pl-5` is the same 20px, on the spacing scale. */}
        <ul className="mb-4 pl-5 text-content-secondary" style={{ listStyleType: "square" }}>
          <li><strong>NO REAL MONETARY TRANSACTIONS OR DEPLOYMENTS</strong>: You acknowledge that the Beta Service uses simulated balances and does not perform live trades on real exchange accounts.</li>
          <li><strong>AS-IS BASIS</strong>: Provided on an "AS IS" and "AS AVAILABLE" basis.</li>
        </ul>
        <SectionHeading>2. Eligibility & Accounts</SectionHeading>
        <p className="mb-4">You are responsible for keeping your credentials (including API keys, passwords, and 2FA settings) secure.</p>
        <SectionHeading>3. Subscription Tiers & Billing</SectionHeading>
        <p className="mb-4">Subscriptions are billed in advance. All transactions are processed via third-party providers (Stripe, Razorpay).</p>
        <SectionHeading>4. Prohibited Uses</SectionHeading>
        <p className="mb-4">You agree not to reverse engineer, decompile, or attempt to extract source code.</p>
      </>
    ),
  }),
  Object.freeze({
    id: "privacy",
    title: "Privacy Policy",
    body: (
      <>
        <SectionHeading>1. Information We Collect</SectionHeading>
        <p className="mb-3">We collect account credentials via Supabase Auth, encrypted exchange API keys, usage telemetry, and billing tokens.</p>
        <SectionHeading>2. How We Use Information</SectionHeading>
        <p className="mb-3">To authenticate users, execute backtests/model training, verify webhooks, and optimize platform performance.</p>
        <SectionHeading>3. Data Storage & Security</SectionHeading>
        <p className="mb-3">Authentication data is stored securely via Supabase Auth. API keys and secrets are encrypted in transit and at rest.</p>
      </>
    ),
  }),
  Object.freeze({
    id: "risk",
    title: "Risk Disclosure",
    body: (
      <>
        {/* The one heading that is not the brand hue. `token.status.loss.fg` → the same
            token as a class; see the header on why it is not renamed to `warning`. */}
        <SectionHeading tone="loss">⚠️ High-Risk Investment Warning</SectionHeading>
        <p className="mb-3">Trading financial instruments involves substantial risk of loss and is not suitable for every investor.</p>
        <SectionHeading>1. Algorithmic & Quantitative Trading Risk</SectionHeading>
        <p className="mb-3">Automated strategies execute based on predefined rules. Bugs, logic errors, or connection drops can lead to unexpected and costly executions.</p>
        <SectionHeading>2. Simulated / Paper Trading Limitations</SectionHeading>
        <p className="mb-3">Paper trading uses simulated balances and idealized execution. It does not account for real-world liquidity, market impact, slippage, or latency.</p>
      </>
    ),
  }),
  Object.freeze({
    id: "refund",
    title: "Refund Policy",
    body: (
      <>
        <SectionHeading>1. Digital Subscription Tiers</SectionHeading>
        <p className="mb-3">All subscription payments (Pro, Enterprise, and ML Add-ons) are digital software entitlements activated immediately. All purchases are final and non-refundable.</p>
        <SectionHeading>2. Cancellation and Renewal</SectionHeading>
        <p className="mb-3">You may cancel your subscription at any time. Upon cancellation, you retain access until the end of your billing period.</p>
      </>
    ),
  }),
]);

/**
 * `ds/Tabs`' items, one per document.
 *
 * The label keeps its `.toUpperCase()` — three words or fewer, so Requirement 3.3 permits
 * it (see the header). The well around each document is the `rgba(0,0,0,0.2)` panel
 * retokened: `minHeight` is the page's own, and it is a MINIMUM rather than a fixed height,
 * so grown text extends the pane instead of being clipped — §2.5's proxy asks for exactly
 * that distinction.
 */
const TAB_ITEMS = DOCUMENTS.map((doc) => ({
  id: doc.id,
  label: doc.title.toUpperCase(),
  content: (
    <div
      className="rounded-lg border border-line-default bg-surface-inset p-5 leading-relaxed text-content-primary"
      style={{ minHeight: 280 }}
    >
      {doc.body}
    </div>
  ),
}));

export const LegalPage = ({ onBack, go }) => {
  const [activeTab, setActiveTab] = useState("tos"); // tos, privacy, risk, refund

  return (
    <div
      className="mx-auto my-10 w-full rounded-xl border border-line-default bg-surface-raised p-6 shadow-raised"
      style={{ maxWidth: 800 }}
    >
      {/* fontSize: 16 → `ds/PageHeader`'s `--text-page` (heading — Q8, the page's own
          `<h1>`), and `LEGAL & RISK CENTER` → title case per Requirement 3.3. The Back
          control's fontSize: 10 goes with it, onto `ds/CommandButton`. */}
      <PageHeader
        title="Legal & Risk Center"
        actions={
          onBack ? (
            <CommandButton intent="secondary" icon={ArrowLeft} onClick={onBack}>
              Back
            </CommandButton>
          ) : null
        }
      />

      {/* fontSize: 10 on the four tab buttons → `ds/Tabs`' own step. Controlled, so the
          page keeps the one piece of state it has always had. */}
      <Tabs
        label="Legal documents"
        items={TAB_ITEMS}
        value={activeTab}
        onChange={setActiveTab}
      />
    </div>
  );
};

export default LegalPage;
