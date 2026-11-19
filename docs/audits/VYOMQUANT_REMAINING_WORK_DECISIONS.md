# VYOMQUANT — the three decisions the remaining work was blocked on

Three tasks were blocked on choices rather than on engineering: T09's storage backend,
T15 C's schema placement, and T03's test mechanism. This records the decisions, the
measurements behind them, and the exact remaining scope for each — so none of them is
waiting on a conversation any more.

**Nothing in this document is implemented.** It is a decision record. Each section ends
with what is left to build, honestly scoped.

---

## 1. T09 attachments — **decision: Supabase Storage, not S3**

### What exists today

- `support_tickets.attachment` and `ticket_comments.attachment` are `jsonb`, added by
  migration 072. They hold `AttachmentMeta` (`filename`, `file_size`, `content_type`,
  optional `url`) — **metadata only**.
- There is no object storage anywhere in the tree. Grep for `storage.from`,
  `create_signed_url`, `boto3.client("s3")`, `put_object` and `generate_presigned` across
  `backend_app` and `algo22-terminal/src` returns nothing. The `storage` hits in the
  frontend are all `localStorage`.
- The product says so plainly rather than pretending: the support FAQ answers "Not as
  uploaded files — there is no file upload yet" (`routers/support.py:239`). That honesty
  should survive the change — the FAQ has to be updated in the same commit that adds
  upload, or it becomes the stale claim.

### Why Supabase Storage

1. **It reuses the authorization model that already exists.** Storage policies are
   evaluated against the same JWT as the database, so an attachment inherits the tenant
   boundary the support tables already enforce through RLS. S3 means a *second*
   authorization model — IAM plus bucket policy plus presigned-URL expiry — kept in sync
   by hand with the first. Two models that must agree is how `strategy_backtests` became
   customer-writable while its RLS policy was correct.
2. **No new credential.** `SUPABASE_SERVICE_ROLE_KEY` is already set on all seven ECS task
   definitions (verified by reading them, names only). An S3 bucket adds an IAM principal
   to provision, scope and rotate.
3. **Data residency does not move.** The Supabase project is already in `ap-southeast-1`
   (`aws-1-ap-southeast-1.pooler.supabase.com`), the same region as the ECS cluster. A new
   S3 bucket would be a deliberate residency decision; this is not.
4. **Cost shape.** Storage is inside the existing Supabase plan. A bucket is a separate
   line item with its own lifecycle policy to own.

### What S3 would be better at, stated so the trade-off is visible

Large files, lifecycle tiering to Glacier, and cross-account access. Support attachments
are capped at **10 MB** by `AttachmentMeta.file_size` and are read by two parties (the
submitter and support staff), so none of those advantages applies here. If attachments
ever become bulk artifacts — exported trade archives, say — revisit it then, on that
evidence rather than this.

### Remaining scope

- A private bucket (`support-attachments`), created as a deployment step, with policies
  scoping read to the ticket owner and to support staff.
- An upload route that validates `content_type` against an allow-list (not a deny-list of
  extensions — the current `.exe/.sh/.bat` rejection is bypassed by renaming), enforces
  the 10 MB cap **server-side**, and runs the existing secret scanner over text types
  before storing.
- Short-lived signed URLs on read. Never a public URL: a support attachment can contain
  an account statement.
- The ownership check must not rely on the bucket policy alone — the route owns it, same
  conclusion as RISK-09 and MKT-10.
- `AttachmentMeta.url` becomes the storage object path, not a URL. A caller-supplied URL
  must be rejected rather than stored, or it is an SSRF vector and a phishing surface.
- Update the FAQ answer at `routers/support.py:239` in the same commit.

---

## 2. T15 C — **decision: a new `marketplace_listing_disclosures` table**, not nine
columns on `marketplace_submissions`

### The nine fields with nowhere to go

Trading pairs (only a single `symbol` exists), exchange compatibility, billing period
(hard-coded 30 days at `listing_projection.py:300`), minimum and maximum recommended
capital, seller-declared maximum drawdown, risk classification, leverage information,
strategy limitations, and investor disclosures.

### Why a separate table

1. **`marketplace_submissions` is a state machine, not a document.** Its columns are
   `submission_state`, `eligibility_outcomes`, `evaluator_version`, `rejection_reason`,
   `reviewed_by`, and four timestamps. Its transitions are table-driven and its invariants
   are about *admission*. Nine seller-authored prose and money fields have different
   lifecycle rules and would sit awkwardly under the same constraints.
2. **Disclosures are per-publication and must be immutable once published.** A subscriber
   bought on the basis of the drawdown and capital figures shown at purchase time. Those
   figures therefore need their own row with its own immutability, the same argument
   `strategy_versions` makes for the node graph — not mutable columns on a workflow record.
3. **The read is already allow-listed.** `listing_projection.py:185-214` enforces
   `assert set(out) <= PUBLIC_LISTING_FIELDS`, with `DENIED_LISTING_COLUMNS` beside it.
   A separate table keeps the projection's job simple: everything in the disclosures row
   is publishable by construction, so the deny-list does not grow nine entries that each
   have to be reasoned about.
4. **`max_drawdown_pct` already exists, derived.** `listing_projection.py:197-200` computes
   it from the publish-time backtest aggregate. The seller-declared figure is a DIFFERENT
   fact and must not overwrite it — both belong, labelled, and a separate table makes that
   distinction structural rather than a naming convention.

### Remaining scope

- Migration: `marketplace_listing_disclosures`, keyed by `(listing_id, version)`, RLS
  owner-write before publication and public-read after, with a rollback.
- `supported_pairs` as a constrained array, not free text — it gates execution.
- `billing_period_days` replacing the hard-coded 30, defaulting to 30 so existing
  entitlements are unchanged (`test_mkt09_entitlement_expires_against_the_clock.py` pins
  the current behaviour and must keep passing).
- Money fields as `numeric` with a currency, never floats.
- The listing form, which today collects only description and category
  (`CreatorMarketplacePanel.jsx:79-80`).
- Projection changes, with `PUBLIC_LISTING_FIELDS` extended deliberately, field by field.

## 2b. T15 E — `live_performance_summary` has no writer

`StrategyMarketplace.jsx:348-368` sets `ENVIRONMENTS = ['BACKTEST']` and records that
nothing in `backend_app` writes `live_performance_summary` or `live_risk_metrics` for any
listing. So "review the strategy's verified live performance" has no data behind it.

**Decision: derive it from `execution_records`, on a schedule, into a stored summary — not
on read.** `execution_records` is the same source the `MP_LIVE_HISTORY` eligibility
criterion already trusts (`eligibility_gate.py:472-475`), and it is `authenticated
SELECT`-only, so it cannot be forged. Computing on read would put an aggregate over a
1,838-row-and-growing table on a public listing page; a scheduled writer also gives the
figure an `as_of` timestamp, which a disclosure needs.

The `marketplace_expiry` worker is the precedent for where such a sweep lives.

---

## 3. T03 / T16 residuals — **decision: Playwright for T03, and T16's Sentry half stays
blocked**

### T03

The layout guard is a source grep (`strategyBuilder.layout.test.js`). It is genuinely
useful — it fails on any `vh` unit in the shell — but it cannot answer "is there a gap
between the toolbar and the canvas at 1280×720", because **jsdom has no layout engine**:
every `getBoundingClientRect` is zero.

**Decision: Playwright**, added as a dev dependency with a `@layout`-tagged project, run
in CI against the built bundle at three viewports. Rejected alternatives: Puppeteer
(Playwright's multi-engine support is what makes "different aspect ratios" answerable),
and a headless-Chrome script of our own (a test harness nobody maintains).

Cost, stated: browser downloads add ~2 minutes to a cold CI run and need caching. That is
the price of being able to assert the thing Task 03 is actually about.

### T16

Route-navigation timing, duplicate-request auditing and JS profiling are all measurable
with the Playwright harness above once it exists — which is why T03 comes first.

**The Sentry half stays blocked, and not for want of trying.** Transactions are ~90%
client-discarded and there is no browser project, so there is no web-vitals or INP data to
read; and the Sentry API needs an auth token that exists nowhere in the environment, in
`~/.aws`, or in the repository's CI secrets (which hold only `SUPABASE_URL` and the four
`VITE_*` values). Creating a browser project and issuing a read token are owner actions.
Until then, any claim about field performance would be invented, and
`VYOMQUANT_PERFORMANCE_BASELINE.md` is correct to record it as unmeasured.

---

## Status, so this document cannot be mistaken for completion

| Task | Decision | Built |
|---|---|---|
| T09 attachments | Supabase Storage | **No** |
| T15 C nine fields | new `marketplace_listing_disclosures` | **No** |
| T15 E live performance | scheduled writer from `execution_records` | **No** |
| T03 layout at resolution | Playwright | **No** |
| T16 nav/duplicate/JS timing | Playwright harness, after T03 | **No** |
| T16 Sentry field data | blocked on owner | **No** |

The decisions are what these were waiting on. The work is not done.
