/**
 * pages/Profile.jsx — the account: identity, security posture, subscription, notification
 * preferences and the referral programme.
 *
 * retail-ui-simplification task 7.8 (commit 14). Requirements 3.1, 3.2, 4.1, 4.2, 4.3, 4.4,
 * 5.1, 5.2, 6.2, 19.1, 19.2, 19.3, 19.5. design.md §2.4 (the nine-question procedure), §6.
 *
 * **79 absolute pixel sizes to 0 — the largest single-file count in the tree** — 7 colour
 * literals to 0, and 51 inline `monospace` declarations resolved against Requirement 3.1.
 * Both budgets are lowered in this commit and the font-size entry is DELETED (Requirement
 * 1.5).
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THIS PAGE TOLD EVERY ACCOUNT ITS SECOND FACTOR WAS ON
 * ═══════════════════════════════════════════════════════════════════════════
 * The *MFA AUTHENTICATION* tile rendered a green dot and the word *Configured* as static
 * JSX. There was no read behind it: the page fetched `GET /api/user/profile`, which answers
 * the `profiles` row, and nothing in that row reports an enrolled factor. So an account with
 * NO second factor read *Configured*, in green, on the panel a trader opens to check exactly
 * that. `supabase.auth.mfa.getAuthenticatorAssuranceLevel` is what answers the question and
 * `pages/Wizard.jsx` already calls it; this page never did.
 *
 * That was the worst of **eleven kinds** of substituted figure — more than any other page in
 * this spec, `Wizard.jsx` included. Each one is declared in `design/pageFields.js` under a
 * `profile` page with its real source path or its explicit unavailability, and with the
 * sentence a trader now reads instead of the number. The full account is there; the short
 * version, so a reviewer knows what to look for in the diff:
 *
 *   1. *Configured* under MFA AUTHENTICATION — static JSX, no read. See above.
 *   2. An *ACCOUNT ACTIVE* pill and a *PROTECTED* pill, both static JSX, both green.
 *   3. `planDisplay` fell through `billing?.name` — **not a key on that response** — to the
 *      literal `'Free Tier'`, so a PAID account whose billing read failed was told it was on
 *      the free tier, in the header badge as well as on the subscription card.
 *   4. `isSubscriptionActive` ORed the server's own `subscription_status` with
 *      `billing?.autoRenew` — also not a key — and with `plan !== 'free'`, so **any non-free
 *      plan reported *Active* whatever the server said**. A `past_due` or `canceled` Pro
 *      subscription rendered Active with a green dot.
 *   5. The renewal line fell back to *Standard 30-day Cycle* — a billing-cycle claim.
 *   6. The environment tile rendered *Live + Paper* and a green *Isolated* beneath it.
 *      `design/semantic.js:236` is the rule that breaks from both directions at once.
 *   7. Four figures off `GET /api/stats` under a card titled *Automation Account Context*.
 *      **That endpoint is platform-wide and unauthenticated** (`backend_app/main.py:938`
 *      declares it with no `Depends(get_current_user)` and calls the aggregation service
 *      with the literal user id `"public"`), and its `total_pnl` is filled from
 *      `overview["today_pnl"]` — so *TOTAL PNL* was wrong about its period as well as about
 *      whose account it described. All four were read with `|| 0`.
 *   8. A referral code manufactured from the account's own UUID:
 *      `referral.referral_code || profile?.id?.substring(0, 8).toUpperCase() || '...'`, with
 *      the copy button copying the same expression — in a field whose entire purpose is to
 *      be handed to other people.
 *   9. `referral.referral_link || 'https://...'`, beside its own copy button.
 *  10. A *20% RECURRING* pill. `ReferralStatsResponse` reports counts, balances and history
 *      and no rate, so the number was a marketing claim rendered as a reading.
 *  11. Three literals on a security audit record — `ip_address || ip || '127.0.0.1'`,
 *      `created_at ? … : 'Active Session'`, and each row's time falling back to *Recent*.
 *      `pages/SecurityLogs.jsx` carried the same three and task 7.4 removed them there: the
 *      same table was read twice and only one reader had been fixed.
 *
 * Plus `'Trader'`, `'unconfigured'`, `'No email'`, `'USER'` and `'Loading...'`, each standing
 * in for a profile field that was not read. A genuine `0`, a genuinely free plan and a
 * genuinely unverified email are all readings and all still render as themselves
 * (Requirement 19.1); what is gone is the substitution that made them indistinguishable from
 * a failed read.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * ONE `loading`/`error` PAIR OUT, SIX PANEL STATES IN (Requirement 4.1)
 * ═══════════════════════════════════════════════════════════════════════════
 * What was here: `loading`, `error` and `errorType` — a hand-rolled seven-way status
 * classification — over one `Promise.allSettled` of six reads, with a "primary contract
 * gate" on the profile read and **five `console.warn` calls as the entire failure handling
 * for the other five**. Each of those five set its state to `null` or `[]` and the page then
 * rendered a plausible constant in its place, which is how items 3 to 11 above stayed
 * invisible: a failed billing read and a free account produced the same screen.
 *
 * Each read now drives its own `usePanelState`, so each panel says what happened to ITS read
 * and a failure is a state rather than a warning in a console nobody has open:
 *
 *   profile   → the identity panel, and the header's tier / status / member-since cluster
 *   billing   → the subscription panel
 *   security  → the recent-access panel (last access, and the three-row audit trail)
 *   stats     → the platform-statistics panel
 *   notif     → the notification panel
 *   referral  → the referral panel
 *
 * Six hooks rather than one because the six failures are six different facts, and only one of
 * them stops a trader recognising their own account. Same six paths, same
 * `getSecurityLogs(20)` limit, once each on mount (`deps: []`) — `api-paths.budget.js` is
 * untouched and no request, route or query parameter changed.
 *
 * **The `AbortController` is gone and nothing regressed.** It was constructed, aborted on
 * unmount and on every re-read, and **passed to no request** — none of the six `api.*` calls
 * takes a signal — so it cancelled nothing. `usePanelState` solves the problem it was aiming
 * at properly, with a monotonic read id that drops a superseded outcome instead of trying to
 * stop it arriving.
 *
 * **Every recovery control survives, and the set is now a superset.** The old error screen
 * offered *Go to Login* (401/403), *Retry Connection* (offline), *Retry* + *Refresh Page*
 * (5xx/timeout/422/unknown) and *Contact Support* (404), each reachable only from its own
 * branch. All of them live in the identity panel's header now, which `ds/Panel` renders in
 * every state: *Try again* and *Reload the page* always, *Sign in again* when the read came
 * back unauthorised, *Contact support* when it came back 404. *Retry Connection* and *Retry*
 * were the same control under two labels — both called `handleRetry` — so they are one
 * control with one label, and the retry counter it maintains is still on screen.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 51 INLINE `monospace` DECLARATIONS (Requirements 3.1, 3.2)
 * ═══════════════════════════════════════════════════════════════════════════
 * The second-largest such block in the tree after Marketplace's original 69, and Requirement
 * 3.2 asks for the value-versus-prose classification to be recorded rather than just applied.
 * It is, in full — 51 sites, by line number in the pre-migration file:
 *
 * **11 are a VALUE or an IDENTIFIER and keep the monospace treatment** (Requirement 3.1's
 * first clause — a figure or an identifier is read character by character, and a proportional
 * face makes `l`/`1` and `0`/`O` ambiguous in exactly the strings where that matters):
 *
 *   :515  `@username`                  the account handle
 *   :617  the account identifier       a UUID, in a `<code>`
 *   :678  the last-access IP address
 *   :735  the active-bot count         → now `ds/Metric`
 *   :741  the P&L figure               → now `ds/Metric`
 *   :943  the referral code
 *   :958  the referral signup link
 *   :975 :979 :983 :987                the four referral figures → `ds/Metric`
 *
 * Seven of those eleven keep it *through a primitive* rather than through a page declaration:
 * `ds/Metric` renders every numeric format as `font-mono tabular-nums`, so the declaration
 * disappears while the treatment survives. The other four keep an explicit `font-mono` class
 * because they are identifiers rather than figures.
 *
 * **40 are PROSE or a LABEL and lose it** (Requirement 3.1's second clause). Grouped, so the
 * list is checkable rather than just long:
 *
 *   17 uppercase eyebrow labels naming an adjacent value — `MFA AUTHENTICATION` (:662),
 *      `LAST ACCESS TIME` (:674), `ACTIVE BOTS` (:734), `TOTAL PNL` (:740), `ENVIRONMENT`
 *      (:748), `CURRENT TIER` (:788), `STATUS` (:792), `INCLUDED CAPABILITIES` (:806),
 *      `REFERRAL CODE` (:941), `SIGNUP LINK` (:956), `TOTAL`/`ACTIVE`/`PENDING`/`LIFETIME`
 *      (:974 :978 :982 :986), and the three `──` category rules (:842 :865 :888)
 *    7 field and row labels — the four editor labels (:534 :542 :550 :559) and the three
 *      metadata row labels `Email Address` / `Telegram Dispatch` / `Account Identifier`
 *      (:592 :607 :615). All seven are `ds/Field`'s or a `<label>`'s now
 *    7 chips — `ACCOUNT ACTIVE` (:439), `TIER:` (:456), `Member since` (:472), `ROLE:`
 *      (:518), `PROTECTED` (:655), `TRADING ENGINE` (:727), `20% RECURRING` (:933). A chip
 *      is three words of state, not a figure
 *    4 sentences — the loading wrapper (:342), the page subtitle (:422), the notification
 *      preamble (:835) and the retry-counter line (:393)
 *    3 commands and headings — `RECENT SECURITY AUDIT TRAIL` (:687), `Review All Logs`
 *      (:690), the renewal-cycle line (:800)
 *    2 the log row timestamp (:704) and the trader's own bio textarea (:574). A timestamp is
 *      a value but not an identifier, and `pages/SecurityLogs.jsx` set the precedent at task
 *      7.4: its `ipAddress` cell carries `font-mono` and its `recordedAt` cell does not
 *
 * 17 + 7 + 7 + 4 + 3 + 2 = 40, and 40 + 11 = 51, which is what the scan measures.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 79 PIXEL SIZES, BY §2.4'S NINE QUESTIONS (Requirement 2.4)
 * ═══════════════════════════════════════════════════════════════════════════
 * Distribution: 10 ×27, 11 ×13, 9 ×10, 12 ×7, 13 ×7, 8 ×4, 14 ×4, 16 ×4, 18 ×1, 20 ×1,
 * 24 ×1. **54 of the 79 sit at or below 11px**, so almost every resolution is upward —
 * §2.5's "growth is the normal case" on the largest file in the group. Each resolution is
 * recorded as a comment on its own call site in the form `// fontSize: 10 → --text-micro
 * (label)`; the ratchet blanks comments, so the audit trail costs nothing and the file
 * measures 0. The cohorts:
 *
 *   **Roughly half leave with their call site**, because the step belongs to the primitive
 *   rather than to this page: the page `<h1>` (20) and its subtitle (11) onto
 *   `ds/PageHeader`; the loading block (13, 11) and the whole error screen (16, 13, 11) onto
 *   `ds/Panel`'s own arms; the four card headings (14 ×4) onto `ds/Panel`'s `<h2>`; the
 *   eight platform and referral labels and their eight figures (18 ×1, 16 ×1, 13 ×4, 10 ×4,
 *   8 ×4) onto `ds/Metric`; the verified / subscription chips (10, 12) onto `ds/StatusBadge`;
 *   the command labels onto `ds/CommandButton`; the four editor labels (10 ×4) onto `ds/Field`
 *   **9 ×10 and 10 ×n → `--text-micro`** where the site survives as an eyebrow label (Q4) or
 *   a chip (Q5)
 *   **11 ×6 → `--text-body`** — Q1 sentences, Q3 control text (the bio textarea) and Q7
 *   secondary values (the `<code>` identifier, the `@handle`)
 *   **12 ×7 → `--text-micro` (Q4 row labels) or `--text-title` (Q8 switch names)**
 *   **8 ×4 → `--text-micro`** — the four referral figure labels, two pixels below the floor
 *   and +25% each. They were the smallest text on the page and they name money
 *   **16 ×2 → `--text-section`** — the identity name and the tier name
 *   **20 ×1 → `--text-page`** (Q8, through `ds/PageHeader`)
 *   **24 ×1 → declaration REMOVED** — it sized no text at all (see below)
 *
 * Four resolutions carry an argument rather than a lookup.
 *
 * **The two 16px values are §2.2's undefined case, resolved up.** 16 is equidistant from
 * `--text-title` (14) and `--text-section` (18), so "nearest step" has no answer — which is
 * §2.2's second argument against a numeric mapping, made on this page twice. Both are Q7
 * values and both are their panel's hero, and §2.2 resolves a tie UP because Requirement 2.2
 * sets a floor and no ceiling.
 *
 * **The 24px is not a font size at all.** It sat on the 56px avatar circle, whose content is
 * either an `<img>` or a 26px lucide glyph carrying its own `size` prop. There has never been
 * text in that container, so the declaration is removed rather than mapped — §2.4's procedure
 * runs on rendered text, and there is none here.
 *
 * **The four 8px referral labels are the layout-yield case.** `TOTAL`, `ACTIVE`, `PENDING`
 * and `LIFETIME` sat in a `repeat(4, 1fr)` grid inside the narrower of two columns, at 8px —
 * the smallest text in the tree outside `PaperTrading.jsx` and `SecurityLogs.jsx`'s `<th>` —
 * labelling two counts and two money figures. At `--text-micro` they grow 25% and
 * `repeat(4, 1fr)` stops fitting, so the grid becomes 2-up below `sm` and 4-up above it,
 * which is §2.5's third mechanism. Shrinking the figures instead would have been §2.5's named
 * tell for an illegal shrink.
 *
 * **The switch label/description pair is Q8 over Q4, and that ordering is `RiskSettings`'s.**
 * The label (12) names a region holding an icon, an explanation and a control — so Q4 misses
 * it, because a control is not a value — and goes to `--text-title`; the description (10) is
 * a sentence and goes to `--text-body`, +30%. The name stays above the explanation in the
 * hierarchy at 14 over 13, exactly as task 7.2 resolved the identical construct.
 *
 * One cohort is removed rather than resolved: `Loading Trading Account Controls…` and
 * *Synchronizing authoritative user contracts* were a hand-built spinner block, and
 * `ds/Panel`'s `loading` arm renders a skeleton shaped like the content it replaces. The
 * second sentence goes with them — a synchronisation of "authoritative user contracts" is
 * not a thing a retail trader is waiting for.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 7 COLOUR LITERALS (Requirements 5.1, 5.2, 5.3)
 * ═══════════════════════════════════════════════════════════════════════════
 * Five of the seven are one hue, spelled four different ways across four pills:
 *
 *   rgba(38, 166, 154, 0.12)  ACCOUNT ACTIVE wash    ┐ all `#26A69A`, which is
 *   rgba(38, 166, 154, 0.3)   ACCOUNT ACTIVE border  ├ `token.status.live.fg` /
 *   rgba(38,166,154,0.1)      Verified wash          │ `.connected.fg` / `.profit.fg`,
 *   rgba(38,166,154,0.12)     PROTECTED wash         │ and whose declared 12% wash is
 *   rgba(38,166,154,0.1)      20% RECURRING wash     ┘ exactly the value being hand-mixed
 *
 * None of them is mapped by hand. The one pill that survives as a pill — *Verified* — becomes
 * `ds/StatusBadge`, which takes the server's state and resolves the hue through `statusToken`.
 * That is Requirement 5.3's point exactly: `design/semantic.js` is the only module that knows
 * which token means "confirmed". The other three pills do not survive at all, because ACCOUNT
 * ACTIVE, PROTECTED and 20% RECURRING were the fabrications above, so their literals go with
 * the claim rather than being retokened into it.
 *
 * The remaining two are the notification switch's knob and its shadow: `#fff` →
 * `token.content.primary` (pure white is not in the palette) and
 * `boxShadow: '0 1px 3px rgba(0,0,0,0.3)'` → `token.shadow.panel`, which is the declared
 * elevation and is the same substitution task 7.2 made on `RiskSettings`'s identical knob.
 *
 * Three off-palette constructs neither guard can see go with them, recorded because a scan
 * will not find them for the next reader: `${token.brand.base}33`, `${token.brand.base}15`
 * and `${token.brand.base}40` — a token with two hex digits concatenated onto it, which is a
 * hand-mixed alpha wearing a token's name and carries no `#`, so `no-colour-literals` never
 * counted any of the three. `token.brand.wash` is the declared wash and is what they become.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * REQUIREMENT 4.4 FINDINGS — RECORDED, NOT RESOLVED
 * ═══════════════════════════════════════════════════════════════════════════
 * Nothing new is added to `ds/`, to `usePanelState` or to `src/design/`. Three gaps were
 * reached and each one is a finding rather than a primitive:
 *
 *   1. **`ds/` has no switch.** `NotificationSwitch` therefore stays page-local, which is the
 *      same finding `pages/RiskSettings.jsx` recorded for its four kill switches at task 7.2.
 *      It keeps the whole of its accessibility contract — `role="switch"`, `aria-checked`, an
 *      accessible name, a tab stop, and Enter/Space activation that `preventDefault`s Space
 *      so the page does not scroll the setting out from under the trader — and every colour
 *      it paints now comes from `token`.
 *   2. **`ds/Field` has no multiline variant.** `resolveInputType` covers text, search, date,
 *      number, decimal, currency and select; there is no `textarea`. The bio field stays a
 *      hand-built `<textarea>` with its own `<label htmlFor>`, at `--text-body`. Adding
 *      `ds/Textarea` for one page is how a second design system starts.
 *   3. **`ds/CommandButton` is a command, not a disclosure.** The reveal/hide control on the
 *      account identifier is a toggle with a pressed state, so it stays a native `<button>`
 *      with an accessible name and `aria-pressed` — which the `title`-only version did not
 *      have. The two copy controls beside it ARE commands and are `ds/CommandButton`s.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT MUST NOT BE LOST, AND WHAT PROVES IT WAS NOT
 * ═══════════════════════════════════════════════════════════════════════════
 * Every account action is carried across with its request unchanged:
 * `api.user.updateProfile(editData)` with the same four whitelisted fields and the same
 * optimistic merge, and `api.user.updateNotificationSettings(payload)` with the payload
 * **constructed by exactly the same expression** — both `??` chains, both boolean coercions,
 * the same eight event keys and the same three channels, and the same revert-to-previous on
 * failure. `routers/user.py:139` validates that body against `NotificationSettingsRequest`,
 * so its shape is a contract and not a detail.
 *
 * The switch positions are that model's own field defaults
 * (`backend_app/core/models/pydantic_models.py:521`–`:536`), not numbers chosen here, which
 * is why they are not declared as `pageFields` entries — the same decision task 7.2 recorded
 * for `RiskSettings`'s three range controls. One divergence is recorded rather than fixed:
 * the page defaults `channels.telegram` to `false` and the model defaults it to `True`. The
 * page renders no Telegram channel switch, and changing the default would change the body
 * this page PUTs, which is out of scope here.
 *
 * Two behaviours DID change, both because the old one lost information:
 *
 *   * A failed profile SAVE used to set the page-level `error`, which replaced the whole
 *     screen with the synchronisation error panel — so a 422 on one field blanked the
 *     account. It is now a `ds/Alert` inside the editor, with the page's own three authored
 *     sentences unchanged.
 *   * A failed notification WRITE used to be one `console.error` and a switch that sprang
 *     back with no explanation. It still reverts, and now says so (Requirement 11.3: logging
 *     is not handling).
 *
 * Key material is referenced by name and never echoed: there is no API key on this page and
 * the one secret-shaped value — the account identifier — is masked to its first eight
 * characters until the trader explicitly reveals it, exactly as before.
 *
 * Every navigation survives: `/app/2fa`, `/app/security-logs`, `/app/exchange`,
 * `/app/strategies`, `/app/billing`, `/app/support`, `/signin`.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity,
  AlertCircle,
  Bell,
  Check,
  ChevronRight,
  Copy,
  Cpu,
  CreditCard,
  Edit2,
  Eye,
  EyeOff,
  Globe,
  Lock,
  Mail,
  RefreshCw,
  Send,
  Shield,
  TrendingUp,
  UserCheck,
} from 'lucide-react';

import { Alert } from '../components/ds/Alert';
import { CommandButton } from '../components/ds/CommandButton';
import { Field } from '../components/ds/Field';
import { Metric, NotAvailableMarker } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { StatusBadge } from '../components/ds/StatusBadge';
import { api } from '../api';
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from '../design/pageFields';
import { available, fromNullable, unavailable } from '../design/reported';
import { token } from '../design/tokens';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';

// ─────────────────────────────────────────────────────────────────────────────
// The declaration, indexed once
// ─────────────────────────────────────────────────────────────────────────────

/**
 * This page's `pageFields.js` entries by `field`, so a call site names the figure and gets
 * its path and its reason together. The one index; see `RiskSettings.jsx` and
 * `SecurityLogs.jsx`.
 */
const FIELD = Object.freeze(
  Object.fromEntries(PAGE_FIELDS_BY_PAGE[PAGES.PROFILE].map((f) => [f.field, f])),
);

/** A dotted path off a response body. `read(body, 'subscription_status')`. */
const read = (body, path) =>
  path.split('.').reduce((node, key) => (node == null ? undefined : node[key]), body);

/**
 * One declared field, as a `Reported<T>`, from the response that carries it.
 *
 * The entry's own `reason` travels with the absence, which is the half Requirement 19.3 calls
 * load-bearing. A `DERIVED` or `UNAVAILABLE` entry has no path by declaration, so it never
 * reaches `read` at all — which is what stops a future `||` fallback being added to a figure
 * the backend cannot fill.
 */
const reported = (body, field) =>
  field.verdict === VERDICT.AVAILABLE
    ? fromNullable(read(body, field.path), field.reason)
    : unavailable(field.reason);

/** The leaf of a `logs[].column` path, which is what one audit row element carries. */
const rowKey = (field) => field.path.split('[].')[1];

/** One declared per-row field of the audit trail. */
const rowField = (row, field) => fromNullable(row?.[rowKey(field)], field.reason);

/** The em-dash marker, carrying the entry's reason. One marker in the app. */
const Marker = ({ field, reason }) => (
  <NotAvailableMarker label={field.label} reason={reason ?? field.reason} />
);

/**
 * `'pro'` → `'Pro Tier'`. The page's own existing expression, carried across unchanged apart
 * from its two fabricated arms.
 *
 * A plan the server reports as a non-string is stringified rather than dropped: it is still
 * the server's answer, and `String(plan)` is what the previous version did.
 */
const planLabel = (plan) =>
  typeof plan === 'string' && plan.length > 0
    ? `${plan.charAt(0).toUpperCase()}${plan.slice(1)} Tier`
    : String(plan);

/**
 * A timestamp the server sent, rendered exactly as this page has always rendered it.
 *
 * `null` for anything unparseable rather than `Invalid Date` or, as the audit rows did
 * before, the word *Recent*.
 */
const formatDateTime = (value) => {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleString();
};

/** The audit row's short form — `new Date(x).toLocaleTimeString`, the same call. */
const formatTimeOfDay = (value) => {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? null
    : parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

/** `'2026-01-15T10:00:00Z'` → `'Jan 2026'`. The same `toLocaleDateString` options. */
const formatMonthYear = (value) => {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? null
    : parsed.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
};

/** A renewal date, through the same `toLocaleDateString` call the page has always made. */
const formatDate = (value) => {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString();
};

/** A Telegram handle, normalised to exactly one leading `@`. The page's own expression. */
const formatHandle = (value) =>
  typeof value === 'string' && value.trim() !== '' ? `@${value.trim().replace(/^@/, '')}` : null;

/**
 * The seven notification switch positions, as `NotificationSettingsRequest`'s own field
 * defaults rather than as numbers chosen on this page.
 *
 * `backend_app/core/models/pydantic_models.py:521`–`:536` declares every one of these, and a
 * switch must have a position before the read lands. See the header for the one divergence
 * (`channels.telegram`) and for why these are NOT `pageFields` entries: a control's position
 * and a reported figure are different things.
 */
const CONTRACT_DEFAULTS = Object.freeze({
  channels: Object.freeze({ email: true, telegram: false, mobile: false }),
  events: Object.freeze({
    trade_executed: true,
    stop_loss_triggered: true,
    daily_pnl_summary: true,
    bot_state_change: false,
    kill_switch_activated: true,
    new_login_detected: true,
    api_key_expiring: true,
    backtest_complete: false,
  }),
});

/** The HTTP status a failed read carried, wherever the client put it. */
const statusOf = (err) => err?.response?.status ?? err?.status ?? null;

/**
 * A safe, client-facing sentence for a failed WRITE.
 *
 * `ApiError.getUserMessage()` answers from the response envelope — `data.message`,
 * `data.detail`, `data.error` — so the server's own refusal survives. `routers/user.py:66`
 * 403s with the list of protected fields it rejected, and that sentence is the most useful
 * thing there is to say about it. What this does NOT do is fall through to `err.message`,
 * which is the one leg that could put an exception class in front of a retail trader
 * (Requirement 11.3). The authored fallbacks passed in are the page's own, word for word.
 */
const writeFailureMessage = (err, fallback) => {
  if (typeof err?.getUserMessage === 'function') {
    const authored = err.getUserMessage();
    if (typeof authored === 'string' && authored.trim()) return authored;
  }
  return fallback;
};

/**
 * The switch a notification preference is.
 *
 * **`ds/` HAS NO SWITCH PRIMITIVE** and one is not added here — see finding 1 in the header.
 * What this keeps, unchanged from before the migration, is the whole of its accessibility
 * contract: the `switch` role, `aria-checked`, the accessible name, a tab stop, and
 * Enter/Space activation that `preventDefault`s Space. Every colour comes from `token`, and
 * the two literals that were here — `#fff` and a hand-mixed `rgba(0,0,0,0.3)` shadow — are
 * `token.content.primary` and `token.shadow.panel`.
 */
function NotificationSwitch({ label, description, icon: Icon, checked, onChange }) {
  return (
    <div
      role="switch"
      aria-checked={checked}
      aria-label={label}
      tabIndex={0}
      onClick={() => onChange(!checked)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
          // Space scrolls the page by default, which would move the row out from under the
          // setting just toggled.
          event.preventDefault();
          onChange(!checked);
        }
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '10px 14px',
        background: token.surface.raised,
        // `${token.brand.base}33` was a token with two hex digits concatenated on — a
        // hand-mixed alpha carrying no `#`, so no guard counted it. `brand.wash` is the
        // declared value.
        border: `1px solid ${checked ? token.brand.wash : token.line.default}`,
        borderRadius: token.radius.lg,
        cursor: 'pointer',
        transition: `all ${token.transition.fast}`,
      }}
    >
      <div
        style={{
          color: checked ? token.brand.base : token.content.muted,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 32,
          height: 32,
          borderRadius: token.radius.md,
          background: checked ? token.brand.wash : token.surface.inset,
          transition: `all ${token.transition.fast}`,
        }}
      >
        {Icon ? <Icon size={16} /> : <Bell size={16} />}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {/* fontSize: 12 → --text-title (heading — Q8: it names a region holding an icon, an
            explanation and a control, so Q4 misses it because a control is not a value). The
            same resolution task 7.2 gave RiskSettings' identical construct. */}
        <div className="text-title font-semibold text-content-primary">{label}</div>
        {/* fontSize: 10 → --text-body (sentence — Q1), +30%. It explains what the
            notification is, which is the one thing a trader reads before switching it. */}
        <div className="text-body leading-snug text-content-muted">{description}</div>
      </div>

      <div
        style={{
          width: 38,
          height: 20,
          background: checked ? token.brand.base : token.line.default,
          borderRadius: token.radius.full,
          position: 'relative',
          transition: `background ${token.transition.base}`,
          flexShrink: 0,
        }}
      >
        <div
          style={{
            width: 16,
            height: 16,
            background: token.content.primary,
            borderRadius: token.radius.full,
            position: 'absolute',
            top: 2,
            left: checked ? 20 : 2,
            transition: `left ${token.transition.base}`,
            boxShadow: token.shadow.panel,
          }}
        />
      </div>
    </div>
  );
}

export default function Profile() {
  const navigate = useNavigate();

  const [editingProfile, setEditingProfile] = useState(false);
  const [editData, setEditData] = useState({
    username: '',
    display_name: '',
    bio: '',
    telegram_id: '',
  });
  const [saving, setSaving] = useState(false);
  const [saveNotice, setSaveNotice] = useState(null);
  const [notifNotice, setNotifNotice] = useState(null);
  const [retryCount, setRetryCount] = useState(0);
  const [showAccountId, setShowAccountId] = useState(false);
  const [copiedKey, setCopiedKey] = useState(null);
  // The optimistic merge the previous version performed with `setProfile(prev => ({...prev,
  // ...editData}))`. `usePanelState` owns the read, so a locally-saved field is layered over
  // the response rather than written into it — the same effect, without a second source of
  // truth for what the server said.
  const [profileEdits, setProfileEdits] = useState(null);
  const [notifOverride, setNotifOverride] = useState(null);

  // ── The six reads (Requirement 4.1) ───────────────────────────────────────
  //
  // `deps: []` on each, so each fires once on mount exactly as the `Promise.allSettled` did.
  // Same six paths, same `limit=20`, no parameter added or changed.
  const readProfile = useCallback(() => api.user.getProfile(), []);
  const readBilling = useCallback(() => api.user.getBillingPlan(), []);
  const readStats = useCallback(() => api.user.getStats(), []);
  const readSecurity = useCallback(() => api.user.getSecurityLogs(20), []);
  const readNotif = useCallback(() => api.user.getNotificationSettings(), []);
  const readReferral = useCallback(() => api.referral.getStats(), []);

  const profile = usePanelState(readProfile, { deps: [] });
  const billing = usePanelState(readBilling, { deps: [] });
  const stats = usePanelState(readStats, { deps: [] });
  const security = usePanelState(readSecurity, { deps: [] });
  const notif = usePanelState(readNotif, { deps: [] });
  const referral = usePanelState(readReferral, { deps: [] });

  // The envelope both shapes the old code accepted: `res.data ?? res`. Kept, because the
  // shape a deployment answers with is not this commit's question.
  const rawProfile = profile.data?.data ?? profile.data;
  const billingBody = billing.data?.data ?? billing.data;
  const statsBody = stats.data?.data ?? stats.data;
  const referralBody = referral.data?.data ?? referral.data;

  /** The server's profile with any field this session has already saved layered over it. */
  const profileBody = useMemo(() => {
    if (rawProfile === null || typeof rawProfile !== 'object') return rawProfile;
    return profileEdits === null ? rawProfile : { ...rawProfile, ...profileEdits };
  }, [rawProfile, profileEdits]);

  /** The stored notification settings, or the position this session has just written. */
  const notifBody = notifOverride ?? (notif.data?.data ?? notif.data);

  /*
   * The profile read seeds the EDITOR. An effect rather than a render-time derivation because
   * these are editable values: once the trader has typed, the server's answer must not reach
   * in and overwrite it. Keyed on `rawProfile` rather than on the merged body, so a save does
   * not re-seed the form from its own result.
   */
  useEffect(() => {
    if (profile.state !== PANEL_STATES.READY) return;
    if (rawProfile === null || typeof rawProfile !== 'object') return;
    setEditData({
      username: rawProfile.username || '',
      display_name: rawProfile.display_name || '',
      bio: rawProfile.bio || '',
      telegram_id: rawProfile.telegram_id || '',
    });
  }, [profile.state, rawProfile]);

  // ── The write paths. Requests unchanged (Requirement 16.2) ────────────────

  const handleProfileUpdate = async () => {
    setSaving(true);
    setSaveNotice(null);
    try {
      // The same call with the same four whitelisted fields. `routers/user.py:27` is what
      // that whitelist is, and this page has never sent anything outside it.
      await api.user.updateProfile(editData);
      setProfileEdits((previous) => ({ ...(previous ?? {}), ...editData }));
      setEditingProfile(false);
      // `ds/Alert` has no `success` severity and one is not added to a shared primitive from
      // a page commit, so a saved profile reports at `info`.
      setSaveNotice({ severity: 'info', message: 'Profile updated.' });
    } catch (err) {
      const status = statusOf(err);
      // The page's own three authored sentences, unchanged. What IS gone is that a failed
      // SAVE used to set the page-level `error`, which replaced the entire screen with the
      // synchronisation error panel — so a rejected 422 on one field blanked the account.
      let fallback = 'Failed to update profile. Please try again.';
      if (status === 401 || status === 403) {
        fallback = 'Authentication required. Please log in again.';
      } else if (status === 422) {
        fallback = 'Invalid profile data. Please check your inputs.';
      }
      setSaveNotice({ severity: 'error', message: writeFailureMessage(err, fallback) });
    } finally {
      setSaving(false);
    }
  };

  /**
   * One notification preference, written immediately.
   *
   * The payload is built by **exactly the expression this page has always used** — both `??`
   * chains, both boolean coercions, the same eight event keys and the same three channels —
   * because `routers/user.py:139` validates it against `NotificationSettingsRequest` and its
   * shape is a contract. The only change is where the pre-toggle state comes from: the stored
   * settings, or `CONTRACT_DEFAULTS`, which is that same model's field defaults.
   */
  const handleNotificationSettingChange = async (targetGroup, key, value) => {
    const previousOverride = notifOverride;
    const currentChannels = notifBody?.channels || CONTRACT_DEFAULTS.channels;
    const currentEvents = notifBody?.events || CONTRACT_DEFAULTS.events;

    const updatedChannels = {
      email:
        typeof currentChannels.email === 'boolean'
          ? currentChannels.email
          : Boolean(currentChannels.email?.active ?? true),
      telegram:
        typeof currentChannels.telegram === 'boolean'
          ? currentChannels.telegram
          : Boolean(currentChannels.telegram?.active ?? false),
      mobile:
        typeof currentChannels.mobile === 'boolean'
          ? currentChannels.mobile
          : Boolean(currentChannels.mobile?.active ?? false),
      ...(targetGroup === 'channels' ? { [key]: Boolean(value) } : {}),
    };

    const updatedEvents = {
      trade_executed: Boolean(currentEvents.trade_executed ?? currentEvents.trade ?? true),
      stop_loss_triggered: Boolean(currentEvents.stop_loss_triggered ?? true),
      daily_pnl_summary: Boolean(currentEvents.daily_pnl_summary ?? true),
      bot_state_change: Boolean(currentEvents.bot_state_change ?? currentEvents.bot ?? false),
      kill_switch_activated: Boolean(
        currentEvents.kill_switch_activated ?? currentEvents.margin ?? true,
      ),
      new_login_detected: Boolean(currentEvents.new_login_detected ?? currentEvents.login ?? true),
      api_key_expiring: Boolean(currentEvents.api_key_expiring ?? true),
      backtest_complete: Boolean(currentEvents.backtest_complete ?? false),
      ...(targetGroup === 'events' ? { [key]: Boolean(value) } : {}),
    };

    const payload = { channels: updatedChannels, events: updatedEvents };

    try {
      setNotifNotice(null);
      setNotifOverride(payload);
      await api.user.updateNotificationSettings(payload);
    } catch (err) {
      // Revert to the previous authoritative state, exactly as before — and then SAY SO. The
      // previous version's entire failure handling was one `console.error`, so a switch that
      // silently sprang back was the only signal a trader got (Requirement 11.3).
      setNotifOverride(previousOverride);
      setNotifNotice({
        severity: 'error',
        message: writeFailureMessage(
          err,
          'That preference could not be saved. It has been put back.',
        ),
      });
    }
  };

  const copyToClipboard = (text, keyName) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedKey(keyName);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  /**
   * Re-read all six. The same six requests the mount issues, and nothing else.
   *
   * Deliberately not a `useCallback`: the six `refetch` functions are each stable by
   * `usePanelState`'s second inversion, but the panel objects holding them are not, so a
   * dependency array would have to list either the panels (defeating the memo) or the
   * functions (which `react-hooks/exhaustive-deps` cannot see through). This is passed to
   * command buttons, not to an effect, so its identity buys nothing.
   */
  const handleRetry = () => {
    setRetryCount((previous) => previous + 1);
    profile.refetch();
    billing.refetch();
    stats.refetch();
    security.refetch();
    notif.refetch();
    referral.refetch();
  };

  // ── The declared figures (Requirement 4.2) ────────────────────────────────

  /**
   * `display_name` or `username`, as a `Reported`. The declaration's own derivation, and the
   * literal `'Trader'` third arm is gone.
   */
  const accountName = useMemo(() => {
    const candidate = profileBody?.display_name || profileBody?.username;
    return fromNullable(
      typeof candidate === 'string' && candidate.trim() !== '' ? candidate : null,
      FIELD.accountName.reason,
    );
  }, [profileBody]);

  const username = reported(profileBody, FIELD.username);
  const email = reported(profileBody, FIELD.email);
  const accountId = reported(profileBody, FIELD.accountId);
  const accountRole = reported(profileBody, FIELD.accountRole);

  /**
   * Verified / not verified, as the PRESENCE of the confirmation timestamp.
   *
   * A null `email_confirmed_at` is a genuine "not verified" and renders as one; only a profile
   * that could not be read at all is an absence. Collapsing the two would turn a real state
   * into a marker, which is the inverse of the substitution this migration is about and just
   * as wrong.
   */
  const emailVerified =
    profileBody === null || typeof profileBody !== 'object'
      ? unavailable(FIELD.emailVerified.reason)
      : available(Boolean(profileBody.email_confirmed_at));

  /** The handle, normalised. `available(null)` is the genuine "not configured". */
  const telegram =
    profileBody === null || typeof profileBody !== 'object'
      ? unavailable(FIELD.telegramHandle.reason)
      : available(formatHandle(profileBody.telegram_id));

  /** `fromNullable` over the FORMATTED string, so an unparseable date is an absence. */
  const memberSince = fromNullable(
    formatMonthYear(read(profileBody, FIELD.memberSince.path)),
    FIELD.memberSince.reason,
  );

  const planName = reported(billingBody, FIELD.planName);
  const subscriptionStatus = reported(billingBody, FIELD.subscriptionStatus);
  const renewalDate = fromNullable(
    formatDate(read(billingBody, FIELD.renewalDate.path)),
    FIELD.renewalDate.reason,
  );

  /** A LIST, so no `Reported` — `isReadableValue` refuses arrays and a figure is a scalar. */
  const planFeatures = Array.isArray(billingBody?.features) ? billingBody.features : [];

  const platformBots = reported(statsBody, FIELD.platformActiveBotCount);
  const platformStrategies = reported(statsBody, FIELD.platformStrategyCount);
  const platformTrades = reported(statsBody, FIELD.platformTradeCount);
  const platformPnl = reported(statsBody, FIELD.platformTodayPnl);

  const referralCode = reported(referralBody, FIELD.referralCode);
  const referralLink = reported(referralBody, FIELD.referralLink);
  const totalReferrals = reported(referralBody, FIELD.totalReferralCount);
  const activeReferrals = reported(referralBody, FIELD.activeReferralCount);
  const pendingEarnings = reported(referralBody, FIELD.pendingEarningsUsd);
  const lifetimeEarnings = reported(referralBody, FIELD.lifetimeEarningsUsd);

  /** The three most recent audit rows, each field resolved once against its declaration. */
  const auditRows = useMemo(() => {
    const body = security.data?.data ?? security.data;
    const raw = Array.isArray(body) ? body : Array.isArray(body?.logs) ? body.logs : [];
    return raw.slice(0, 3).map((row, index) => ({
      // `id` keeps its index fallback because it is only a React key and is not rendered.
      id: row?.id || `audit-${index}`,
      eventType: rowField(row, FIELD.securityEventType),
      // `fromNullable` over the FORMATTED time, so an unparseable stamp is an absence rather
      // than `Invalid Date` or, as before, the word "Recent".
      recordedAt: fromNullable(formatTimeOfDay(row?.created_at), FIELD.securityEventAt.reason),
    }));
  }, [security.data]);

  const firstAuditRow = useMemo(() => {
    const body = security.data?.data ?? security.data;
    const raw = Array.isArray(body) ? body : Array.isArray(body?.logs) ? body.logs : [];
    return raw.length > 0 ? raw[0] : null;
  }, [security.data]);

  const lastAccessAt = fromNullable(
    formatDateTime(firstAuditRow?.created_at),
    FIELD.lastAccessAt.reason,
  );
  const lastAccessIp = rowField(firstAuditRow, FIELD.lastAccessIp);

  // ── The panel states ──────────────────────────────────────────────────────

  const profileState = profile.state;
  const profileFailed =
    profileState === PANEL_STATES.ERROR || profileState === PANEL_STATES.UNAUTHORISED;
  const anyLoading = [profile, billing, stats, security, notif, referral].some(
    (panel) => panel.state === PANEL_STATES.LOADING || panel.state === PANEL_STATES.REFRESHING,
  );

  /*
   * `empty` resolves to `ready` on these two, on purpose, and for two different reasons.
   *
   * NOTIFICATIONS: `GET /api/notifications/settings` answers `{}` for an account with no
   * stored row, so `isEmptyPayload` reports `empty` — and an empty panel would remove the only
   * place the preferences can be SET (Requirement 16.2). The switches render at
   * `NotificationSettingsRequest`'s own defaults, which is what the server would apply.
   *
   * ACCESS: an account with no audit rows still has a panel — the last-access tile renders its
   * declared markers, and the trail states its own emptiness inline below, which keeps `empty`
   * distinguishable from `error` (Requirement 19.4) without taking the *Review All Logs*
   * control off the screen.
   */
  const notifState = notif.state === PANEL_STATES.EMPTY ? PANEL_STATES.READY : notif.state;
  const accessState = security.state === PANEL_STATES.EMPTY ? PANEL_STATES.READY : security.state;
  const accessIsEmpty = security.state === PANEL_STATES.EMPTY;

  /** The stored position of one switch, or the contract default. */
  const eventOn = (key, ...aliases) => {
    const events = notifBody?.events;
    if (events && typeof events === 'object') {
      for (const name of [key, ...aliases]) {
        if (typeof events[name] === 'boolean') return events[name];
      }
    }
    return CONTRACT_DEFAULTS.events[key];
  };

  /** The email channel, which the server has been seen to answer as a boolean or an object. */
  const emailChannelOn = (() => {
    const channel = notifBody?.channels?.email;
    if (typeof channel === 'boolean') return channel;
    if (channel && typeof channel === 'object') return Boolean(channel.active ?? true);
    return CONTRACT_DEFAULTS.channels.email;
  })();

  /*
   * Every recovery control the old error screen offered, in the identity panel's header —
   * which `ds/Panel` renders in all eight states, so none of them is reachable only from the
   * branch that used to own it. See the header for why *Retry Connection* and *Retry* are one
   * control.
   */
  const recoveryControls = (
    <>
      {/* *Try again* is deliberately NOT here: `ds/ErrorState` renders its own retry inside
          the panel body, gated on `ApiError.isRetryable()`, and pointed at `handleRetry` by
          the `error` config below. Two buttons doing the same thing is how a retail screen
          gets harder to read, and the primitive's gate is the better one — an unretryable
          failure should not offer a retry, which is why the unauthorised arm offers *Sign in
          again* instead. */}
      <CommandButton intent="ghost" onClick={() => window.location.reload()}>
        Reload the page
      </CommandButton>
      {profileState === PANEL_STATES.UNAUTHORISED ? (
        <CommandButton
          intent="ghost"
          icon={Lock}
          onClick={() => {
            window.location.href = '/signin';
          }}
        >
          Sign in again
        </CommandButton>
      ) : null}
      {statusOf(profile.error) === 404 ? (
        <CommandButton intent="ghost" onClick={() => navigate('/app/support')}>
          Contact support
        </CommandButton>
      ) : null}
    </>
  );

  return (
    <div className="flex-1 overflow-y-auto p-5">
      {/* fontSize: 20 → --text-page (heading — Q8, the page <h1>) and fontSize: 11 →
          --text-body (sentence — Q1, the subtitle), both set by `ds/PageHeader` rather than
          by this page. */}
      <PageHeader
        title="PROFILE & ACCOUNT"
        subtitle="Trading terminal identity, automation context, security posture, and account preferences"
        actions={
          <CommandButton
            intent="secondary"
            icon={RefreshCw}
            onClick={handleRetry}
            loading={anyLoading}
            loadingLabel="Reading your account"
          >
            Refresh
          </CommandButton>
        }
        meta={
          <div className="flex flex-wrap items-center gap-2">
            {/* fontSize: 10 → --text-micro (chip — Q5). The `TIER:` pill, which fell back to
                the literal "Free Tier" through a key the response does not carry. */}
            {planName.available ? (
              <span className="inline-flex shrink-0 items-center rounded-sm border border-line-default bg-surface-inset px-2 py-0.5 text-micro font-semibold uppercase tracking-wide text-brand">
                {`Tier: ${planLabel(planName.value)}`}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-content-muted">
                {FIELD.planName.label}
                <Marker field={FIELD.planName} reason={planName.reason} />
              </span>
            )}
            {/* fontSize: 10 → --text-micro (chip — Q5). This was the `ACCOUNT ACTIVE` pill: a
                green dot and two words of static JSX. Being able to open the page is not
                evidence that the account is in good standing. */}
            <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-content-muted">
              {FIELD.accountStatus.label}
              <Marker field={FIELD.accountStatus} />
            </span>
            {/* fontSize: 10 → --text-micro (chip — Q5). */}
            <span className="inline-flex items-center gap-1 text-micro text-content-muted">
              {FIELD.memberSince.label}
              {memberSince.available ? (
                <span className="text-content-secondary">{memberSince.value}</span>
              ) : (
                <Marker field={FIELD.memberSince} reason={memberSince.reason} />
              )}
            </span>
          </div>
        }
      />

      {/* fontSize: 11 → --text-micro (label — Q4). The retry counter the old error screen
          carried, kept because it is the only thing on screen that distinguishes one failed
          read from four. It loses its monospace: it is a labelled count, not an identifier. */}
      {retryCount > 0 ? (
        <p className="mt-2 text-micro text-content-muted">{`Sync attempts: ${retryCount}`}</p>
      ) : null}

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* ══ COLUMN 1: identity, security, access, platform statistics ══════ */}
        <div className="flex min-w-0 flex-col gap-4">
          <Panel
            title="Account identity"
            state={profileState}
            loading={{ kind: 'skeleton-cards', rows: 2, label: 'Loading your account identity' }}
            empty={{
              headline: 'Your profile record is empty',
              body: 'No profile details are stored against this account yet. Edit profile is '
                + 'where a username, display name and bio are set.',
              action: { label: 'Read again', onClick: profile.refetch },
            }}
            error={{ error: profile.error, onRetry: handleRetry }}
            unavailable={{ reason: 'Your profile cannot be read right now.' }}
            unauthorised={{ error: profile.error }}
            actions={
              <>
                {profileFailed ? recoveryControls : null}
                {profileState === PANEL_STATES.READY
                || profileState === PANEL_STATES.REFRESHING ? (
                  <CommandButton
                    intent="secondary"
                    icon={Edit2}
                    onClick={() => setEditingProfile(!editingProfile)}
                  >
                    {editingProfile ? 'Cancel' : 'Edit profile'}
                  </CommandButton>
                  ) : null}
              </>
            }
          >
            <div className="flex flex-col gap-4">
              <div className="flex items-start gap-3.5">
                {/* fontSize: 24 → declaration REMOVED. The container holds no text: it is a
                    56px circle whose content is an <img> or a 26px glyph carrying its own
                    `size` prop, so the size was sizing nothing. */}
                <div className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-full border-2 border-line-default bg-surface-inset text-brand">
                  {profileBody?.avatar_url ? (
                    <img
                      src={profileBody.avatar_url}
                      alt=""
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <UserCheck size={26} aria-hidden="true" />
                  )}
                </div>
                <div className="min-w-0">
                  {/* fontSize: 16 → --text-section (value — Q7, this panel's hero). 16 is
                      equidistant from --text-title (14) and --text-section (18), which is
                      §2.2's undefined case, and §2.2 resolves a tie UP. */}
                  {accountName.available ? (
                    <p className="truncate text-section font-bold text-content-primary">
                      {accountName.value}
                    </p>
                  ) : (
                    <Marker field={FIELD.accountName} reason={accountName.reason} />
                  )}
                  {/* fontSize: 11 → --text-body (value — Q7, secondary). An @handle is an
                      IDENTIFIER, so Requirement 3.1 keeps the monospace here. */}
                  {username.available ? (
                    <p className="truncate font-mono text-body text-content-muted">
                      {`@${username.value}`}
                    </p>
                  ) : (
                    <Marker field={FIELD.username} reason={username.reason} />
                  )}
                  {/* fontSize: 9 → --text-micro (chip — Q5). The literal `"USER"` is gone; a
                      role is a server enum, and the chip loses its monospace because the
                      words around the value are a label. */}
                  <p className="mt-1 inline-flex items-center gap-1 rounded-sm border border-line-default bg-surface-inset px-1.5 py-0.5 text-micro uppercase tracking-wide text-brand">
                    {`${FIELD.accountRole.label}:`}
                    {accountRole.available ? (
                      String(accountRole.value).toUpperCase()
                    ) : (
                      <Marker field={FIELD.accountRole} reason={accountRole.reason} />
                    )}
                  </p>
                </div>
              </div>

              {editingProfile ? (
                <div className="grid gap-2.5 rounded-lg border border-line-default bg-surface-inset p-3.5">
                  {/* fontSize: 10 ×4 → the four editor labels leave with their call site onto
                      `ds/Field`, which renders a visible label at its own declared step. */}
                  <Field
                    id="profile-edit-username"
                    label="Username"
                    value={editData.username}
                    onChange={(e) => setEditData({ ...editData, username: e.target.value })}
                  />
                  <Field
                    id="profile-edit-display-name"
                    label="Display name"
                    value={editData.display_name}
                    onChange={(e) => setEditData({ ...editData, display_name: e.target.value })}
                  />
                  <Field
                    id="profile-edit-telegram-id"
                    label="Telegram handle"
                    placeholder="@yourtelegram"
                    value={editData.telegram_id}
                    onChange={(e) => setEditData({ ...editData, telegram_id: e.target.value })}
                  />
                  {/* Requirement 4.4 finding 2: `ds/Field` has no multiline variant, so this
                      stays a hand-built <textarea> with its own <label htmlFor>. */}
                  <div>
                    <label
                      htmlFor="profile-edit-bio"
                      className="mb-1 block text-micro font-medium uppercase tracking-wide text-content-secondary"
                    >
                      Trader bio / strategy notes
                    </label>
                    {/* fontSize: 11 → --text-body (control text — Q3). The trader's own
                        prose, so the monospace goes with the size. */}
                    <textarea
                      id="profile-edit-bio"
                      rows={3}
                      value={editData.bio}
                      onChange={(e) => setEditData({ ...editData, bio: e.target.value })}
                      className="w-full resize-y rounded-md border border-line-default bg-surface-raised p-2 text-body text-content-primary outline-none focus-visible:border-brand"
                    />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <CommandButton
                      icon={Check}
                      loading={saving}
                      loadingLabel="Saving changes"
                      onClick={handleProfileUpdate}
                    >
                      Save changes
                    </CommandButton>
                    <CommandButton intent="secondary" onClick={() => setEditingProfile(false)}>
                      Cancel
                    </CommandButton>
                  </div>
                  {saveNotice ? (
                    <Alert
                      severity={saveNotice.severity}
                      title={saveNotice.message}
                      onDismiss={() => setSaveNotice(null)}
                      dismissLabel="Dismiss this message"
                    />
                  ) : null}
                </div>
              ) : (
                <div className="flex flex-col gap-2.5 border-t border-line-default pt-3.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    {/* fontSize: 12 → --text-micro (label — Q4). */}
                    <span className="text-micro uppercase tracking-wide text-content-muted">
                      {FIELD.email.label}
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5">
                      <Mail
                        size={13}
                        aria-hidden="true"
                        className="shrink-0 text-content-secondary"
                      />
                      {/* fontSize: 12 → --text-body (value — Q7, secondary). The literal
                          `"No email"` is gone. */}
                      {email.available ? (
                        <span className="truncate text-body text-content-primary">
                          {email.value}
                        </span>
                      ) : (
                        <Marker field={FIELD.email} reason={email.reason} />
                      )}
                      {/* fontSize: 10 → `ds/StatusBadge`'s own step, with the hue coming from
                          `statusToken` rather than from a hand-mixed green. A null
                          `email_confirmed_at` is a genuine "not verified" and renders as one;
                          only an unreadable profile is the marker. */}
                      {emailVerified.available ? (
                        <StatusBadge
                          state={emailVerified.value ? 'ok' : 'pending'}
                          label={emailVerified.value ? 'Verified' : 'Not verified'}
                          dot
                        />
                      ) : (
                        <Marker field={FIELD.emailVerified} reason={emailVerified.reason} />
                      )}
                    </span>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2">
                    {/* fontSize: 12 → --text-micro (label — Q4). */}
                    <span className="text-micro uppercase tracking-wide text-content-muted">
                      {FIELD.telegramHandle.label}
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5">
                      <Send
                        size={13}
                        aria-hidden="true"
                        className={
                          telegram.value ? 'shrink-0 text-brand' : 'shrink-0 text-content-muted'
                        }
                      />
                      {/* fontSize: 12 → --text-body (value — Q7). A null handle is a genuine
                          "not configured" and says so; only an unreadable profile is the
                          marker. */}
                      {telegram.available ? (
                        <span className="truncate text-body text-content-primary">
                          {telegram.value ?? 'Not configured'}
                        </span>
                      ) : (
                        <Marker field={FIELD.telegramHandle} reason={telegram.reason} />
                      )}
                    </span>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2">
                    {/* fontSize: 12 → --text-micro (label — Q4). */}
                    <span className="text-micro uppercase tracking-wide text-content-muted">
                      {FIELD.accountId.label}
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5">
                      {/* fontSize: 11 → --text-body (value — Q7). A UUID is an IDENTIFIER and
                          keeps its monospace (Requirement 3.1). Masked to eight characters
                          until the trader reveals it, exactly as before, and the literal
                          `"Loading..."` that outlived the load is gone. */}
                      {accountId.available ? (
                        <code className="truncate rounded-sm bg-surface-inset px-2 py-0.5 font-mono text-body text-content-primary">
                          {showAccountId
                            ? accountId.value
                            : `${String(accountId.value).substring(0, 8)}••••••••`}
                        </code>
                      ) : (
                        <Marker field={FIELD.accountId} reason={accountId.reason} />
                      )}
                      {/* Requirement 4.4 finding 3: a disclosure toggle is not a command, so
                          `ds/CommandButton` is not the primitive for it. It keeps a text
                          alternative and now exposes its pressed state programmatically,
                          which the `title`-only version did not. */}
                      <button
                        type="button"
                        aria-pressed={showAccountId}
                        aria-label={
                          showAccountId
                            ? 'Hide the account identifier'
                            : 'Reveal the account identifier'
                        }
                        onClick={() => setShowAccountId(!showAccountId)}
                        className="flex shrink-0 items-center rounded-sm p-0.5 text-content-secondary hover:text-content-primary"
                      >
                        {showAccountId ? (
                          <EyeOff size={13} aria-hidden="true" />
                        ) : (
                          <Eye size={13} aria-hidden="true" />
                        )}
                      </button>
                      <CommandButton
                        intent="ghost"
                        icon={copiedKey === 'account_id' ? Check : Copy}
                        aria-label="Copy the account identifier"
                        onClick={() =>
                          copyToClipboard(
                            accountId.available ? accountId.value : null,
                            'account_id',
                          )
                        }
                      />
                    </span>
                  </div>

                  {/* fontSize: 11 → --text-body (sentence — Q1). The trader's own bio. */}
                  {typeof profileBody?.bio === 'string' && profileBody.bio.trim() !== '' ? (
                    <p className="rounded-md border border-line-default bg-surface-raised px-3 py-2 text-body text-content-secondary">
                      {profileBody.bio}
                    </p>
                  ) : null}
                </div>
              )}
            </div>
          </Panel>

          {/*
            * Security & access carries no read of its own: both of its declared figures are
            * ❌ UNAVAILABLE, so there is nothing to fetch and nothing that can fail. That is
            * also what keeps *Manage MFA* on screen unconditionally — it was previously in a
            * card that a failed security-log read would have hidden it behind.
            */}
          <Panel
            title="Security & Access"
            actions={
              <CommandButton intent="secondary" icon={Shield} onClick={() => navigate('/app/2fa')}>
                Manage MFA
              </CommandButton>
            }
          >
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div className="rounded-lg border border-line-default bg-surface-raised px-3 py-2.5">
                {/* fontSize: 10 → --text-micro (label — Q4). */}
                <p className="text-micro uppercase tracking-wide text-content-muted">
                  {FIELD.mfaConfigured.label}
                </p>
                {/* fontSize: 12 → --text-body (value — Q7). This rendered a green dot and the
                    word *Configured* with NO READ BEHIND IT. */}
                <p className="mt-1 text-body">
                  <Marker field={FIELD.mfaConfigured} />
                </p>
              </div>
              <div className="rounded-lg border border-line-default bg-surface-raised px-3 py-2.5">
                {/* fontSize: 9 → --text-micro (label — Q4). Was the `PROTECTED` pill. */}
                <p className="text-micro uppercase tracking-wide text-content-muted">
                  {FIELD.securityPosture.label}
                </p>
                <p className="mt-1 text-body">
                  <Marker field={FIELD.securityPosture} />
                </p>
              </div>
            </div>
          </Panel>

          <Panel
            title="Recent access"
            state={accessState}
            loading={{ kind: 'skeleton-cards', rows: 3, label: 'Loading your access events' }}
            empty={{
              headline: 'No access events are recorded',
              body: 'Every sign-in, sign-out and access event against this account is listed '
                + 'here once it has been recorded.',
              action: { label: 'Read again', onClick: security.refetch },
            }}
            error={{ error: security.error, onRetry: security.refetch }}
            unavailable={{ reason: 'Your access events cannot be read right now.' }}
            unauthorised={{ error: security.error }}
            actions={
              <CommandButton
                intent="ghost"
                icon={ChevronRight}
                onClick={() => navigate('/app/security-logs')}
              >
                Review All Logs
              </CommandButton>
            }
          >
            <div className="flex flex-col gap-3">
              <div className="rounded-lg border border-line-default bg-surface-raised px-3 py-2.5">
                {/* fontSize: 10 → --text-micro (label — Q4). */}
                <p className="text-micro uppercase tracking-wide text-content-muted">
                  {FIELD.lastAccessAt.label}
                </p>
                {/* fontSize: 11 → --text-body (value — Q7). The literal *Active Session* is
                    gone; it answered "when were you last here" with a reassurance. The
                    `whiteSpace: nowrap` and its ellipsis went with it, which is §2.5's first
                    and cheapest layout yield. */}
                <p className="mt-0.5 text-body font-semibold text-content-primary">
                  {lastAccessAt.available ? (
                    lastAccessAt.value
                  ) : (
                    <Marker field={FIELD.lastAccessAt} reason={lastAccessAt.reason} />
                  )}
                </p>
                {/* fontSize: 9 → --text-micro (label — Q4) on the name, with the ADDRESS
                    keeping its monospace: an IP address is an identifier (Requirement 3.1),
                    and the literal `"127.0.0.1"` on an audit record is gone. */}
                <p className="mt-1 flex flex-wrap items-center gap-1 text-micro uppercase tracking-wide text-content-muted">
                  {`${FIELD.lastAccessIp.label}:`}
                  {lastAccessIp.available ? (
                    <span className="font-mono normal-case text-content-secondary">
                      {lastAccessIp.value}
                    </span>
                  ) : (
                    <Marker field={FIELD.lastAccessIp} reason={lastAccessIp.reason} />
                  )}
                </p>
              </div>

              {accessIsEmpty ? (
                /* fontSize: 11 → --text-body (sentence — Q1). `empty` stays distinguishable
                   from `error` (Requirement 19.4): this states that nothing has been
                   recorded, which is a fact about the account, and the panel's error arm
                   states that the read failed, which is not. */
                <p className="text-body text-content-muted">
                  No access events have been recorded against this account yet.
                </p>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {auditRows.map((row) => (
                    <li
                      key={row.id}
                      className="flex items-center justify-between gap-2 rounded-md bg-surface-inset px-2.5 py-1.5"
                    >
                      <span className="flex min-w-0 items-center gap-1.5">
                        <Lock
                          size={12}
                          aria-hidden="true"
                          className="shrink-0 text-content-muted"
                        />
                        {/* fontSize: 11 → --text-body (value — Q7). The three dead fallbacks
                            `log.event`, `log.action` and `"Security Event"` are gone;
                            `event_type` is NOT NULL. */}
                        {row.eventType.available ? (
                          <span className="truncate text-body font-medium text-content-primary">
                            {row.eventType.value}
                          </span>
                        ) : (
                          <Marker field={FIELD.securityEventType} reason={row.eventType.reason} />
                        )}
                      </span>
                      {/* fontSize: 10 → --text-micro (label — Q4). A timestamp is a value but
                          not an identifier, so it loses the monospace — the same split
                          `pages/SecurityLogs.jsx` applies to its own two columns. The literal
                          `"Recent"` is gone. */}
                      <span className="shrink-0 text-micro text-content-muted">
                        {row.recordedAt.available ? (
                          row.recordedAt.value
                        ) : (
                          <Marker field={FIELD.securityEventAt} reason={row.recordedAt.reason} />
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Panel>

          <Panel
            title="Platform-wide statistics"
            state={stats.state}
            loading={{
              kind: 'skeleton-metric',
              rows: 1,
              columns: 4,
              label: 'Loading the platform statistics',
            }}
            empty={{
              headline: 'No platform statistics were reported',
              body: 'The statistics endpoint answered without any figures. Your own fleet and '
                + 'its results are on the dashboard and the strategies page.',
              action: { label: 'Read again', onClick: stats.refetch },
            }}
            error={{ error: stats.error, onRetry: stats.refetch }}
            unavailable={{ reason: 'The platform statistics cannot be read right now.' }}
            unauthorised={{ error: stats.error }}
            actions={
              <>
                <CommandButton
                  intent="secondary"
                  icon={Globe}
                  onClick={() => navigate('/app/exchange')}
                >
                  Manage Exchanges
                </CommandButton>
                <CommandButton
                  intent="secondary"
                  icon={Activity}
                  onClick={() => navigate('/app/strategies')}
                >
                  Manage Strategies
                </CommandButton>
              </>
            }
          >
            {/*
              * fontSize: 9 → --text-body (sentence — Q1), +44%. THIS PANEL WAS TITLED
              * *Automation Account Context* AND REPORTED THE PLATFORM. Stating the basis once
              * on the panel is `RiskSettings.jsx`'s `LIMITS_BASIS` pattern: it belongs to the
              * read, not to each figure, and a sentence per tile would be four copies of one
              * fact.
              */}
            <p className="mb-3 text-body text-content-secondary">
              These four figures come from the platform-wide statistics endpoint, which reports
              across all accounts rather than yours. Your own deployments are on the strategies
              page and your own results are on the dashboard.
            </p>
            <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
              {/* fontSize: 10 ×4 (the labels) and 18, 16, 9, 9 (the figures and their
                  sub-lines) all leave with their call site onto `ds/Metric`, which renders the
                  label at --text-micro and the figure at its tier step — and renders every
                  numeric format in `font-mono tabular-nums`, so the monospace treatment on
                  the two figures survives without a declaration here. Every `|| 0` is gone:
                  a genuine `0` is a reading and still renders `0`. */}
              <Metric
                label={FIELD.platformActiveBotCount.label}
                value={platformBots}
                format="integer"
                tier={2}
              />
              <Metric
                label={FIELD.platformStrategyCount.label}
                value={platformStrategies}
                format="integer"
                tier={2}
              />
              <Metric
                label={FIELD.platformTradeCount.label}
                value={platformTrades}
                format="integer"
                tier={2}
              />
              {/*
                * `unit="USD"` rather than a `$` prefix, and NO profit/loss hue. The hue is
                * dropped deliberately: the figure is not the trader's P&L, so colouring it
                * green would frame the platform's day as the account's. `ds/PnLDisplay` is
                * the primitive for a real P&L and is not reached from this page.
                */}
              <Metric
                label={FIELD.platformTodayPnl.label}
                value={platformPnl}
                format="currency"
                precision={2}
                unit="USD"
                tier={2}
                hint={FIELD.platformTodayPnl.tooltip}
              />
            </div>
            <div className="mt-2 rounded-lg border border-line-default bg-surface-raised px-3 py-2.5">
              {/* fontSize: 10 → --text-micro (label — Q4). */}
              <p className="text-micro uppercase tracking-wide text-content-muted">
                {FIELD.tradingEnvironment.label}
              </p>
              {/* fontSize: 11 → --text-body (value — Q7). Was the literal *Live + Paper* with
                  a green *Isolated* beneath it — two claims, neither of them read. */}
              <p className="mt-1 text-body">
                <Marker field={FIELD.tradingEnvironment} />
              </p>
            </div>
          </Panel>
        </div>

        {/* ══ COLUMN 2: subscription, notifications, referral ════════════════ */}
        <div className="flex min-w-0 flex-col gap-4">
          <Panel
            title="Subscription & Plan"
            state={billing.state}
            loading={{ kind: 'skeleton-metric', rows: 1, columns: 2, label: 'Loading your plan' }}
            empty={{
              headline: 'No plan is recorded',
              body: 'Billing answered without a plan for this account. Manage subscription is '
                + 'where a plan is chosen.',
              action: { label: 'Read again', onClick: billing.refetch },
            }}
            error={{ error: billing.error, onRetry: billing.refetch }}
            unavailable={{ reason: 'Your plan cannot be read right now.' }}
            unauthorised={{ error: billing.error }}
            actions={
              <CommandButton
                intent="secondary"
                icon={CreditCard}
                onClick={() => navigate('/app/billing')}
              >
                Manage Subscription
              </CommandButton>
            }
          >
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line-default bg-surface-raised px-4 py-3">
              <div className="min-w-0">
                {/* fontSize: 10 → --text-micro (label — Q4). */}
                <p className="text-micro uppercase tracking-wide text-content-muted">
                  {FIELD.planName.label}
                </p>
                {/* fontSize: 16 → --text-section (value — Q7, this panel's hero; 16 ties
                    between --text-title and --text-section and §2.2 resolves a tie up). */}
                {planName.available ? (
                  <p className="text-section font-bold text-content-primary">
                    {planLabel(planName.value)}
                  </p>
                ) : (
                  <Marker field={FIELD.planName} reason={planName.reason} />
                )}
              </div>
              <div className="min-w-0 text-right">
                {/* fontSize: 10 → --text-micro (label — Q4). */}
                <p className="text-micro uppercase tracking-wide text-content-muted">
                  {FIELD.subscriptionStatus.label}
                </p>
                {/* fontSize: 12 → `ds/StatusBadge`'s own step, and the hue comes from
                    `statusToken` rather than from a hand-picked green (Requirement 5.3). THE
                    STATUS IS NOW THE SERVER'S, VERBATIM: the `|| autoRenew || plan !== "free"`
                    chain that reported Active for any paid plan is gone. */}
                <p className="mt-1 flex justify-end">
                  {subscriptionStatus.available ? (
                    <StatusBadge state={String(subscriptionStatus.value)} dot />
                  ) : (
                    <Marker field={FIELD.subscriptionStatus} reason={subscriptionStatus.reason} />
                  )}
                </p>
              </div>
            </div>

            {/* fontSize: 11 → --text-body (sentence — Q1). *Standard 30-day Cycle* is gone; a
                renewal date is either recorded against the account or it is not. */}
            <p className="mt-2.5 flex flex-wrap items-center gap-1.5 text-body text-content-muted">
              <span>{`${FIELD.renewalDate.label}:`}</span>
              {renewalDate.available ? (
                <span className="text-content-primary">{renewalDate.value}</span>
              ) : (
                <Marker field={FIELD.renewalDate} reason={renewalDate.reason} />
              )}
            </p>

            {planFeatures.length > 0 ? (
              <div className="mt-2.5 border-t border-line-default pt-2.5">
                {/* fontSize: 10 → --text-micro (label — Q4). */}
                <p className="mb-2 text-micro uppercase tracking-wide text-content-muted">
                  Included capabilities
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {planFeatures.map((feature) => (
                    /* fontSize: 10 → --text-micro (chip — Q5). A LIST, so no `Reported`:
                       `isReadableValue` refuses arrays, and the absence of the chips is
                       visible as the absence of the chips. */
                    <li
                      key={String(feature)}
                      className="inline-flex items-center gap-1 rounded-sm border border-line-default bg-surface-inset px-2 py-0.5 text-micro text-content-secondary"
                    >
                      <Check size={10} aria-hidden="true" />
                      {String(feature)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </Panel>

          <Panel
            title="Notification Preferences"
            state={notifState}
            loading={{
              kind: 'skeleton-cards',
              rows: 7,
              label: 'Loading your notification preferences',
            }}
            empty={{
              headline: 'No notification preferences are stored',
              body: 'Switching any preference below stores the full set against your account.',
              action: { label: 'Read again', onClick: notif.refetch },
            }}
            error={{ error: notif.error, onRetry: notif.refetch }}
            unavailable={{ reason: 'Your notification preferences cannot be read right now.' }}
            unauthorised={{ error: notif.error }}
          >
            {/* fontSize: 10 → --text-body (sentence — Q1), +30%. */}
            <p className="mb-3 text-body text-content-muted">
              Notification Dispatch Preferences: trade execution, risk trigger, and account
              security alert dispatches
            </p>

            {notifNotice ? (
              <Alert
                severity={notifNotice.severity}
                title={notifNotice.message}
                onDismiss={() => setNotifNotice(null)}
                dismissLabel="Dismiss this message"
                className="mb-3"
              />
            ) : null}

            <div className="flex flex-col gap-3.5">
              <div>
                {/* fontSize: 10 → --text-micro (label — Q4). */}
                <p className="mb-1.5 text-micro font-bold uppercase tracking-wide text-brand">
                  Trading executions
                </p>
                <div className="flex flex-col gap-1.5">
                  <NotificationSwitch
                    label="Trade Executions"
                    description="Real-time order fill alerts and position entries"
                    icon={TrendingUp}
                    checked={eventOn('trade_executed', 'trade')}
                    onChange={(checked) =>
                      handleNotificationSettingChange('events', 'trade_executed', checked)
                    }
                  />
                  <NotificationSwitch
                    label="Daily PnL Summaries"
                    description="Daily performance and closed trade balance recap"
                    icon={Activity}
                    checked={eventOn('daily_pnl_summary')}
                    onChange={(checked) =>
                      handleNotificationSettingChange('events', 'daily_pnl_summary', checked)
                    }
                  />
                </div>
              </div>

              <div>
                {/* fontSize: 10 → --text-micro (label — Q4). The hue is READ from
                    `token.status.loss.fg` rather than written; it was already a token. */}
                <p
                  className="mb-1.5 text-micro font-bold uppercase tracking-wide"
                  style={{ color: token.status.loss.fg }}
                >
                  Risk &amp; circuit breakers
                </p>
                <div className="flex flex-col gap-1.5">
                  <NotificationSwitch
                    label="Stop Loss & Margin Alerts"
                    description="Stop loss execution and drawdown limit notifications"
                    icon={AlertCircle}
                    checked={eventOn('stop_loss_triggered')}
                    onChange={(checked) =>
                      handleNotificationSettingChange('events', 'stop_loss_triggered', checked)
                    }
                  />
                  <NotificationSwitch
                    label="Emergency Kill Switch & Liquidations"
                    description="Venue liquidation proximity and emergency halt activations"
                    icon={Shield}
                    checked={eventOn('kill_switch_activated', 'margin')}
                    onChange={(checked) =>
                      handleNotificationSettingChange('events', 'kill_switch_activated', checked)
                    }
                  />
                </div>
              </div>

              <div>
                {/* fontSize: 10 → --text-micro (label — Q4). */}
                <p className="mb-1.5 text-micro font-bold uppercase tracking-wide text-content-secondary">
                  Automation &amp; security
                </p>
                <div className="flex flex-col gap-1.5">
                  <NotificationSwitch
                    label="Email Notifications"
                    description="Receive verified critical alerts to your primary email address"
                    icon={Mail}
                    checked={emailChannelOn}
                    onChange={(checked) =>
                      handleNotificationSettingChange('channels', 'email', checked)
                    }
                  />
                  <NotificationSwitch
                    label="Bot Lifecycle State Changes"
                    description="Strategy start, pause, runtime errors, and halts"
                    icon={Cpu}
                    checked={eventOn('bot_state_change', 'bot')}
                    onChange={(checked) =>
                      handleNotificationSettingChange('events', 'bot_state_change', checked)
                    }
                  />
                  <NotificationSwitch
                    label="Security & Login Events"
                    description="New login detections and authentication state alterations"
                    icon={Lock}
                    checked={eventOn('new_login_detected', 'login')}
                    onChange={(checked) =>
                      handleNotificationSettingChange('events', 'new_login_detected', checked)
                    }
                  />
                </div>
              </div>
            </div>

            {/* fontSize: 10 → --text-body (sentence — Q1), +30%. This is a statement about
                what the platform will do whatever the switches say, which makes it the one
                sentence in the panel that must not be the smallest text in it. */}
            <p className="mt-3 flex items-start gap-1.5 text-body text-content-muted">
              <Lock size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
              <span>Mandatory risk alerts (global kill switch triggers) cannot be disabled.</span>
            </p>
          </Panel>

          <Panel
            title="Affiliate & Referral Program"
            state={referral.state}
            loading={{
              kind: 'skeleton-metric',
              rows: 1,
              columns: 4,
              label: 'Loading your referrals',
            }}
            empty={{
              headline: 'No referral record yet',
              body: 'A referral code and signup link are created the first time the referral '
                + 'programme is used on this account.',
              action: { label: 'Read again', onClick: referral.refetch },
            }}
            error={{ error: referral.error, onRetry: referral.refetch }}
            unavailable={{ reason: 'Your referral figures cannot be read right now.' }}
            unauthorised={{ error: referral.error }}
            actions={
              /* fontSize: 9 → --text-micro (label — Q4). This was a *20% RECURRING* pill, in
                 green, as static JSX: `ReferralStatsResponse` reports counts, balances and
                 history and no rate at all. */
              <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-content-muted">
                {FIELD.commissionRatePct.label}
                <Marker field={FIELD.commissionRatePct} />
              </span>
            }
          >
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div className="min-w-0 rounded-md border border-line-default bg-surface-raised px-2.5 py-2">
                {/* fontSize: 9 → --text-micro (label — Q4). */}
                <p className="text-micro uppercase tracking-wide text-content-muted">
                  {FIELD.referralCode.label}
                </p>
                <div className="mt-0.5 flex items-center justify-between gap-1.5">
                  {/* fontSize: 12 → --text-body (value — Q7). A referral code is an IDENTIFIER
                      and keeps its monospace. THE UUID-DERIVED FALLBACK IS GONE: the previous
                      version handed the trader the first eight characters of their own primary
                      key, and the copy button copied it. */}
                  {referralCode.available ? (
                    <code className="truncate font-mono text-body font-bold text-brand">
                      {referralCode.value}
                    </code>
                  ) : (
                    <Marker field={FIELD.referralCode} reason={referralCode.reason} />
                  )}
                  <CommandButton
                    intent="ghost"
                    icon={copiedKey === 'ref_code' ? Check : Copy}
                    aria-label="Copy your referral code"
                    onClick={() =>
                      copyToClipboard(
                        referralCode.available ? referralCode.value : null,
                        'ref_code',
                      )
                    }
                  />
                </div>
              </div>

              <div className="min-w-0 rounded-md border border-line-default bg-surface-raised px-2.5 py-2">
                {/* fontSize: 9 → --text-micro (label — Q4). */}
                <p className="text-micro uppercase tracking-wide text-content-muted">
                  {FIELD.referralLink.label}
                </p>
                <div className="mt-0.5 flex items-center justify-between gap-1.5">
                  {/* fontSize: 10 → --text-body (value — Q7). A URL is an IDENTIFIER and keeps
                      its monospace; truncation with a `title` is permitted for an identifier
                      and never for prose (§2.5, mechanism 4). The placeholder
                      `"https://..."` is gone. */}
                  {referralLink.available ? (
                    <span
                      title={String(referralLink.value)}
                      className="truncate font-mono text-body text-content-primary"
                    >
                      {referralLink.value}
                    </span>
                  ) : (
                    <Marker field={FIELD.referralLink} reason={referralLink.reason} />
                  )}
                  <CommandButton
                    intent="ghost"
                    icon={copiedKey === 'ref_link' ? Check : Copy}
                    aria-label="Copy your signup link"
                    onClick={() =>
                      copyToClipboard(
                        referralLink.available ? referralLink.value : null,
                        'ref_link',
                      )
                    }
                  />
                </div>
              </div>
            </div>

            {/*
              * fontSize: 8 ×4 → --text-micro (label — Q4), +25% each. They were the smallest
              * text on the page, two pixels below the floor, and they labelled two counts and
              * two money figures. At --text-micro `repeat(4, 1fr)` stops fitting inside the
              * narrower column, so the grid is 2-up below `sm` and 4-up above — §2.5's third
              * mechanism. Shrinking the figures instead would have been §2.5's named tell for
              * an illegal shrink. fontSize: 13 ×4 (the figures) leaves with the call site onto
              * `ds/Metric`, which keeps them monospace.
              */}
            <div className="mt-2 grid grid-cols-2 gap-2 rounded-md border border-line-default bg-surface-raised p-2 sm:grid-cols-4">
              <Metric
                label={FIELD.totalReferralCount.label}
                value={totalReferrals}
                format="integer"
                tier={3}
              />
              <Metric
                label={FIELD.activeReferralCount.label}
                value={activeReferrals}
                format="integer"
                tier={3}
              />
              <Metric
                label={FIELD.pendingEarningsUsd.label}
                value={pendingEarnings}
                format="currency"
                precision={2}
                unit="USD"
                tier={3}
              />
              <Metric
                label={FIELD.lifetimeEarningsUsd.label}
                value={lifetimeEarnings}
                format="currency"
                precision={2}
                unit="USD"
                tier={3}
              />
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
