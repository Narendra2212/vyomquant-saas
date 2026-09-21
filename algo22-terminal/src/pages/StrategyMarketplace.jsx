/**
 * pages/StrategyMarketplace.jsx — the Marketplace catalogue and Listing detail surface.
 *
 * Spec: marketplace-subscriptions-paper-trading task 32.6.
 * Requirements 6.6, 20.9, 20.10, 22.8, 28.1.
 *
 * EVERY BACKEND CALL TRAVELS `api.library`
 * ---------------------------------------
 * The page imports no low-level HTTP helper, builds no URL and knows no host: the eleven
 * direct client calls this file used to carry are now `api.library.*` calls (Requirement
 * 20.2 as applied by 20.10). The authenticated / anonymous split survives as the `*Public`
 * method pairs the module exports — the token check decides which method is called, not
 * which client is constructed.
 *
 * OUTCOMES GO THROUGH THE PRODUCT'S NOTIFICATION MECHANISM
 * -------------------------------------------------------
 * `window.showToast(type, message)` — installed by `AppShell` — replaces the three browser
 * dialogs this page used to raise (Requirement 20.10). When the page is mounted outside
 * `AppShell` the message is rendered inline instead, so an outcome is never silently dropped.
 *
 * OWNER-SUPPLIED TEXT IS TEXT
 * ---------------------------
 * Listing name, description, tags, review text and any rejection reason are rendered as
 * React text children. No raw-HTML injection prop and no rich-text (Markdown) renderer
 * appears in this file, so owner-supplied markup is displayed, not executed (Req 22.8).
 *
 * FIGURES ARE GROUPED BY EXECUTION_ENVIRONMENT, AND AN ABSENT FIGURE IS ABSENT
 * ---------------------------------------------------------------------------
 * Requirement 6.6: each figure carries its Execution_Environment label, the three groups sit
 * in visually separated sections, a section whose Listing carries no figures is omitted
 * entirely rather than rendered as zeros, and the `BACKTEST` section states that past
 * performance does not indicate future results.
 *
 * `(strat.backtest_sharpe_ratio || 0).toFixed(2)` — the shape this file used to use — turns
 * "not measured" into "0.00", which is precisely the substitution Requirement 28.5 forbids
 * and which Requirement 28.1's no-placeholder rule is about. Every figure here goes through
 * {@link toFiniteNumber}: a value the server did not send renders as an em dash, and a
 * whole section with nothing measured does not render at all.
 *
 * The response is the public Listing projection
 * (`backend_app/backend/marketplace/listing_projection.py`), so the fields read below are
 * `listing_id`, `creator_alias`, `performance_summary`, `risk_metrics`, `price_minor` /
 * `price_display` / `currency` and `condition_summaries` — the allow-list, not the raw row.
 * The `PAPER` and `LIVE` groups read the same `{summary, risk}` pair under their own
 * environment prefix; the projection carries no such figures today, which is exactly why
 * those two sections are omitted rather than zero-filled.
 *
 * EVERY COLOUR COMES FROM THE TOKEN LAYER (Requirements 1.1, 1.3, 1.5)
 * -------------------------------------------------------------------
 * This file used to name 182 colours of its own, all of them a hex sitting inside an
 * arbitrary-value Tailwind class — a background, a border or a text colour, over fourteen
 * distinct values. Task 26.1 replaced the six on the subscription indicator; the other
 * 176 go here, and the class names are deliberately not spelled out in this comment:
 * Tailwind v4's content scanner is a text scanner over the whole project, so a retired
 * class named in prose would keep materialising in the production stylesheet. Nothing in
 * this file names a colour now: every surface, line and content utility resolves through
 * `src/styles/tokens.css`, and the one element that paints a STATE — the subscription
 * badge — asks `ds/StatusBadge`, which asks `design/semantic.js`. The map applied, so a
 * reader can check it against `tokens.css` rather than re-deriving it:
 *
 *   #080A0D #08090c #0d1117  -> surface-canvas    #131722 -> surface-panel
 *   #1A222C                  -> surface-inset     #202938 -> line-default
 *   #00D4FF                  -> brand             #E6EDF3 #e2e8f0 #C9D1D9 -> content-primary
 *   #8B949E                  -> content-secondary #FFB74D -> status-warning
 *   #26A69A                  -> status-profit / status-live / status-connected
 *   #EF5350                  -> status-loss / status-error
 *
 * WHICH GREEN, AND WHY THREE NAMES FOR ONE HUE
 * -------------------------------------------
 * `tokens.css` gives `status.live`, `status.connected` and `status.profit` one green and
 * `status.loss` and `status.error` one red — two hue families, five names (see the KNOWN
 * TOKEN COLLISION note in `design/semantic.js`). The names are still worth choosing
 * correctly, because the name is what a reader checks: each site below is given the group
 * `statusToken` would return for the fact it marks, not the nearest-looking one.
 *
 *   * the signed return figures -> `profit` / `loss`, which is `pnlToken`'s pair;
 *   * the Subscribed button and the Subscribe action -> `live`, which is
 *     `statusToken('active')`'s group for the ACTIVE subscription they are about, and the
 *     same group the badge beside them renders;
 *   * the fallback notice's success arm -> `connected`, which is `statusToken('ok')`;
 *     its failure arm and the load-error banner -> `error`, `statusToken('error')`.
 *
 * FOUR THINGS THIS RETOKEN DID NOT DECIDE, RECORDED RATHER THAN QUIETLY CHANGED
 * ---------------------------------------------------------------------------
 *   1. #FFB74D has no token: `tokens.css` retires it into `--color-status-warning`
 *      (#F59E0B), so that is where all nine of its sites go. Exactly one of them is a
 *      genuine caution — the historical-results statement. The other eight are DECORATIVE
 *      uses of the warning hue, which Requirement 1.5 spends on state: the Featured chip,
 *      the two Sharpe figures, the three star ratings, the Featured-section heading glyph
 *      and the hero's Trending count. The hue is preserved rather than re-decided here.
 *      The hero's Featured count is the same finding in the profit green, which makes nine
 *      in all. The hero's third count is brand cyan, which is not a state hue and so is
 *      not one of them.
 *   2. The per-environment chip in `renderEnvironmentSection` is brand cyan for BACKTEST,
 *      PAPER and LIVE alike, where `design/semantic.js`'s `ENVIRONMENT` gives the three
 *      distinct treatments Requirement 12.3 asks for (`env.live` red, `env.paper` indigo,
 *      `env.backtest` grey, each with its own icon and border style). Routing it through
 *      `ds/TradingEnvironmentBadge` is a markup change, not a colour change, so it is not
 *      this task's; the hue is preserved and the divergence is on the record.
 *   3. The signed figures branch on `value >= 0`, so a flat 0.00% renders profit green.
 *      `pnlToken` calls zero NEUTRAL on purpose — "a position that has made nothing has
 *      not made a profit". The threshold is left exactly as it was; only the hue's source
 *      moved.
 *   4. #C9D1D9 (the description and review prose) has no token either. It sits between
 *      `content-primary` (#F0F2F5) and `content-secondary` (#8B95A5); it takes
 *      `content-primary`, because it is the Listing's own prose and the micro-labels
 *      around it are already `content-secondary`. `tokens.css`'s note moves DIM body text
 *      up to secondary, and this was never the dim end.
 *
 * The two `shadow-[0_0_20px_rgba(…)]` glows on the Subscribe / Clone action are gone
 * rather than migrated: Requirement 1.5 retires coloured glows and `tokens.css` declares
 * no coloured shadow to migrate them to. Both arms take `shadow-raised`, the same
 * elevation every other raised surface in the app reads.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Search, TrendingUp, Users, Star, ArrowRight, Shield, Activity, Cpu, Hexagon,
  AlertTriangle, ArrowLeft, Sparkles, Flame, Trophy, BarChart3, Info, Clock,
} from 'lucide-react';
import api from '../api';
import { StatusBadge } from '../components/ds/StatusBadge';
import { BADGE_SUBSCRIBED, resolveSubscriptionView } from '../design/subscriptionState';

// ─────────────────────────────────────────────────────────────────────────────
// Measurement honesty helpers (Requirements 28.1, 28.5)
// ─────────────────────────────────────────────────────────────────────────────

/** What an unmeasured figure looks like. Never `0`. */
const NOT_MEASURED = '—';

/**
 * `value` as a finite number, or `null` when the server sent no usable figure.
 *
 * Numeric strings are accepted because PostgREST serialises `NUMERIC` columns as strings;
 * `null`, `undefined`, `''`, `NaN` and `Infinity` all mean "not measured".
 */
const toFiniteNumber = (value) => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const formatPercent = (value) => `${value.toFixed(2)}%`;
const formatSignedPercent = (value) => `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
const formatRatio = (value) => value.toFixed(2);
const formatCount = (value) => String(Math.trunc(value));

/**
 * `GET /api/library/{id}/subscribe`'s answer for "there is no subscription row".
 *
 * It is a sentinel of that one response, not one of the seven `SubscriptionState` values, which
 * is why it is named here beside the read that receives it and not in
 * `design/subscriptionState.js`. See {@link loadSubscription} for what it maps to.
 */
const NO_SUBSCRIPTION_ROW = 'not_subscribed';

/** A date the server sent, or `null`. Never "today" as a stand-in. */
const formatDate = (value) => {
  if (typeof value !== 'string' || !value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString();
};

/**
 * A safe, client-facing sentence for a failed call.
 *
 * Prefers the structured envelope the Marketplace_API returns
 * (`{error: {code, message}}`, Requirement 22.9), then `ApiError.getUserMessage()`, then the
 * error's own message, then the caller's fallback. No stack trace is ever surfaced.
 */
const errorMessage = (err, fallback) => {
  const structured = err?.data?.error?.message;
  if (typeof structured === 'string' && structured.trim()) return structured;
  if (typeof err?.getUserMessage === 'function') {
    const userMessage = err.getUserMessage();
    if (typeof userMessage === 'string' && userMessage.trim()) return userMessage;
  }
  if (typeof err?.message === 'string' && err.message.trim()) return err.message;
  return fallback;
};

/** True when a credential is present, which decides authenticated vs `*Public` methods. */
const hasAuthToken = () => {
  try {
    return Boolean(sessionStorage.getItem('token'));
  } catch {
    return false;
  }
};

// ─────────────────────────────────────────────────────────────────────────────
// The three Execution_Environments (Requirement 6.6)
// ─────────────────────────────────────────────────────────────────────────────

const ENVIRONMENTS = ['BACKTEST', 'PAPER', 'LIVE'];

/**
 * Where each environment's figures live in the Listing response.
 *
 * `BACKTEST` reads the projection's `performance_summary` / `risk_metrics` — the publish-time
 * `backtest_*` aggregate, which is BACKTEST by construction. `PAPER` and `LIVE` read the same
 * two shapes under their own prefix. Nothing is inferred from one environment for another:
 * a group whose fields are absent produces no figures and therefore no section.
 */
const ENVIRONMENT_FIGURE_SOURCES = {
  BACKTEST: { summary: 'performance_summary', risk: 'risk_metrics' },
  PAPER: { summary: 'paper_performance_summary', risk: 'paper_risk_metrics' },
  LIVE: { summary: 'live_performance_summary', risk: 'live_risk_metrics' },
};

const ENVIRONMENT_DESCRIPTIONS = {
  BACKTEST: 'Simulated on historical data.',
  PAPER: 'Simulated execution on live market data. No real order was placed.',
  LIVE: 'Recorded from live execution.',
};

/** The historical-results statement Requirement 6.6 requires inside the BACKTEST section. */
const HISTORICAL_RESULTS_STATEMENT =
  'Past performance does not indicate future results. These figures were measured on '
  + 'historical data and do not guarantee how the strategy will behave in future.';

/**
 * The figures a section can carry, in display order.
 *
 * `group` names the response object the figure is read from, so one table serves all three
 * environments and no environment gets a bespoke read path.
 */
const FIGURE_DEFS = [
  { key: 'total_return_pct', group: 'summary', label: 'Total Return', format: formatSignedPercent, signed: true, icon: TrendingUp },
  { key: 'sharpe_ratio', group: 'risk', label: 'Sharpe Ratio', format: formatRatio, icon: Activity },
  { key: 'win_rate_pct', group: 'summary', label: 'Win Rate', format: formatPercent, icon: Shield },
  { key: 'max_drawdown_pct', group: 'risk', label: 'Max Drawdown', format: formatPercent, icon: AlertTriangle },
  { key: 'profit_factor', group: 'summary', label: 'Profit Factor', format: formatRatio, icon: BarChart3 },
  { key: 'total_trades', group: 'summary', label: 'Trades', format: formatCount, icon: Users },
];

/**
 * The measured figures one environment carries, or an empty array.
 *
 * An empty array is the signal to omit the whole section — the caller renders nothing rather
 * than a grid of zeros (Requirements 6.6, 28.5).
 */
const environmentFigures = (listing, environment) => {
  const sources = ENVIRONMENT_FIGURE_SOURCES[environment];
  const groups = {
    summary: listing?.[sources.summary],
    risk: listing?.[sources.risk],
  };
  const figures = [];
  for (const def of FIGURE_DEFS) {
    const group = groups[def.group];
    const raw = group && typeof group === 'object' ? group[def.key] : undefined;
    const value = toFiniteNumber(raw);
    if (value === null) continue;
    figures.push({ ...def, value });
  }
  return figures;
};

/** The per-condition summaries, outcome metrics only, or an empty array. */
const conditionSummaries = (listing) =>
  Array.isArray(listing?.condition_summaries) ? listing.condition_summaries : [];

/**
 * The Listing's price, decided from `price_minor` — the exact integer — and never from a
 * formatted string. `state` is `'paid'`, `'free'` or `'unknown'`; `'unknown'` disables the
 * action rather than offering a purchase at a price nobody read.
 */
const priceOf = (listing) => {
  const minor = toFiniteNumber(listing?.price_minor);
  const currency = typeof listing?.currency === 'string' ? listing.currency : null;
  const display = typeof listing?.price_display === 'string' ? listing.price_display : null;
  if (minor === null || !Number.isInteger(minor)) {
    return { state: 'unknown', text: 'Price unavailable', currency };
  }
  if (minor === 0) return { state: 'free', text: 'Free', currency };
  const text = display
    ? `${display}${currency ? ` ${currency}` : ''}`
    : `${minor} ${currency ? `${currency} ` : ''}minor units`;
  return { state: 'paid', text, currency };
};

const StrategyMarketplace = () => {
  const navigate = useNavigate();
  const [view, setView] = useState('browse');

  // Data states
  const [featuredStrategies, setFeaturedStrategies] = useState([]);
  const [trendingStrategies, setTrendingStrategies] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [categories, setCategories] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  // §7.9's resolved badge for the open Listing, or `null` when nothing has been read.
  // The resolution happens once, beside the read; nothing downstream re-derives it.
  const [subscriptionView, setSubscriptionView] = useState(null);

  // Pagination & Filter state
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState(null);
  const [sort, setSort] = useState('clones');

  // UI states
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [cloneLoading, setCloneLoading] = useState(false);
  const [subscribeLoading, setSubscribeLoading] = useState(false);

  /**
   * Report an outcome through the mechanism `AppShell` installs (Requirement 20.10).
   * Falls back to an inline banner when the page is mounted without that shell, so the
   * message is never lost — and never becomes an `alert`.
   */
  const notify = useCallback((type, message) => {
    if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
      window.showToast(type, message);
      return;
    }
    setNotice({ type, message });
  }, []);

  // Fetch Featured
  const fetchFeatured = useCallback(async () => {
    try {
      const data = hasAuthToken()
        ? await api.library.featured(3)
        : await api.library.featuredPublic(3);
      setFeaturedStrategies(Array.isArray(data?.items) ? data.items : []);
    } catch (err) {
      console.error('Failed to fetch featured:', err);
    }
  }, []);

  // Fetch Trending
  const fetchTrending = useCallback(async () => {
    try {
      const data = hasAuthToken()
        ? await api.library.trending(5)
        : await api.library.trendingPublic(5);
      setTrendingStrategies(Array.isArray(data?.items) ? data.items : []);
    } catch (err) {
      console.error('Failed to fetch trending:', err);
    }
  }, []);

  // Fetch Categories
  const fetchCategories = useCallback(async () => {
    try {
      const data = hasAuthToken()
        ? await api.library.categories()
        : await api.library.categoriesPublic();
      setCategories(Array.isArray(data?.categories) ? data.categories : []);
    } catch (err) {
      console.error('Failed to fetch categories:', err);
    }
  }, []);

  // Fetch Catalogue
  const fetchStrategies = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { page, limit: 20, sort };
      if (searchQuery) params.q = searchQuery;
      if (selectedCategory) params.category = selectedCategory;

      const data = hasAuthToken()
        ? await api.library.browse(params)
        : await api.library.browsePublic(params);
      setStrategies(Array.isArray(data?.items) ? data.items : []);
      const pages = toFiniteNumber(data?.pages);
      setTotalPages(pages !== null && pages >= 1 ? Math.trunc(pages) : 1);
    } catch (err) {
      console.error(err);
      setError('Failed to load marketplace data. Please try again.');
    } finally {
      setLoading(false);
    }
  }, [page, sort, searchQuery, selectedCategory]);

  // Initial load
  useEffect(() => {
    if (view === 'browse') {
      fetchFeatured();
      fetchTrending();
      fetchCategories();
      fetchStrategies();
    }
  }, [view, fetchFeatured, fetchTrending, fetchCategories, fetchStrategies]);

  /**
   * The caller's Subscription state for one Listing (Requirement 20.9's
   * subscription-status indication, and Requirement 13.1's badge). Authenticated-only — there
   * is nothing to report for an anonymous visitor — and a read that does not complete leaves
   * the indicator absent rather than claiming "not subscribed".
   *
   * THE ADAPTER, AND WHY IT IS HERE RATHER THAN IN `design/subscriptionState.js`
   * ---------------------------------------------------------------------------
   * `resolveSubscriptionView` reads §7.9's entry shape — `{subscription: {state,
   * period_expiry, renewal_state}, entitling, unavailable_reason}`, which is what
   * `library_entries.py::subscription_view` publishes. `GET /api/library/{id}/subscribe` is a
   * different response: `{status, subscription_id, library_id, started_at, expires_at,
   * message}`, where `status` is the raw lower-case `library_subscriptions.status` column, and
   * it carries no `subscription` object, no `entitling` and no `unavailable_reason`. So the two
   * shapes are reconciled here, at the one call site that knows which endpoint it read:
   *
   *   * `status` → `subscription.state`. The mapping normalises `'active'` and `'ACTIVE'` to
   *     the same state, so the column value goes across verbatim rather than being reshaped.
   *   * `expires_at` → `subscription.period_expiry`. Same fact, this endpoint's name for it.
   *   * `renewal_state` is `null`: this response does not carry it, and absent is not a value.
   *
   * `'not_subscribed'` IS A SENTINEL OF THIS ENDPOINT, NOT A SUBSCRIPTION STATE
   * -------------------------------------------------------------------------
   * The route answers `'not_subscribed'` when there is no `library_subscriptions` row at all,
   * and a body with no readable `status` is the same absence. Both must become
   * `subscription: null` — §7.9's **Available** row — because passing them through as a state
   * word would land on `{state: 'not_subscribed'}`, which is outside the seven and therefore
   * fails closed to **Expired**: a strategy nobody has subscribed to would be badged as though
   * a subscription had ended. `'unknown'` — what the route answers when the column itself is
   * missing — is deliberately NOT treated as absence: a row exists and its state cannot be
   * read, which is exactly the input the mapping fails closed on. A `status` that is present
   * but blank is treated the same way, for the same reason.
   *
   * Part 1 kept this sentinel out of `design/subscriptionState.js` on purpose: it belongs to
   * one endpoint's response shape, not to the state vocabulary the whole app shares.
   */
  const loadSubscription = useCallback(async (listingId) => {
    if (!hasAuthToken()) {
      setSubscriptionView(null);
      return;
    }
    try {
      const data = await api.library.subscriptionStatus(listingId);
      const status = typeof data?.status === 'string' ? data.status : null;
      const hasRow = status !== null && status.trim().toLowerCase() !== NO_SUBSCRIPTION_ROW;
      const row = hasRow
        ? { state: status, period_expiry: data.expires_at ?? null, renewal_state: null }
        : null;
      setSubscriptionView(resolveSubscriptionView({ subscription: row }));
    } catch (err) {
      console.error('Failed to read subscription status:', err);
      setSubscriptionView(null);
    }
  }, []);

  // Fetch Detail
  const loadDetail = useCallback(async (listingId) => {
    if (!listingId) return;
    setLoading(true);
    setError(null);
    setSubscriptionView(null);
    try {
      const data = hasAuthToken()
        ? await api.library.detail(listingId)
        : await api.library.detailPublic(listingId);
      setSelectedStrategy(data);
      setView('detail');
      loadSubscription(listingId);
    } catch (err) {
      console.error(err);
      setError('Failed to load strategy details.');
    } finally {
      setLoading(false);
    }
  }, [loadSubscription]);

  // Clone Flow
  const handleClone = async (strat) => {
    const listingId = strat?.listing_id;
    if (!listingId) return;
    setCloneLoading(true);
    try {
      const result = await api.library.clone(listingId);
      const message = typeof result?.message === 'string' && result.message
        ? result.message
        : 'Strategy cloned into your builder.';
      notify('success', message);
      navigate('/app/builder');
    } catch (err) {
      console.error(err);
      notify('error', errorMessage(err, 'Failed to clone strategy.'));
    } finally {
      setCloneLoading(false);
    }
  };

  // Subscribe Flow
  const handleSubscribe = async (strat) => {
    const listingId = strat?.listing_id;
    if (!listingId) return;
    const { currency } = priceOf(strat);
    setSubscribeLoading(true);
    try {
      const session = await api.library.checkout(listingId, currency || 'USD');
      if (typeof session?.checkout_url === 'string' && session.checkout_url) {
        // The provider's hosted checkout. The URL comes from the server; the page composes none.
        window.location.href = session.checkout_url;
        return;
      }
      const provider = typeof session?.provider === 'string' && session.provider
        ? session.provider
        : 'the payment provider';
      notify(
        'error',
        `A payment session was opened with ${provider}, but this browser cannot present its `
        + 'checkout. Nothing has been charged.',
      );
    } catch (err) {
      console.error(err);
      notify('error', errorMessage(err, 'Failed to create checkout session.'));
    } finally {
      setSubscribeLoading(false);
    }
  };

  // ───────────────────────────────────────────────────────────────────────────
  // Performance sections, one per Execution_Environment (Requirement 6.6)
  // ───────────────────────────────────────────────────────────────────────────

  const renderEnvironmentSection = (listing, environment) => {
    const figures = environmentFigures(listing, environment);
    // Omitted entirely — not a section of zeros — when the Listing carries no figures.
    if (figures.length === 0) return null;

    const conditions = environment === 'BACKTEST' ? conditionSummaries(listing) : [];
    const conditionCount = toFiniteNumber(listing?.condition_count);

    return (
      <section
        key={environment}
        data-environment={environment}
        className="bg-surface-canvas border border-line-default rounded-xl p-5 flex flex-col gap-4"
      >
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line-default pb-3">
          <div className="flex items-center gap-3">
            {/* Brand cyan for all three environments — see finding 2 in the header. The
                distinct per-environment treatment lives in `design/semantic.js` and is
                reached through `ds/TradingEnvironmentBadge`, which is a markup change. */}
            <span className="bg-surface-panel border border-brand text-brand text-[10px] font-bold font-mono uppercase tracking-widest px-3 py-1 rounded">
              {environment}
            </span>
            <h3 className="text-content-primary font-bold font-mono text-sm uppercase tracking-wider">
              {environment === 'BACKTEST' ? 'Backtest Performance' : `${environment} Performance`}
            </h3>
          </div>
          <span className="text-content-secondary text-[11px] font-mono">
            {ENVIRONMENT_DESCRIPTIONS[environment]}
          </span>
        </header>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {figures.map((figure) => {
            const Icon = figure.icon;
            const tone = figure.signed
              ? (figure.value >= 0 ? 'text-status-profit' : 'text-status-loss')
              : 'text-content-primary';
            return (
              <div
                key={figure.key}
                className="bg-surface-panel border border-line-default rounded-lg p-3 flex flex-col gap-1"
              >
                <span className="text-content-secondary text-[9px] font-mono uppercase tracking-widest flex items-center gap-1">
                  <Icon size={11} /> {figure.label}
                </span>
                <span className={`text-lg font-bold font-mono ${tone}`}>
                  {figure.format(figure.value)}
                </span>
                {/* Every figure carries its Execution_Environment label (Requirement 6.6). */}
                <span className="text-content-secondary text-[9px] font-mono uppercase tracking-widest">
                  {environment}
                </span>
              </div>
            );
          })}
        </div>

        {conditions.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="text-content-secondary text-[10px] font-mono uppercase tracking-widest">
              Per-condition results
              {conditionCount !== null ? ` (${formatCount(conditionCount)} conditions)` : ''}
            </div>
            <div className="flex flex-col gap-2">
              {conditions.map((condition, index) => {
                const label = typeof condition?.label === 'string' && condition.label
                  ? condition.label
                  : `Condition ${index + 1}`;
                const metrics = FIGURE_DEFS
                  .map((def) => ({ def, value: toFiniteNumber(condition?.[def.key]) }))
                  .filter((entry) => entry.value !== null);
                return (
                  <div
                    key={label}
                    className="bg-surface-panel border border-line-default rounded-lg px-3 py-2 flex flex-wrap items-center gap-x-5 gap-y-1"
                  >
                    <span className="text-content-primary font-mono text-xs font-bold min-w-[96px]">
                      {label}
                    </span>
                    {metrics.length === 0 ? (
                      <span className="text-content-secondary font-mono text-[11px]">
                        No figures recorded for this condition.
                      </span>
                    ) : (
                      metrics.map(({ def, value }) => (
                        <span key={def.key} className="font-mono text-[11px] text-content-secondary">
                          {def.label}{' '}
                          <span className="text-content-primary font-bold">{def.format(value)}</span>
                        </span>
                      ))
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {environment === 'BACKTEST' && (
          <p
            data-testid="historical-results-statement"
            className="text-status-warning text-[11px] font-mono leading-relaxed flex items-start gap-2 border-t border-line-default pt-3"
          >
            <Info size={14} className="mt-0.5 shrink-0" />
            {HISTORICAL_RESULTS_STATEMENT}
          </p>
        )}
      </section>
    );
  };

  const renderPerformanceSections = (listing) => {
    const sections = ENVIRONMENTS
      .map((environment) => renderEnvironmentSection(listing, environment))
      .filter(Boolean);
    if (sections.length === 0) {
      return (
        <div className="bg-surface-canvas border border-dashed border-line-default rounded-xl p-5 text-content-secondary font-mono text-xs">
          No performance figures have been recorded for this listing.
        </div>
      );
    }
    return <div className="flex flex-col gap-5">{sections}</div>;
  };

  // ───────────────────────────────────────────────────────────────────────────
  // Cards
  // ───────────────────────────────────────────────────────────────────────────

  const renderFeaturedCard = (strat) => {
    const price = priceOf(strat);
    const totalReturn = toFiniteNumber(strat?.performance_summary?.total_return_pct);
    const sharpe = toFiniteNumber(strat?.risk_metrics?.sharpe_ratio);
    const subscribers = toFiniteNumber(strat?.subscriber_count);
    return (
      <div
        key={strat.listing_id}
        onClick={() => loadDetail(strat.listing_id)}
        className="relative bg-gradient-to-br from-surface-panel to-surface-canvas border border-brand/30 rounded-2xl p-6 cursor-pointer group hover:border-brand transition-all overflow-hidden"
      >
        <div className="absolute top-0 right-0 bg-gradient-to-l from-brand to-transparent w-32 h-32 opacity-10 group-hover:opacity-20 transition-opacity" />
        <div className="absolute top-3 right-3 bg-status-warning text-content-inverse text-[10px] font-bold px-3 py-1 rounded-full font-mono uppercase flex items-center gap-1">
          <Sparkles size={12} /> Featured
        </div>
        <div className="relative z-10">
          <div className="flex items-start gap-4">
            <div className="w-16 h-16 rounded-xl bg-surface-canvas border border-line-default flex items-center justify-center">
              <Cpu className="text-brand" size={32} />
            </div>
            <div className="flex-1">
              <h3 className="font-bold text-xl text-content-primary group-hover:text-brand transition-colors">{strat.name}</h3>
              {strat.creator_alias && (
                <div className="text-content-secondary text-xs font-mono mt-1">
                  by <span className="text-brand font-bold">{strat.creator_alias}</span>
                </div>
              )}
            </div>
          </div>
          <div className="grid grid-cols-4 gap-3 mt-6">
            <div className="bg-surface-canvas p-3 rounded-lg border border-line-default">
              <div className="text-content-secondary text-[9px] font-mono uppercase">Sharpe · BACKTEST</div>
              <div className="text-status-warning font-bold font-mono text-lg">
                {sharpe === null ? NOT_MEASURED : formatRatio(sharpe)}
              </div>
            </div>
            <div className="bg-surface-canvas p-3 rounded-lg border border-line-default">
              <div className="text-content-secondary text-[9px] font-mono uppercase">Return · BACKTEST</div>
              <div
                className={`font-bold font-mono text-lg ${
                  totalReturn === null
                    ? 'text-content-secondary'
                    : (totalReturn >= 0 ? 'text-status-profit' : 'text-status-loss')
                }`}
              >
                {totalReturn === null ? NOT_MEASURED : formatSignedPercent(totalReturn)}
              </div>
            </div>
            <div className="bg-surface-canvas p-3 rounded-lg border border-line-default">
              <div className="text-content-secondary text-[9px] font-mono uppercase">Subs</div>
              <div className="text-content-primary font-bold font-mono text-lg">
                {subscribers === null ? NOT_MEASURED : formatCount(subscribers)}
              </div>
            </div>
            <div className="bg-surface-canvas p-3 rounded-lg border border-line-default">
              <div className="text-content-secondary text-[9px] font-mono uppercase">Price</div>
              <div className="text-brand font-bold font-mono text-lg">{price.text}</div>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const renderCard = (strat) => {
    const price = priceOf(strat);
    const totalReturn = toFiniteNumber(strat?.performance_summary?.total_return_pct);
    const sharpe = toFiniteNumber(strat?.risk_metrics?.sharpe_ratio);
    const subscribers = toFiniteNumber(strat?.subscriber_count);
    const rating = toFiniteNumber(strat?.avg_rating);
    return (
      <div
        key={strat.listing_id}
        onClick={() => loadDetail(strat.listing_id)}
        className="bg-surface-panel border border-line-default rounded-xl p-5 cursor-pointer group hover:border-brand/50 transition-all relative overflow-hidden"
      >
        <div className="flex justify-between items-start mb-4">
          <div>
            <h3 className="font-bold text-content-primary group-hover:text-brand transition-colors">{strat.name}</h3>
            {strat.creator_alias && (
              <div className="text-content-secondary text-xs font-mono mt-1">
                by <span className="text-brand font-bold">{strat.creator_alias}</span>
              </div>
            )}
          </div>
          {strat.category && (
            <span className="bg-surface-canvas border border-line-default px-2 py-1 rounded text-[10px] font-mono text-content-secondary uppercase">
              {strat.category}
            </span>
          )}
        </div>

        <div className="grid grid-cols-3 gap-2 py-4 border-y border-line-default">
          <div className="flex flex-col gap-1">
            <span className="text-content-secondary text-[9px] font-mono uppercase flex items-center gap-1"><Users size={10} /> Subs</span>
            <span className="text-content-primary font-bold font-mono">
              {subscribers === null ? NOT_MEASURED : formatCount(subscribers)}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-content-secondary text-[9px] font-mono uppercase flex items-center gap-1"><TrendingUp size={10} /> Return · BACKTEST</span>
            <span
              className={`font-bold font-mono ${
                totalReturn === null
                  ? 'text-content-secondary'
                  : (totalReturn >= 0 ? 'text-status-profit' : 'text-status-loss')
              }`}
            >
              {totalReturn === null ? NOT_MEASURED : formatSignedPercent(totalReturn)}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-content-secondary text-[9px] font-mono uppercase flex items-center gap-1"><Activity size={10} /> Sharpe · BACKTEST</span>
            <span className="text-status-warning font-bold font-mono">
              {sharpe === null ? NOT_MEASURED : formatRatio(sharpe)}
            </span>
          </div>
        </div>

        <div className="flex justify-between items-center mt-4">
          {rating === null ? (
            <span className="text-[10px] font-mono text-content-secondary">No rating recorded</span>
          ) : (
            <span className="text-[10px] font-mono font-bold flex items-center gap-1 text-status-warning">
              <Star size={10} fill="currentColor" /> {rating.toFixed(1)}
            </span>
          )}
          <span className="text-brand font-bold font-mono">
            {price.state === 'paid' ? `${price.text}/mo` : price.text}
          </span>
        </div>
      </div>
    );
  };

  // ───────────────────────────────────────────────────────────────────────────
  // Detail
  // ───────────────────────────────────────────────────────────────────────────

  /**
   * §7.9's badge — one of four words, for every shape the status read can return.
   *
   * WHAT THIS USED TO RENDER
   * -----------------------
   * The raw `library_subscriptions.status` column, uppercased by CSS, beside a date labelled
   * "Renews / expires". A trader with a failed card read `PAYMENT_FAILED`, one whose settlement
   * had been reversed read `REFUNDED`, and both dates claimed to be a renewal and an expiry at
   * once. Worse, the whole element was skipped whenever `status` was not a string — so the one
   * response that most needs explaining, a row whose state this build cannot read, showed
   * nothing at all. `resolveSubscriptionView` is total, so there is now always exactly one
   * badge: `label` is the declared word and never the server's, and the date appears only where
   * §7.9 says it is a fact worth stating.
   *
   * `showsExpiry` is true for `CANCELLED` alone, and it is not a renewal date: the backend keeps
   * the entitlement alive to the unchanged period end (backend Requirement 11.9), so this is the
   * last day the strategy runs. It is worded as that and nothing else. A `CANCELLED` row that
   * carries no date renders the badge without one rather than inventing a day.
   *
   * The hue is not named here. `tokenState` is a semantic state and `ds/StatusBadge` resolves it
   * through `design/semantic.js`, the one module allowed to turn a state into a colour
   * (Requirement 1.4) — which is also why the badge takes no colour prop to give it.
   *
   * Absent when nothing has been read: an anonymous visitor, a read still in flight, or a read
   * that failed. Rendering **Available** there would be a claim that this trader holds no
   * subscription, which is precisely what a failed read did not establish.
   */
  const renderSubscriptionIndicator = () => {
    if (!subscriptionView) return null;
    const expires = subscriptionView.showsExpiry ? formatDate(subscriptionView.periodExpiry) : null;
    return (
      <div className="bg-surface-canvas border border-line-default rounded-xl px-4 py-3 flex flex-wrap items-center gap-3 font-mono text-xs">
        <Clock size={14} className="text-brand" />
        <span className="text-content-secondary uppercase tracking-widest text-[10px]">Your subscription</span>
        <StatusBadge state={subscriptionView.tokenState} label={subscriptionView.label} size="md" />
        {expires && <span className="text-content-secondary">Access ends {expires}</span>}
      </div>
    );
  };

  const renderDetail = () => {
    if (!selectedStrategy) return null;
    const strat = selectedStrategy;
    const price = priceOf(strat);
    const subscriberCount = toFiniteNumber(strat.subscriber_count);
    const rating = toFiniteNumber(strat.avg_rating);
    const ratingCount = toFiniteNumber(strat.rating_count);
    const publishedAt = formatDate(strat.published_at);
    /*
     * Requirement 13.1. This was `subscription?.status === 'active'` — the frontend deciding
     * entitlement by comparing one of seven server words against a string literal, which got
     * `CANCELLED` wrong in the direction that costs money: a trader who has stopped the next
     * charge is still entitled to the unchanged period end, and was being offered a second
     * purchase of a subscription they already hold. The badge is the whole answer now, and it
     * says **Subscribed** for `ACTIVE` and `CANCELLED` alike.
     *
     * `view.badge`, not `view.entitling`: this endpoint carries no `entitling` field at all
     * (the resolver's verdict rides on §7.9's `browse()` / `myStrategies()` entry, not on
     * `GET /api/library/{id}/subscribe`), so reading it here would evaluate a field that is
     * always absent — false for every subscriber, including an `ACTIVE` one.
     */
    const isSubscribed = subscriptionView?.badge === BADGE_SUBSCRIBED;
    const reviews = Array.isArray(strat.recent_ratings) ? strat.recent_ratings : [];

    return (
      <div className="flex flex-col gap-6">
        <button onClick={() => setView('browse')} className="flex items-center gap-2 text-content-secondary hover:text-content-primary transition-colors w-max font-mono text-sm">
          <ArrowLeft size={16} /> Back to Marketplace
        </button>

        <div className="bg-surface-panel border border-line-default rounded-2xl p-8 shadow-lg flex flex-col gap-8">
          {/* Header */}
          <div className="flex justify-between items-start gap-6 flex-wrap">
            <div className="flex-1 min-w-[260px]">
              <div className="flex items-center gap-3 mb-3 flex-wrap">
                {strat.category && (
                  <span className="bg-surface-canvas border border-line-default px-3 py-1 rounded text-[10px] font-mono text-content-secondary uppercase">
                    {strat.category}
                  </span>
                )}
                {strat.difficulty && (
                  <span className="bg-surface-canvas border border-line-default px-3 py-1 rounded text-[10px] font-mono text-content-secondary uppercase">
                    {strat.difficulty}
                  </span>
                )}
                {strat.validation_status && (
                  <span className="bg-surface-canvas border border-line-default px-3 py-1 rounded text-[10px] font-mono text-content-secondary uppercase">
                    {strat.validation_status}
                  </span>
                )}
              </div>
              <h1 className="text-4xl font-black tracking-tight text-content-primary mb-2">{strat.name}</h1>
              {strat.creator_alias && (
                <div className="text-content-secondary font-mono text-sm">
                  Created by <span className="text-brand font-bold">{strat.creator_alias}</span>
                </div>
              )}
            </div>

            {/* Pricing section + subscribe action (Requirement 20.9) */}
            <div className="flex flex-col gap-3 items-end">
              <div className="text-right">
                <div className="text-content-secondary text-xs font-mono uppercase mb-1">
                  Monthly Price
                </div>
                <div className="text-3xl font-black text-brand font-mono">{price.text}</div>
                {price.state === 'paid' && (
                  <div className="text-content-secondary text-[10px] font-mono mt-1">
                    Billed each calendar month.
                  </div>
                )}
              </div>
              {price.state === 'unknown' ? (
                <button
                  type="button"
                  disabled
                  className="px-8 py-3 rounded-xl text-sm font-bold font-mono bg-surface-inset text-content-secondary cursor-not-allowed"
                >
                  Price unavailable
                </button>
              ) : isSubscribed ? (
                <button
                  type="button"
                  disabled
                  className="px-8 py-3 rounded-xl text-sm font-bold font-mono bg-surface-inset text-status-live cursor-not-allowed"
                >
                  Subscribed
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => (price.state === 'paid' ? handleSubscribe(strat) : handleClone(strat))}
                  disabled={cloneLoading || subscribeLoading}
                  className={`px-8 py-3 rounded-xl text-sm font-bold font-mono flex items-center gap-2 transition-colors shadow-raised disabled:opacity-50 ${
                    price.state === 'paid'
                      ? 'bg-status-live hover:bg-status-live/90 text-white'
                      : 'bg-brand hover:bg-brand/90 text-white'
                  }`}
                >
                  {price.state === 'paid' ? 'Subscribe' : 'Clone Free'} <ArrowRight size={16} />
                </button>
              )}
            </div>
          </div>

          {renderSubscriptionIndicator()}

          {/* Owner-supplied description, rendered as a text child */}
          <div className="text-content-primary leading-relaxed max-w-4xl text-sm whitespace-pre-line">
            {strat.description || 'No description provided.'}
          </div>

          {/* BACKTEST / PAPER / LIVE, visually separated, empty ones omitted */}
          {renderPerformanceSections(strat)}

          {/* Owner-supplied tags, rendered as text children */}
          {Array.isArray(strat.tags) && strat.tags.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {strat.tags.map((tag) => (
                <span key={String(tag)} className="text-[10px] border border-line-default bg-surface-inset text-content-secondary px-3 py-1 rounded-full font-mono uppercase">
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Stats */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-6 border-t border-line-default">
            <div className="flex flex-col gap-1">
              <span className="text-content-secondary text-[10px] font-mono uppercase">Subscribers</span>
              <span className="text-xl font-bold font-mono text-content-primary">
                {subscriberCount === null ? NOT_MEASURED : formatCount(subscriberCount)}
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-content-secondary text-[10px] font-mono uppercase">Rating</span>
              {rating === null ? (
                <span className="text-xl font-bold font-mono text-content-secondary">{NOT_MEASURED}</span>
              ) : (
                <span className="text-xl font-bold font-mono text-status-warning flex items-center gap-2">
                  <Star size={16} fill="currentColor" /> {rating.toFixed(1)}
                  {ratingCount !== null && (
                    <span className="text-content-secondary font-normal text-sm">({formatCount(ratingCount)})</span>
                  )}
                </span>
              )}
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-content-secondary text-[10px] font-mono uppercase">Published</span>
              <span className="text-xl font-bold font-mono text-content-primary">
                {publishedAt || NOT_MEASURED}
              </span>
            </div>
          </div>

          {/* Reviews — owner/subscriber supplied text, rendered as text children */}
          {reviews.length > 0 && (
            <div className="flex flex-col gap-3 pt-6 border-t border-line-default">
              <div className="text-content-secondary text-[10px] font-mono uppercase tracking-widest">
                Recent reviews
              </div>
              {reviews.map((review, index) => {
                const reviewRating = toFiniteNumber(review?.rating);
                const reviewedAt = formatDate(review?.created_at);
                return (
                  <div
                    key={`${review?.created_at || 'review'}-${index}`}
                    className="bg-surface-canvas border border-line-default rounded-xl p-4 flex flex-col gap-2"
                  >
                    <div className="flex items-center gap-3 font-mono text-[11px]">
                      {reviewRating !== null && (
                        <span className="text-status-warning font-bold flex items-center gap-1">
                          <Star size={11} fill="currentColor" /> {reviewRating.toFixed(1)}
                        </span>
                      )}
                      {reviewedAt && <span className="text-content-secondary">{reviewedAt}</span>}
                    </div>
                    {review?.review_text && (
                      <p className="text-content-primary text-sm leading-relaxed whitespace-pre-line">
                        {review.review_text}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="flex-1 overflow-y-auto bg-surface-canvas text-content-primary font-['Inter']">
      <div className="max-w-7xl mx-auto p-6 flex flex-col gap-8">

        {/* Hero Section */}
        <div className="relative bg-gradient-to-br from-surface-panel to-surface-canvas border border-line-default rounded-2xl p-8 overflow-hidden">
          <div className="absolute top-0 right-0 w-96 h-96 bg-brand/5 rounded-full blur-3xl" />
          <div className="relative z-10">
            <div className="flex items-center gap-3 mb-4">
              <Hexagon className="text-brand" size={32} />
              <h1 className="text-4xl font-black tracking-tight text-content-primary">Strategy Marketplace</h1>
            </div>
            <p className="text-content-secondary font-mono text-sm max-w-2xl mb-6">
              Browse published strategies, review the recorded results behind each one, and
              subscribe to run them without ever holding their logic.
            </p>
            <div className="flex gap-4 flex-wrap">
              <div className="flex items-center gap-2 text-status-live font-mono text-sm">
                <Trophy size={16} /> {featuredStrategies.length} Featured
              </div>
              <div className="flex items-center gap-2 text-status-warning font-mono text-sm">
                <Flame size={16} /> {trendingStrategies.length} Trending
              </div>
              <div className="flex items-center gap-2 text-brand font-mono text-sm">
                <Users size={16} /> {strategies.length} Strategies
              </div>
            </div>
          </div>
        </div>

        {error && (
          <div className="bg-status-error/20 border border-status-error text-status-error p-4 rounded-lg flex items-center gap-3 font-mono text-sm">
            <AlertTriangle size={18} /> {error}
          </div>
        )}

        {/* Fallback outcome banner — used only when no toast host is mounted */}
        {notice && (
          <div
            className={`p-4 rounded-lg flex items-center justify-between gap-3 font-mono text-sm border ${
              notice.type === 'error'
                ? 'bg-status-error/20 border-status-error text-status-error'
                : 'bg-status-connected/20 border-status-connected text-status-connected'
            }`}
          >
            <span>{notice.message}</span>
            <button
              type="button"
              onClick={() => setNotice(null)}
              className="text-content-secondary hover:text-content-primary text-xs uppercase"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Search & Filter Bar */}
        <div className="bg-surface-panel border border-line-default rounded-xl p-4 flex flex-wrap gap-4 items-center">
          <div className="flex items-center gap-2 px-4 bg-surface-canvas border border-line-default rounded-lg flex-1 min-w-[250px]">
            <Search size={16} className="text-content-secondary" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && fetchStrategies()}
              placeholder="Search strategies..."
              aria-label="Search strategies"
              className="bg-transparent border-none outline-none text-sm p-2 w-full font-mono text-content-primary placeholder-content-secondary"
            />
          </div>

          {/* Category Pills */}
          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => setSelectedCategory(null)}
              className={`px-3 py-1.5 rounded-full text-xs font-mono transition-colors ${
                selectedCategory === null
                  ? 'bg-brand text-white'
                  : 'bg-surface-canvas border border-line-default text-content-secondary hover:text-content-primary'
              }`}
            >
              All
            </button>
            {categories.slice(0, 6).map((cat) => {
              const count = toFiniteNumber(cat?.count);
              return (
                <button
                  type="button"
                  key={cat.name}
                  onClick={() => setSelectedCategory(cat.name)}
                  className={`px-3 py-1.5 rounded-full text-xs font-mono transition-colors ${
                    selectedCategory === cat.name
                      ? 'bg-brand text-white'
                      : 'bg-surface-canvas border border-line-default text-content-secondary hover:text-content-primary'
                  }`}
                >
                  {cat.name}{count === null ? '' : ` (${formatCount(count)})`}
                </button>
              );
            })}
          </div>

          <select
            className="bg-surface-canvas border border-line-default text-content-secondary font-mono text-xs rounded-lg px-4 py-2 outline-none"
            value={sort}
            aria-label="Sort listings"
            onChange={(e) => { setSort(e.target.value); setPage(1); }}
          >
            <option value="clones">Most Popular</option>
            <option value="rating">Highest Rated</option>
            <option value="sharpe">Highest Sharpe</option>
            <option value="return">Highest Return</option>
            <option value="newest">Newest</option>
          </select>
        </div>

        {/* View Router */}
        {view === 'detail' && renderDetail()}

        {view === 'browse' && (
          <>
            {/* Featured Section */}
            {featuredStrategies.length > 0 && (
              <div className="flex flex-col gap-4">
                <div className="flex items-center gap-2">
                  <Sparkles className="text-status-warning" size={20} />
                  <h2 className="text-xl font-bold font-mono">Featured Strategies</h2>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  {featuredStrategies.map(renderFeaturedCard)}
                </div>
              </div>
            )}

            {/* Strategy Grid */}
            {loading ? (
              <div className="flex justify-center items-center py-20 text-content-secondary font-mono">
                <Activity className="animate-spin mr-2" size={20} /> Loading Marketplace...
              </div>
            ) : strategies.length === 0 ? (
              <div className="flex flex-col justify-center items-center py-20 text-content-secondary font-mono gap-4 border border-dashed border-line-default rounded-xl">
                <Search size={48} className="opacity-20" />
                No strategies found matching your criteria.
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                  {strategies.map(renderCard)}
                </div>

                {/* Pagination */}
                {totalPages > 1 && (
                  <div className="flex justify-center items-center gap-4 mt-8 font-mono text-sm">
                    <button
                      type="button"
                      disabled={page === 1}
                      onClick={() => { setPage((p) => p - 1); }}
                      className="px-4 py-2 bg-surface-panel border border-line-default rounded-lg hover:border-content-secondary disabled:opacity-50"
                    >
                      Previous
                    </button>
                    <span className="text-content-secondary">Page {page} of {totalPages}</span>
                    <button
                      type="button"
                      disabled={page === totalPages}
                      onClick={() => { setPage((p) => p + 1); }}
                      className="px-4 py-2 bg-surface-panel border border-line-default rounded-lg hover:border-content-secondary disabled:opacity-50"
                    >
                      Next
                    </button>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default StrategyMarketplace;
