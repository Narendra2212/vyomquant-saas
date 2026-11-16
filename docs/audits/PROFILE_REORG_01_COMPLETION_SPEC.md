# PROFILE-REORG-01 — completion spec

**Status:** `components/NotificationPreferences.jsx` is extracted and committed (this branch).
The Profile surgery is specified below and **not** applied.
**Why it is written down:** the implementation was built, verified to **0 eslint errors**, and
reverted twice — not because it was wrong, but because the test updates it requires were
larger than the session budget left, and deleting tests to go green is forbidden
(Part 5: *"Do not disable existing tests merely to make the test suite pass"*).

Everything below is measured, not estimated. Line numbers are against `main` @ `878cec87`.

---

## 1. What is already done

`components/NotificationPreferences.jsx` — the preferences panel extracted whole: switch
primitive, contract defaults, reader, optimistic-override state, notice state, write handler,
both derivations, render. Self-contained; the host passes nothing.

The payload expression is carried across byte-for-byte — both `??` chains, both boolean
coercions, the same eight event keys, the same three channels — because
`NotificationSettingsRequest` validates the whole object and its shape is a contract.

eslint clean. Zero colour literals, so **no `no-colour-literals` budget entry is needed**.

---

## 2. Remaining steps

### 2.1 Wire it

In `components/NotificationCenter.jsx`:

```jsx
import { NotificationPreferences } from './NotificationPreferences';
```

and render `<NotificationPreferences className="mt-6" />` **after** the notification list,
immediately before the trailing `<style>` block. After, not before: someone opening this page
came to read what happened, not to configure what happens next, and eight switches above the
feed makes the common case scroll past the rare one.

### 2.2 Strip Profile — three panels

Remove the `<Panel>…</Panel>` blocks titled **Notification Preferences**, **Subscription &
Plan** and **Platform-wide statistics**.

Match structurally: the opening `<Panel` at its indent, the `title="…"` that identifies it,
and the first `</Panel>` at that same indent. Measured extents: 102, 92 and 137 lines.

### 2.3 Strip Profile — the declarations those orphan

Twenty, all named by eslint after 2.2. Multi-line ones need an **explicit end anchor**:

| Declaration | End anchor |
|---|---|
| `const CONTRACT_DEFAULTS` | `});` |
| `function NotificationSwitch` | `}` |
| `const handleNotificationSettingChange` | `  };` |
| `const eventOn = (` | `  };` |
| `const emailChannelOn = (() =>` | `  })();` |
| `const renewalDate = fromNullable(` | `  );` |

Single-line: `notifOverride`, `notifNotice`, `readStats`, `readNotif`, `stats`, `notif`,
`statsBody`, `notifBody`, `notifState`, `subscriptionStatus`, `planFeatures`, `platformBots`,
`platformStrategies`, `platformTrades`, `platformPnl`.

Also: `formatDate` (only caller was the renewal date), the `token` import (only inline-styled
construct was `NotificationSwitch`), and five lucide icons — `Activity`, `AlertCircle`, `Cpu`,
`Bell`, `TrendingUp`. Plus `CreditCard` and `Globe` if eslint still reports them.

Then three live references need editing, not deleting:

- `handleRetry` — drop `stats.refetch()` and `notif.refetch()`.
- `anyLoading` — `[profile, billing, security, referral]`, four reads not six.

> **DO NOT USE BRACKET BALANCING TO FIND EXTENTS.** That was tried and it failed twice,
> because it did not skip comments and the comments in this file contain backticks and
> apostrophes (`` `${token.brand.base}33` ``, "the page's"). Those flip the in-string flag and
> every subsequent count is garbage: it truncated `NotificationSwitch` at the first line that
> looked balanced and ended in `}`, and ran 263 lines past the end of
> `handleNotificationSettingChange`, taking live code with it. A guard that refuses any
> "single-line" declaration not ending in `;` is what caught `renewalDate` and prevented a
> repeat.

### 2.4 `billing` STAYS — the one non-obvious finding

`readBilling`, `billing` and `billingBody` must **not** be removed, even though the
Subscription & Plan panel that motivated them is going.

`billingBody` has a second consumer: `planName`, which renders the plan tier chip in
**Account identity** — a panel Task 13 explicitly keeps (*"Keep relevant account and security
functionality, including: Account identity"*). Dropping the read to tidy up blanks a field in
a preserved panel.

`stats` and `notif` have no second consumer and leave cleanly. This asymmetry is the thing to
get right.

### 2.5 Tests — six edits, measured

Running the two Profile suites against the applied change gives **8 failed, 10 passed**.

**`profile_phase5b_remediation.test.jsx`**

| Case | Line | Action |
|---|---|---|
| `12, 13, 14. Notification toggles send exact boolean channels and events` | 235 | **Move** to a new component suite. This is the payload-contract test and the most valuable one here — it asserts the exact booleans reaching the backend. |
| `15, 16. Failed notification update reverts state` | 275 | **Move.** Asserts the optimistic revert and its notice. |
| `17, 18, 19. Billing plan and security log render correctly` | 298 | **Check, probably passes** — its `'Enterprise Tier'` assertion (319) is the Account identity chip, which survives per §2.4. |
| `1. Profile initial successful load renders all sections` | 114 | Narrow to the sections that remain. |

**`profile_phase5e_trader_ux.test.jsx`**

| Case | Line | Action |
|---|---|---|
| `1 & 2. Account overview …` | 127 | **Should pass unchanged** — `'Enterprise Tier'` (148) is the kept chip. Confirms §2.4. |
| `5 & 6. Automation context renders fleet metrics …` | 201 | **Invert.** Assert `'Platform-wide statistics'` is absent. Keep the `/app/exchange` and `/app/strategies` navigation assertions — those are not part of the removed panel. |
| `7, 8, 9. Categorized notifications …` | 231 | **Move** to the component suite. |
| `10. Subscription card maps plan, renewal date, features …` | 261 | **Invert.** Assert `'Subscription & Plan'` and `/Dedicated Co-location/` are absent; the owner has confirmed this panel duplicated the Billing & plan page, so nothing is stranded. |

Invert rather than delete, as Waves A and B did throughout: asserting a removed control's
**absence** is what stops a later pass reintroducing it after reading only the page and not
its history.

The moved cases need `getNotificationSettings` / `updateNotificationSettings` mocks and a
`<NotificationPreferences />` render instead of `<Profile />` — no other change, since the
component reads and writes its own state.

### 2.6 Baselines

`tests/regression/test_baseline_unchanged.py` will report two surfaces. Re-record with
`_baseline_note` authorisations, per the procedure PR #273 established:

- **`surface_profile`** — loses `getNotificationSettings`, `updateNotificationSettings` and
  `getStats`. **Keeps `getBillingPlan`**, per §2.4. Worth stating in the note, because a
  reviewer expecting all three billing/stats calls to leave will read its survival as a
  mistake.
- **`surface_notifications`** — gains `getNotificationSettings` and
  `updateNotificationSettings`.

`api_call_count` moves on both.

---

## 3. Definition of done

Both Profile suites green, a component suite covering the moved cases green, the full frontend
suite at its pre-change count plus the new cases, and CI's `Unit & Integration Tests` lane
green on the re-recorded baselines.

Not "the page renders" — the payload-contract test from §2.5 must pass in its new home,
because that is the assertion protecting the one piece of this work that could break a user's
stored settings.

---

## 4. One thing deliberately left alone

The *Email Notifications* switch moves **as-is**, promising "Receive verified critical alerts
to your primary email address". That promise is currently false:
`docs/audits/VYOMQUANT_EMAIL_NOTIFICATION_DELIVERY_AUDIT.md` has the four measurements showing
`email_notifications` is written, persisted and read by nothing that sends mail.

Relocating a panel is not the place to change what the product promises. The owner has
scheduled email work last; that switch is where it lands.
