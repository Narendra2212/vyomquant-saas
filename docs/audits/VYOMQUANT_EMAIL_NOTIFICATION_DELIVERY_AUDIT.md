# Email notification delivery — forensic audit

**Date:** 2026-11-16
**Repository:** `github.com/Narendra2212/vyomquant-saas` @ `main` = `878cec87`
**Trigger:** owner report — *"email notification and alerting … I think it is only visible in
UI now."*
**Verdict:** **The report is correct.** Per-user email notification is UI-only. The preference
is written, persisted, and read by nothing that sends mail.
**Risk:** **HIGH.** Not because email is a premium feature, but because the toggle it belongs
to promises delivery of stop-loss, margin and kill-switch alerts, and a trader who enables it
is entitled to believe those arrive.

---

## 1. What the UI promises

`pages/Profile.jsx`'s Notification Preferences panel renders a switch labelled:

> **Email Notifications** — *Receive verified critical alerts to your primary email address*

alongside switches for stop-loss and margin alerts, emergency kill switch and liquidations,
strategy lifecycle changes and security/login events. The panel closes with:

> *Mandatory risk alerts (global kill switch triggers) cannot be disabled.*

Toggling it calls `api.user.updateNotificationSettings(payload)`, which `routers/user.py`
validates against `NotificationSettingsRequest` and persists.

So the product states that enabling this switch causes critical alerts to reach the account's
email address.

## 2. What actually happens — four measurements

### 2.1 The preference is written and never read

`email_notifications` appears in the **entire** `backend_app/` tree exactly once:

```
backend_app/routers/notifications.py:71    email_notifications: bool = True
```

That is a field declaration in a Pydantic request model. There is no reader. No code branches
on it, and nothing consults it before or instead of sending anything.

### 2.2 Creating a notification has no email side

`routers/notifications.py:143 create_notification` inserts a row and returns:

```
:180    res = supabase.table("notifications").insert(data).execute()
```

A grep of that module for `email|smtp|Email` returns the single line in §2.1 — the model
field. The in-app notification is the whole of the delivery. There is no dispatch step, no
queue hand-off, and no per-channel fan-out.

### 2.3 The email channel that exists cannot address a user

`backend/alert_system.py:161` declares `EmailAlertChannel`, and it is real working SMTP code.
But its constructor is:

```python
def __init__(self, smtp_host=None, smtp_port=587, smtp_user=None,
             smtp_password=None, from_address=None,
             to_addresses: Optional[List[str]] = None):
```

`to_addresses` is a **static list supplied at construction**. The class has no notion of a
recipient per alert, so it cannot send to the user an event concerns. `is_configured()`
returns `False` unless that list is non-empty, and `send()` logs *"Email not configured,
skipping"* and returns `False`.

Its registration sites (`alert_system.py:333`, `:520`) and its callers are all
**operator-facing**: `circuit_breaker.py`, `order_watchdog.py`, `data_observability.py`,
`dag_engine.py`, `reconciliation_scheduler.py`. This is an on-call distribution list, not a
customer notification channel. Nothing is wrong with it; it is simply a different feature,
and it is the only email path that is wired to anything.

### 2.4 It is disabled by default anyway

```
backend_app/core/deployment_config.py:64     ALERT_EMAIL_ENABLED: bool = False
backend_app/core/deployment_config.py:156    ALERT_EMAIL_ENABLED=os.getenv("ALERT_EMAIL_ENABLED", "false")...
```

`config.py:232-234` reads `EMAIL_SMTP_HOST` and `EMAIL_USER`. Whether either is populated in
the deployed ECS task definition is **UNVERIFIED** — this workspace has no AWS access
(`VYOMQUANT_MASTER_ENGINEERING_AUDIT.md` §4). Even if they are, §2.1–2.3 mean no per-user mail
would be sent regardless.

### 2.5 Three alerting implementations, which is its own finding

`backend/alert_engine.py`, `backend/alert_system.py` and `core/alerting_system.py` each
implement SMTP sending independently (`aiosmtplib` with an `smtplib` fallback in two of them).
Different subsystems import different ones. Any delivery work should pick one and retire the
others rather than adding a fourth.

---

## 3. Why this is worse than a missing feature

A missing feature is visible. This one reports success: the switch flips, the write persists,
the UI shows it enabled, and the next page load reads it back enabled. Every signal available
to the trader says email alerting is on.

The switches it sits beside are *Stop Loss & Margin Alerts* and *Emergency Kill Switch &
Liquidations*. A trader who turns on email expecting to be woken by a liquidation warning has
been told something untrue by the product, and the only way to discover it is for the alert
not to arrive during the event it was for.

This is the same defect class as the *Manage billing* button removed in `BILLING-PORTAL-02`
(a control whose only possible outcome was a refusal) — but inverted and more serious, because
that control failed loudly and this one fails silently at the moment it matters.

---

## 4. What remediation requires

Two halves. The second is not mine to do.

### 4.1 Application — implementable and testable here

1. **A dispatcher seam.** `create_notification` is the single funnel (§2.2) and is the right
   place: resolve the recipient, read the preference, fan out per channel. Nothing else needs
   to learn about email.
2. **Recipient resolution.** The address belongs to Supabase Auth, not to a column anyone
   writes. It must be read per dispatch and must never be accepted from a request body.
3. **Preference gating with a mandatory floor.** `email_notifications` plus the per-event
   switches decide ordinary alerts. Kill-switch and global-halt events must dispatch
   regardless, because the UI already promises exactly that — a preference must not be able to
   silence them.
4. **Delivery status and retry** (Task 10's explicit requirement). A durable per-dispatch
   record with attempt count and terminal outcome, so "sent" is a fact and not an assumption.
   **This needs a migration**, and therefore the database access Wave C is blocked on.
5. **Idempotency.** One event must not produce two emails on a worker restart. The repository
   already has the pattern — `045_sec03_durable_webhook_events.sql` — and it should be reused
   rather than reinvented.
6. **One implementation.** Consolidate onto a single channel abstraction (§2.5) and extend it
   with a per-alert recipient, instead of adding a fourth sender.

### 4.2 Infrastructure — OWNER ACTION, BLOCKING

No amount of application code delivers mail without a configured transport. Required before
any of §4.1 can be verified end to end:

- An SMTP provider or **Amazon SES** in `ap-southeast-1`, with a **verified sending domain**
  and DKIM/SPF. Unverified SES is sandboxed and will only send to verified addresses, which
  looks exactly like the current silent failure.
- `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_USER`, `EMAIL_PASSWORD` and a `from` address in
  the ECS task definition, through Secrets Manager — never committed.
- A decision on `ALERT_EMAIL_ENABLED`, which currently defaults to `false`.

I have no AWS access and cannot set, read or verify any of this. I also will not send test
mail to a real address.

### 4.3 What "done" must mean

Not "the code compiles" and not "the switch persists". One real alert, triggered on staging,
arriving in a real inbox, with a durable delivery record showing it — and a second run of the
same event producing no second email.

---

## 5. Recommended immediate mitigation

Until §4.1 and §4.2 are both complete, **the Email Notifications switch should not claim
delivery.** Two honest options:

1. Disable the control with a `disabledReason` stating email delivery is not yet configured —
   the pattern `ds/CommandButton` already enforces elsewhere (Requirement 15.3), and the
   smaller change.
2. Remove the switch until the path exists, as `BILLING-PORTAL-02` did for the dead portal
   control.

Either is better than the current state, and either is a few lines. Leaving a switch that
reports success for a channel that sends nothing is the one option that should not survive
launch — and it is the reason this audit is filed at HIGH rather than MEDIUM.

The in-app notification path is **not** affected: those rows are written, read, and scoped to
their owner. This audit is about the email channel only.

---

## 6. Scope of this audit

Static reading of `backend_app/` at `878cec87`. No email was sent, no SMTP endpoint was
contacted, no deployed configuration was inspected, and no database was queried. Items marked
UNVERIFIED are blocked on the access gaps recorded in
`VYOMQUANT_MASTER_ENGINEERING_AUDIT.md` §4.
