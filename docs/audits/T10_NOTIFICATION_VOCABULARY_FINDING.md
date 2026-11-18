# T10 — the notification problem is two disjoint vocabularies, not a missing filter

**Measured 2026-11-18 against production and against `backend_app` by AST.** This
supersedes the standing description of T10's remainder ("preference filtering is not
built; 7 of the 8 preference keys have no producer"). That was true but it understated
the shape of the problem, and it pointed at the wrong fix.

## 1. Production has never recorded a notification

```
public.notifications rows : 0
support tickets           : 6     (each should notify support_staff)
strategy_deployments      : 28
execution_records (fills) : 1838
notification_settings     : 4 rows, all 8 Pydantic keys present
```

Backend live at `5cd0fd21`, i.e. current, so this is not a stale-deploy artifact.
1838 fills and 28 deployments produced nothing.

## 2. The preferences panel and the dispatcher speak different languages

`NotificationEvents` offers **8** toggles. The tree dispatches **15** literal event
types. **One** appears in both.

| the 8 preference keys | has a producer? |
|---|---|
| `kill_switch_activated` | **yes** — `routers/risk.py:1042` |
| `api_key_expiring` | no |
| `backtest_complete` | no |
| `bot_state_change` | no |
| `daily_pnl_summary` | no |
| `new_login_detected` | no |
| `stop_loss_triggered` | no |
| `trade_executed` | no |

And the other direction, which is the half nobody had written down — **14 event types
are dispatched that are not preference keys at all**:

```
cancellation_reversed   exchange_connected      exchange_disconnected
kill_switch_recovered   paper_account_reset     paper_order_placed
payment_succeeded       profile_updated         risk_settings_updated
strategy_created        strategy_deployed       strategy_stopped
subscription_cancelled  subscription_updated
```

So the system is not "missing 7 producers". It has **two vocabularies with a single
point of contact**:

* the panel shows a customer 8 switches, **7 of which govern nothing**;
* the dispatcher emits 14 kinds of event **no preference can govern**.

## 3. Why building the filter would have made this worse

A filter keyed on the 8 preference fields would consult a preference for exactly one
event type and pass the other fourteen through ungoverned — while *looking*, in code
review and in the UI, like notification preferences now work. The panel would stop being
visibly inert and start being invisibly inert, which is strictly harder to find.

This is the same failure mode as the `notification_settings` column default recorded in
the handoff: a declared vocabulary that no stored row and no code path uses, which reads
as an enforced invariant.

## 4. What to do instead, in order

1. **Reconcile the vocabularies, and write the mapping down.** Either rename the
   dispatched types to the preference keys, or declare an explicit
   `event_type -> preference_key` table in one reviewable place. A mapping that lives in
   eight scattered string literals is how the two drifted apart.
2. **Decide what each of the 14 unmapped types is.** Some are plainly not
   preference-governed (`payment_succeeded`, `subscription_cancelled` — billing facts a
   customer cannot opt out of), some clearly are (`strategy_stopped`,
   `exchange_disconnected`). That split is the actual design work, and it must precede
   the filter or the filter encodes a guess.
3. **Then filter**, against a mapping a test can enumerate — and the three mandatory
   events (`kill_switch_activated`, `stop_loss_triggered`, `new_login_detected`) stay
   un-filterable, which migration 073's CHECK already enforces at the column.
4. **Separately: find out why dispatch produces no rows.** Fourteen types are dispatched
   and 28 deployments exist, yet the table is empty. NOTIF-01 made persistence a property
   of the dispatcher rather than of each call site, and NOTIF-03 (#334) fixed the two
   support sites that wrote as the wrong user — **#334 is merged but was NOT in
   `5cd0fd21`**, so the next deploy is the first honest test of whether anything persists.
   Re-run this census after it lands before investigating further.

## 5. The one-line version

Do not build preference filtering yet. The preferences and the events are different
vocabularies, and a filter over a vocabulary nothing emits is a feature that reports
success while doing nothing. Reconcile first.

## Reproducing this

`.kiro-tmp/producers.py` (AST over `backend_app`, compares against `NotificationEvents`'
field list read from the model rather than retyped) and `.kiro-tmp/notif_census.py`
(read-only against production, never prints the DSN).
