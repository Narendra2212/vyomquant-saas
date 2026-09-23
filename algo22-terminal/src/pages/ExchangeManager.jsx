/**
 * pages/ExchangeManager.jsx — the venue page: which exchange credentials this account has
 * stored, what each one is doing, and how a new one is added, tested and removed.
 *
 * retail-ui-simplification task 7.10 (commit 16). Requirements 3.1, 3.2, 4.1, 4.2, 4.3, 4.4,
 * 5.1, 5.2, 5.3, 6.2, 19.1, 19.2, 19.3, 19.5, 22.1, 22.2. design.md §2.4 (the nine-question
 * procedure), §2.5, §6 (the zero-versus-unavailable hazard).
 *
 * **52 absolute pixel sizes to 0** and **128 colour literals to 0** — the largest colour diff
 * in the pass, 48% of that budget's remaining 268 and the entry
 * `no-colour-literals.budget.js`'s own header named as a ratchet's resting position. Both
 * budgets are lowered in this commit and the font-size entry is DELETED (Requirement 1.5).
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * NO SECRET IS RENDERED, AND ONE EXPOSURE PATH IS REPORTED RATHER THAN FIXED
 * ═══════════════════════════════════════════════════════════════════════════
 * Nothing on this page echoes a stored credential. `masked_key` is not key material at all
 * (see below). There is no `console` call left in the file, no key in a `title`, and the only
 * place a typed secret is held is the controlled `<input>` the trader is filling in, behind a
 * reveal control they operate themselves.
 *
 * **The one path that can put a typed secret back on screen is the server's own error
 * detail, and it is recorded here rather than patched.** `POST /api/exchanges/test` raises
 * `HTTPException(400, f"Connection failed: {str(e)}")` and `POST /api/exchanges/keys` raises
 * `f"Exchange connection verification failed: {str(e)}"` (`routers/exchange.py:519`, `:161`),
 * where `e` is a ccxt exception raised while connecting with the credential the trader just
 * typed — and ccxt authentication errors routinely quote the request they failed on.
 * `sanitizeErrorMessage` below returns an unmatched `detail` verbatim, so such a string would
 * reach the alert. It is the trader's own key on the trader's own screen rather than a
 * cross-account leak, but it can travel into a screenshot or a support ticket. The fix is a
 * server-side one and Requirement 16.7 puts `backend_app/` outside this spec, so this is a
 * finding, not a silent client-side redaction — and `sanitizeErrorMessage` is carried across
 * byte-identical, because `exchange_manager_phase6.test.jsx` case 7 is what pins its
 * traceback, SQL and raw-exception branches.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE CONNECTION STATE WAS NOT A MEASUREMENT, AND NEITHER WAS THE HEALTH
 * ═══════════════════════════════════════════════════════════════════════════
 * This is the page that answers "is my exchange reachable and does my key work". It answered
 * yes, always, from constants. `production-launch-hardening` found the same shape in
 * `dashboard_aggregation_service` — a hardcoded `"status": "connected"` and `"latency_ms": 35`
 * for every row, with `can_trade` derived from "does a row exist" — and fixed it there, with
 * the consequence that `can_trade` is now `false` for every account until validity is
 * recorded. `routers/exchange.py`'s `list_exchanges` was not part of that fix and still
 * composes every row out of constants:
 *
 *   `"status": "CONNECTED"`      (`:238`) — the same word for every stored key
 *   `"health": "healthy"`        (`:247`) — likewise
 *   `"account_type": "Spot"`     (`:241`) — likewise
 *   `"subscription_tier": "free"`(`:249`) — likewise, whatever the account actually holds
 *   `"credential_configured": true` (`:235`) — true because a row exists
 *
 * A constant is not a reading, so none of the four is rendered as one. That is the rule
 * `pageFields.js`'s `exchangeHealth` note already states for the dashboard's copy of this
 * data ("TWO of those four are constants in the aggregation service … so neither is a
 * measurement"), applied to the page the trader opens to check a venue. A green *Connected*
 * dot with nothing behind it is the same class of defect as `Profile.jsx`'s static
 * *MFA Configured*, and it was worse here: the dot was `#10b981` with the word beside it in
 * the same hue, so the page asserted a working connection in two channels at once.
 *
 * What replaces it is `ds/ExchangeStatus`, which exists for exactly this row and refuses to
 * guess: no `connectionState` renders *Connection not reported* in the neutral group, and no
 * `latencyMs` renders the marker with "No round-trip latency has been measured for this
 * venue … It is not a measurement of zero." **`GET /api/exchanges/` carries no latency field
 * at all**, so there is nothing to pass, and the primitive's own test records why `0 ms` on
 * the strength of a `null` "would be a fabricated reading a trader would act on".
 *
 * Whether a credential works is knowable, and the page already had the control for it:
 * *Test connection* probes the venue now and reports what the exchange answered. That result
 * is a probe, not a stored state, so it stays in the alert where the trader asked for it and
 * is not written onto the card as a standing claim.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THIRTEEN FABRICATED FIGURES, EACH DECLARED
 * ═══════════════════════════════════════════════════════════════════════════
 * All thirteen are now declared in `design/pageFields.js` under an `exchange-manager` page —
 * 16 entries — with their real source paths or their explicit unavailability, and render
 * through `design/reported.js`.
 *
 *   1. **`connectedExchanges.length > 0 ? "Healthy" : "-"`, in green, labelled *Health*.** An
 *      account-wide health grade derived from whether a row exists. This is `can_trade`'s
 *      defect verbatim, on the client, and nothing in any response grades venue health.
 *   2. **`ex.health || "healthy"`, in green, per card.** The server sends the constant
 *      `"healthy"`, so the fallback and the value were the same word: a venue that had
 *      stopped answering read *healthy* and so did a row with the field missing.
 *   3. **`ex.status` beside a hardcoded green dot.** The constant `"CONNECTED"`.
 *   4. **`ex.account_type || "Spot"`.** The server sends the constant `"Spot"`; the fallback
 *      also hid a genuine absence behind it.
 *   5. **`ex.subscription_tier || "free"`.** The exchange service writes `"free"` against
 *      every row whatever the account holds, so a Pro account's card said *Tier: free*.
 *      `pages/Profile.jsx` carried the same claim as `'Free Tier'` and task 7.8 removed it;
 *      `GET /api/billing/entitlements` is the authority and `pages/Billing.jsx` renders it.
 *   6. **`ex.connected_at ? … : "Recently"`.** A timestamp replaced by a vague claim about
 *      when the credential was stored. `pages/SecurityLogs.jsx` had the same substitution
 *      stamped with `Date.now()`; this one is a word, which is harder to spot.
 *   7. **`ex.bot_count || 0`.** A real count, but `|| 0` makes an unread count and a genuine
 *      zero the same glyph — and this figure gates deletion, so it is the one number on the
 *      page a trader acts on.
 *   8. **`supportedExchanges.length` with a literal `+` concatenated on.** *`103+`* claims
 *      more venues than were counted. `total` is the response's own count of the same array.
 *   9. **`"Select from 100+ supported CCXT integrations"`.** A second, hardcoded copy of that
 *      count, in prose, which cannot move when the directory does.
 *  10. **`result?.usdt_balance || 0`, twice.** A **balance** defaulted to zero, on both probe
 *      paths, so a wallet snapshot carrying no USDT entry reported a settled, empty account.
 *  11. **`$` in front of it.** `$0 USDT` denominates one figure in two currencies;
 *      `pages/Billing.jsx` carried the identical defect as `$2499 INR` and task 7.9 removed
 *      it. The server already rounds to two places (`round(total_usdt, 2)`).
 *  12. **`result?.clock_sync || 'Synchronized'`.** A **clock-synchronisation claim invented
 *      when the field is absent** — and the server's own vocabulary is `ok`, `synchronized`
 *      and `assumed_ok`, none of which is the capitalised word the page substituted.
 *  13. **A failed read rendered "No exchanges connected".** Both loaders caught, raised a
 *      toast and left the list at `[]`, so a 500 on `GET /api/exchanges/` told a trader they
 *      had no exchange connected — a claim about their account, from a read that failed.
 *      Same for *No exchanges available* over the venue directory.
 *
 * A genuine `0` is still a reading everywhere: a connection with no bots reads 0, an account
 * with no connections reads 0 in the summary tile with the empty arm beside it, and a zero
 * USDT balance from a real snapshot is quoted as 0 (Requirement 19.1).
 *
 * **`masked_key` is declared with a tooltip rather than a reason, because the label is what
 * is wrong with it.** `routers/exchange.py:236` composes it as
 * `exchange_id[:3].upper() + '•'×24 + exchange_id[-2:].upper()` — a function of the VENUE
 * NAME. No key material reaches the client, which is right, but the value is identical for
 * every account on the same venue and is not part of the stored key, so a label reading *API
 * Key* misstates what the figure is. It is rendered (it is the server's answer, and case 1 of
 * the page's suite reads it) with `pageFields`'s `tooltip` saying what it actually is.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THREE `usePanelState` READS REPLACE TWO LOADING FLAGS AND SIX TOASTS (Req 4.1)
 * ═══════════════════════════════════════════════════════════════════════════
 * What was here: `loadingExchanges`, `loadingSchema`, five pieces of read state
 * (`connectedExchanges`, `supportedExchanges`, `authSchema`, …), four loader functions — one
 * of which, `loadSupportedExchanges`, was never called by anything — and an `AbortController`
 * that was constructed, aborted on unmount and on every re-read, and **passed to no request**,
 * because none of the `api.exchange.*` calls takes a signal. It cancelled nothing.
 * `usePanelState`'s monotonic read id drops a superseded outcome instead of trying to stop it
 * arriving, so `isMountedRef` and the controller both go with it.
 *
 *   supported   → the venue directory, and the *Supported* tile
 *   connections → the connected-exchange cards and the first three summary tiles
 *   schema      → the credential form for the selected venue
 *
 * Same three paths, same absence of parameters. `api-paths.budget.js` is untouched and the
 * request count per action is unchanged: two GETs on mount (as the `Promise.allSettled` did),
 * one `GET /api/exchanges/schema/{id}` per selection, one `GET /api/exchanges/` on Refresh,
 * on save and on reconnect — and **none after a delete**, because the old code filtered the
 * row out locally and that is preserved as a removal layer over the response rather than
 * being turned into a re-read. `pages/Profile.jsx` set that precedent for a locally-applied
 * change: layer it, do not write it into what the server said.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 52 PIXEL SIZES, BY §2.4'S NINE QUESTIONS (Requirement 2.4)
 * ═══════════════════════════════════════════════════════════════════════════
 * Distribution: 12 ×14, 13 ×14, 11 ×10, 24 ×5, 14 ×3, 16 ×3, 10 ×2, 18 ×1. **Fifty of the 52
 * sit at or below 16px and 26 of them at or below 12px**, so nearly every resolution is
 * upward — §2.5's "growth is the normal case". Each is recorded as a comment on its own call
 * site in the form `// fontSize: 11 → --text-body (sentence)`; the ratchet blanks comments,
 * so the audit trail costs nothing and the file measures 0.
 *
 * **Forty of the 52 leave with their call site**, because the step belongs to the primitive:
 * the `<h1>` and its subtitle (24, 14) onto `ds/PageHeader`; four command labels (13, 12, 13,
 * 13) onto `ds/CommandButton`; the four summary tiles and the four per-card tiles, labels and
 * figures alike (12 ×4, 24 ×4, 11 ×4, 12 ×4), onto `ds/Metric`; four region headings (18, 16,
 * 12, 12) onto `ds/Panel`'s `<h2>`; six loading, empty and placeholder blocks (14, 16, 13,
 * 13, 13, 13) onto `ds/Panel`'s own arms; the card's venue name and its status word (16, 12)
 * onto `ds/ExchangeStatus`; the credential label, four control texts and the field
 * description (11, 13 ×4, 10) onto `ds/Field`; and the toast (13) onto `ds/Alert`.
 *
 * **The other 12 resolve in place:**
 *   **6 → `--text-micro`** as a label (Q4) or a decorative initial: the letter-group header
 *   (11), each venue's capability line (11), the selected venue's capability line (11), the
 *   two avatar initials (10, 12 — both `aria-hidden`, since each restates the first letter of
 *   the name beside it), and the *Plan* footer label (11)
 *   **4 → `--text-body`** as a Q1 sentence or a Q7 secondary value: the panel's own
 *   introduction (12), each venue row's name (13), and the two footer values — *Connected*'s
 *   date and its label pair (11 ×2), which is §2.4's one-call-site-two-roles case, so each
 *   container loses its declaration and the label takes `--text-micro` while the value takes
 *   `--text-body`
 *   **1 → `--text-title`** the selected venue's name (14), unchanged in size and now a step
 *   rather than a number
 *   **1 declaration is REMOVED rather than resolved**: the 200px-tall centred *Loading
 *   exchange connections…* row (14) was the whole of the connections loading state, and
 *   `ds/Panel`'s `loading` arm renders a skeleton shaped like the cards it replaces
 *
 * **The five 24px figures resolve UP to `--text-figure` (28px), not down.** 24 is not one of
 * the seven steps; `tokens.css:74` reserves `--text-page` for the page `<h1>` and `:75`
 * reserves `--text-figure` for a tier-1 metric. On a page whose subject is exchange
 * connections, the count of connections IS the headline figure, so all four summary tiles are
 * tier 1 and share the step. §2.2 forbids inventing an eighth step to keep 24.
 *
 * **The three 16px values split by role, which is why the tie is not resolved once.** The
 * empty-state headline and the *Connect a new exchange* heading go to `ds/Panel`'s arms and
 * heading; the card's venue name goes to `ds/ExchangeStatus`, which sets `--text-body` on it
 * — a shrink, and a legal one under §2.5 because it is hierarchy: a card heading inside a
 * panel may not outrank the panel's own `--text-title`, and the card's dominant element is
 * the venue, which keeps `font-mono font-semibold uppercase`.
 *
 * **The one inline `monospace` stays, and it is the only one on the page** (Requirements 3.1,
 * 3.2). `masked_key` is read character by character — a run of 24 bullets between two short
 * letter groups, which a proportional face makes ambiguous — so it keeps the treatment,
 * through `font-mono` on the VALUE rather than an inline `fontFamily`. That is also why its
 * tile is the one on the card not built from `ds/Metric`: `ds/Metric` applies `font-mono` to
 * its numeric formats only, and putting the class on its wrapper would put the label in
 * monospace too, which Requirement 3.1's second clause forbids. `ds/Tooltip` carries the
 * declared `tooltip` beside it. The toast's `fontFamily: "system-ui"` was not monospace and
 * was not a token either; it leaves with the toast.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 128 COLOUR LITERALS (Requirements 5.1, 5.2, 5.3)
 * ═══════════════════════════════════════════════════════════════════════════
 * Twenty-four distinct values, one every seven lines, and the whole visual layer was
 * hand-built: there was no `token` import, no utility class and no primitive in the file.
 * Mapped through `tokens.css` by the group the colour MARKS rather than by nearest hue
 * (Requirement 5.3):
 *
 *   #64748b ×19  labels and secondary text → `content-secondary` (#8B95A5, 6.2:1 on panel).
 *                NOT `content-muted`: `tokens.css:30` marks that one NON-TEXT ONLY at 3.2:1,
 *                and every one of these 19 was body or label text.
 *   #334155 ×17  card, input and divider borders → `line-default`
 *   #f1f5f9 ×15  primary text → `content-primary`
 *   #1e293b ×13  card and input surfaces → `surface-panel` / `surface-inset`, mostly through
 *                `ds/Panel` and `ds/Field`, which own both
 *   #94a3b8 ×12  secondary text → `content-secondary`
 *   #3b82f6 ×9   the selection and link hue → `brand` (#00D4FF). Seven leave with the
 *   #3b82f620 ×5 elements they painted; the two that stay are the selected venue row's wash
 *   #3b82f650 ×1 and text, which are `brand-wash` and `text-brand`
 *   #3b82f610 ×1
 *   #10b981 ×9   **the fabricated green.** Seven of the twelve painted a *Connected* dot, a
 *   #10b98120 ×3 *Connected* word, a *Healthy* grade or a *healthy* per-card value — none of
 *                which was a measurement — so they are REMOVED WITH THE CLAIM rather than
 *                retokened to `--color-status-connected`. Retokening them would have kept the
 *                assertion and changed its shade. The remaining ones are the *Save keys*
 *                button and the success alert, which are `ds/CommandButton`'s primary intent
 *                and `ds/Alert`'s `info` severity (Requirement 1.4: the hue comes from the
 *                intent, and neither primitive takes a colour)
 *   #0f172a ×7   the page canvas and the inset wells → the page background declaration is
 *                REMOVED (the shell owns the canvas, which is why no migrated page sets one)
 *                and the wells are `surface-inset`
 *   #ef4444 ×4   the destructive control, the required-field asterisk and the error toast →
 *   #ef444450 ×1 `ds/CommandButton`'s `destructive` intent, `ds/Field`'s `required` (which
 *   #ef444410 ×1 renders the WORD instead of an asterisk, so the hue has nothing to carry)
 *   #ef444420 ×1 and `ds/Alert`'s `error` severity. `--color-status-error` is #EF5350
 *   #475569 ×3   secondary control borders → `line-strong`
 *   #8b5cf6 ×1   **violet, and there is no violet in the palette.** It tinted the *Health*
 *   #8b5cf620 ×1 tile's shield glyph — the tile that was a fabrication — so both leave with
 *                it. Nothing is substituted: `semantic.js` has no group that means "health",
 *                which is the Requirement 5.3 question, and the honest answer on this page is
 *                that health is not reported
 *   #f59e0b ×1   the *Supported* tile's glyph. **It is `--color-status-warning` exactly** —
 *   #f59e0b20 ×1 the same six digits the token layer declares — but a count of available
 *                venues is not a warning, so it is not retokened to the group whose value it
 *                happens to share. The glyph is `content-secondary`, which is what a
 *                decorative icon beside a labelled figure is (Requirement 1.5)
 *   #374151 ×1   the disabled *Save keys* background → `ds/CommandButton`'s disabled
 *                treatment, which also renders the `disabledReason` the control never had
 *   #ffffff ×1   the *Save keys* label → `content-primary` (#F0F2F5). Pure white is not in
 *                the palette; the fourth page in this spec to carry that substitution
 *   rgba() ×1    the toast's `0 10px 40px rgba(0,0,0,0.4)` → `ds/Alert`, which has no shadow
 *                at all: it is an inline strip rather than a floating layer, so the declared
 *                elevation it needs is none
 *
 * **Unusually, all fifteen of the hand-mixed alphas WERE counted.** The eight-digit form this
 * page uses — `#3b82f620`, `#10b98120`, `#ef444450` — is a hex literal and `HEX_LITERAL`
 * matches it, unlike the `` `${token.brand.base}20` `` form `pages/Billing.jsx` carried
 * fourteen of, which has no `#` and so was invisible to the guard. That is the whole
 * difference between a page written before the token layer and one written against it badly.
 *
 * **Thirty-nine off-scale constructs neither guard can see go with them**, recorded because a
 * scan will not find them for the next reader: twenty-eight radii are TOKENS SPELLED AS
 * NUMBERS (`borderRadius: 8` ×18 is `--radius-lg`, `borderRadius: 12` ×9 is `--radius-xl`,
 * `borderRadius: 6` is `--radius-md`), one is off the declared scale entirely
 * (`borderRadius: 10`), `transition: "all 0.2s"` ×5 is not `--transition-base`'s
 * `180ms cubic-bezier(…)`, `backdropFilter: "blur(10px)"` is an effect the token layer does
 * not declare at all, `opacity: 0.3` ×2 dims a glyph to a contrast the palette has no name
 * for, and `letterSpacing: 1` ×2 is off the scale as well. Every one leaves with the element
 * it was on — the radii onto `ds/Panel`, `ds/Field` and `ds/CommandButton`, which own them.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * REQUIREMENT 4.4 FINDINGS — RECORDED, NOT RESOLVED
 * ═══════════════════════════════════════════════════════════════════════════
 * Nothing is added to `ds/`, to `usePanelState` or to `src/design/` beyond this page's own
 * `pageFields.js` entries. Six gaps were reached.
 *
 *   1. **`ds/Field` has no trailing-adornment slot**, so the show/hide control for a password
 *      field cannot live inside the control the way it did. Its `unit` slot is
 *      `pointer-events-none` by declaration. The reveal control is a `ds/CommandButton`
 *      BESIDE the field instead, in a row that bottom-aligns the two — which keeps the
 *      affordance, keeps it in the tab order, and upgrades it from an icon with no name to
 *      one carrying `Show {field label}` / `Hide {field label}`.
 *   2. **`ds/Field` has no boolean control.** A schema field of type `boolean` stays a
 *      page-local `<label>` + `<input type="checkbox">`, which is the same call task 7.2 made
 *      for `RiskSettings`'s `Toggle` and for the same reason: `ds/Switch` invented for one
 *      page is how the second design system starts. Passing `type="checkbox"` to `ds/Field`
 *      is not the alternative — it would put a controlled `value` on a checkbox.
 *   3. **`semantic.js`'s vocabulary has no entry for a venue's health or its clock sync.**
 *      `healthy` resolves to the live group and `connected` to its own, which is why neither
 *      is rendered here: the groups exist, the MEASUREMENTS do not, and Requirement 5.3 puts
 *      "which token means this state" on `semantic.js` and nowhere else. `clock_sync`'s three
 *      server values (`ok`, `synchronized`, `assumed_ok`) are not in the table either, so the
 *      probe result is quoted as the server's own word with no hue at all.
 *   4. **The native `confirm()` on disconnect stays.** `ds/ConfirmDialog` and
 *      `ds/CommandButton`'s `confirm` prop are sitting right there and this is the most
 *      valuable thing on the page this commit is not taking: swapping the dialog changes what
 *      a destructive action DOES on the way to doing it, and task 7.3 made the same call for
 *      `TwoFA.jsx`'s *Reset Authenticator* so that `no-native-dialogs.budget.js` is not
 *      touched from a typography commit. The confirmation itself is deliberate friction on an
 *      irreversible action (Requirement 16.5) and is carried across with its wording intact,
 *      as is the bot-count pre-check that refuses the delete before asking.
 *   5. **Read failures and write failures translate differently, on purpose.** Every read
 *      goes through `ds/Panel`'s `error` arm and therefore through `design/errorCopy.js`'s
 *      `translateError`. Every WRITE keeps this page's own `sanitizeErrorMessage`, which is
 *      what its suite pins and what carries the exchange-specific 429 and
 *      credential-verification sentences `errorCopy.js` has no vocabulary for. Unifying them
 *      is Requirement 11's shape and Requirement 11 is scoped to Marketplace (task 6.1).
 *   6. **The vault calls itself AES-256 and is Fernet.** The removed copy claimed *"Keys are
 *      encrypted with AES-256"*; `backend/api_key_vault.py` uses `MultiFernet`, which is
 *      AES-128-CBC with an HMAC-SHA256 tag derived from the same 32-byte key. The claim is
 *      wrong in the backend's own docblock, so the replacement sentence states what is true
 *      and checkable — that the keys are encrypted before they are stored — in the words the
 *      server's own success response uses. Correcting the docblock is out of scope.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT MUST NOT BE LOST, AND WHAT PROVES IT WAS NOT
 * ═══════════════════════════════════════════════════════════════════════════
 * Every one of the five write paths is carried across with its request unchanged:
 * `testConnection({exchange_id, api_key, secret_key, password, uid})`,
 * `saveKeys({… , label})`, `delete(exchangeId)`, `testStoredConnection({exchange_id})` and
 * `reconnect(exchangeId)` — same bodies, same argument shapes, same order, same guards
 * against a double submit. Add, test, reconnect and delete all still work — there is no
 * enable/disable toggle on this page and none is added; *Reconnect* is what re-establishes a
 * stored connection. The delete still refuses while bots are running, with the same sentence
 * and the same pre-check ahead of the confirmation, and the venue rows keep their
 * `role="button"`, `aria-pressed`, tab stop and Enter/Space activation (including the
 * `preventDefault` on Space, which stops the list scrolling out from under the row just
 * picked). Two behaviours DID change, each because the old one lost information:
 *
 *   * **The alert no longer disappears after four seconds.** A `setTimeout(…, 4000)` cleared
 *     every message, including the ones reporting a failed credential test. It is a
 *     `ds/Alert` with an explicit *Dismiss* now, which is `pages/Billing.jsx`'s form.
 *   * **A failed read is a panel state rather than a toast over an empty list.** That is
 *     item 13 above and Property 9's whole subject.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  CheckCircle,
  Database,
  Eye,
  EyeOff,
  Globe,
  Lock,
  RefreshCw,
  Shield,
  Trash2,
  Wifi,
  Zap,
} from 'lucide-react';

import { Alert } from '../components/ds/Alert';
import { CommandButton } from '../components/ds/CommandButton';
import { ExchangeStatus } from '../components/ds/ExchangeStatus';
import { Field } from '../components/ds/Field';
import { Metric, NotAvailableMarker } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { Tooltip } from '../components/ds/Tooltip';
import { api } from '../api';
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from '../design/pageFields';
import { available, fromNullable, unavailable } from '../design/reported';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';

// ─────────────────────────────────────────────────────────────────────────────
// The declaration, indexed once
// ─────────────────────────────────────────────────────────────────────────────

/**
 * This page's `pageFields.js` entries by `field`, so a call site names the figure and gets
 * its path and its reason together. The one index; see `Billing.jsx` and `Profile.jsx`.
 */
const FIELD = Object.freeze(
  Object.fromEntries(PAGE_FIELDS_BY_PAGE[PAGES.EXCHANGE_MANAGER].map((f) => [f.field, f])),
);

/** A string with visible content. The same test `ds/devAssert.hasText` applies. */
const hasText = (value) => typeof value === 'string' && value.trim() !== '';

/** A dotted path off a response body. `read(body, 'total')`. */
const read = (body, path) =>
  path.split('.').reduce((node, key) => (node == null ? undefined : node[key]), body);

/**
 * One declared field, as a `Reported<T>`, from the response that carries it.
 *
 * The entry's own `reason` travels with the absence, which is the half Requirement 19.3 calls
 * load-bearing. A `DERIVED` or `UNAVAILABLE` entry has no path by declaration, so it never
 * reaches `read` at all — which is what stops a future `|| 0` being added to a balance or a
 * `|| "healthy"` to a grade the backend does not measure.
 */
const reported = (body, field) =>
  field.verdict === VERDICT.AVAILABLE
    ? fromNullable(read(body, field.path), field.reason)
    : unavailable(field.reason);

/** The leaf of a `[].column` path, which is what one list-row element carries. */
const rowKey = (field) => field.path.split('[].')[1];

/** One declared per-row field of a list response. */
const rowField = (row, field) =>
  field.verdict === VERDICT.AVAILABLE
    ? fromNullable(row?.[rowKey(field)], field.reason)
    : unavailable(field.reason);

/** The em-dash marker, carrying the entry's reason. One marker in the app. */
const Marker = ({ field, reason }) => (
  <NotAvailableMarker label={field.label} reason={reason ?? field.reason} />
);

// ─────────────────────────────────────────────────────────────────────────────
// Failure copy for the five WRITE paths — carried across byte-identical
// ─────────────────────────────────────────────────────────────────────────────

/*
 * Unchanged from before the migration, deliberately. `exchange_manager_phase6.test.jsx`
 * case 7 is what pins the traceback / SQL / raw-exception branch, and the 429 and
 * credential-verification sentences are exchange-specific copy `design/errorCopy.js` has no
 * vocabulary for (Requirement 4.4 finding 5). Its remaining `return detail` is the exposure
 * path this file's header reports.
 */
function sanitizeErrorMessage(err, fallback = "An error occurred. Please try again.") {
  if (!err) return fallback;
  if (err.name === "AbortError" || err.name === "CanceledError") return null;

  const status = err?.response?.status;
  if (status === 401) return "Session expired. Please log in again.";
  if (status === 403) return "Access denied. Insufficient permissions.";
  if (status === 429) return "Exchange rate limit reached. Please try again shortly.";

  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") {
    // Check for internal stack traces, DB errors, or raw exceptions
    if (detail.includes("Traceback") || detail.includes("SELECT") || detail.includes("TypeError") || detail.includes("500 Internal")) {
      return "Exchange service temporarily unavailable. Please try again.";
    }
    if (detail.toLowerCase().includes("cannot delete exchange")) {
      return detail;
    }
    if (detail.toLowerCase().includes("verification failed") || detail.toLowerCase().includes("authentication failed")) {
      return "Exchange credentials could not be verified. Please check API Key and Secret.";
    }
    return detail;
  }
  return fallback;
}

// ─────────────────────────────────────────────────────────────────────────────
// Presentation helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * A date the server sent, through the same `toLocaleDateString` call this page has always
 * made — and `null`, never the word "Recently", for anything unparseable.
 */
const formatDate = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString();
};

/**
 * A venue's capability line, from the directory's own boolean flags.
 *
 * Carried across unchanged in content and in separator. These are a CONTROL'S metadata — what
 * the row offers — rather than a reported figure, which is the distinction `Billing.jsx`
 * recorded for its 21-entry currency option set, so they get no `pageFields` entry.
 */
const capabilityLine = (venue) =>
  [
    venue?.spot_support ? 'Spot' : null,
    venue?.futures_support ? 'Futures' : null,
    venue?.sandbox_support ? 'Sandbox' : null,
  ]
    .filter(Boolean)
    .join(' \u2022 ');

/** A venue's display label, falling back to its ccxt id so no row is unreadable. */
const venueLabel = (venue) =>
  hasText(venue?.display_name) ? venue.display_name : String(venue?.id ?? '');

// ─────────────────────────────────────────────────────────────────────────────
// One stored connection
// ─────────────────────────────────────────────────────────────────────────────

/**
 * One card in the connected-exchange grid.
 *
 * Every figure on it goes through `rowField`, so a column the response did not carry renders
 * the marker with its declared reason rather than a constant.
 */
function ConnectionCard({ row, busy, onReconnect, onTest, onDelete }) {
  const venue = rowField(row, FIELD.connectionName);
  const maskedKey = rowField(row, FIELD.maskedApiKey);
  const accountType = rowField(row, FIELD.accountType);
  const botCount = rowField(row, FIELD.connectionBotCount);
  const connectedAt = rowField(row, FIELD.connectedAt);
  const connectedAtLabel = connectedAt.available
    ? fromNullable(formatDate(connectedAt.value), FIELD.connectedAt.reason)
    : connectedAt;

  const name = venue.available ? String(venue.value) : String(row?.exchange_id ?? '');

  return (
    <div className="flex min-w-0 flex-col gap-4 rounded-lg border border-line-default bg-surface-raised p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        {/* fontSize: 16 → --text-body and fontSize: 12 → the status word, both set by
            `ds/ExchangeStatus` rather than by this page. NEITHER `connectionState` NOR
            `latencyMs` is passed: the server's `status` is the constant "CONNECTED" and this
            response carries no latency at all, so the primitive renders "Connection not
            reported" and the latency marker with its reason — which is the truth about a
            stored credential nobody has probed. */}
        {venue.available ? (
          <ExchangeStatus exchange={name} className="min-w-0 flex-1" />
        ) : (
          <Marker field={FIELD.connectionName} reason={venue.reason} />
        )}

        <div className="flex shrink-0 items-center gap-2">
          {/* fontSize: 12 → --text-body, set by `ds/CommandButton`. */}
          <CommandButton
            intent="secondary"
            icon={Zap}
            loading={busy}
            loadingLabel="Reconnecting"
            onClick={() => onReconnect(row?.exchange_id)}
            title="Reconnect"
            aria-label={`Reconnect ${name}`}
          >
            Reconnect
          </CommandButton>
          <CommandButton
            intent="ghost"
            icon={RefreshCw}
            loading={busy}
            loadingLabel="Testing"
            onClick={() => onTest(row?.exchange_id)}
            title="Test Connection"
            aria-label={`Test connection for ${name}`}
          />
          <CommandButton
            intent="destructive"
            icon={Trash2}
            loading={busy}
            loadingLabel="Disconnecting"
            onClick={() => onDelete(row?.exchange_id, name)}
            title="Disconnect"
            aria-label={`Disconnect ${name}`}
          />
        </div>
      </div>

      {/* fontSize: 11 ×4 (the labels) and fontSize: 12 ×4 (the values) → `ds/Metric`, which
          owns both steps. The four tiles keep their order and their headings. */}
      <div className="grid grid-cols-2 gap-3">
        {/* The one tile built by hand rather than by `ds/Metric`, and the reason is
            Requirement 3.1: a masked token is read character by character, so it keeps the
            monospace face — and `ds/Metric` applies `font-mono` to its numeric formats only,
            while putting the class on its wrapper would put the LABEL in monospace too,
            which Requirement 3.1's second clause forbids. `ds/Tooltip` carries the declared
            `tooltip` copy, which is here because the label alone misstates what this is. */}
        <div className="flex min-w-0 flex-col gap-1">
          <Tooltip content={FIELD.maskedApiKey.tooltip}>
            <span className="text-micro uppercase tracking-wide text-content-secondary">
              {FIELD.maskedApiKey.label}
            </span>
          </Tooltip>
          {maskedKey.available ? (
            <span className="truncate font-mono text-body text-content-primary">
              {maskedKey.value}
            </span>
          ) : (
            <Marker field={FIELD.maskedApiKey} reason={maskedKey.reason} />
          )}
        </div>
        <Metric label={FIELD.accountType.label} value={accountType} tier={3} format="raw" />
        <Metric label={FIELD.connectionBotCount.label} value={botCount} tier={3} format="integer" />
        {/* The per-card grade the exchange service writes as the constant "healthy", over a
            page-side `|| "healthy"`. Declared unavailable, so the tile keeps its heading and
            loses only the green word that was never a reading. */}
        <Metric
          label={FIELD.connectionHealth.label}
          value={null}
          tier={3}
          format="raw"
          unavailable
          unavailableReason={FIELD.connectionHealth.reason}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-subtle pt-3">
        {/* fontSize: 11 → §2.4's one-call-site-two-roles case: the container held an eyebrow
            word and a date, so it loses its declaration and the label takes --text-micro
            (Q4) while the value takes --text-body (Q7, secondary). */}
        <span className="inline-flex items-center gap-1.5 text-micro uppercase tracking-wide text-content-secondary">
          <Zap size={12} aria-hidden="true" />
          {`${FIELD.connectedAt.label}:`}
          {connectedAtLabel.available ? (
            <span className="text-body normal-case tracking-normal text-content-secondary">
              {connectedAtLabel.value}
            </span>
          ) : (
            <Marker field={FIELD.connectedAt} reason={connectedAtLabel.reason} />
          )}
        </span>
        {/* fontSize: 11 → --text-micro (label — Q4). The `Tier: free` the exchange service
            writes against every row whatever the account holds. */}
        <span className="inline-flex items-center gap-1.5 text-micro uppercase tracking-wide text-content-secondary">
          {`${FIELD.subscriptionTier.label}:`}
          <Marker field={FIELD.subscriptionTier} />
        </span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// One credential field, from the venue's own auth schema
// ─────────────────────────────────────────────────────────────────────────────

/**
 * One control declared by `GET /api/exchanges/schema/{exchange_id}`.
 *
 * The schema's `label`, `placeholder`, `description`, `required`, `options` and `default` are
 * a control's metadata and not reported figures, so none of them is declared in
 * `pageFields.js` — `Billing.jsx`'s currency option set is the precedent.
 */
function CredentialField({ field, value, revealed, onChange, onToggleReveal }) {
  const name = field?.name || field?.field_id;
  const label = hasText(field?.label) ? field.label : String(name ?? '');
  const placeholder = hasText(field?.placeholder) ? field.placeholder : undefined;
  // fontSize: 10 → `ds/Field`'s `hint`, which also associates the sentence with the input
  // through `aria-describedby`. Before the migration it was an unassociated <div>.
  const hint = hasText(field?.description) ? field.description : undefined;
  const required = field?.required === true;

  if (!hasText(String(name ?? ''))) return null;

  // fontSize: 13 → `ds/Field`'s control text, and the red `*` becomes the WORD "Required",
  // which is what `ds/Field` renders and why #ef4444 has nothing left to carry here.
  if (field?.type === 'password') {
    return (
      <div className="flex items-end gap-2">
        <Field
          className="min-w-0 flex-1"
          label={label}
          type={revealed ? 'text' : 'password'}
          placeholder={placeholder}
          hint={hint}
          required={required}
          value={value ?? ''}
          onChange={(event) => onChange(name, event.target.value)}
        />
        {/* Requirement 4.4 finding 1: `ds/Field` has no trailing-adornment slot, so the
            reveal control sits beside the field. It gains the name it never had. */}
        <CommandButton
          intent="ghost"
          icon={revealed ? EyeOff : Eye}
          onClick={() => onToggleReveal(name)}
          aria-label={`${revealed ? 'Hide' : 'Show'} ${label}`}
        />
      </div>
    );
  }

  if (field?.type === 'select') {
    return (
      <Field
        label={label}
        hint={hint}
        required={required}
        value={value ?? ''}
        onChange={(event) => onChange(name, event.target.value)}
        options={(Array.isArray(field?.options) ? field.options : []).map((option) => ({
          value: option?.value,
          label: hasText(option?.label) ? option.label : String(option?.value ?? ''),
        }))}
      />
    );
  }

  if (field?.type === 'boolean') {
    // Requirement 4.4 finding 2: `ds/` has no boolean control, so this stays page-local —
    // task 7.2's call for `RiskSettings`'s `Toggle`. fontSize: 13 → --text-body (control
    // text — Q3); the label wraps the input, so the association survives the move.
    return (
      <label className="flex cursor-pointer items-center gap-2.5 text-body text-content-primary">
        <input
          type="checkbox"
          className="h-4 w-4 accent-brand"
          checked={value === true}
          onChange={(event) => onChange(name, event.target.checked)}
        />
        <span>{hasText(field?.placeholder) ? field.placeholder : label}</span>
      </label>
    );
  }

  return (
    <Field
      label={label}
      type={hasText(field?.type) ? field.type : 'text'}
      placeholder={placeholder}
      hint={hint}
      required={required}
      value={value ?? ''}
      onChange={(event) => onChange(name, event.target.value)}
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────

export default function ExchangeManager() {
  const [selectedExchange, setSelectedExchange] = useState(null);
  const [credentialValues, setCredentialValues] = useState({});
  const [revealed, setRevealed] = useState({});
  const [isTesting, setIsTesting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [processingById, setProcessingById] = useState({});
  /** One `{ severity, message }`, where `toast` was a `{ type, msg }` with a 4s timer. */
  const [notice, setNotice] = useState(null);
  /*
    Rows this page has deleted, layered over the response rather than written into it.

    Before the migration a successful delete did `setConnectedExchanges(rows =>
    rows.filter(…))` and issued no re-read. `usePanelState` owns the payload, so the local
    change is applied on top of it — `pages/Profile.jsx`'s precedent for a locally-saved
    field — which keeps the request count after a delete at exactly zero. Cleared by Refresh,
    which is the action that asks the server again.
  */
  const [removedIds, setRemovedIds] = useState([]);

  const selectedExchangeId = selectedExchange?.id ?? null;
  const venueListRef = useRef(null);

  // ── The three reads (Requirement 4.1) ─────────────────────────────────────
  //
  // Same three paths, no parameter added or changed. `deps: []` on the first two, so each
  // fires once on mount exactly as the `Promise.allSettled` did; the schema read's question
  // IS the selected venue, so it carries it as a dep and is `idle` until one is picked.
  const readSupported = useCallback(() => api.exchange.getSupported(), []);
  const readConnections = useCallback(() => api.exchange.list(), []);
  const readSchema = useCallback(
    () => api.exchange.getAuthSchema(selectedExchangeId),
    [selectedExchangeId],
  );

  const supported = usePanelState(readSupported, { deps: [] });
  const connections = usePanelState(readConnections, { deps: [] });
  const schema = usePanelState(readSchema, {
    deps: [selectedExchangeId],
    enabled: selectedExchangeId !== null,
  });

  // Stable by `usePanelState`'s second inversion.
  const refetchConnections = connections.refetch;

  const venues = useMemo(
    () => (Array.isArray(supported.data?.exchanges) ? supported.data.exchanges : []),
    [supported.data],
  );
  const schemaFields = useMemo(
    () => (Array.isArray(schema.data?.fields) ? schema.data.fields : []),
    [schema.data],
  );
  const connectionRows = useMemo(() => {
    const rows = Array.isArray(connections.data) ? connections.data : [];
    return rows.filter((row) => !removedIds.includes(row?.exchange_id));
  }, [connections.data, removedIds]);

  /*
    Seed the form from the schema's own defaults.

    This is `loadAuthSchema`'s tail, moved out of the loader and onto the payload: the same
    `field.default ?? ""` per field, in the same order, which is what makes an untouched
    optional field submit as `''` rather than as `undefined`. A deselection empties it,
    because `schemaFields` is `[]` whenever there is no schema on screen.
  */
  useEffect(() => {
    const initialValues = {};
    schemaFields.forEach((field) => {
      const fieldName = field?.name || field?.field_id;
      if (fieldName) {
        initialValues[fieldName] = field?.default ?? '';
      }
    });
    setCredentialValues(initialValues);
    setRevealed({});
  }, [schemaFields]);

  // ── The figures (Requirement 4.2) ─────────────────────────────────────────

  /*
    Whether the connections read produced an answer at all.

    It has to be read off the STATE and not off `data`: `usePanelState` sets `data` to `null`
    for an empty collection as well as for a failure, so an account with no stored credential
    and a 500 on the read are the same payload and a different fact. This is the distinction
    item 13 collapsed — and the tiles below depend on it, because "0 connected" is a reading
    and "we could not read your connections" is not a zero.
  */
  const connectionsAnswered = [
    PANEL_STATES.READY,
    PANEL_STATES.REFRESHING,
    PANEL_STATES.EMPTY,
  ].includes(connections.state);

  const connectedCount = connectionsAnswered
    ? available(connectionRows.length)
    : unavailable(FIELD.connectedExchangeCount.reason);

  /*
    The sum of `bot_count` across connections.

    A row whose count is missing makes the TOTAL unknown, not smaller: treating an absent
    count as 0 inside the sum would publish a total that reads as complete. So the derivation
    is all-or-nothing, and an account with no connections sums to a genuine 0.
  */
  const activeBotCount = useMemo(() => {
    if (!connectionsAnswered) return unavailable(FIELD.activeBotCount.reason);
    const counts = connectionRows.map((row) => row?.bot_count);
    return counts.every((count) => typeof count === 'number' && Number.isFinite(count))
      ? available(counts.reduce((total, count) => total + count, 0))
      : unavailable(FIELD.activeBotCount.reason);
  }, [connectionsAnswered, connectionRows]);

  const supportedCount = reported(supported.data, FIELD.supportedExchangeCount);

  const alphabeticalVenues = useMemo(() => {
    const grouped = {};
    venues.forEach((venue) => {
      const label = venueLabel(venue);
      if (!hasText(label)) return;
      const firstLetter = label[0].toUpperCase();
      if (!grouped[firstLetter]) grouped[firstLetter] = [];
      grouped[firstLetter].push(venue);
    });
    return grouped;
  }, [venues]);

  // ── The panel states ──────────────────────────────────────────────────────

  /*
    `GET /api/exchanges/supported` answers `{exchanges, total}`, and an object with two keys
    is not an empty payload whatever the array inside it holds — so `usePanelState` reports
    `ready` for a directory of zero venues and the `empty` arm would be unreachable. Mapped
    here rather than by widening the hook's `COLLECTION_KEYS`, which is a shared module a page
    commit may not change (Requirement 17).
  */
  const supportedState =
    supported.state === PANEL_STATES.READY && venues.length === 0
      ? PANEL_STATES.EMPTY
      : supported.state;

  /* No venue picked is not a failed read; it is the form having nothing to ask for yet. */
  const credentialsState = selectedExchangeId === null ? PANEL_STATES.EMPTY : schema.state;

  const focusVenueList = useCallback(() => {
    const first = venueListRef.current?.querySelector('[role="button"]');
    if (first && typeof first.focus === 'function') first.focus();
  }, []);

  const connectionsBusy =
    connections.state === PANEL_STATES.LOADING || connections.state === PANEL_STATES.REFRESHING;

  // ── The five write paths, carried across ──────────────────────────────────

  const handleRefresh = useCallback(() => {
    setNotice(null);
    setRemovedIds([]);
    refetchConnections();
  }, [refetchConnections]);

  const handleCredentialChange = useCallback((fieldName, value) => {
    setCredentialValues((prev) => ({ ...prev, [fieldName]: value }));
  }, []);

  const toggleReveal = useCallback((fieldName) => {
    setRevealed((prev) => ({ ...prev, [fieldName]: !prev[fieldName] }));
  }, []);

  /**
   * What a probe actually reported, as one sentence.
   *
   * `usdt_balance` and `clock_sync` are declared figures, so each is `fromNullable`'d against
   * its entry and an absence contributes the entry's REASON rather than a substituted number.
   * That is items 10, 11 and 12: no `|| 0`, no `$` in front of a USDT figure, and no invented
   * word for a clock the exchange did not answer about.
   */
  const describeProbe = (result, opening, balanceField, clockField) => {
    const balance = reported(result, balanceField);
    const parts = [opening];
    parts.push(
      balance.available
        ? `${balanceField.label}: ${balance.value} USDT.`
        : balance.reason,
    );
    if (clockField) {
      const clock = reported(result, clockField);
      parts.push(
        clock.available ? `${clockField.label}: ${clock.value}.` : clock.reason,
      );
    }
    return parts.join(' ');
  };

  const handleTestConnection = async () => {
    if (!selectedExchange || !credentialValues.api_key || !credentialValues.secret_key || isTesting) return;
    setIsTesting(true);
    setNotice(null);
    try {
      const result = await api.exchange.testConnection({
        exchange_id: selectedExchange.id,
        api_key: credentialValues.api_key,
        secret_key: credentialValues.secret_key,
        password: credentialValues.password,
        uid: credentialValues.uid,
      });
      setNotice({
        severity: 'info',
        message: describeProbe(
          result,
          'Connection verified.',
          FIELD.probeUsdtBalance,
          FIELD.probeClockSync,
        ),
      });
    } catch (err) {
      const msg = sanitizeErrorMessage(err, "Connection test failed. Please check your API credentials.");
      if (msg) setNotice({ severity: 'error', message: msg });
    } finally {
      setIsTesting(false);
    }
  };

  const handleSaveKey = async () => {
    if (!selectedExchange || !credentialValues.api_key || !credentialValues.secret_key || isSaving) return;
    setIsSaving(true);
    setNotice(null);
    try {
      await api.exchange.saveKeys({
        exchange_id: selectedExchange.id,
        api_key: credentialValues.api_key,
        secret_key: credentialValues.secret_key,
        password: credentialValues.password,
        uid: credentialValues.uid,
        label: credentialValues.label,
      });
      setNotice({ severity: 'info', message: 'Exchange keys encrypted and stored securely.' });

      setCredentialValues({});
      setRevealed({});
      setSelectedExchange(null);

      refetchConnections();
    } catch (err) {
      const msg = sanitizeErrorMessage(err, "Failed to save exchange keys.");
      if (msg) setNotice({ severity: 'error', message: msg });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteSaved = async (exchangeId, exchangeName) => {
    if (processingById[exchangeId]) return;

    const exchange = connectionRows.find((e) => e.exchange_id === exchangeId);
    if (exchange && exchange.bot_count > 0) {
      setNotice({
        severity: 'error',
        message: `Cannot delete: ${exchange.bot_count} active bot(s) running. Stop bots first.`,
      });
      return;
    }

    /*
      Requirement 16.5 and 4.4 finding 4: the confirmation is deliberate friction on an
      irreversible action and is carried across with its wording intact. It is still the
      native dialog, which is what keeps `no-native-dialogs.budget.js` out of this commit.
    */
    if (!confirm(`Are you sure you want to disconnect ${String(exchangeName ?? exchangeId).toUpperCase()}? This action cannot be undone.`)) {
      return;
    }

    setProcessingById((p) => ({ ...p, [exchangeId]: true }));
    try {
      await api.exchange.delete(exchangeId);
      setRemovedIds((ids) => (ids.includes(exchangeId) ? ids : [...ids, exchangeId]));
      setNotice({ severity: 'info', message: 'Exchange connection removed.' });
    } catch (err) {
      const msg = sanitizeErrorMessage(err, "Failed to delete exchange connection.");
      if (msg) setNotice({ severity: 'error', message: msg });
    } finally {
      setProcessingById((p) => ({ ...p, [exchangeId]: false }));
    }
  };

  const handleTestStoredConnection = async (exchangeId) => {
    if (processingById[exchangeId]) return;
    setProcessingById((p) => ({ ...p, [exchangeId]: true }));
    setNotice(null);
    try {
      const result = await api.exchange.testStoredConnection({ exchange_id: exchangeId });
      setNotice({
        severity: 'info',
        message: describeProbe(
          result,
          `${String(exchangeId ?? '').toUpperCase()} verified.`,
          FIELD.storedProbeUsdtBalance,
          // `POST /api/exchanges/test-stored` does not answer `clock_sync` at all
          // (`routers/exchange.py:565`), so there is no field to report and none is
          // invented — which is the half of item 12 the old code got wrong twice over.
          null,
        ),
      });
    } catch (err) {
      const msg = sanitizeErrorMessage(err, "Stored connection test failed.");
      if (msg) setNotice({ severity: 'error', message: msg });
    } finally {
      setProcessingById((p) => ({ ...p, [exchangeId]: false }));
    }
  };

  const handleReconnect = async (exchangeId) => {
    if (processingById[exchangeId]) return;
    setProcessingById((p) => ({ ...p, [exchangeId]: true }));
    setNotice(null);
    try {
      await api.exchange.reconnect(exchangeId);
      setNotice({
        severity: 'info',
        message: `${String(exchangeId ?? '').toUpperCase()} reconnected successfully!`,
      });
      refetchConnections();
    } catch (err) {
      const msg = sanitizeErrorMessage(err, "Reconnect failed.");
      if (msg) setNotice({ severity: 'error', message: msg });
    } finally {
      setProcessingById((p) => ({ ...p, [exchangeId]: false }));
    }
  };

  const credentialsDisabledReason = !credentialValues.api_key || !credentialValues.secret_key
    ? 'Enter an API key and a secret key first.'
    : undefined;

  return (
    <div className="flex-1 overflow-y-auto p-5">
      {/* fontSize: 24 → --text-page (heading — Q8, the page <h1>) and fontSize: 14 →
          --text-body (sentence — Q1, the subtitle), both set by `ds/PageHeader`. The page's
          own `background: #0f172a` is gone: the shell owns the canvas. */}
      <PageHeader
        title="EXCHANGE MANAGEMENT"
        subtitle="Manage institutional API connections via CCXT engine"
        actions={
          // fontSize: 13 → --text-body, set by `ds/CommandButton`.
          <CommandButton
            intent="secondary"
            icon={RefreshCw}
            onClick={handleRefresh}
            loading={connectionsBusy}
            loadingLabel="Reading your exchange connections"
          >
            Refresh
          </CommandButton>
        }
      />

      {/* fontSize: 13 → --text-body, set by `ds/Alert`. This was a fixed-position toast that
          cleared itself after four seconds, including when it was reporting a failed
          credential test. `ds/Alert` has no `success` severity and one is not added to a
          shared primitive from a page commit, so a completed action is `info`. */}
      {notice ? (
        <div className="mt-3">
          <Alert
            severity={notice.severity}
            title={notice.message}
            onDismiss={() => setNotice(null)}
            dismissLabel="Dismiss this message"
          />
        </div>
      ) : null}

      {/* fontSize: 12 ×4 (the labels) and fontSize: 24 ×4 (the figures) → `ds/Metric`, which
          owns both. The four tiles resolve to tier 1, so the figures grow 24 → --text-figure
          (28): 24 is not one of the seven steps and --text-page is reserved for the <h1>.
          Each tile's own availability carries its read's state, which is why there is no
          panel state over a row of figures that comes from two different reads. */}
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div className="flex items-center gap-3 rounded-lg border border-line-default bg-surface-panel p-4 shadow-panel">
          <CheckCircle size={18} aria-hidden="true" className="shrink-0 text-content-secondary" />
          <Metric
            label={FIELD.connectedExchangeCount.label}
            value={connectedCount}
            tier={1}
            format="integer"
            className="min-w-0"
          />
        </div>
        <div className="flex items-center gap-3 rounded-lg border border-line-default bg-surface-panel p-4 shadow-panel">
          <Activity size={18} aria-hidden="true" className="shrink-0 text-content-secondary" />
          <Metric
            label={FIELD.activeBotCount.label}
            value={activeBotCount}
            tier={1}
            format="integer"
            className="min-w-0"
          />
        </div>
        <div className="flex items-center gap-3 rounded-lg border border-line-default bg-surface-panel p-4 shadow-panel">
          {/* The tile that read "Healthy" in green whenever a row existed. It keeps its
              heading and its place; what it loses is a grade nothing measures. */}
          <Shield size={18} aria-hidden="true" className="shrink-0 text-content-secondary" />
          <Metric
            label={FIELD.accountExchangeHealth.label}
            value={null}
            tier={1}
            format="raw"
            unavailable
            unavailableReason={FIELD.accountExchangeHealth.reason}
            className="min-w-0"
          />
        </div>
        <div className="flex items-center gap-3 rounded-lg border border-line-default bg-surface-panel p-4 shadow-panel">
          <Database size={18} aria-hidden="true" className="shrink-0 text-content-secondary" />
          {/* `total` is the response's own count of the array beside it, where the page read
              `supportedExchanges.length` and concatenated a literal `+` onto it. */}
          <Metric
            label={FIELD.supportedExchangeCount.label}
            value={supportedCount}
            tier={1}
            format="integer"
            className="min-w-0"
          />
        </div>
      </div>

      {/* fontSize: 18 → --text-title, set by `ds/Panel`'s <h2>. A shrink, and a consequence
          of adopting the primitive rather than a mapping: the three regions on this page are
          siblings, and one heading at --text-section would read as a section containing the
          other two. */}
      <div className="mt-4">
        <Panel
          title="Connected exchanges"
          state={connections.state}
          loading={{
            // fontSize: 14 → declaration REMOVED. The 200px centred spinner row was the whole
            // of this region's loading state; the skeleton is shaped like the cards instead.
            kind: 'skeleton-cards',
            rows: 2,
            label: 'Reading your exchange connections',
          }}
          empty={{
            // fontSize: 16 and 13 → `ds/EmptyState`, through `ds/Panel`'s `empty` arm. This
            // is now reachable ONLY from a 200 carrying no rows: a failed read is the `error`
            // arm below, where it says so (item 13).
            icon: Globe,
            headline: 'No exchanges connected',
            body: 'Nothing trades until an exchange credential is stored. Connect one below '
              + 'to start trading.',
            action: { label: 'Connect an exchange', onClick: focusVenueList },
          }}
          error={{ error: connections.error, onRetry: refetchConnections }}
          unavailable={{
            reason: 'Your exchange connections cannot be read right now, so none is listed '
              + 'rather than an empty list being shown as though you had none.',
          }}
          unauthorised={{ error: connections.error }}
        >
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {connectionRows.map((row) => (
              <ConnectionCard
                key={row?.id ?? row?.exchange_id}
                row={row}
                busy={!!processingById[row?.exchange_id]}
                onReconnect={handleReconnect}
                onTest={handleTestStoredConnection}
                onDelete={handleDeleteSaved}
              />
            ))}
          </div>
        </Panel>
      </div>

      {/* fontSize: 16 → --text-title, set by `ds/Panel`'s <h2>, for the same reason as the
          heading above. The two hand-built `role="group"` wrappers and their 12px headings
          are gone: `ds/Panel` names each region through `aria-labelledby` on a <section>. */}
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel
          title="Select an exchange"
          state={supportedState}
          loading={{
            // fontSize: 13 → `ds/Panel`'s loading arm.
            kind: 'skeleton-table',
            rows: 6,
            label: 'Reading the supported exchange directory',
          }}
          empty={{
            // fontSize: 13 → `ds/EmptyState`. "No exchanges available" over a 500 was the
            // directory's half of item 13.
            icon: Globe,
            headline: 'No exchanges available',
            body: 'The supported exchange directory answered with no venues, so there is '
              + 'nothing to choose from yet.',
            action: { label: 'Read again', onClick: supported.refetch },
          }}
          error={{ error: supported.error, onRetry: supported.refetch }}
          unavailable={{
            reason: 'The supported exchange directory cannot be read right now, so no venues '
              + 'are listed rather than an empty directory being shown.',
          }}
          unauthorised={{ error: supported.error }}
        >
          <div className="flex flex-col gap-3">
            {/* fontSize: 12 → --text-body (sentence — Q1). The hardcoded "100+" is gone: the
                real count is the declared figure in the tile above, and the encryption
                sentence is the server's own wording rather than a cipher name its docblock
                gets wrong (Requirement 4.4 finding 6). */}
            <p className="text-body text-content-secondary">
              Keys are encrypted before they are stored.
            </p>
            <div
              ref={venueListRef}
              // `maxHeight: 500` was a fixed pixel box around text. In rem it moves with a
              // reader who raised their browser default, which is the whole point of §2.
              className="flex max-h-[31.25rem] flex-col overflow-y-auto rounded-md border border-line-default bg-surface-inset"
            >
              {Object.entries(alphabeticalVenues).map(([letter, group]) => (
                <div key={letter}>
                  {/* fontSize: 11 → --text-micro (label — Q4). It names the rows below it
                      and carries their count, which is a real count of the group. */}
                  <div className="sticky top-0 z-10 border-b border-line-default bg-surface-raised px-4 py-2 text-micro font-semibold text-content-secondary">
                    {`${letter} (${group.length})`}
                  </div>
                  {group.map((venue) => (
                    <div
                      key={venue.id}
                      // A pointer-only row cannot be selected without a mouse. It picks
                      // the exchange, so it gets the button role, its selected state, a
                      // tab stop and Enter/Space activation.
                      role="button"
                      tabIndex={0}
                      aria-pressed={selectedExchange?.id === venue.id}
                      onClick={() => setSelectedExchange(venue)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
                          // Space scrolls the page by default, which would move the list
                          // out from under the row just selected.
                          event.preventDefault();
                          setSelectedExchange(venue);
                        }
                      }}
                      // fontSize: 13 → --text-body (value — Q7, secondary). The selection
                      // wash and text are `brand-wash` and `text-brand`; every other blue
                      // on this row left with the treatment it was tinting.
                      className={`flex cursor-pointer items-center gap-2.5 border-b border-line-subtle py-2.5 pl-6 pr-4 text-body ${
                        selectedExchange?.id === venue.id
                          ? 'bg-brand-wash font-semibold text-brand'
                          : 'text-content-secondary'
                      }`}
                    >
                      {/* fontSize: 10 → --text-micro. `aria-hidden`, because it restates the
                          first letter of the name beside it. */}
                      <div
                        aria-hidden="true"
                        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-sm bg-surface-raised text-micro font-bold text-content-secondary"
                      >
                        {venueLabel(venue)[0]}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate font-medium">{venueLabel(venue)}</div>
                        {/* fontSize: 11 → --text-micro (label — Q4). */}
                        <div className="truncate text-micro text-content-secondary">
                          {capabilityLine(venue)}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel
          title="API credentials"
          state={credentialsState}
          loading={{
            // fontSize: 13 → `ds/Panel`'s loading arm, replacing "Loading authentication
            // schema…".
            kind: 'skeleton-metric',
            rows: 3,
            label: 'Reading this exchange\u2019s required credentials',
          }}
          empty={
            selectedExchangeId === null
              ? {
                // fontSize: 13 → `ds/EmptyState`. "Select an exchange to view required
                // credentials" kept its guidance and gained the next action that makes it
                // operable from the keyboard (Requirement 14.1).
                icon: Globe,
                headline: 'No exchange selected',
                body: 'Choose an exchange on the left to see the credentials it needs.',
                action: { label: 'Choose an exchange', onClick: focusVenueList },
              }
              : {
                icon: Globe,
                headline: 'This exchange declares no credential fields',
                body: 'Its connection schema answered with no fields, so there is nothing '
                  + 'to fill in and nothing to save.',
                action: { label: 'Read again', onClick: schema.refetch },
              }
          }
          error={{ error: schema.error, onRetry: schema.refetch }}
          unavailable={{
            reason: 'This exchange\u2019s credential requirements cannot be read right now, '
              + 'so no form is shown rather than the wrong fields being asked for.',
          }}
          unauthorised={{ error: schema.error }}
        >
          <div className="flex flex-col gap-4">
            {selectedExchange ? (
              <div className="flex items-center gap-2 rounded-md border border-line-default bg-surface-inset p-3">
                {/* fontSize: 12 → --text-micro. `aria-hidden`: it restates the first letter
                    of the name beside it. */}
                <div
                  aria-hidden="true"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm bg-brand-wash text-micro font-bold text-brand"
                >
                  {venueLabel(selectedExchange)[0]}
                </div>
                <div className="min-w-0">
                  {/* fontSize: 14 → --text-title (heading — Q8). Unchanged in size, and a
                      step rather than a number. */}
                  <div className="truncate text-title font-semibold text-content-primary">
                    {venueLabel(selectedExchange)}
                  </div>
                  {/* fontSize: 11 → --text-micro (label — Q4). */}
                  <div className="truncate text-micro text-content-secondary">
                    {capabilityLine(selectedExchange)}
                  </div>
                </div>
              </div>
            ) : null}

            {schemaFields.map((field) => (
              <CredentialField
                key={field?.name || field?.field_id}
                field={field}
                value={credentialValues[field?.name || field?.field_id]}
                revealed={!!revealed[field?.name || field?.field_id]}
                onChange={handleCredentialChange}
                onToggleReveal={toggleReveal}
              />
            ))}

            {/* fontSize: 13 ×2 → `ds/CommandButton`. Both controls gain the
                `disabledReason` they never had: a greyed *Save keys* with no stated reason
                left a trader guessing whether a permission was missing or the page was
                broken (Requirement 15.3). */}
            <div className="flex flex-wrap gap-3">
              <CommandButton
                intent="secondary"
                icon={Wifi}
                className="flex-1"
                loading={isTesting}
                loadingLabel="Testing"
                disabled={Boolean(credentialsDisabledReason) || isSaving}
                disabledReason={
                  credentialsDisabledReason
                  ?? (isSaving ? 'The keys are being stored.' : undefined)
                }
                onClick={handleTestConnection}
              >
                Test Connection
              </CommandButton>
              <CommandButton
                intent="primary"
                icon={Lock}
                className="flex-1"
                loading={isSaving}
                loadingLabel="Encrypting"
                disabled={Boolean(credentialsDisabledReason) || isTesting}
                disabledReason={
                  credentialsDisabledReason
                  ?? (isTesting ? 'The connection is being tested.' : undefined)
                }
                onClick={handleSaveKey}
              >
                Save Keys
              </CommandButton>
            </div>
          </div>
        </Panel>
      </div>

    </div>
  );
}
