/**
 * CreatorMarketplacePanel — the owner's path from a strategy to a Marketplace listing (MKT-04).
 *
 * Three steps, each driven by the server's own routes and answered by its own state:
 *
 *   1. LISTING. `POST /api/library` creates the Listing (moderation `pending`) the Submission
 *      references. Shown as done once `/api/library/me` returns a row for this strategy.
 *   2. SUBMIT FOR REVIEW. Pick 3–10 completed backtests and `POST /api/library/submissions`. The
 *      Eligibility_Gate decides; a refusal names EVERY failed criterion, and each is shown in the
 *      gate's own owner-facing words. The newest Submission's state is read back with
 *      `GET /api/library/submissions?strategy_id=` on every load, so a reload loses nothing.
 *   3. PRICE. `POST .../price-range` computes the range from the immutable evidence;
 *      `POST .../price` sets a price inside it. Money crosses the wire as integer Minor_Units.
 *
 * The page never decides eligibility, a price bound or a transition — it renders what the server
 * said. The pre-checks in `marketplaceFlow.js` only stop a request the server would refuse.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api';
import { get, post } from '../../apiClient';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import { Alert } from '../ds/Alert';
import { Tag2 } from '../common/primitives';
import { token } from '../../design/tokens';
import {
  EVIDENCE_THRESHOLDS,
  LISTING_CATEGORIES,
  PRICE_CURRENCIES,
  SUBMISSION_STATE_TEXT,
  canStartSubmission,
  currentSubmission,
  eligibilityFailureText,
  marketplaceRefusal,
  minorToText,
  priceToMinor,
  runShortfall,
  selectionProblem,
  windowDays,
} from './marketplaceFlow';

const label = {
  color: token.content.muted,
  fontSize: token.text.micro,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
};
const body = { color: token.content.secondary, fontSize: token.text.body };
const inputStyle = {
  background: token.surface.inset,
  border: `1px solid ${token.line.default}`,
  color: token.content.primary,
  borderRadius: 6,
  padding: '6px 8px',
  fontSize: token.text.body,
};

function StepHeader({ n, title, done }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
      <Tag2 c={done ? 'green' : 'gray'}>{done ? 'Done' : `Step ${n}`}</Tag2>
      <span style={{ color: token.content.primary, fontWeight: 700, fontSize: token.text.title }}>{title}</span>
    </div>
  );
}

export default function CreatorMarketplacePanel({ strategy }) {
  const strategyId = strategy?.id;
  const [load, setLoad] = useState({ status: 'loading', error: null });
  const [listing, setListing] = useState(null);
  const [submissions, setSubmissions] = useState([]);
  const [runs, setRuns] = useState([]);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(null);

  // step 1
  const [description, setDescription] = useState(strategy?.description || '');
  const [category, setCategory] = useState('trend_following');
  // step 2
  const [selected, setSelected] = useState([]);
  const [failures, setFailures] = useState([]);
  // step 3
  const [currency, setCurrency] = useState('USD');
  const [range, setRange] = useState(null);
  const [priceText, setPriceText] = useState('');

  const refresh = useCallback(async () => {
    if (!strategyId) return;
    setLoad((prev) => ({ ...prev, status: 'loading' }));
    try {
      const [mine, subs, bts] = await Promise.all([
        get('/api/library/me'),
        api.library.submissions.list(strategyId),
        api.strategies.listBacktests({ strategyId, limit: 100 }),
      ]);
      const items = mine?.items ?? [];
      setListing(items.find((row) => row.source_strategy_id === strategyId) || null);
      setSubmissions(subs?.submissions ?? []);
      setRuns(
        (bts?.backtests ?? []).filter((run) => String(run.status).toLowerCase() === 'completed'),
      );
      setLoad({ status: 'ready', error: null });
    } catch (err) {
      const refusal = marketplaceRefusal(err);
      setLoad({ status: 'error', error: refusal.message || 'Could not load the marketplace state for this strategy.' });
    }
  }, [strategyId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const submission = useMemo(() => currentSubmission(submissions), [submissions]);
  const mayStartSubmission = canStartSubmission(submission);
  const problem = selectionProblem(selected);

  const createListing = async () => {
    setBusy('listing');
    setNotice(null);
    try {
      await post('/api/library', {
        strategy_id: strategyId,
        name: strategy?.name,
        description: description.trim() || null,
        category,
        difficulty: 'intermediate',
        tags: [],
        price: null,
        currency: 'USD',
        subscription_tier: 'free',
      });
      setNotice({ kind: 'ok', text: 'Listing created. It stays hidden until a reviewer publishes it.' });
      await refresh();
    } catch (err) {
      const refusal = marketplaceRefusal(err);
      setNotice({ kind: 'error', text: refusal.message || 'The listing could not be created.' });
    } finally {
      setBusy(null);
    }
  };

  const toggleRun = (id) => {
    setFailures([]);
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const submitForReview = async () => {
    if (problem) return;
    setBusy('submit');
    setNotice(null);
    setFailures([]);
    try {
      await api.library.submissions.create({ strategy_id: strategyId, backtest_ids: selected });
      setNotice({ kind: 'ok', text: 'Submitted for review. A reviewer will approve or reject it.' });
      setSelected([]);
      await refresh();
    } catch (err) {
      const refusal = marketplaceRefusal(err);
      const codes = Array.isArray(refusal.details?.failures) ? refusal.details.failures : [];
      if (codes.length) {
        setFailures(codes);
        setNotice({ kind: 'error', text: 'The submission was not admitted. Every reason is listed below.' });
      } else {
        setNotice({ kind: 'error', text: refusal.message || 'The submission could not be created.' });
      }
    } finally {
      setBusy(null);
    }
  };

  const computeRange = async () => {
    if (!submission) return;
    setBusy('range');
    setNotice(null);
    try {
      const result = await api.library.submissions.priceRange(submission.id, currency);
      setRange({ ...result, currency });
    } catch (err) {
      const refusal = marketplaceRefusal(err);
      setRange(null);
      setNotice({ kind: 'error', text: refusal.message || 'The price range could not be computed.' });
    } finally {
      setBusy(null);
    }
  };

  const parsedPrice = priceToMinor(priceText, currency);
  const priceOutOfRange =
    range && parsedPrice.ok
      && (parsedPrice.minor < range.minimum_price_minor || parsedPrice.minor > range.maximum_price_minor);

  const setPrice = async () => {
    if (!submission || !parsedPrice.ok || priceOutOfRange) return;
    setBusy('price');
    setNotice(null);
    try {
      await api.library.submissions.setPrice(submission.id, parsedPrice.minor, currency);
      setNotice({ kind: 'ok', text: `Price set to ${minorToText(parsedPrice.minor, currency)} a month.` });
    } catch (err) {
      const refusal = marketplaceRefusal(err);
      setNotice({ kind: 'error', text: refusal.message || 'The price could not be set.' });
    } finally {
      setBusy(null);
    }
  };

  if (load.status === 'loading' && !listing && submissions.length === 0 && runs.length === 0) {
    return <div style={body}>Loading marketplace status…</div>;
  }
  if (load.status === 'error') {
    return (
      <Alert severity="error" title="Marketplace status could not be loaded">
        {load.error}{' '}
        <Button variant="outline" size="xs" onClick={refresh}>Retry</Button>
      </Alert>
    );
  }

  return (
    <div data-testid="creator-marketplace-panel" style={{ display: 'grid', gap: 12 }}>
      {notice && (
        <div data-testid="marketplace-notice">
          <Alert
            severity={notice.kind === 'error' ? 'error' : 'info'}
            title={notice.text}
            onDismiss={() => setNotice(null)}
            dismissLabel="Dismiss this message"
          />
        </div>
      )}

      {/* ── 1. LISTING ─────────────────────────────────────────────────────────── */}
      <Card className="p-4">
        <StepHeader n={1} title="Create the listing" done={Boolean(listing)} />
        {listing ? (
          <div style={body}>
            Listing <b>{listing.name}</b> — moderation status{' '}
            <Tag2 c={listing.moderation_status === 'approved' ? 'green' : 'gray'}>{listing.moderation_status}</Tag2>
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 8 }}>
            <div style={body}>
              A listing is the Marketplace page buyers see. It stays hidden until a reviewer publishes it.
            </div>
            <label style={label} htmlFor="mkt-description">Description</label>
            <textarea
              id="mkt-description"
              rows={3}
              maxLength={2000}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              style={inputStyle}
            />
            <label style={label} htmlFor="mkt-category">Category</label>
            <select id="mkt-category" value={category} onChange={(e) => setCategory(e.target.value)} style={inputStyle}>
              {LISTING_CATEGORIES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
            </select>
            <div>
              <Button variant="primary" size="sm" onClick={createListing} disabled={busy !== null}>
                {busy === 'listing' ? 'Creating…' : 'Create listing'}
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* ── 2. SUBMIT FOR REVIEW ──────────────────────────────────────────────── */}
      <Card className="p-4">
        <StepHeader n={2} title="Submit backtest evidence for review" done={Boolean(submission) && !mayStartSubmission} />
        {submission && (
          <div style={{ ...body, marginBottom: 8 }} data-testid="submission-state">
            <Tag2 c={submission.submission_state === 'PUBLISHED' ? 'green' : 'gray'}>{submission.submission_state}</Tag2>{' '}
            {SUBMISSION_STATE_TEXT[submission.submission_state] || ''}
            {submission.submission_state === 'REJECTED' && submission.rejection_reason && (
              <div style={{ marginTop: 4 }}>Reviewer&apos;s reason: {submission.rejection_reason}</div>
            )}
          </div>
        )}
        {!listing && <div style={body}>Create the listing first.</div>}
        {listing && mayStartSubmission && (
          <div style={{ display: 'grid', gap: 8 }}>
            <div style={body}>
              Choose {EVIDENCE_THRESHOLDS.minConditions}–{EVIDENCE_THRESHOLDS.maxConditions} completed backtests of the
              same saved version, each over a different stretch of market history: at least{' '}
              {EVIDENCE_THRESHOLDS.minWindowDays} days, {EVIDENCE_THRESHOLDS.minTrades} trades and{' '}
              {EVIDENCE_THRESHOLDS.minBars} bars each.
            </div>
            {runs.length === 0 ? (
              <div style={body}>No completed backtests yet. Run some in the Backtester first.</div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: token.text.body }}>
                <thead>
                  <tr style={{ color: token.content.muted, textAlign: 'left' }}>
                    <th />
                    <th>Period</th>
                    <th>Days</th>
                    <th>Trades</th>
                    <th>Return</th>
                    <th>Qualifies</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => {
                    const shortfall = runShortfall(run);
                    return (
                      <tr key={run.id} style={{ color: token.content.primary, borderTop: `1px solid ${token.line.default}` }}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Use backtest ${String(run.start_date).slice(0, 10)} to ${String(run.end_date).slice(0, 10)}`}
                            checked={selected.includes(run.id)}
                            disabled={Boolean(shortfall)}
                            onChange={() => toggleRun(run.id)}
                          />
                        </td>
                        <td>{String(run.start_date).slice(0, 10)} → {String(run.end_date).slice(0, 10)}</td>
                        <td>{windowDays(run) ?? '—'}</td>
                        <td>{run.total_trades ?? '—'}</td>
                        <td>{run.total_return_pct == null ? '—' : `${Number(run.total_return_pct).toFixed(2)}%`}</td>
                        <td style={{ color: shortfall ? token.content.muted : undefined }}>{shortfall || 'Yes'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            {failures.length > 0 && (
              <ul data-testid="eligibility-failures" style={{ ...body, margin: 0, paddingLeft: 18 }}>
                {failures.map((code) => <li key={code}>{eligibilityFailureText(code)}</li>)}
              </ul>
            )}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Button variant="primary" size="sm" onClick={submitForReview} disabled={busy !== null || Boolean(problem)}>
                {busy === 'submit' ? 'Submitting…' : 'Submit for review'}
              </Button>
              {problem && <span style={body}>{problem}</span>}
            </div>
          </div>
        )}
      </Card>

      {/* ── 3. PRICE ──────────────────────────────────────────────────────────── */}
      <Card className="p-4">
        <StepHeader n={3} title="Set the monthly price" done={false} />
        {!submission || !['SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'PUBLISHED', 'SUSPENDED'].includes(submission.submission_state) ? (
          <div style={body}>Available once the strategy has been submitted for review.</div>
        ) : (
          <div style={{ display: 'grid', gap: 8 }}>
            <div style={body}>
              The allowed range is computed from your submitted backtest evidence. Subscribers pay monthly; you receive 90%.
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <select aria-label="Currency" value={currency} onChange={(e) => { setCurrency(e.target.value); setRange(null); }} style={inputStyle}>
                {Object.keys(PRICE_CURRENCIES).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <Button variant="outline" size="sm" onClick={computeRange} disabled={busy !== null}>
                {busy === 'range' ? 'Calculating…' : 'Calculate allowed range'}
              </Button>
            </div>
            {range && (
              <div data-testid="price-range" style={body}>
                Minimum {minorToText(range.minimum_price_minor, range.currency)} · Recommended{' '}
                <b>{minorToText(range.recommended_price_minor, range.currency)}</b> · Maximum{' '}
                {minorToText(range.maximum_price_minor, range.currency)}
              </div>
            )}
            {range && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                  aria-label="Monthly price"
                  placeholder={minorToText(range.recommended_price_minor, currency).split(' ')[0]}
                  value={priceText}
                  onChange={(e) => setPriceText(e.target.value)}
                  style={{ ...inputStyle, width: 140 }}
                />
                <Button variant="primary" size="sm" onClick={setPrice} disabled={busy !== null || !parsedPrice.ok || priceOutOfRange}>
                  {busy === 'price' ? 'Saving…' : 'Set price'}
                </Button>
                {priceText && !parsedPrice.ok && <span style={body}>{parsedPrice.message}</span>}
                {priceOutOfRange && <span style={body}>Outside the allowed range.</span>}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
