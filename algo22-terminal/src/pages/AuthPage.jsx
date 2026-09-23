/**
 * pages/AuthPage.jsx — sign in, sign up, and the 6-digit email second factor.
 *
 * retail-ui-simplification task 7.11 (commit 17). Requirements 4.1, 4.2, 4.3, 4.4, 5.1, 5.2,
 * 6.2, 10.1, 16.1, 16.3, 22.1, 22.2. design.md §2.4, §2.5, D5.
 *
 * 29 absolute pixel sizes to 0 and 30 colour literals to 0, both budgets lowered in this
 * commit. 29 inline `monospace` declarations to 3. The three gradients go. **The last page in
 * Requirement 4.5's group, and the largest: 1,180 lines** (the task says 1,181 — the file has
 * 1,180 newline-terminated lines, and the figure is off by one).
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE AUTHENTICATION FLOW IS NOT TOUCHED. NOT ONE ARGUMENT OF IT.
 * ═══════════════════════════════════════════════════════════════════════════
 * Requirement 16.1 puts authentication logic out of scope and 16.3 puts route paths and guard
 * conditions out of scope. `signInWithPassword`, `signInWithOtp` (both dispatches),
 * `verifyOtp`, `signUp` and `signInWithOAuth` are carried across with the same arguments in
 * the same order, the same 60-second resend cooldown, the same `AUTH_STATES` machine and the
 * same transitions between its five states, the same `passwordVerified` invariant in front of
 * the OTP check, the same `sessionStorage` write on a returned session and the same
 * `/app/dashboard` navigation. **This commit is presentation only.**
 *
 * `emailRedirectTo` / `redirectTo` still come from `getAuthRedirectUrl` on every one of the
 * four calls that can put a link in a mailbox. That helper exists because omitting the option
 * made Supabase fall back to the project's dashboard Site URL — a developer's laptop — so a
 * production user clicking a verification link got a connection refusal with a perfectly valid
 * token in the hash. `tests/unit/pages/authRedirectOrigin.test.jsx` asserts it 29 ways,
 * including by scanning `src/` for the call SITES, which is why this header names the methods
 * bare rather than spelling the expressions: a second occurrence in prose would read as a
 * second call site.
 *
 * WHICH IS WHY `usePanelState` IS NOT ADOPTED HERE, AND THAT IS A RECORDED FINDING
 * ------------------------------------------------------------------------------
 * Requirement 4.1 asks every read to resolve through `usePanelState`. **There is no read on
 * this page.** Every call it makes is a MUTATION — a credential check, an account creation, a
 * code dispatch, a code verification, a provider round-trip — and `usePanelState` drives ONE
 * reader it may re-issue on a `deps` change or on `refetch`. A hook that can re-run its
 * subject must never be pointed at a sign-up or a code dispatch. The hook also classifies a
 * THROWN `ApiError` through `classifyReadFailure`, and Supabase resolves `{data, error}`
 * rather than throwing, so the specific conditions this page distinguishes — invalid
 * credentials, an expired code, a wrong code, a verification that returned no session —
 * would flatten into one generic arm and Requirement 16 forbids losing them.
 *
 * So the page keeps its own `loadingAction` / `error` / `success`, and what changes is what
 * RENDERS them: `ds/CommandButton`'s `loading` for the four in-flight commands and `ds/Alert`
 * for the failure and the confirmation. That is the same resolution `pages/TwoFA.jsx` (7.3)
 * and `pages/UpdatePasswordPage.jsx` (7.5) took, so the three auth surfaces do not diverge on
 * one question.
 *
 * NO `pageFields.js` ENTRY EITHER, AND FOR THE SAME REASON THOSE TWO GAVE (Requirement 4.2)
 * ---------------------------------------------------------------------------------------
 * **This page renders no figure from any response body.** There is no count, balance,
 * percentage or timestamp on it, and nothing it shows is read from a server payload: the
 * masked address is the trader's own input, the cooldown is a local timer, and the password
 * strength is computed from the characters in the field. Requirement 4.2 declares every
 * trader-visible FIGURE with its source path and its not-available reason; a page with no
 * path to declare would put an entry in `PAGE_FIELDS_BY_PAGE` with nothing in it, which makes
 * that audit look complete where it is empty.
 *
 * ONE FABRICATED CLAIM WAS HERE, AND IT IS THE SAME SHAPE AS PROFILE'S STATIC SECOND FACTOR
 * ---------------------------------------------------------------------------------------
 * **The page told every trader that a code had been emailed, whether or not one had.** On the
 * sign-in path the OTP dispatch sat inside a `try`/`catch` whose body discarded the result:
 * the call was awaited and its `{data, error}` was never bound, so a refusal — a rate limit is
 * the common one — was invisible. The `catch` could not see it either, because supabase-js
 * RESOLVES with an error rather than throwing, so that arm had never run for the condition its
 * own comment named. The screen then said *"Password verified. Enter the 6-digit verification
 * code sent to your email"* over *"A 6-digit verification code has been sent to t••••r@…"*,
 * and the trader waited for mail that was never sent.
 *
 * The fix is presentation, and it reads a value the call already returns: the dispatch's own
 * `error` now decides which sentence those two lines carry. Nothing else moves — the call, its
 * arguments, the transition to `OTP_REQUIRED`, the cooldown and the resend are all exactly as
 * they were, and a failed dispatch still leaves the trader on the OTP step with a working
 * *Resend code*, which is what the swallowed failure was relying on all along. It just says so
 * now. The sign-up path reports dispatched because its own call throws on a refusal, so
 * reaching that line means Supabase accepted the send.
 *
 * ONE MORE FIGURE DEFECT IS NAMED AND NOT CHANGED: **the cooldown renders `00:60`.** The
 * counter is `00:` concatenated with the seconds, so the first second of a 60-second wait
 * displays a minute-and-second reading that does not exist. `tests/unit/auth_otp.test.jsx`
 * pins the string `Resend code in 00:` verbatim, so correcting the format belongs to a commit
 * that owns that test rather than to a presentation pass that must leave it green.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 29 PIXEL SIZES, BY §2.4'S NINE QUESTIONS
 * ═══════════════════════════════════════════════════════════════════════════
 * 9 ×9, 11 ×8, 10 ×7, 12 ×3, 20 ×1, 22 ×1 — every one of the 24 at or below 12px, so almost
 * every resolution is upward, which §2.5 says is the normal case and not the exception. Each
 * is a comment on its call site. Eleven resolve by moving onto a primitive that already reads
 * a declared step, so their declaration is removed rather than mapped.
 *
 *   22 ×1  the `<h1>`                         Q8 heading, page level  → `--text-page`
 *   20 ×1  the six code inputs                Q3 control text         → `--text-page`
 *   12 ×2  the password and confirm inputs    Q3 control text         → `--text-body`
 *   12 ×1  the Google command                 → `ds/CommandButton`; removed
 *   11 ×1  the header subtitle                Q1 sentence             → `--text-body`
 *   11 ×1  the terms-and-risk checkbox line   Q1 sentence             → `--text-body`
 *   11 ×1  the cooldown/back row container    → split; see below
 *   11 ×2  the resend and back commands       → `ds/CommandButton`; removed
 *   11 ×2  the failure and confirmation bands → `ds/Alert`; removed
 *   11 ×1  the duplicate Google command       → removed with the duplicate; see below
 *   10 ×1  the code-entry label               Q1 sentence             → `--text-body`
 *   10 ×1  *Passwords match*                  Q1 sentence             → `--text-body`
 *   10 ×1  the confirm-mismatch message       Q1 sentence             → `--text-body`
 *   10 ×1  the sign-in/sign-up footer line    Q1 sentence             → `--text-body`
 *   10 ×1  the *OR* divider                   Q9; see below           → `--text-micro`
 *   10 ×1  the strength verdict               → `ds/StatusBadge`; removed
 *   10 ×1  the inline email message           → `ds/Field`'s `error`; removed
 *    9 ×2  the two password labels            Q4 label; see below     → `--text-small`
 *    9 ×1  *Password Strength*                Q4 label                → `--text-micro`
 *    9 ×1  the four strength requirements     Q5 chips                → `--text-micro`
 *    9 ×4  the legal footer links             Q9; see below           → `--text-micro`
 *    9 ×1  the *OR ENTER CREDENTIALS* divider → removed with the duplicate command
 *
 * FOUR OF THOSE ARE JUDGEMENTS, AND EACH IS A FINDING ABOUT THE ROLE SET (§2.4's Q9)
 * --------------------------------------------------------------------------------
 * **The two password labels go to `--text-small`, not to Q4's `--text-micro`.** They sit
 * beside a third label — the email field's — which `ds/Field` renders at `--text-small` and
 * which this page cannot move without editing a shared primitive. Three labels in one column
 * at two sizes is a worse outcome than one step of disagreement with Q4, and §2.5's tell for
 * an illegitimate resolution is a resolution chosen to avoid a layout change: this one grows
 * 9 → 11. The finding is that Q4's step and `ds/Field`'s step are not the same number.
 *
 * **§2.3 has no role for connective text and none for a navigation link.** The *OR* divider
 * names nothing, carries no state, is not editable, is not a cell, is not a figure and is not
 * a heading; the four legal links in the footer answer the same nine questions the same way.
 * Q9 says to record the call site rather than guess, so: both are taken at the label step
 * (`--text-micro`), which is where a word of connective tissue and a standing link belong, and
 * the gap in the role set is what is recorded. An order id and an API key prefix reached the
 * same gap from the other direction at task 7.3, so this is the second kind of call site the
 * nine questions do not have a row for.
 *
 * **`control text`'s step is a FLOOR, not a cap** — task 7.3's finding, and the same control
 * reaches it again. Q3 would take a 6-digit one-time code from 20px to 13px inside a 54px-tall
 * box, a 35% shrink on the one control this screen exists for. Read as a floor the role admits
 * any step at or above `body`, and `--text-page` is what the sibling screen's six boxes take,
 * so the two do not diverge. The two password inputs take `--text-body` itself: they are
 * masked, so character identity is not being read from them, and 12 → 13 is still growth.
 *
 * **An imperative label satisfies Q1's finite-verb test, and Q1 is asked first.** *Enter
 * 6-Digit Email Code* is a `<label htmlFor>` — the canonical Q4 element — and it opens with a
 * verb. The order is load-bearing and stated so the same call site resolves the same way
 * twice, so it goes to `--text-body` as a sentence rather than to `--text-micro` as a label.
 * This is the call site where that ordering bites, and the role set having no
 * "instruction that is also a label" row is the finding.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 29 INLINE `monospace` DECLARATIONS → 3 (Requirements 3.1, 3.2)
 * ═══════════════════════════════════════════════════════════════════════════
 * Requirement 3.1's rule, and the classification Requirement 3.2 asks to be recorded:
 *
 *   KEPT (3): the password input, the confirm-password input, and the six code boxes. All
 *   three hold a string that has to be typed exactly, and when the trader reveals a password
 *   a fixed advance width is what stops `I`/`l` and `0`/`O` being a transcription risk. The
 *   six boxes hold the code itself — task 7.3 kept the same construct for the same reason.
 *   All three now read the `font-mono` utility rather than the generic `monospace` family, so
 *   they resolve `--font-mono` (JetBrains Mono, then the fallbacks `tokens.css` declares) and
 *   the file holds **no inline `monospace` declaration at all**.
 *
 *   DROPPED (26): every label, every message, every divider, the header subtitle, the terms
 *   line, the strength verdict and its four requirements, the cooldown counter, the four
 *   commands, the mode-toggle footer and the four legal links. Not one of them is an
 *   identifier: they are prose, labels and controls. A monospace sentence is the strongest
 *   signal a page can give that it belongs to the previous generation, and on the screen where
 *   a trader types a password it also reads as machine output at the moment they most need to
 *   be reading English. `ds/StatusBadge`, `ds/CommandButton` and `ds/Alert` bring their own
 *   families, which is why eleven of the 26 leave with their declarations.
 *
 * ONE `uppercase` GOES, AND FOUR STAY (Requirement 3.3's three-word rule)
 * ---------------------------------------------------------------------
 * *Enter 6-Digit Email Code* is four words and loses the transform: a four-word instruction
 * in capitals loses word-shape recognition and gains nothing, which is the mechanism that
 * clause is about. *Password*, *Confirm Password*, *Password Strength* and *OR* are one and
 * two words and keep it. The clause is about the CSS transform; the four shouting button
 * labels (*SIGN IN →*, *CREATE ACCOUNT & SEND OTP →*, *VERIFY & CONTINUE →*) are literal copy
 * rather than a transform of it, and copy is Requirement 19.6's, not this pass's.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 30 COLOUR LITERALS (Requirements 5.1, 5.2, 5.3)
 * ═══════════════════════════════════════════════════════════════════════════
 *   #EF5350 ×4   **the palette's own loss red, spelled as a number.** Two are the strength
 *                function's `color` for a weak password and two are the form's two inline
 *                messages. `token.status.error.fg` is that exact value, so all four are a
 *                token written out by hand.
 *   #F59E0B ×1   the *Fair* verdict. **`--color-status-warning` exactly** — the same
 *                coincidence `pages/Billing.jsx`'s quota bar and `pages/ExchangeManager.jsx`'s
 *                venue count carried — and here the group is right as well as the value: a
 *                password the form will accept but would rather not is what `warning` means.
 *   #10B981 ×6   **NOT in the palette.** Tailwind's emerald, where this app's green is
 *                #26A69A. Five painted the four met-requirement ticks and *Passwords match*
 *                and one painted the *Strong* verdict; all six now come from
 *                `statusToken('ok')`, so the hue is chosen by `design/semantic.js` and not
 *                here. Retokening them one for one would have been re-deciding three states'
 *                hues by hand at the same time (Requirement 5.3).
 *   rgba ×2      the two radial washes of the background glow. REMOVED with the element —
 *                see the gradient table below. One of them is a violet, and **there is no
 *                violet in the palette**, which is the Requirement 5.3 question answered the
 *                way task 7.10 answered it: nothing is substituted.
 *   rgba ×1      `0 40px 80px rgba(0,0,0,0.7)` under the card → `token.shadow.overlay`
 *                (`0 16px 48px rgba(0,0,0,0.60)`), the largest declared elevation, for the one
 *                floating layer on the screen. A hand-mixed shadow half again as deep as any
 *                declared one.
 *   #00d4ff      the header medallion's gradient. #00d4ff IS `token.brand.base`; #0055ff is a
 *   #0055ff      blue the palette does not have. Both leave with the gradient.
 *   rgba ×1      `0 0 20px rgba(0,212,255,0.25)`, the medallion's glow. REMOVED, and nothing
 *                replaces it: `tokens.css`'s elevation block says *no coloured glows. Calm by
 *                default (Req 1.5)* and its alias block records that `--animate-pulse-glow`
 *                "was not carried forward" for the same reason.
 *   #000 ×2      the medallion glyph, on the gradient. Pure black is not in the palette; the
 *                glyph is `token.brand.base` on `token.brand.wash` now, which is task 7.3's
 *                resolution for the same medallion on the sibling auth screen.
 *   rgba ×1      `rgba(255,255,255,0.05)` behind the Google command →
 *                `ds/CommandButton`'s `secondary` intent, which owns its own surface.
 *   #4285F4      **Google's four brand colours, in an inline logo mark.** REMOVED with the
 *   #34A853      duplicate command they sat on, and they would have gone anyway:
 *   #FBBC05      `pages/Billing.jsx` settled this at task 7.9 — *there is no token for another
 *   #EA4335      company's identity and there should not be one* — and the provider is stated
 *                in WORDS on the control, which is the channel that cannot be wrong about
 *                which provider it is.
 *   #FF3D00      the failure band's text, wash and border. **The RETIRED pre-M1 orange-red**,
 *   rgba ×2      not the current #EF5350, so migrating them one for one would have been
 *                re-deciding the error hue by hand. All three leave with the band, which is
 *                `ds/Alert` at `error` now and takes no colour at all.
 *   #00C853      the confirmation band's text, wash and border. **The RETIRED pre-M1 green.**
 *   rgba ×2      Same resolution, at `info`: `ds/Alert` has no `success` severity and one is
 *                not added to a shared primitive from inside a page commit, so a completed
 *                step reports at `info` with its sentence unchanged — tasks 7.3 and 7.5 both.
 *
 * TWELVE OFF-SCALE CONSTRUCTS NEITHER GUARD CAN SEE went with them: two radii off the
 * declared scale — `borderRadius: 18` on the card (the scale stops at `--radius-xl`, 12px, and
 * this is the third page in this group to carry that exact number) and `borderRadius: 10` on
 * the medallion; four `transition: all` durations, 150ms ×3 and 200ms ×1, against
 * `--transition-fast` and `--transition-base`; and six `letterSpacing` values — `1` ×2, `2` ×3
 * and a NEGATIVE `-0.5` on the `<h1>` — against `tracking-wide`. Seven more `borderRadius: 8`
 * are `--radius-lg` spelled as a number and now read the utility.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE THREE GRADIENTS — DECISION D5, EVERY REMOVAL NAMES ITS REPLACEMENT
 * ═══════════════════════════════════════════════════════════════════════════
 * `tokens.css` declares no gradient, so Requirement 10.1 leaves each one resolving to a flat
 * surface it does declare — or to nothing, which is a replacement too and has to be said.
 *
 *   1. `radial-gradient(ellipse 70% 70% at 20% 50%, brand 5%, transparent)`  → NOTHING. It
 *      was half of one `position: absolute; inset: 0` element with no content, no state and
 *      `pointerEvents: "none"`. What replaces it is the surface underneath it, which is
 *      `token.surface.canvas` and is already declared on the root — the same flat canvas every
 *      migrated page shows behind a centred card.
 *   2. `radial-gradient(ellipse 50% 50% at 80% 30%, violet 4%, transparent)` → NOTHING, same
 *      element. Recorded separately because it is the one that could not have been retokened:
 *      the palette has no violet at any opacity.
 *   3. `linear-gradient(135deg, #00d4ff, #0055ff)` on the header medallion → `token.brand.wash`
 *      flat, with `token.line.strong` as the border where a semi-transparent brand ring used
 *      to be, and the glyph in `token.brand.base`. That is exactly what task 7.3 did to the
 *      same medallion on the two-factor screen and what task 5.2 did to Marketplace's featured
 *      card, so the three surfaces resolve one construct one way. The emphasis the gradient
 *      carried is carried by the wash and the ring.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * ONE CONTROL COUNT CHANGES, AND IT IS A DEDUPLICATION (Requirement 16)
 * ═══════════════════════════════════════════════════════════════════════════
 * **The page rendered the Google command TWICE** — once above the credential fields behind a
 * *OR ENTER CREDENTIALS* divider, once below the submit behind a *OR* divider — with the same
 * handler, the same label and two different treatments (a `Globe` glyph at 11px, and a
 * four-colour logo mark at 12px). Two controls, one action, twenty lines apart.
 *
 * They are now one, and the surviving control keeps everything either copy of it had that a
 * trader or a test can reach: the label *Continue with Google*, the `data-testid` two tests
 * bind to, the *Redirecting to Google...* in-flight text, the disabled-while-busy behaviour
 * and the `Globe` glyph. What goes is the second treatment and the top divider that separated
 * a block from nothing once the command below it became the only one. The removed copy's
 * `aria-label` — *Sign in with Google* — goes with it, and the survivor's accessible name is
 * its visible label, which is the stronger arrangement (a name that does not contain the
 * visible text is its own defect). Requirement 16 forbids removing a control; this removes a
 * DUPLICATE of one, which is the same move task 5.2 made when Marketplace's two card
 * renderers carrying the same mistake became one.
 *
 * `ds/Field` TAKES THE EMAIL FIELD AND NOT THE TWO PASSWORD FIELDS (Requirement 4.4)
 * --------------------------------------------------------------------------------
 * `ds/Field` renders a visible `<label htmlFor>`, links its message with `aria-describedby`
 * and never names a control from its placeholder, which is the defect `Inp` carries — so the
 * email field is `ds/Field` and `Inp` leaves this file. Its `Mail` glyph goes with it: the
 * field is labelled *Email Address* and `ds/Field` has no icon slot, which is the same
 * trade task 7.5 recorded when the padlock left the reset form.
 *
 * **The two password fields cannot adopt it, and that is the finding rather than a fix.**
 * `ds/Field` has no slot for a control inside or beside the input — `unit` is a
 * `pointer-events-none` span — and each password field carries a show/hide toggle, which is a
 * control Requirement 16 forbids losing. So the two keep their own `<label htmlFor>` and
 * input, retokened, at the type step their `ds/Field` sibling reads. A trailing-affordance
 * slot on `ds/Field` is the need; adding one from a page commit is how the second convention
 * starts (Requirements 4.4, 17.1–17.3).
 *
 * TWO MORE FINDINGS ON THE PRIMITIVES, RECORDED AND NOT RESOLVED
 * -------------------------------------------------------------
 *   * **`ds/Field` renders its validation message at `--text-micro`.** Requirement 2.3 puts a
 *     sentence at `--text-body` or larger, and *Please enter a valid email address (e.g.
 *     user@domain.com)* is a sentence. Adopting the primitive is still right — the message is
 *     linked by `aria-describedby`, which the hand-built `<p>` never was — but the step is one
 *     the page cannot set and would not choose. Same shape as task 7.6's `ds/SectionHeader`
 *     finding.
 *   * **The strength meter's bar stays hand-built.** `ds/RiskIndicator` reports risk, not
 *     progress, and there is no meter primitive; the bar is `aria-hidden` decoration behind
 *     the `ds/StatusBadge` that carries the verdict in text, so nothing is announced twice and
 *     no `role="progressbar"` is invented for it.
 *
 * EVERY FOCUS RING ON THIS PAGE WAS PER-ELEMENT, AND NOW THERE IS ONE
 * -------------------------------------------------------------------
 * Sixteen controls carried their own cyan focus ring — `focus:ring-2` ×4 and `focus:ring-1`
 * ×12 — which is sixteen chances to get a keyboard affordance wrong and one page's worth of
 * drift from every other. `tokens.css:186` applies `:focus-visible` once, to
 * `a, button, input, select, textarea, summary, [tabindex]` — Requirement 18.2 applied in one
 * place "so no control can omit it" — so the rings are removed rather than replaced and every
 * control on this page, including the six code boxes and the two show/hide toggles, still
 * shows a visible focus indicator on the keyboard path.
 *
 * The six code inputs keep the accessible name they have — *Digit N of 6*, one per box.
 * `pages/TwoFA.jsx` set the precedent with *Verification code digit N of 6* and the wording is
 * NOT adopted here: `tests/unit/auth_otp.test.jsx` binds to these six names verbatim, the
 * existing name already distinguishes the six from each other, and renaming a control to match
 * a sibling page's phrasing is not worth editing a test that guards a second factor.
 */

import { useState, useEffect, useMemo, useRef } from "react";
import { useNavigate, Link } from "react-router-dom";
import { ArrowLeft, Check, Eye, EyeOff, Globe, ShieldCheck, X, Zap } from "lucide-react";

import { Alert } from "../components/ds/Alert";
import { CommandButton } from "../components/ds/CommandButton";
import { Field } from "../components/ds/Field";
import { StatusBadge } from "../components/ds/StatusBadge";
import { statusToken } from "../design/semantic";
import { token } from "../design/tokens";
import { supabase } from "../supabase";
// ONE origin derivation for every auth link this page can cause Supabase to email.
// Omitting the redirect made Supabase fall back to the dashboard Site URL
// (a developer's laptop), so production users clicking a verification link got
// ERR_CONNECTION_REFUSED with a perfectly valid token in the hash. See `config.js`.
import { getAuthRedirectUrl } from "../config";

const RESEND_COOLDOWN_SECONDS = 60;

export const AUTH_STATES = {
  PASSWORD_REQUIRED: "PASSWORD_REQUIRED",
  PASSWORD_AUTHENTICATING: "PASSWORD_AUTHENTICATING",
  OTP_REQUIRED: "OTP_REQUIRED",
  OTP_VERIFYING: "OTP_VERIFYING",
  AUTHENTICATED: "AUTHENTICATED",
};

/**
 * Safely masks an email address to avoid unnecessary exposure on verification screen.
 * Example: trader.alpha@vyomquant.io -> t••••a@vyomquant.io
 */
export function maskEmail(email) {
  if (!email || typeof email !== "string" || !email.includes("@")) return email || "";
  const parts = email.trim().split("@");
  const localPart = parts[0];
  const domain = parts.slice(1).join("@");

  if (localPart.length <= 2) {
    return `${localPart[0] || ""}••••@${domain}`;
  }
  const maskedLocal = `${localPart[0]}••••${localPart[localPart.length - 1]}`;
  return `${maskedLocal}@${domain}`;
}

/**
 * The strength verdict now answers with a STATE rather than a colour.
 *
 * `color` was one of three literals — two of them the palette's own values spelled by hand and
 * one of them Tailwind's emerald, which the palette does not have. Requirement 1.4 puts every
 * status colour behind `design/semantic.js`, so this returns a key from that module's
 * vocabulary and the caller resolves the hue: `blocked` because the form refuses a score below
 * 2, `warning` for a password it will take but would rather not, `ok` for one that meets every
 * rule. `score`, `label` and `checks` are unchanged — `auth_otp.test.jsx` asserts on `score`.
 */
export const evaluatePasswordStrength = (pwd) => {
  if (!pwd) return { score: 0, label: "", state: "unknown", checks: { length: false, upper: false, lower: false, number: false, special: false } };

  const checks = {
    length: pwd.length >= 8,
    upper: /[A-Z]/.test(pwd),
    lower: /[a-z]/.test(pwd),
    number: /[0-9]/.test(pwd),
    special: /[^A-Za-z0-9]/.test(pwd),
  };

  const count = Object.values(checks).filter(Boolean).length;

  let label = "Weak";
  let state = "blocked"; // → the `error` group: below a score of 2 the form refuses to submit

  if (count <= 2) {
    label = "Weak";
    state = "blocked";
  } else if (count === 3 || count === 4) {
    label = "Fair";
    state = "warning";
  } else if (count === 5) {
    label = "Strong";
    state = "ok";
  }

  return { score: count, label, state, checks };
};

/** One met requirement, as a chip. `ds/StatusBadge` is not used: these four are not states. */
function StrengthRequirement({ met, children }) {
  /* fontSize: 9 → --text-micro on the row below (chip — Q5: three words or fewer, carrying a
     met/unmet state). The tick and the cross are the second channel beside the hue. */
  return (
    <span
      className="inline-flex items-center gap-1"
      style={{ color: met ? statusToken("ok").fg : token.content.muted }}
    >
      {met ? <Check size={10} aria-hidden="true" /> : <X size={10} aria-hidden="true" />}
      {children}
    </span>
  );
}

function PasswordStrengthMeter({ strength }) {
  if (!strength || strength.score === 0) return null;
  const pct = (strength.score / 5) * 100;
  // The verdict's hue, chosen by `design/semantic.js` from the state and by nothing else.
  const { fg } = statusToken(strength.state);

  return (
    <div className="mt-1.5 flex flex-col gap-1">
      <div className="flex items-center justify-between">
        {/* fontSize: 9 → --text-micro (label — Q4: it names the meter and the verdict beside
            it). Two words, so Requirement 3.3's rule leaves the transform alone. */}
        <span className="text-micro font-medium uppercase tracking-wide text-content-secondary">
          Password Strength
        </span>
        {/* fontSize: 10 → the primitive's step. A hand-built verdict in a hand-picked hue
            becomes `ds/StatusBadge`, which derives colour, weight and case from the state. */}
        <StatusBadge state={strength.state} label={strength.label} />
      </div>
      {/* Decoration, and `aria-hidden` for it: the badge above already says the verdict, and
          a second announcement of the same fact is noise. See the header on why this is not a
          `role="progressbar"` and not a new primitive. */}
      <div
        aria-hidden="true"
        className="overflow-hidden rounded-sm border border-line-default"
        style={{ height: 4, background: token.surface.inset }}
      >
        <div className="h-full" style={{ width: `${pct}%`, background: fg, transition: `width ${token.transition.base}` }} />
      </div>
      <div className="mt-1 flex flex-wrap gap-2.5 text-micro">
        <StrengthRequirement met={strength.checks.length}>8+ chars</StrengthRequirement>
        <StrengthRequirement met={strength.checks.upper && strength.checks.lower}>
          Upper &amp; lower
        </StrengthRequirement>
        <StrengthRequirement met={strength.checks.number}>Number</StrengthRequirement>
        <StrengthRequirement met={strength.checks.special}>Symbol</StrengthRequirement>
      </div>
    </div>
  );
}

export default function AuthPage({ mode = "signin" }) {
  const navigate = useNavigate();

  // Mode: "signin" | "signup"
  const [authMode, setAuthMode] = useState(mode === "signup" ? "signup" : "signin");

  // Explicit Authentication State Machine
  const [authState, setAuthState] = useState(AUTH_STATES.PASSWORD_REQUIRED);
  const [passwordVerified, setPasswordVerified] = useState(false);

  // Form states
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);

  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);

  const [confirmPassword, setConfirmPassword] = useState("");
  const [confirmTouched, setConfirmTouched] = useState(false);
  const [showConfirmPw, setShowConfirmPw] = useState(false);

  const [agreedToPolicies, setAgreedToPolicies] = useState(false);

  // OTP form state: 6 separate digits
  const [otpDigits, setOtpDigits] = useState(["", "", "", "", "", ""]);
  const otpInputRefs = useRef([]);

  // UI / Status state
  const [loadingAction, setLoadingAction] = useState(""); // "" | "password_auth" | "verify_otp" | "google" | "resend_otp"
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Whether the code dispatch REPORTED success. See the header: this used to be assumed, and
  // the screen said an email had been sent whether or not one had.
  const [otpDispatched, setOtpDispatched] = useState(false);

  // Resend cooldown timer
  const [cooldown, setCooldown] = useState(0);

  const isUp = authMode === "signup";
  const isLoading = !!loadingAction;

  const clearStatus = () => {
    setError("");
    setSuccess("");
  };

  useEffect(() => {
    setAuthMode(mode === "signup" ? "signup" : "signin");
    setAuthState(AUTH_STATES.PASSWORD_REQUIRED);
    setPasswordVerified(false);
    clearStatus();
    setEmailTouched(false);
    setConfirmTouched(false);
  }, [mode]);

  // Handle countdown interval
  useEffect(() => {
    if (cooldown <= 0) return;
    const interval = setInterval(() => {
      setCooldown((prev) => {
        if (prev <= 1) {
          clearInterval(interval);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [cooldown]);

  // Focus first OTP digit upon entering OTP stage
  useEffect(() => {
    if (authState === AUTH_STATES.OTP_REQUIRED) {
      const timer = setTimeout(() => {
        otpInputRefs.current[0]?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [authState]);

  // Inline email format validation
  const emailInlineError = useMemo(() => {
    if (!emailTouched || !email) return "";
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email.trim())) {
      return "Please enter a valid email address (e.g. user@domain.com)";
    }
    return "";
  }, [email, emailTouched]);

  // Password strength score
  const passwordStrength = useMemo(() => {
    return evaluatePasswordStrength(password);
  }, [password]);

  // Confirm password match check
  const confirmPasswordError = useMemo(() => {
    if (!isUp || !confirmTouched || !confirmPassword) return "";
    if (password !== confirmPassword) {
      return "Passwords do not match";
    }
    return "";
  }, [isUp, password, confirmPassword, confirmTouched]);

  // STEP 1: PASSWORD AUTHENTICATION
  const handlePasswordAuth = async (e) => {
    if (e) e.preventDefault();
    setEmailTouched(true);

    const targetEmail = email.trim();
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!targetEmail || !emailRegex.test(targetEmail)) {
      setError("Please enter a valid email address.");
      return;
    }

    if (!password) {
      setError("Please enter your password.");
      return;
    }

    if (isUp) {
      setConfirmTouched(true);
      if (passwordStrength.score < 2) {
        setError("Please choose a stronger password before continuing.");
        return;
      }
      if (confirmPasswordError || password !== confirmPassword) {
        setError("Passwords do not match.");
        return;
      }
      if (!agreedToPolicies) {
        setError("You must agree to the Terms and Risk Disclosure.");
        return;
      }
    }

    if (loadingAction) return;

    clearStatus();
    setLoadingAction("password_auth");
    setAuthState(AUTH_STATES.PASSWORD_AUTHENTICATING);

    try {
      if (isUp) {
        // Sign Up Flow: Create account with password
        const { error: signUpError } = await supabase.auth.signUp({
          email: targetEmail,
          password: password,
          options: {
            // The confirmation email carries a link as well as the 6-digit code.
            emailRedirectTo: getAuthRedirectUrl(),
          },
        });

        if (signUpError) throw signUpError;

        // Account registered, dispatch email verification OTP. The call above throws on a
        // refusal, so reaching this line means the send was accepted.
        setPasswordVerified(true);
        setAuthState(AUTH_STATES.OTP_REQUIRED);
        setCooldown(RESEND_COOLDOWN_SECONDS);
        setOtpDigits(["", "", "", "", "", ""]);
        setOtpDispatched(true);
        setSuccess("Account credentials registered. Enter the 6-digit verification code sent to your email.");
      } else {
        // Sign In Flow: Verify password first
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email: targetEmail,
          password: password,
        });

        if (signInError) {
          throw signInError;
        }

        // Password verified successfully!
        // DO NOT grant application access or persist session to sessionStorage yet.
        // Immediately dispatch the 2nd factor Email OTP challenge.
        let dispatched = false;
        try {
          // The result is READ now. It was discarded, and a refused dispatch — a rate limit
          // is the usual one — left the screen claiming an email had been sent. supabase-js
          // resolves with an error rather than throwing, so the `catch` below had never run
          // for the condition it was written for. The call itself is unchanged.
          const dispatch = await supabase.auth.signInWithOtp({
            email: targetEmail,
            options: {
              shouldCreateUser: false,
              // The OTP email carries a magic link beside the 6-digit code. Without this
              // the link pointed at the project's Site URL, i.e. the developer's laptop.
              emailRedirectTo: getAuthRedirectUrl(),
            },
          });
          dispatched = !dispatch?.error;
        } catch {
          // Unchanged: a throw here does not stop the flow, because the trader can still
          // ask for a new code from the OTP step. What changes is that the step now says so.
          dispatched = false;
        }

        setPasswordVerified(true);
        setAuthState(AUTH_STATES.OTP_REQUIRED);
        setCooldown(RESEND_COOLDOWN_SECONDS);
        setOtpDigits(["", "", "", "", "", ""]);
        setOtpDispatched(dispatched);
        setSuccess(
          dispatched
            ? "Password verified. Enter the 6-digit verification code sent to your email."
            : "Password verified. The verification code could not be sent. Use Resend code below to request it again.",
        );
      }
    } catch (err) {
      setPasswordVerified(false);
      setAuthState(AUTH_STATES.PASSWORD_REQUIRED);
      const msg = err.message || "";
      if (msg.toLowerCase().includes("invalid login credentials") || msg.toLowerCase().includes("invalid credentials")) {
        setError("Invalid email or password. Please check your credentials and try again.");
      } else {
        setError(msg || "Authentication failed. Please check your inputs.");
      }
    } finally {
      setLoadingAction("");
    }
  };

  // STEP 2: 6-DIGIT EMAIL OTP VERIFICATION
  const handleVerifyOtp = async (e) => {
    if (e) e.preventDefault();
    const token = otpDigits.join("").trim();

    if (token.length !== 6 || !/^\d{6}$/.test(token)) {
      setError("Please enter all 6 numeric digits of your verification code.");
      return;
    }

    // Security Invariant: Must have completed password authentication
    if (!passwordVerified) {
      setError("Password authentication must be completed before verifying OTP.");
      setAuthState(AUTH_STATES.PASSWORD_REQUIRED);
      return;
    }

    if (loadingAction) return;

    clearStatus();
    setLoadingAction("verify_otp");
    setAuthState(AUTH_STATES.OTP_VERIFYING);

    try {
      const { data, error: verifyError } = await supabase.auth.verifyOtp({
        email: email.trim(),
        token: token,
        type: "email",
      });

      if (verifyError) {
        throw verifyError;
      }

      if (data?.session?.access_token) {
        setAuthState(AUTH_STATES.AUTHENTICATED);
        sessionStorage.setItem("token", data.session.access_token);
        setSuccess("Authentication successful. Redirecting to dashboard...");
        navigate("/app/dashboard");
      } else {
        setAuthState(AUTH_STATES.OTP_REQUIRED);
        setError("Verification accepted, but no active session was returned. Please try signing in again.");
      }
    } catch (err) {
      setAuthState(AUTH_STATES.OTP_REQUIRED);
      const msg = err.message || "";
      if (msg.toLowerCase().includes("expired")) {
        setError("The verification code has expired. Please request a new code.");
      } else if (msg.toLowerCase().includes("invalid") || msg.toLowerCase().includes("token")) {
        setError("Invalid verification code. Please check your email or request a new code.");
      } else {
        setError(msg || "Verification failed. Please check the code and try again.");
      }
      setOtpDigits(["", "", "", "", "", ""]);
      otpInputRefs.current[0]?.focus();
    } finally {
      setLoadingAction("");
    }
  };

  // Resend OTP
  const handleResendOtp = async () => {
    if (cooldown > 0 || loadingAction || !passwordVerified) return;
    clearStatus();
    setLoadingAction("resend_otp");

    try {
      const { error: otpError } = await supabase.auth.signInWithOtp({
        email: email.trim(),
        options: {
          shouldCreateUser: false,
          // Same email, same link, same requirement as the first dispatch above.
          emailRedirectTo: getAuthRedirectUrl(),
        },
      });

      if (otpError) throw otpError;

      setCooldown(RESEND_COOLDOWN_SECONDS);
      setOtpDispatched(true);
      setSuccess("A new 6-digit verification code has been dispatched.");
    } catch (err) {
      setOtpDispatched(false);
      setError(err.message || "Failed to resend verification code. Please try again later.");
    } finally {
      setLoadingAction("");
    }
  };

  // OTP Digit Change Handler
  const handleDigitChange = (index, value) => {
    const numericChar = value.replace(/\D/g, "").slice(-1);
    const newDigits = [...otpDigits];
    newDigits[index] = numericChar;
    setOtpDigits(newDigits);

    // Auto-advance focus
    if (numericChar && index < 5) {
      otpInputRefs.current[index + 1]?.focus();
    }
  };

  // OTP Keydown Handler (Backspace and arrow navigation)
  const handleDigitKeyDown = (index, e) => {
    if (e.key === "Backspace") {
      if (!otpDigits[index] && index > 0) {
        otpInputRefs.current[index - 1]?.focus();
      }
    } else if (e.key === "ArrowLeft" && index > 0) {
      otpInputRefs.current[index - 1]?.focus();
    } else if (e.key === "ArrowRight" && index < 5) {
      otpInputRefs.current[index + 1]?.focus();
    }
  };

  // OTP Paste Handler
  const handleOtpPaste = (e) => {
    e.preventDefault();
    const pastedData = e.clipboardData.getData("text").trim();
    const digitsOnly = pastedData.replace(/\D/g, "").slice(0, 6);

    if (digitsOnly.length > 0) {
      const newDigits = [...otpDigits];
      for (let i = 0; i < 6; i++) {
        newDigits[i] = digitsOnly[i] || "";
      }
      setOtpDigits(newDigits);
      const nextFocusIndex = Math.min(digitsOnly.length, 5);
      otpInputRefs.current[nextFocusIndex]?.focus();
    }
  };

  // Return to Password step
  const handleBackToPassword = () => {
    clearStatus();
    setPasswordVerified(false);
    setAuthState(AUTH_STATES.PASSWORD_REQUIRED);
    setOtpDigits(["", "", "", "", "", ""]);
  };

  // Google OAuth Fallback
  const handleGoogleAuth = async () => {
    if (loadingAction) return;
    clearStatus();
    setLoadingAction("google");
    try {
      const { error: oAuthError } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          // Was `${window.location.origin}/app/dashboard` inline. Correct, but a second
          // copy of the derivation: the helper is what stops a fifth call site getting it
          // wrong, so the one call site that was already right reads from it too.
          redirectTo: getAuthRedirectUrl(),
          queryParams: {
            access_type: "offline",
            prompt: "consent",
          },
        },
      });
      if (oAuthError) throw oAuthError;
    } catch (err) {
      setError(err.message || "Google authentication failed.");
      setLoadingAction("");
    }
  };

  const isOtpStage = authState === AUTH_STATES.OTP_REQUIRED || authState === AUTH_STATES.OTP_VERIFYING;
  const formattedCooldown = `00:${cooldown.toString().padStart(2, "0")}`;

  // Requirement 15.3, on the three commands that can be disabled: a disabled control states
  // why. The conditions are the ones the page already had — only the sentence is new.
  const credentialsIncomplete = !email.trim() || !password;
  const passwordSubmitReason = credentialsIncomplete
    ? "Enter your email address and password to continue."
    : "Another sign-in attempt is already in progress.";
  const otherActionInFlight = "Another sign-in attempt is already in progress.";

  // The two password fields share every treatment; `ds/Field` cannot hold their show/hide
  // toggle, so the classes are declared once here rather than twice below. See the header.
  /* fontSize: 12 ×2 → --text-body (control text — Q3, read as a floor). Monospace KEPT: a
     revealed password is a string that has to be typed exactly. */
  const passwordInputClass =
    "w-full rounded-lg border bg-surface-inset px-3 py-2 pr-12 font-mono text-body text-content-primary outline-none";
  /* fontSize: 9 ×2 → --text-small (label — Q4, taken one step above `micro` so the three
     field labels in this column agree; `ds/Field` owns the third and this page cannot move
     it). One and two words, so Requirement 3.3 leaves the transform alone. */
  const passwordLabelClass = "text-small font-medium uppercase tracking-wide text-content-secondary";

  return (
    <div
      className="flex min-h-screen items-center justify-center p-4"
      style={{ background: token.surface.canvas }}
    >
      {/* The background glow's two radial gradients were here, in one contentless absolutely
          positioned element. What replaces them is the canvas above — see the header's
          gradient table (Requirement 10.1, design.md D5). */}

      {/* Main Terminal Card. `borderRadius: 18` → `rounded-xl` (12px, where the declared
          radius scale stops) and the hand-mixed `0 40px 80px rgba(0,0,0,0.7)` →
          `token.shadow.overlay`, the largest elevation the token layer declares. */}
      {/* `width: 520, maxWidth: "100%"` is carried across unchanged: no declared `max-w-*`
          step is this card's width, and §2.5's proxy is about not ADDING a px dimension to a
          text container rather than about removing one that was already load-bearing. */}
      <div
        className="relative w-full rounded-xl border border-line-default p-8"
        style={{
          background: token.surface.raised,
          boxShadow: token.shadow.overlay,
          width: 520,
          maxWidth: "100%",
        }}
      >
        {/* Header Branding */}
        <div className="mb-6 text-center">
          <div
            className="mb-3 inline-flex rounded-lg border border-line-strong px-2.5 py-2"
            style={{
              // The declared 10% brand wash where a two-stop gradient used to be, and
              // `line.strong` where a semi-transparent brand ring used to be — task 7.3's
              // resolution for the same medallion on the two-factor screen.
              background: token.brand.wash,
            }}
          >
            {!isOtpStage ? (
              <Zap size={22} strokeWidth={2.5} aria-hidden="true" style={{ color: token.brand.base }} />
            ) : (
              <ShieldCheck size={22} strokeWidth={2.5} aria-hidden="true" style={{ color: token.brand.base }} />
            )}
          </div>

          {/* fontSize: 22 → --text-page (heading — Q8, the page's own <h1>). */}
          <h1 className="m-0 text-page font-semibold text-content-primary">
            {!isOtpStage
              ? isUp
                ? "Create Your Account"
                : "Sign In to Terminal"
              : "Verify Your Identity"}
          </h1>

          {/* fontSize: 11 → --text-body (sentence). The monospace goes with it: this is the
              line that tells the trader what the screen is for. The OTP arm no longer claims
              a code was sent when the dispatch said otherwise — see the header. */}
          <p className="mt-1.5 text-body leading-relaxed text-content-secondary">
            {!isOtpStage ? (
              "Institutional Password + Email OTP Authentication"
            ) : otpDispatched ? (
              <span>
                A 6-digit verification code has been sent to{" "}
                <span className="font-semibold text-brand">{maskEmail(email)}</span>
              </span>
            ) : (
              <span>
                The 6-digit verification code could not be sent to{" "}
                <span className="font-semibold text-brand">{maskEmail(email)}</span>. Use Resend
                code below to request it again.
              </span>
            )}
          </p>
        </div>

        {/* STEP 1: PASSWORD AUTHENTICATION */}
        {!isOtpStage && (
          <form noValidate onSubmit={handlePasswordAuth} className="flex flex-col gap-3.5">
            {/* Email Input. `ds/Field` renders a visible `<label htmlFor>` and links its
                message with `aria-describedby`; `Inp` named the control from its
                placeholder, which is the defect that component exists to replace.
                fontSize: 10 → the primitive's step; see the header on `--text-micro` for a
                sentence being a finding rather than a fix. */}
            <Field
              id="auth-email"
              label="Email Address"
              placeholder="trader@vyomquant.io"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => setEmailTouched(true)}
              invalid={!!emailInlineError}
              error={emailInlineError}
              disabled={isLoading}
              disabledReason="A sign-in attempt is in progress."
              autoComplete="email"
              required
            />

            {/* Password Input */}
            <div className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <label htmlFor="auth-password" className={passwordLabelClass}>
                  Password
                </label>
                {/* The word, not an asterisk — `ds/Field`'s treatment, so the three fields
                    in this form say "required" the same way. */}
                <span className="text-micro uppercase tracking-wide text-content-muted">Required</span>
              </div>
              <div className="relative flex items-center">
                <input
                  id="auth-password"
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••••••"
                  disabled={isLoading}
                  autoComplete={isUp ? "new-password" : "current-password"}
                  required
                  className={passwordInputClass}
                  style={{
                    borderColor: token.line.default,
                    opacity: isLoading ? 0.6 : 1,
                    cursor: isLoading ? "not-allowed" : "text",
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowPw(!showPw)}
                  aria-label={showPw ? "Hide password" : "Show password"}
                  className="absolute right-3 inline-flex items-center rounded-sm p-1 text-content-muted"
                >
                  {showPw ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                </button>
              </div>
              {isUp && <PasswordStrengthMeter strength={passwordStrength} />}
            </div>

            {/* Confirm Password (Sign-Up only) */}
            {isUp && (
              <div className="flex flex-col gap-1.5">
                <div className="flex items-baseline justify-between gap-2">
                  <label htmlFor="auth-confirm-password" className={passwordLabelClass}>
                    Confirm Password
                  </label>
                  <span className="text-micro uppercase tracking-wide text-content-muted">Required</span>
                </div>
                <div className="relative flex items-center">
                  <input
                    id="auth-confirm-password"
                    type={showConfirmPw ? "text" : "password"}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    onBlur={() => setConfirmTouched(true)}
                    placeholder="••••••••••••"
                    disabled={isLoading}
                    autoComplete="new-password"
                    required
                    aria-invalid={confirmPasswordError ? "true" : undefined}
                    aria-describedby={confirmPasswordError ? "auth-confirm-password-error" : undefined}
                    className={passwordInputClass}
                    style={{
                      borderColor: confirmPasswordError ? token.status.error.fg : token.line.default,
                      opacity: isLoading ? 0.6 : 1,
                      cursor: isLoading ? "not-allowed" : "text",
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirmPw(!showConfirmPw)}
                    aria-label={showConfirmPw ? "Hide confirm password" : "Show confirm password"}
                    className="absolute right-3 inline-flex items-center rounded-sm p-1 text-content-muted"
                  >
                    {showConfirmPw ? <EyeOff size={13} aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                  </button>
                </div>
                {confirmPasswordError && (
                  /* fontSize: 10 → --text-body (sentence). Linked from the control it
                     belongs to, which it was not before. */
                  <p id="auth-confirm-password-error" className="text-body text-status-error">
                    {confirmPasswordError}
                  </p>
                )}
                {confirmTouched && confirmPassword && !confirmPasswordError && (
                  /* fontSize: 10 → --text-body (sentence — Q1 catches the finite verb before
                     Q5 could call it a chip, and the order is deliberate). */
                  <p
                    className="inline-flex items-center gap-1 text-body"
                    style={{ color: statusToken("ok").fg }}
                  >
                    <Check size={10} aria-hidden="true" /> Passwords match
                  </p>
                )}
              </div>
            )}

            {/* Terms Checkbox for Sign-Up */}
            {isUp && (
              <div className="mt-1 flex flex-col gap-1">
                {/* fontSize: 11 → --text-body (sentence, and the one on this page a trader is
                    agreeing to). */}
                <label className="flex cursor-pointer items-start gap-2 text-body leading-relaxed text-content-secondary">
                  <input
                    type="checkbox"
                    required
                    checked={agreedToPolicies}
                    onChange={(e) => setAgreedToPolicies(e.target.checked)}
                    className="mt-0.5"
                    style={{ accentColor: token.brand.base }}
                  />
                  <span>
                    I agree to the{" "}
                    <Link to="/legal/terms" className="text-brand underline">
                      Terms
                    </Link>{" "}
                    and{" "}
                    <Link to="/legal/risk" className="text-brand underline">
                      Risk Disclosure
                    </Link>
                    .
                  </span>
                </label>
              </div>
            )}

            {/* fontSize: 11 → the primitive's step. `ds/CommandButton` keeps `type="submit"`
                and takes no `onClick`, so the pointer path and Enter inside a field both run
                the form's one handler once. */}
            <CommandButton
              intent="primary"
              type="submit"
              loading={loadingAction === "password_auth"}
              loadingLabel="Authenticating..."
              disabled={credentialsIncomplete || (isLoading && loadingAction !== "password_auth")}
              disabledReason={passwordSubmitReason}
              className="mt-1 w-full justify-center"
            >
              {isUp ? "CREATE ACCOUNT & SEND OTP →" : "SIGN IN →"}
            </CommandButton>

            {/* Divider & Google OAuth. ONE Google command now, and one divider — see the
                header on the duplicate this page carried. */}
            <div className="mt-4 mb-4 flex items-center gap-3">
              <div className="h-px flex-1" style={{ background: token.line.default }} />
              {/* fontSize: 10 → --text-micro (Q9: connective text has no row in the role
                  set; taken at the label step and recorded). One word, transform kept. */}
              <span className="text-micro uppercase tracking-wide text-content-muted">OR</span>
              <div className="h-px flex-1" style={{ background: token.line.default }} />
            </div>

            {/* fontSize: 12 → the primitive's step. The four-colour inline logo mark goes
                with the duplicate it sat on; the provider is named in words on the control,
                which is the channel that cannot be wrong about which provider it is. */}
            <CommandButton
              intent="secondary"
              icon={Globe}
              data-testid="google-auth-button"
              onClick={handleGoogleAuth}
              loading={loadingAction === "google"}
              loadingLabel="Redirecting to Google..."
              disabled={isLoading && loadingAction !== "google"}
              disabledReason={otherActionInFlight}
              className="w-full justify-center"
            >
              Continue with Google
            </CommandButton>
          </form>
        )}

        {/* STEP 2: 6-DIGIT EMAIL OTP VERIFICATION */}
        {isOtpStage && (
          <form noValidate onSubmit={handleVerifyOtp} className="flex flex-col gap-5">
            <div className="flex flex-col items-center gap-2">
              {/* fontSize: 10 → --text-body (sentence: Q1's finite-verb test catches an
                  imperative label and Q1 is asked first — see the header). Four words, so
                  Requirement 3.3's three-word rule takes the uppercase transform off. */}
              <label htmlFor="otp-digit-0" className="text-body font-medium text-content-secondary">
                Enter 6-Digit Email Code
              </label>

              {/* 6 Digit Input Group */}
              <div className="flex w-full justify-center gap-2" onPaste={handleOtpPaste}>
                {otpDigits.map((digit, i) => (
                  /* fontSize: 20 → --text-page (control text — Q3, read as a floor and not a
                     cap: the sibling screen's six boxes take the same step. Monospace KEPT:
                     these six hold the code itself. */
                  <input
                    key={i}
                    id={`otp-digit-${i}`}
                    ref={(el) => (otpInputRefs.current[i] = el)}
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={1}
                    value={digit}
                    onChange={(e) => handleDigitChange(i, e.target.value)}
                    onKeyDown={(e) => handleDigitKeyDown(i, e)}
                    disabled={isLoading}
                    // Six identical boxes announced as six identical "edit text" fields is a
                    // control with no accessible name in practice. Requirement 20.1.
                    aria-label={`Digit ${i + 1} of 6`}
                    className="rounded-lg border text-center font-mono text-page font-semibold outline-none"
                    style={{
                      background: token.surface.inset,
                      borderColor: digit ? token.brand.base : token.line.default,
                      color: token.content.primary,
                      width: 46,
                      height: 54,
                      transition: `border-color ${token.transition.fast}`,
                      opacity: isLoading ? 0.6 : 1,
                    }}
                  />
                ))}
              </div>
            </div>

            {/* Resend Cooldown Counter & Back to Password. fontSize: 11 was on this
                container, which holds a sentence and two commands: §2.4 runs on rendered
                text, so the container loses the declaration and each child takes its own
                step. */}
            <div className="flex items-center justify-between gap-2 px-1">
              {cooldown > 0 ? (
                /* → --text-body (sentence). The `00:` prefix is carried across unchanged and
                   named in the header: at 60 seconds it reads `00:60`. */
                <span className="text-body text-content-muted">Resend code in {formattedCooldown}</span>
              ) : (
                <CommandButton
                  intent="ghost"
                  onClick={handleResendOtp}
                  loading={loadingAction === "resend_otp"}
                  loadingLabel="Resending..."
                  disabled={isLoading && loadingAction !== "resend_otp"}
                  disabledReason={otherActionInFlight}
                >
                  Resend code
                </CommandButton>
              )}

              <CommandButton
                intent="ghost"
                icon={ArrowLeft}
                onClick={handleBackToPassword}
                disabled={isLoading}
                disabledReason={otherActionInFlight}
              >
                Back
              </CommandButton>
            </div>

            <CommandButton
              intent="primary"
              type="submit"
              loading={loadingAction === "verify_otp"}
              loadingLabel="Verifying OTP..."
              disabled={otpDigits.join("").length !== 6 || (isLoading && loadingAction !== "verify_otp")}
              disabledReason={
                otpDigits.join("").length !== 6
                  ? "Enter all 6 digits of the code from your email."
                  : otherActionInFlight
              }
              className="w-full justify-center"
            >
              VERIFY &amp; CONTINUE →
            </CommandButton>
          </form>
        )}

        {/* Feedback. fontSize: 11 ×2 → the primitive's step. `severity` carries the hue, the
            icon and whether the announcement interrupts; every sentence either band could
            report arrives unchanged. `ds/Alert` has no `success` severity and one is not
            added to a shared primitive from a page commit, so a completed step reports at
            `info` and its sentence says what happened. */}
        {!!error && <Alert severity="error" title={error} className="mt-4" />}

        {!!success && <Alert severity="info" title={success} className="mt-4" />}

        {/* Auth Mode Toggle Footer */}
        {!isOtpStage && (
          /* fontSize: 10 → --text-body (sentence). */
          <p className="mt-6 text-center text-body text-content-muted">
            {isUp ? "Already registered on VyomQuant? " : "No account yet? "}
            <button
              type="button"
              className="cursor-pointer text-brand underline"
              style={{ background: "transparent", border: "none" }}
              onClick={() => {
                setAuthMode(isUp ? "signin" : "signup");
                clearStatus();
              }}
            >
              {isUp ? "Sign in" : "Create one free"}
            </button>
          </p>
        )}

        {/* Legal Links Footer. fontSize: 9 ×4 → --text-micro (Q9: the role set has no row
            for a standing navigation link either; taken at the label step and recorded). */}
        <div className="mt-6 flex justify-center gap-4 border-t border-line-default pt-4">
          <Link to="/legal/terms" className="text-micro text-content-muted underline">
            Terms
          </Link>
          <Link to="/legal/privacy" className="text-micro text-content-muted underline">
            Privacy
          </Link>
          <Link to="/legal/risk" className="text-micro text-content-muted underline">
            Risk Disclosure
          </Link>
          <Link to="/legal/refund" className="text-micro text-content-muted underline">
            Refunds
          </Link>
        </div>
      </div>
    </div>
  );
}
