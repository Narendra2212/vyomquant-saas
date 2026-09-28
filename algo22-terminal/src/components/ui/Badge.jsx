/**
 * ui/Badge — the legacy status chip, retokened.
 *
 * ═══ WHY THIS CHANGED ═══
 *
 * This component rendered a DIFFERENT green and red from the rest of the application, on every
 * page that uses it. Its maps held 68 raw colour literals, 40 of them `#10B981` (24) and
 * `#EF4444` (16) — the pre-convergence trading palette. `styles/tokens.css` moved profit to
 * `#26A69A` and loss to `#EF5350` and records the change explicitly ("profit #10B981 → #26A69A,
 * loss #EF4444 → #EF5350"), and every `components/ds/` status surface takes its hue from
 * `design/semantic.js`, which reads those tokens.
 *
 * So a `ds/StrategyStatus` reading RUNNING and a `ui/Badge` reading RUNNING sat side by side in
 * the same table rendering two different greens. That is the visible inconsistency, and it was
 * scheduled: `no-colour-literals.budget.js` says of this file's entry, "task 6.6 is scheduled to
 * move `ui/Badge.jsx`'s 40 #10B981/#EF4444 occurrences to the trading palette. Out-of-scope does
 * not mean unscheduled." This is that move. The entry goes 68 → 0.
 *
 * Two of the literals were not even in the palette: `#94A3B8` (draft) and `#64748B` (stopped)
 * are Tailwind's slate ramp, with no token behind them at all. Both collapse onto
 * `content-secondary`, which is the one neutral text colour that passes AA on these surfaces —
 * `content-muted` is marked NON-TEXT ONLY in tokens.css at 3.2:1 and a chip label is text.
 *
 * ═══ SHAPE, NOT JUST COLOUR ═══
 *
 * The chip now matches `ds/StatusBadge` so the two are indistinguishable where they meet:
 * `rounded-sm` (the app's control radius) rather than a bare `rounded`, `text-micro` rather than
 * `text-xs`, and the same `font-mono uppercase tracking-wide` treatment.
 *
 * THE DOT NO LONGER PULSES. `tests/unit/ds/StatusBadge.test.jsx` has a case titled "carries no
 * pulse, no halo and no injected keyframes", whose comment names this component's dot as the
 * thing it was written against: "Badge's dot was animate-pulse". Requirement 1.5 is calm by
 * default, and a chip that pulses forever on a page of thirty rows is the motion that rule
 * retires.
 *
 * ═══ WHAT IS DELIBERATELY UNCHANGED ═══
 *
 * Every variant key, the `c` alias for `variant`, the `dot` flag and the fallback to `cyan` on
 * an unknown key. This is a retokening, not an API change: `StrategyBuilder`, `PaperTrading` and
 * the marketplace pass these keys today and none of them should have to change. `ds/StatusBadge`
 * is this component's successor — it derives hue from a state word and accepts no colour prop —
 * and adopting it per call site is what eventually deletes this file.
 *
 * ONE CAVEAT FOR A FUTURE EDITOR. `dead-tailwind.test.js` records that class lists held in an
 * object literal — "`ui/Badge.jsx`'s `variants` and `dotColors` maps, read as
 * `variants[activeVariant]`" — are invisible to its scanner. Tailwind's own content pass still
 * sees them, because it scans files as raw text, so the utilities below are emitted. But the
 * guard will NOT catch a typo here. Every class below is a token that exists in tokens.css; keep
 * it that way by construction rather than by trusting the guard.
 */

import React from 'react';

/**
 * Variant → chip treatment.
 *
 * Grouped by the hue each set resolves to, so a reader can see that eleven of the eighteen keys
 * are three colours. The `/30` and `/40` border alphas are carried over from the values this
 * replaced — the stronger border on the deployment states is what distinguished them.
 */
const variants = {
  // Brand.
  cyan: 'bg-brand-wash text-brand border-brand/30',

  // Warning / attention.
  gold: 'bg-status-warning-wash text-status-warning border-status-warning/30',
  warning: 'bg-status-warning-wash text-status-warning border-status-warning/30',
  paused: 'bg-status-warning-wash text-status-warning border-status-warning/40',

  // Profit / success.
  profit: 'bg-status-profit-wash text-status-profit border-status-profit/30',
  green: 'bg-status-profit-wash text-status-profit border-status-profit/30',
  success: 'bg-status-profit-wash text-status-profit border-status-profit/30',

  // Loss / failure.
  loss: 'bg-status-loss-wash text-status-loss border-status-loss/30',
  red: 'bg-status-loss-wash text-status-loss border-status-loss/30',
  danger: 'bg-status-loss-wash text-status-loss border-status-loss/30',
  failed: 'bg-status-loss-wash text-status-loss border-status-loss/40',

  // Running / deployed. `status-live` rather than `status-profit`: the hue is the same value,
  // but the name says what the chip means, and a future divergence should follow the meaning.
  running: 'bg-status-live-wash text-status-live border-status-live/40',
  deployed: 'bg-status-live-wash text-status-live border-status-live/40',
  active: 'bg-status-live-wash text-status-live border-status-live/40',

  // Neutral. `draft` and `stopped` were two different Tailwind slates with no token behind
  // either; they are one neutral now, which is what they always meant.
  muted: 'bg-status-neutral-wash text-content-secondary border-line-default',
  draft: 'bg-status-neutral-wash text-content-secondary border-line-default',
  stopped: 'bg-status-neutral-wash text-content-secondary border-line-default',
};

/** The optional leading dot. Same hue as the label, at full strength. */
const dotColors = {
  cyan: 'bg-brand',
  gold: 'bg-status-warning',
  warning: 'bg-status-warning',
  paused: 'bg-status-warning',
  profit: 'bg-status-profit',
  green: 'bg-status-profit',
  success: 'bg-status-profit',
  loss: 'bg-status-loss',
  red: 'bg-status-loss',
  danger: 'bg-status-loss',
  failed: 'bg-status-loss',
  running: 'bg-status-live',
  deployed: 'bg-status-live',
  active: 'bg-status-live',
  muted: 'bg-status-neutral',
  draft: 'bg-status-neutral',
  stopped: 'bg-status-neutral',
};

export function Badge({ children, variant = 'cyan', c, dot = false, className = '' }) {
  const activeVariant = (c || variant || 'cyan').toLowerCase();
  const badgeClass = variants[activeVariant] || variants.cyan;
  const dotColorClass = dotColors[activeVariant] || dotColors.cyan;

  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-sm border px-2 py-0.5 font-mono text-micro font-semibold uppercase tracking-wide ${badgeClass} ${className}`}
    >
      {dot && <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColorClass}`} aria-hidden="true" />}
      {children}
    </span>
  );
}
