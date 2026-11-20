/**
 * AdminMarketplaceReview — the reviewer's queue for Marketplace Submissions (MKT-04).
 *
 * Mounted at `/admin/marketplace` behind `AdminGuard` (Supabase `app_metadata.role === 'admin'`);
 * every route it calls is admin-only server-side as well, so this page is never the control.
 *
 *   list    `GET  /api/library/admin/submissions?state=`  (state filter, newest first)
 *   detail  `GET  /api/library/admin/submissions/{id}`     (submission, evidence copy, history)
 *   act     `POST /api/library/admin/submissions/{id}/{approve|reject|publish|suspend|unpublish}`
 *
 * Only the actions the Submission's state admits are offered (`adminActionsFor`, the
 * transition table); an illegal one would be a 409 anyway. A reject carries a written reason,
 * which the owner sees.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { Alert } from '../components/ds/Alert';
import { Tag2 } from '../components/common/primitives';
import { token } from '../design/tokens';
import {
  SUBMISSION_STATE_TEXT,
  adminActionsFor,
  marketplaceRefusal,
  windowDays,
} from '../components/marketplace/marketplaceFlow';

const STATES = ['SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'PUBLISHED', 'SUSPENDED', 'REJECTED', 'UNPUBLISHED'];
const ACTION_LABEL = {
  approve: 'Approve',
  reject: 'Reject',
  publish: 'Publish',
  suspend: 'Suspend',
  unpublish: 'Unpublish',
};
const body = { color: token.content.secondary, fontSize: token.text.body };
const cell = { padding: '6px 8px', borderTop: `1px solid ${token.line.default}`, textAlign: 'left' };
const fmt = (v) => (v == null ? '—' : String(v).replace('T', ' ').slice(0, 16));
const pct = (v) => (v == null ? '—' : `${Number(v).toFixed(2)}%`);

export default function AdminMarketplaceReview() {
  const [state, setState] = useState('SUBMITTED');
  const [list, setList] = useState({ status: 'loading', items: [], error: null });
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState({ status: 'idle', data: null, error: null });
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);

  const loadList = useCallback(async () => {
    setList((prev) => ({ ...prev, status: 'loading' }));
    try {
      const res = await api.library.admin.listSubmissions({ state, pageSize: 100 });
      setList({ status: 'ready', items: res?.items ?? [], error: null });
    } catch (err) {
      setList({ status: 'error', items: [], error: marketplaceRefusal(err).message || 'The queue could not be read.' });
    }
  }, [state]);

  const loadDetail = useCallback(async (id) => {
    if (!id) return;
    setDetail({ status: 'loading', data: null, error: null });
    try {
      const res = await api.library.admin.getSubmission(id);
      setDetail({ status: 'ready', data: res, error: null });
    } catch (err) {
      setDetail({ status: 'error', data: null, error: marketplaceRefusal(err).message || 'The submission could not be read.' });
    }
  }, []);

  useEffect(() => { loadList(); }, [loadList]);
  useEffect(() => { setReason(''); loadDetail(selectedId); }, [selectedId, loadDetail]);

  const act = async (action) => {
    if (!selectedId) return;
    if (action === 'reject' && !reason.trim()) {
      setNotice({ kind: 'error', text: 'Write the reason the owner will see before rejecting.' });
      return;
    }
    setBusy(action);
    setNotice(null);
    try {
      const fn = api.library.admin[action];
      const res = action === 'reject' ? await fn(selectedId, reason.trim()) : await fn(selectedId);
      setNotice({ kind: 'ok', text: `${ACTION_LABEL[action]}: now ${res?.submission_state ?? 'updated'}.` });
      await Promise.all([loadList(), loadDetail(selectedId)]);
    } catch (err) {
      const refusal = marketplaceRefusal(err);
      setNotice({ kind: 'error', text: refusal.message || `${ACTION_LABEL[action]} was refused.` });
    } finally {
      setBusy(null);
    }
  };

  const submission = detail.data?.submission ?? null;
  const evidence = detail.data?.evidence ?? [];
  const transitions = detail.data?.transitions ?? [];
  const outcomes = Array.isArray(submission?.eligibility_outcomes) ? submission.eligibility_outcomes : [];

  return (
    <div style={{ background: token.surface.canvas, minHeight: '100vh', padding: 24, color: token.content.primary }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: token.text.section, fontWeight: 800, margin: 0 }}>Marketplace review</h1>
        <Link to="/app/dashboard" style={{ color: token.content.muted, fontSize: token.text.body }}>Back to the app</Link>
      </div>

      {notice && (
        <div data-testid="admin-notice" style={{ marginBottom: 12 }}>
          <Alert
            severity={notice.kind === 'error' ? 'error' : 'info'}
            title={notice.text}
            onDismiss={() => setNotice(null)}
            dismissLabel="Dismiss this message"
          />
        </div>
      )}

      <div role="tablist" aria-label="Submission state" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {STATES.map((s) => (
          <Button
            key={s}
            role="tab"
            aria-selected={state === s}
            variant={state === s ? 'primary' : 'outline'}
            size="xs"
            onClick={() => { setState(s); setSelectedId(null); }}
          >
            {s.replace('_', ' ')}
          </Button>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 1fr) minmax(320px, 2fr)', gap: 12 }}>
        <Card className="p-3">
          {list.status === 'loading' && <div style={body}>Loading…</div>}
          {list.status === 'error' && (
            <Alert severity="error" title={list.error}>
              <Button variant="outline" size="xs" onClick={loadList}>Retry</Button>
            </Alert>
          )}
          {list.status === 'ready' && list.items.length === 0 && (
            <div style={body}>No submissions are {state.replace('_', ' ').toLowerCase()}.</div>
          )}
          {list.status === 'ready' && list.items.length > 0 && (
            <ul data-testid="admin-queue" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {list.items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    style={{
                      width: '100%',
                      textAlign: 'left',
                      background: item.id === selectedId ? token.surface.raised : 'transparent',
                      border: 'none',
                      borderTop: `1px solid ${token.line.default}`,
                      color: token.content.primary,
                      padding: '8px 6px',
                      cursor: 'pointer',
                      fontSize: token.text.body,
                    }}
                  >
                    <div>Strategy {String(item.source_strategy_id).slice(0, 8)}…</div>
                    <div style={{ color: token.content.muted }}>Submitted {fmt(item.submitted_at)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="p-3">
          {!selectedId && <div style={body}>Select a submission to review it.</div>}
          {selectedId && detail.status === 'loading' && <div style={body}>Loading…</div>}
          {selectedId && detail.status === 'error' && <Alert severity="error" title={detail.error} />}
          {submission && (
            <div style={{ display: 'grid', gap: 12 }} data-testid="admin-detail">
              <div style={body}>
                <Tag2 c={submission.submission_state === 'PUBLISHED' ? 'green' : 'gray'}>{submission.submission_state}</Tag2>{' '}
                {SUBMISSION_STATE_TEXT[submission.submission_state]}
                <div>Strategy {submission.source_strategy_id} · version {submission.version_id}</div>
                <div>Owner {submission.owner_id}</div>
                {submission.rejection_reason && <div>Rejection reason: {submission.rejection_reason}</div>}
              </div>

              <div>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>Eligibility (evaluated at submission)</div>
                {outcomes.length === 0 ? (
                  <div style={body}>No outcomes recorded.</div>
                ) : (
                  <ul style={{ ...body, margin: 0, paddingLeft: 18 }}>
                    {outcomes.map((o, i) => (
                      <li key={`${o.code}-${i}`}>{o.passed ? 'Pass' : 'Fail'} — {o.code}</li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>Backtest evidence (immutable copy)</div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: token.text.body }}>
                  <thead>
                    <tr style={{ color: token.content.muted }}>
                      <th style={cell}>Period</th><th style={cell}>Days</th><th style={cell}>Trades</th>
                      <th style={cell}>Return</th><th style={cell}>Max DD</th><th style={cell}>Sharpe</th><th style={cell}>Win rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {evidence.map((e) => (
                      <tr key={e.condition_index ?? e.id}>
                        <td style={cell}>{String(e.start_date).slice(0, 10)} → {String(e.end_date).slice(0, 10)}</td>
                        <td style={cell}>{windowDays(e) ?? '—'}</td>
                        <td style={cell}>{e.total_trades ?? '—'}</td>
                        <td style={cell}>{pct(e.total_return_pct)}</td>
                        <td style={cell}>{pct(e.max_drawdown_pct)}</td>
                        <td style={cell}>{e.sharpe_ratio == null ? '—' : Number(e.sharpe_ratio).toFixed(2)}</td>
                        <td style={cell}>{pct(e.win_rate_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>History</div>
                <ul style={{ ...body, margin: 0, paddingLeft: 18 }}>
                  {transitions.map((t) => (
                    <li key={t.id}>{fmt(t.transitioned_at)} — {t.from_state ?? '∅'} → {t.to_state}{t.reason ? ` (${t.reason})` : ''}</li>
                  ))}
                </ul>
              </div>

              {adminActionsFor(submission.submission_state).length > 0 && (
                <div style={{ display: 'grid', gap: 8 }}>
                  {adminActionsFor(submission.submission_state).includes('reject') && (
                    <>
                      <label htmlFor="reject-reason" style={{ ...body, fontWeight: 600 }}>Reason (shown to the owner if you reject)</label>
                      <textarea
                        id="reject-reason"
                        rows={2}
                        maxLength={2000}
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        style={{ background: token.surface.inset, color: token.content.primary, border: `1px solid ${token.line.default}`, borderRadius: 6, padding: 6 }}
                      />
                    </>
                  )}
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {adminActionsFor(submission.submission_state).map((action) => (
                      <Button
                        key={action}
                        variant={action === 'reject' || action === 'unpublish' ? 'danger' : 'primary'}
                        size="sm"
                        onClick={() => act(action)}
                        disabled={busy !== null}
                      >
                        {busy === action ? `${ACTION_LABEL[action]}…` : ACTION_LABEL[action]}
                      </Button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
