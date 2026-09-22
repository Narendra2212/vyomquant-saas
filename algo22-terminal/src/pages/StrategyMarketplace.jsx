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
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE VISUAL GENERATION (retail-ui-simplification task 5.2, Requirements 3, 10)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The colour retoken above left the page's TYPOGRAPHY and its DECORATION where the
 * previous generation put them, and said so. This is that half. Four axes, and the rule
 * over all of them is that a removal names its replacement — this page is the product's
 * shopfront, and a shopfront that is only subtracted from stops selling anything.
 *
 * AXIS 1 — 30 arbitrary pixel text sizes → the seven declared steps
 * ----------------------------------------------------------------
 * Every one was a device-pixel value, so a trader who raises their browser's default
 * font size moved nothing on this page. They are gone, by role rather than by size:
 * chips and figure labels to `--text-micro`, table-body figures to `--text-small`, and
 * the four SENTENCES up to `--text-body` — the environment description, the
 * per-condition metric rows, the empty-condition account, and the historical-results
 * statement, which was set 2px BELOW the page default while saying that past figures do
 * not predict future ones. `absolute-font-sizes.budget.js`'s entry for this file is
 * deleted in the same commit rather than lowered to zero (Requirement 1.5).
 *
 * AXIS 2 — 69 monospace and 25 all-caps usages, classified (Requirement 3.2)
 * -------------------------------------------------------------------------
 * The classification is the record that clause asks for, and it is here rather than in a
 * 94-row table because a per-call-site table is a second design system in table form:
 *
 *   KEPT, as values: every figure inside a `ds/Metric` — the primitive sets monospace
 *   with tabular numerals for its five numeric formats, so a column of returns still
 *   has its decimal points in a line. Kept as identifiers: the category, difficulty and
 *   validation words (server-supplied literals), and the environment word, which is
 *   `LIVE`/`PAPER`/`BACKTEST` as the server spells it and is now rendered by
 *   `ds/TradingEnvironmentBadge` on every surface instead of by a hand-built chip.
 *
 *   CONVERTED, as prose: the hero paragraph and its three count captions, the
 *   environment descriptions, the per-condition rows and their empty-condition account,
 *   the historical-results statement, the review text, the outcome banner and the load
 *   failure line. A monospace paragraph is the single strongest signal on a page that it
 *   belongs to the previous generation.
 *
 *   ALL-CAPS: dropped from the three section NAMES — the subscription eyebrow, the
 *   per-condition heading and the reviews heading — which gain word-shape recognition
 *   and lose nothing. Kept on single-word chips and on the server's own environment
 *   literal. `ds/Metric` applies it to its own labels, which are two or three words.
 *
 * AXIS 3 — the decoration the token layer does not declare (Decision D5)
 * ---------------------------------------------------------------------
 * `tokens.css`'s elevation block says it in its own comment: *no coloured glows. Calm by
 * default.* Three gradient fills, three 16px radii and one blurred hero wash are gone,
 * and each removal names what took its place:
 *
 *   * the featured card's gradient → `ds/Panel`'s flat `--color-surface-panel`, with
 *     `--color-line-strong` in place of the semi-transparent brand border and
 *     `--shadow-raised` against the ordinary card's `--shadow-panel`. The elevation
 *     carries the emphasis the gradient carried.
 *   * the 128px corner wash and the hero's blurred glow → nothing. They are the two
 *     elements deleted outright, because they carried no information at any opacity.
 *     The hero keeps its weight from `ds/PageHeader`'s `--text-page` title.
 *   * the three 16px radii → `--radius-lg`, which is what `ds/Panel` renders. The
 *     radius scale stops at `--radius-xl` (12px); the full-round pill radius is
 *     `--radius-full` and is untouched.
 *
 * AXIS 4 — "featured" and "trending" stay distinguishable (Decision D6)
 * --------------------------------------------------------------------
 * This one is not a styling decision, and the backend settles it.
 * `GET /api/library/featured` (`library.py:674`) selects `is_featured` — an operator flag
 * set through the moderate route — ordered by `published_at`. **Featured is editorial and
 * is derived from no figure.** `GET /api/library/trending` (`:759`) orders by
 * `clone_count` desc then `avg_rating` desc. **Trending has an exact, statable basis.**
 *
 * The old treatment said the opposite of both: a gold pill in the hue
 * `design/semantic.js` reserves for a state a trader should act on, carrying a sparkle
 * glyph beside a Sharpe ratio — a claim about quality that the server does not make. It
 * is replaced by three declared means, in this order: the section heading STATES the
 * basis (once per section rather than implied once per card); elevation and border carry
 * the distinction, so it is a shape rather than a hue; and the per-card marker survives
 * as a `ds/StatusBadge` reading *Selected* in the neutral group, because the server does
 * draw this distinction and dropping it silently would be its own defect.
 *
 * The trending listings are now RENDERED. `api.library.trending(5)` has always been
 * called on every browse load and its five listings were used for one number in the
 * hero; the section that states their basis is where they belong. No request, path or
 * query parameter changed — `api-paths.budget.js` is untouched.
 *
 * WHAT IS DELIBERATELY UNCHANGED HERE
 * -----------------------------------
 * The read path. The `loading`/`error` pair, the single failure string and the
 * hand-styled banner it renders in are task 6.1's, which lands on its own because it is
 * the one Marketplace change with a behavioural surface. `design/subscriptionState.js` is
 * NOT reimplemented (Requirement 10.6): the badge still resolves through it, unchanged.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Search, Users, ArrowRight, Activity, Cpu,
  AlertTriangle, ArrowLeft, Flame, Trophy, Info, Clock,
} from 'lucide-react';
import api from '../api';
import { CommandButton } from '../components/ds/CommandButton';
import { Metric, formatFigure } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { SectionHeader } from '../components/ds/SectionHeader';
import { StatusBadge } from '../components/ds/StatusBadge';
import { TradingEnvironmentBadge } from '../components/ds/TradingEnvironmentBadge';
import { BADGE_SUBSCRIBED, resolveSubscriptionView } from '../design/subscriptionState';

// ─────────────────────────────────────────────────────────────────────────────
// Measurement honesty helpers (Requirements 28.1, 28.5)
// ─────────────────────────────────────────────────────────────────────────────

/*
 * The page's own em-dash marker was declared here — `const NOT_MEASURED = '—'` — and
 * rendered at eleven call sites. It is gone, and not because the marker is: every figure
 * on this page now renders through `ds/Metric`, whose `NotAvailableMarker` IS that marker,
 * with two things this constant could not carry. It names itself to a screen reader
 * ("Rating: not available") and it states WHY the figure is absent, from the
 * `unavailableReason` each call site passes. A bare dash says only that something is
 * missing; Requirement 19.3's whole point is that the reason is the load-bearing half.
 * `ds/Metric`'s docblock asks for exactly one marker in the app, and this is what removing
 * the second one looks like.
 */

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

const formatCount = (value) => String(Math.trunc(value));

/**
 * The semantic state a SIGNED figure reports, or nothing at all.
 *
 * `undefined` for a flat figure, which is `ds/Metric`'s "no state, no colour" arm and
 * `design/semantic.js`'s own rule: a position that has made nothing has not made a
 * profit. The three page-local sites that used to branch on `value >= 0` — and therefore
 * painted a flat `0.00%` in profit green — all resolve through here now.
 */
const signedState = (value) => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value === 0) return undefined;
  return value > 0 ? 'profit' : 'loss';
};

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
 * What the two curated sections ARE, stated rather than implied (Requirement 10.5).
 *
 * Both sentences are read off the routes that produce the lists, not composed to sound
 * good: `GET /api/library/featured` selects the operator-set `is_featured` flag and orders
 * by publication date, so featured is editorial and rests on no figure at all;
 * `GET /api/library/trending` orders by clone count and then by average rating, so
 * trending has an exact basis a trader can check. Each is rendered once per section, on
 * the heading, which is the only placement that stays correct when a listing appears in
 * both lists.
 */
const FEATURED_BASIS = 'Chosen by VyomQuant. Not a statement about performance.';
const TRENDING_BASIS = 'Ordered by how many traders cloned each strategy, then by rating.';

/** What the catalogue is: everything published, in the order the sort control asks for. */
const CATALOGUE_BASIS = 'Every published strategy, in the order you choose.';

/**
 * The figures a section can carry, in display order.
 *
 * `group` names the response object the figure is read from, so one table serves all three
 * environments and no environment gets a bespoke read path.
 *
 * `format` and `precision` are `ds/Metric`'s, not this page's: the formatter that rounds,
 * groups thousands and appends the percent sign lives in the primitive, and the six
 * per-figure icons that used to sit beside these labels are gone with the hand-built grid
 * they decorated. `percent` does NOT multiply by 100 — every percentage the projection
 * sends is already in percent units.
 */
const FIGURE_DEFS = [
  { key: 'total_return_pct', group: 'summary', label: 'Total Return', format: 'percent', precision: 2, signed: true },
  { key: 'sharpe_ratio', group: 'risk', label: 'Sharpe Ratio', format: 'number', precision: 2 },
  { key: 'win_rate_pct', group: 'summary', label: 'Win Rate', format: 'percent', precision: 2 },
  { key: 'max_drawdown_pct', group: 'risk', label: 'Max Drawdown', format: 'percent', precision: 2 },
  { key: 'profit_factor', group: 'summary', label: 'Profit Factor', format: 'number', precision: 2 },
  { key: 'total_trades', group: 'summary', label: 'Trades', format: 'integer' },
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
        {/* The per-environment treatment `design/semantic.js` declares — its own hue, its
            own icon and its own border style for each of the three — reached through the
            primitive that renders it on every other page. Finding 2 in the header above
            recorded this divergence when the colour retoken could not fix it; this is the
            markup change it was waiting for. */}
        <SectionHeader
          level={3}
          title={environment === 'BACKTEST' ? 'Backtest performance' : `${environment} performance`}
          right={<TradingEnvironmentBadge environment={environment} variant="chip" />}
        />
        {/* A sentence, so it reads as one: sans, at the page's default step rather than
            2px under it. */}
        <p className="text-body text-content-secondary">
          {ENVIRONMENT_DESCRIPTIONS[environment]}
        </p>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {figures.map((figure) => (
            <Metric
              key={figure.key}
              // Requirement 6.6: every figure carries its Execution_Environment label.
              // It rides in the label rather than in a third line of its own, so the
              // figure and the environment it was measured in cannot be separated.
              label={`${figure.label} · ${environment}`}
              value={figure.value}
              format={figure.format}
              precision={figure.precision}
              state={figure.signed ? signedState(figure.value) : undefined}
              tier={2}
              className="bg-surface-panel border border-line-default rounded-lg p-3"
            />
          ))}
        </div>

        {conditions.length > 0 && (
          <div className="flex flex-col gap-2">
            {/* Three words, so the all-caps transform goes: it suppresses the word shape a
                reader skims by (Requirement 3.3). */}
            <div className="text-title font-semibold text-content-primary">
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
                    <span className="text-body font-semibold text-content-primary min-w-[96px]">
                      {label}
                    </span>
                    {metrics.length === 0 ? (
                      // An ACCOUNT OF AN ABSENCE, which is the one class of string this
                      // pass may enlarge and may never shorten. It goes UP a step.
                      <span className="text-body text-content-secondary">
                        No figures recorded for this condition.
                      </span>
                    ) : (
                      metrics.map(({ def, value }) => (
                        <span key={def.key} className="text-body text-content-secondary">
                          {def.label}{' '}
                          {/* Monospace on the FIGURE and not on the label beside it: the
                              numeral is what a reader compares down a column. */}
                          <span className="font-mono font-semibold text-content-primary">
                            {formatFigure(value, { format: def.format, precision: def.precision })}
                          </span>
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
          // The one genuine caution on the page, so it keeps the warning hue — and it
          // stops being the smallest text in the section while saying that past figures
          // do not predict future ones. `data-testid` is load-bearing: it is how this
          // statement is asserted.
          <p
            data-testid="historical-results-statement"
            className="text-status-warning text-body leading-relaxed flex items-start gap-2 border-t border-line-default pt-3"
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
        // Another account of an absence, in prose rather than in monospace.
        <div className="bg-surface-canvas border border-dashed border-line-default rounded-xl p-5 text-body text-content-secondary">
          No performance figures have been recorded for this listing.
        </div>
      );
    }
    return <div className="flex flex-col gap-5">{sections}</div>;
  };

  // ───────────────────────────────────────────────────────────────────────────
  // Cards
  // ───────────────────────────────────────────────────────────────────────────

  /**
   * ONE listing card, for all three sections (Requirement 10.6, Decision D6).
   *
   * There were two renderers, and the differences between them were not information: the
   * featured card carried a gradient, a 128px corner wash, a gold sparkle ribbon and four
   * figures; the catalogue card carried three of the same four plus a rating and a price
   * on a footer row. A trader comparing a featured listing against a catalogue one was
   * comparing two different tables.
   *
   * Now there is one table — subscribers, backtest return, backtest Sharpe, rating, price
   * — so the only differences left between `kind`s are the two Decision D6 declares:
   *
   *   * **elevation and border.** Featured sits at `--shadow-raised` with
   *     `--color-line-strong`; trending and catalogue sit at `ds/Panel`'s own
   *     `--shadow-panel` and `--color-line-default`. A shape, not a hue — Requirement
   *     20.5 forbids a state carried by colour alone, and this replaces a hue with a
   *     shape rather than the other way round.
   *   * **a `ds/StatusBadge` reading *Selected*** on a featured card only, in the neutral
   *     group, with the basis on its `title`. The section heading above it states the
   *     same basis in full; this is the marker that survives being reached from search or
   *     from the full catalogue, where there is no heading above the card.
   *
   * THE INTERACTION IS DELIBERATELY STILL A BARE `onClick` ON A WRAPPER
   * ------------------------------------------------------------------
   * Task 5.3 is what makes this card keyboard-operable, and it lands on its own because
   * the waiver in `eslint-rules/a11y-ratchet.js` records this file at exactly 4 findings —
   * two rules on each of two elements — and that guard asserts equality in BOTH
   * directions. Moving the handler onto `ds/Panel` here would take the count to zero in a
   * commit whose waiver still says four, so the wrapper stays for exactly one commit and
   * 5.3 deletes it along with the waiver line.
   */
  const renderListingCard = (strat, kind) => {
    const price = priceOf(strat);
    const totalReturn = toFiniteNumber(strat?.performance_summary?.total_return_pct);
    const sharpe = toFiniteNumber(strat?.risk_metrics?.sharpe_ratio);
    const subscribers = toFiniteNumber(strat?.subscriber_count);
    const rating = toFiniteNumber(strat?.avg_rating);
    const featured = kind === 'featured';
    const priceText = price.state === 'paid' ? `${price.text}/mo` : price.text;

    return (
      <div
        key={strat.listing_id}
        onClick={() => loadDetail(strat.listing_id)}
        className="cursor-pointer"
      >
        <Panel
          title={strat.name}
          level={3}
          data-listing-card={kind}
          data-listing-id={strat.listing_id}
          className={`h-full transition-colors hover:border-brand ${
            featured ? 'border-line-strong shadow-raised' : ''
          }`.trim()}
          actions={featured ? (
            <StatusBadge state="selected" label="Selected" title={FEATURED_BASIS} />
          ) : null}
        >
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2 text-body text-content-secondary">
              {/* The icon tile is the card's anchor and the one decoration that stayed:
                  it carries the listing's identity at a glance and the token layer
                  declares every value in it. */}
              <span className="w-10 h-10 rounded-lg bg-surface-canvas border border-line-default flex items-center justify-center shrink-0">
                <Cpu className="text-brand" size={20} />
              </span>
              {strat.creator_alias && (
                <span>
                  by{' '}
                  <span className="font-semibold text-content-primary">{strat.creator_alias}</span>
                </span>
              )}
              {strat.category && (
                // A server-supplied literal, so monospace and the transform both stay.
                <span className="bg-surface-inset border border-line-default px-2 py-0.5 rounded-sm text-micro font-mono uppercase text-content-secondary">
                  {strat.category}
                </span>
              )}
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <Metric
                label="Subs"
                value={subscribers}
                format="integer"
                tier={3}
                unavailableReason="No subscriber count was reported for this listing."
              />
              <Metric
                label="Return · BACKTEST"
                value={totalReturn}
                format="percent"
                precision={2}
                state={signedState(totalReturn)}
                tier={3}
                unavailableReason="No backtest return was recorded for this listing."
              />
              <Metric
                label="Sharpe · BACKTEST"
                value={sharpe}
                format="number"
                precision={2}
                tier={3}
                unavailableReason="No backtest Sharpe ratio was recorded for this listing."
              />
              <Metric
                label="Rating"
                value={rating}
                format="number"
                precision={1}
                tier={3}
                unavailableReason="No rating has been recorded for this listing yet."
              />
              <Metric
                label="Price"
                // `unknown` is the price the server sent something unreadable for, and it
                // renders the marker WITH its reason rather than a figure nobody read.
                value={price.state === 'unknown' ? null : priceText}
                format="raw"
                tier={3}
                unavailableReason="This listing did not report a price we can read."
              />
            </div>
          </div>
        </Panel>
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
      <div className="bg-surface-canvas border border-line-default rounded-xl px-4 py-3 flex flex-wrap items-center gap-3 text-body">
        <Clock size={14} className="text-brand" />
        {/* Two words and a section name, so the transform goes (Requirement 3.3). */}
        <span className="text-content-secondary">Your subscription</span>
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
        <CommandButton intent="ghost" icon={ArrowLeft} onClick={() => setView('browse')}>
          Back to Marketplace
        </CommandButton>

        <Panel className="w-full">
          <div className="flex flex-col gap-8">
            {/* Header */}
            <div className="flex justify-between items-start gap-6 flex-wrap">
              <div className="flex-1 min-w-[260px]">
                <div className="flex items-center gap-3 mb-3 flex-wrap">
                  {/* Three server-supplied literals. Monospace and the transform both
                      stay: this is the word the server chose, not a restyling of one. */}
                  {strat.category && (
                    <span className="bg-surface-canvas border border-line-default px-3 py-1 rounded-sm text-micro font-mono text-content-secondary uppercase">
                      {strat.category}
                    </span>
                  )}
                  {strat.difficulty && (
                    <span className="bg-surface-canvas border border-line-default px-3 py-1 rounded-sm text-micro font-mono text-content-secondary uppercase">
                      {strat.difficulty}
                    </span>
                  )}
                  {strat.validation_status && (
                    <span className="bg-surface-canvas border border-line-default px-3 py-1 rounded-sm text-micro font-mono text-content-secondary uppercase">
                      {strat.validation_status}
                    </span>
                  )}
                </div>
                {/* An `h2`, not a second `h1`: `ds/PageHeader` above owns the page's only
                    `<h1>`, and the listing's name is the heading of a region inside it.
                    `--text-page` is the largest step the scale declares for a title. */}
                <h2 className="text-page font-semibold text-content-primary mb-2">{strat.name}</h2>
                {strat.creator_alias && (
                  <div className="text-body text-content-secondary">
                    Created by{' '}
                    <span className="font-semibold text-content-primary">{strat.creator_alias}</span>
                  </div>
                )}
              </div>

              {/* Pricing section + subscribe action (Requirement 20.9) */}
              <div className="flex flex-col gap-3 items-end">
                <div className="flex flex-col items-end gap-1">
                  <Metric
                    label="Monthly price"
                    value={price.state === 'unknown' ? null : price.text}
                    format="raw"
                    tier={1}
                    className="items-end"
                    unavailableReason="This listing did not report a price we can read."
                  />
                  {price.state === 'paid' && (
                    <span className="text-micro text-content-secondary">
                      Billed each calendar month.
                    </span>
                  )}
                </div>
                {/* Both disabled arms are `secondary`, and neither is `live`: that intent
                    resolves to `ui/Button`'s danger variant, which is the treatment this
                    design system reserves for an action with live-trading consequence. A
                    subscription that is already held is a STATE, and the state's colour is
                    the `ds/StatusBadge` in the indicator directly below — one hue, one
                    owner (Requirement 1.4). */}
                {price.state === 'unknown' ? (
                  <CommandButton
                    intent="secondary"
                    disabled
                    disabledReason="This listing did not report a price we can read, so nothing can be bought here."
                  >
                    Price unavailable
                  </CommandButton>
                ) : isSubscribed ? (
                  <CommandButton
                    intent="secondary"
                    disabled
                    disabledReason="You already hold a subscription to this strategy, so there is nothing to buy."
                  >
                    Subscribed
                  </CommandButton>
                ) : (
                  <CommandButton
                    intent="primary"
                    icon={ArrowRight}
                    loading={cloneLoading || subscribeLoading}
                    loadingLabel={price.state === 'paid' ? 'Opening checkout' : 'Cloning strategy'}
                    onClick={() => (price.state === 'paid' ? handleSubscribe(strat) : handleClone(strat))}
                  >
                    {price.state === 'paid' ? 'Subscribe' : 'Clone Free'}
                  </CommandButton>
                )}
              </div>
            </div>

            {renderSubscriptionIndicator()}

            {/* Owner-supplied description, rendered as a text child */}
            <div className="text-body text-content-primary leading-relaxed max-w-4xl whitespace-pre-line">
              {strat.description || 'No description provided.'}
            </div>

            {/* BACKTEST / PAPER / LIVE, visually separated, empty ones omitted */}
            {renderPerformanceSections(strat)}

            {/* Owner-supplied tags, rendered as text children */}
            {Array.isArray(strat.tags) && strat.tags.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {strat.tags.map((tag) => (
                  <span key={String(tag)} className="text-micro border border-line-default bg-surface-inset text-content-secondary px-3 py-1 rounded-full font-mono uppercase">
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {/* Stats. Three figures through the one primitive that renders a figure: the
                marker for an absent one carries its reason, and the rating's count travels
                as the figure's unit rather than as a parenthesis nobody can read. */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-6 border-t border-line-default">
              <Metric
                label="Subscribers"
                value={subscriberCount}
                format="integer"
                tier={2}
                unavailableReason="No subscriber count was reported for this listing."
              />
              <Metric
                label="Rating"
                value={rating}
                format="number"
                precision={1}
                unit={ratingCount === null ? undefined : `from ${formatCount(ratingCount)} ratings`}
                tier={2}
                unavailableReason="No rating has been recorded for this listing yet."
              />
              <Metric
                label="Published"
                value={publishedAt}
                format="raw"
                tier={2}
                unavailableReason="The listing carries no publication date."
              />
            </div>

            {/* Reviews — owner/subscriber supplied text, rendered as text children */}
            {reviews.length > 0 && (
              <div className="flex flex-col gap-3 pt-6 border-t border-line-default">
                <SectionHeader level={3} title="Recent reviews" />
                {reviews.map((review, index) => {
                  const reviewRating = toFiniteNumber(review?.rating);
                  const reviewedAt = formatDate(review?.created_at);
                  return (
                    <div
                      key={`${review?.created_at || 'review'}-${index}`}
                      className="bg-surface-canvas border border-line-default rounded-xl p-4 flex flex-col gap-2"
                    >
                      <div className="flex items-center gap-3 text-small text-content-secondary">
                        {reviewRating !== null && (
                          // The figure keeps monospace; the date beside it is prose.
                          <span className="font-mono font-semibold text-content-primary">
                            {formatFigure(reviewRating, { format: 'number', precision: 1 })} / 5
                          </span>
                        )}
                        {reviewedAt && <span>{reviewedAt}</span>}
                      </div>
                      {review?.review_text && (
                        <p className="text-body text-content-primary leading-relaxed whitespace-pre-line">
                          {review.review_text}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </Panel>
      </div>
    );
  };

  return (
    <div className="flex-1 overflow-y-auto bg-surface-canvas text-content-primary font-sans">
      <div className="max-w-7xl mx-auto p-6 flex flex-col gap-8">

        {/* The page's only `<h1>`, at `--text-page`, through the primitive every other
            in-scope page uses. What was here was a gradient card carrying a 36px
            `font-black` title — a step the scale does not have — over a 384px blurred
            brand-coloured wash, with a monospace paragraph and three counts painted in
            three state hues. The title, the paragraph and the three counts all survive;
            the decoration does not, and the counts are calm because a count is not a
            state. */}
        <PageHeader
          title="Strategy Marketplace"
          meta={(
            <div className="flex flex-wrap items-center gap-4">
              <span className="flex items-center gap-1.5">
                <Trophy size={14} /> {featuredStrategies.length} featured
              </span>
              <span className="flex items-center gap-1.5">
                <Flame size={14} /> {trendingStrategies.length} trending
              </span>
              <span className="flex items-center gap-1.5">
                <Users size={14} /> {strategies.length} on this page
              </span>
            </div>
          )}
        />
        <p className="text-body text-content-secondary max-w-2xl">
          Browse published strategies, review the recorded results behind each one, and
          subscribe to run them without ever holding their logic.
        </p>

        {error && (
          <div className="bg-status-error/20 border border-status-error text-status-error p-4 rounded-lg flex items-center gap-3 text-body">
            <AlertTriangle size={18} /> {error}
          </div>
        )}

        {/* Fallback outcome banner — used only when no toast host is mounted */}
        {notice && (
          <div
            className={`p-4 rounded-lg flex items-center justify-between gap-3 text-body border ${
              notice.type === 'error'
                ? 'bg-status-error/20 border-status-error text-status-error'
                : 'bg-status-connected/20 border-status-connected text-status-connected'
            }`}
          >
            <span>{notice.message}</span>
            <button
              type="button"
              onClick={() => setNotice(null)}
              className="text-content-secondary hover:text-content-primary text-body"
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
              className="bg-transparent border-none outline-none text-body p-2 w-full text-content-primary placeholder-content-secondary"
            />
          </div>

          {/* Category Pills */}
          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => setSelectedCategory(null)}
              aria-pressed={selectedCategory === null}
              className={`px-3 py-1.5 rounded-full text-small transition-colors ${
                selectedCategory === null
                  ? 'bg-brand text-content-inverse'
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
                  aria-pressed={selectedCategory === cat.name}
                  className={`px-3 py-1.5 rounded-full text-small transition-colors ${
                    selectedCategory === cat.name
                      ? 'bg-brand text-content-inverse'
                      : 'bg-surface-canvas border border-line-default text-content-secondary hover:text-content-primary'
                  }`}
                >
                  {/* The category is the server's own word, so it keeps monospace; the
                      count beside it is a figure and keeps it for the same reason. */}
                  <span className="font-mono">
                    {cat.name}{count === null ? '' : ` (${formatCount(count)})`}
                  </span>
                </button>
              );
            })}
          </div>

          <select
            className="bg-surface-canvas border border-line-default text-content-secondary text-body rounded-lg px-4 py-2 outline-none"
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
            {/* THE FEATURED SECTION, WITH ITS BASIS ON THE HEADING (Decision D6).
                What was here: a sparkle glyph in the warning hue and the words "Featured
                Strategies", which say nothing about what earns a listing that word. The
                heading now says it — and it is the only placement that stays true for a
                listing that appears in both curated lists. */}
            {featuredStrategies.length > 0 && (
              <section className="flex flex-col gap-4">
                <SectionHeader title="Selected by VyomQuant" subtitle={FEATURED_BASIS} />
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  {featuredStrategies.map((strat) => renderListingCard(strat, 'featured'))}
                </div>
              </section>
            )}

            {/* THE TRENDING SECTION. `api.library.trending(5)` has been called on every
                browse load since this page was written, and its five listings were spent
                on one number in the hero. They are listed here, under the basis the route
                actually orders by. No new request: the same call, rendered. */}
            {trendingStrategies.length > 0 && (
              <section className="flex flex-col gap-4">
                <SectionHeader title="Most cloned" subtitle={TRENDING_BASIS} />
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                  {trendingStrategies.map((strat) => renderListingCard(strat, 'trending'))}
                </div>
              </section>
            )}

            {/* The catalogue. Its own heading, so the three lists on this page are three
                named regions in the heading outline rather than one unlabelled grid. */}
            <section className="flex flex-col gap-4">
              <SectionHeader title="All strategies" subtitle={CATALOGUE_BASIS} />

              {loading ? (
                <div className="flex justify-center items-center py-20 text-body text-content-secondary">
                  <Activity className="animate-spin mr-2" size={20} /> Loading Marketplace...
                </div>
              ) : strategies.length === 0 ? (
                <div className="flex flex-col justify-center items-center py-20 text-body text-content-secondary gap-4 border border-dashed border-line-default rounded-xl">
                  <Search size={48} className="opacity-20" />
                  No strategies found matching your criteria.
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                    {strategies.map((strat) => renderListingCard(strat, 'catalogue'))}
                  </div>

                  {/* Pagination */}
                  {totalPages > 1 && (
                    <div className="flex justify-center items-center gap-4 mt-8 text-body">
                      <button
                        type="button"
                        disabled={page === 1}
                        onClick={() => { setPage((p) => p - 1); }}
                        className="px-4 py-2 bg-surface-panel border border-line-default rounded-lg hover:border-content-secondary disabled:opacity-50"
                      >
                        Previous
                      </button>
                      <span className="text-content-secondary">
                        Page{' '}
                        <span className="font-mono text-content-primary">{page}</span> of{' '}
                        <span className="font-mono text-content-primary">{totalPages}</span>
                      </span>
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
            </section>
          </>
        )}
      </div>
    </div>
  );
};

export default StrategyMarketplace;
