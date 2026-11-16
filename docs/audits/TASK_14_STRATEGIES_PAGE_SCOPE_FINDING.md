# Task 14 — the two strategy lists are not duplicates

**Date:** 2026-11-16 · `main` @ `878cec87` · `pages/Strategies.jsx` (2,667 lines)
**Finding:** Task 14's instruction to remove the *Owned and subscribed* section is
**conditional**, and the condition is **not satisfied today**. Acting on it as written would
remove paying subscribers' only access to the strategies they bought.
**Status:** no code changed. Scope correction filed before implementation.

---

## 1. What the task says

> *"Remove the separate Owned and Subscribed section **if the same information can be
> presented clearly in the main strategy listing**."*

The conditional is doing real work. On the supplied screenshot the two sections look
redundant — strategy `vyom` appears in both — but that is an artefact of the account in the
screenshot owning one strategy and subscribing to none.

## 2. What they actually are

They are two different surfaces over two different reads.

| | *Owned and subscribed* (`:2005`) | *Your strategies* (`:2414`) |
|---|---|---|
| Read | `api.library.myStrategies()` | the strategies list |
| Rows | owned **and SUBSCRIBED** entries | owned strategies only |
| Identity | Listing id for subscribed, strategy id for owned | strategy id |
| Purpose | entitlement | strategy management |

The combined list is the **entitlement** surface. It is the only place in the product that
renders a subscribed marketplace Listing, and it carries controls the table does not have:

- `api.library.renewSubscription(entry.subscription_id)` — `:945`
- `api.library.cancelSubscription(entry.subscription_id)` — `:990`
- deploy of a subscribed Listing through `POST /api/library/{id}/deploy` — `:519`, which is
  the subscriber-safe path that binds a deployment without copying the seller's logic

A subscribed entry has **no row in the table**, because it is not the subscriber's strategy.
That is the whole point of the marketplace design: `MKT-05` keeps the Listing's logic out of
the subscriber's tenant, so there is no strategy record for the table to show.

## 3. What deleting the section would actually do

Not "tidy a duplicate". It would remove, for any account with a subscription:

1. every view of what they are subscribed to;
2. the only renew control, so a lapsing subscription could not be renewed in-product;
3. the only cancel control, which is a consumer-protection problem, not a UX one;
4. the only way to deploy a strategy they are paying for.

On the screenshot's account — `Owned: 1, Subscribed: 0` — none of that is visible, which is
exactly why the duplication looks total. It is not.

This is the same shape as two findings already recorded this programme: the dead
*Manage billing* control (removing something that could not work) and the *Manage
Subscription* panel on Profile (removing a genuine duplicate). This one is the inverse —
something that **looks** duplicated and is load-bearing — and the cost of getting it wrong
falls on the paying users rather than on the operator.

## 4. What satisfying the condition requires

The task is achievable; it is just a merge rather than a deletion, and it must happen in this
order:

1. **Give the main listing a subscribed row.** One list keyed by ownership, with the
   `Owned` / `Subscribed` / `Listed` labels Task 14 names. A subscribed row has a Listing id
   and no strategy id, so every column that assumes a strategy — version, deployment state,
   last signal — needs a declared "not applicable to a subscription" reading rather than a
   blank or a zero.
2. **Carry the entitlement actions across.** Renew, cancel and listing-deploy must survive
   the merge. `ACTION_CATALOG` (`:433`) already resolves actions per entry from the server's
   own labels, and the file notes there is no
   `ownership === "SUBSCRIBED" ? … : …` branch anywhere in the section — so the catalogue is
   the thing to reuse, not to reimplement per row.
3. **Preserve the withheld-subscription state.** `:886` records that the server states
   whether the subscribed half was read at all, and `subscribed_total` must be *withheld*
   rather than shown as `0` when it was not: "rendering it would put a measured-looking `0`
   beside a real `Owned` count". A merged list has to keep that distinction, or an account
   whose subscription read failed is told it has no subscriptions.
4. **Then, and only then, remove the separate section.**
5. **Cards** — Task 14's actual presentational request — are orthogonal to all of the above
   and can be done first against the owned list alone if a smaller change is wanted.

## 5. Recommendation

Split Task 14 into two, and do them in this order:

- **14a — cards.** Convert the owned listing's rows to the card layout Task 14 specifies
  (name, market, exchange, timeframe, ownership, subscription state, listing state, execution
  state, version, last activity, actions). Preserves search, filters and every existing
  action. No entitlement semantics involved, so it is reviewable on its own.
- **14b — merge the entitlement list.** Steps 1–4 above. This touches marketplace
  entitlements and overlaps Task 15's audit of the same infrastructure, so it should be
  sequenced with Task 15 rather than against it.

Doing 14b blind — deleting the section because the screenshot looks redundant — is the one
path that should not be taken.

## 6. Scope of this finding

Static reading of `pages/Strategies.jsx` and the `api.library` call sites at `878cec87`. No
database was queried, so the claim that a subscribed entry has no strategy row is read from
the marketplace design (`MKT-05`, migration `056`) and from the page's own identity handling
at `:471-475`, not from data.
