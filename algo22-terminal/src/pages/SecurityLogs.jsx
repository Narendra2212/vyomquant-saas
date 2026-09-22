/**
 * pages/SecurityLogs.jsx — the account's authentication and access audit trail.
 *
 * retail-ui-simplification task 7.4 (commit 10). Requirements 4.1, 4.2, 4.3, 4.4, 5.1, 5.2,
 * 6.2, 19.1, 19.2, 19.5. design.md §2.4, §6.
 *
 * 7 absolute pixel sizes to 0 and 3 colour literals to 0, both budgets lowered in this commit.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THIS PAGE RENDERED SIX COLUMNS OVER A FIVE-COLUMN TABLE, AND FILLED THE GAP
 * ═══════════════════════════════════════════════════════════════════════════
 * `GET /api/security/logs` is `security_logs.select('*')`, and that table
 * (`migrations/006_reconcile_production_database.sql:327`) holds `event_type NOT NULL`,
 * `ip_address`, `user_agent`, `details`, `created_at`. **No `location` column. No `status`
 * column.** The page rendered both, and the `||` fallbacks fired on every single row:
 *
 *     status: (l.status || "success")      → a green chip reading SUCCESS on EVERY row
 *     loc:    … || "Secure Session"        → a "location" that is a reassurance
 *     ip:     … || "127.0.0.1"             → a localhost address on an audit record
 *     device: … || "Browser / Desktop Client"
 *     time:   l.created_at ? … : NEW DATE()  → the moment the PAGE was opened
 *
 * The `status` one is the worst defect this pass has found. A security audit log exists to
 * answer one question — has anyone else been in this account — and the page answered it
 * `SUCCESS`, in green, for every record, on a field the database does not have. The
 * `failed_attempts` card then counted the same absent field and published **0**. §6's argument
 * is that the danger is plausibility rather than wrongness, and `Failed Attempts: 0` on a
 * security page is the most reassuring figure in the product.
 *
 * Three of the four summary cards were not measurements at all:
 *
 *     api_calls_24h:   normalized.length * 12   invented; nothing reports API call volume
 *     failed_attempts: see above                structurally always 0
 *     active_sessions: 1                        a literal
 *
 * And the fourth was mislabelled rather than wrong: *Logins (30d)* was counted over
 * `getSecurityLogs(100)` — the 100 most recent records, with no date window in the request at
 * all — and substituted the total record count (floored at 1) when nothing matched.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT REPLACES ALL OF IT (Requirements 4.2, 19.1, 19.2, 19.5)
 * ═══════════════════════════════════════════════════════════════════════════
 * Ten entries in `design/pageFields.js` under a `security-logs` page, each with its source
 * path or its explicit unavailability, and each with the sentence a trader reads instead of the
 * figure. Every cell and every card renders through `design/reported.js` into `ds/Metric` or
 * its one `NotAvailableMarker`, so:
 *
 *   * **Every column survives.** `Location` and `Status` still have their headers and their
 *     cells; the cells carry the marker and the reason instead of a constant. Requirement
 *     16.2 keeps the column, Requirement 19.5 forbids filling it with a plausible value, and
 *     those two are only compatible if the empty cell explains itself.
 *   * **The timestamp's exact rendering is unchanged** where it exists —
 *     `new Date(created_at).toLocaleString()`, the same call — and is the marker where it does
 *     not. Never the current time.
 *   * **A genuine zero renders as zero.** The login count no longer floors at 1, so an account
 *     with no sign-in events reads `0` and means it.
 *
 * NOT FIXED, AND RECORDED RATHER THAN SMUGGLED: `routers/user.py:174` returns `[]` when the
 * request carries no Supabase client, so "you have no security events" and "we could not read
 * them" arrive identically. Requirement 16.7 puts `backend_app/` out of reach; the page cannot
 * tell them apart and `pageFields.js` says so beside the entries.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * ONE READ, ONE PANEL STATE (Requirement 4.1)
 * ═══════════════════════════════════════════════════════════════════════════
 * The `isLoading` / `error` pair and its authored string — *Unable to load security audit
 * records. Please check your network connection.* — are gone. `usePanelState` drives both
 * regions from the one read, so the 503 `routers/user.py:195` raises reaches the trader as
 * `ds/ErrorState`'s authored copy with a support reference, an empty log reaches them as
 * `empty` rather than as a table with a colspan, and `console.error` goes because logging is
 * not handling (Requirement 11.3). Same path, same `limit=100`, once on mount —
 * `api-paths.budget.js` is untouched.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 7 PIXEL SIZES, BY §2.4'S NINE QUESTIONS
 * ═══════════════════════════════════════════════════════════════════════════
 * Six of the seven resolve by moving onto a primitive that already reads a declared step, so
 * their declaration is removed rather than mapped; one resolves in place.
 *
 *    9 ×1  the summary card labels   Q4 label       → `ds/Metric`'s `--text-micro`
 *   18 ×1  the summary card figures  Q7 value       → `ds/Metric`'s tier-2 step
 *   11 ×1  the error banner          → `ds/Panel`'s error arm; removed
 *   10 ×1  the `<table>`             Q6 table cell  → `ds/DataTable`'s `--text-small`
 *    8 ×1  the `<th>`s               Q4 label       → `ds/DataTable`'s `--text-micro`
 *    9 ×1  the device cell           Q6 table cell  → `--text-small` with its siblings
 *   11 ×1  the search input          Q3 control text → `--text-body`, in place
 *
 * The `<th>` at 8px is the one worth naming: it was the smallest text in the tree outside
 * `PaperTrading.jsx`, two pixels below the floor, and it named the columns of a security log.
 * §2.4 orders Q4 ahead of Q6 exactly so a `<th>` is read as a label rather than as a cell, and
 * `ds/DataTable` already renders it at `--text-micro`.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 3 COLOUR LITERALS (Requirements 5.1, 5.2)
 * ═══════════════════════════════════════════════════════════════════════════
 * All three are the error banner's — `rgba(239,68,68,0.1)` wash, `rgba(239,68,68,0.3)` border
 * and `#fca5a5` text — and all three go with the banner, which is now `ds/Panel`'s `error`
 * state. None was in the palette: they are Tailwind's red ramp rather than this app's
 * `#EF5350`, so mapping them one for one would have re-decided the error hue by hand.
 *
 * Two off-palette constructs the guard cannot see go with them, and they are worth recording
 * because a scan will not find them for the next reader:
 *
 *   * `border: 1px solid ${token.line.default}15` on every row — a token with two hex digits
 *     concatenated onto it, which is a hand-mixed 8% alpha wearing a token's name. No `#` in
 *     the source, so `no-colour-literals` never counted it. `ds/DataTable` owns row separation.
 *   * `placeholder:text-slate-600` on the search input — Tailwind's own slate ramp, which is
 *     not this palette at all. It becomes `placeholder:text-content-muted`.
 */

import React, { useCallback, useMemo, useState } from 'react';
import { Download, RefreshCw, Search } from 'lucide-react';

import { CommandButton } from '../components/ds/CommandButton';
import { DataTable } from '../components/ds/DataTable';
import { Metric, NotAvailableMarker } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { api } from '../api';
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from '../design/pageFields';
import { fromNullable, unavailable } from '../design/reported';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';

/** This page's `pageFields.js` entries by `field`. The one index; see `RiskSettings.jsx`. */
const FIELD = Object.freeze(
  Object.fromEntries(PAGE_FIELDS_BY_PAGE[PAGES.SECURITY_LOGS].map((f) => [f.field, f])),
);

/** The leaf of a `[].column` path, which is what one row element carries. */
const rowKey = (field) => field.path.split('[].')[1];

/**
 * One declared per-row field, as a `Reported<T>`.
 *
 * An entry the audit declared `UNAVAILABLE` has no path to read, so it resolves to its reason
 * without touching the row at all — which is what stops a future `||` fallback from being
 * added to a column the database cannot fill.
 */
const rowField = (row, field) =>
  field.verdict === VERDICT.AVAILABLE
    ? fromNullable(row?.[rowKey(field)], field.reason)
    : unavailable(field.reason);

/**
 * A timestamp the server sent, rendered exactly as the page has always rendered it.
 *
 * `new Date(value).toLocaleString()`, unchanged — and `null` for anything unparseable rather
 * than `Invalid Date` or, as before, the current time.
 */
const formatRecordedAt = (value) => {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleString();
};

/** `event_type` matches the login/auth family the login count is over. */
const isLoginEvent = (eventType) => {
  const text = typeof eventType === 'string' ? eventType.toLowerCase() : '';
  return text.includes('login') || text.includes('auth');
};

/** A CSV field. Unavailable fields export as an empty cell, never as their reason. */
const csvCell = (reported) => (reported.available ? String(reported.value) : '');

/** The marker, for a cell or a card. One marker in the app, carrying the entry's reason. */
const Marker = ({ field, reason }) => (
  <NotAvailableMarker label={field.label} reason={reason ?? field.reason} />
);

export default function SecurityLogs() {
  const [searchTerm, setSearchTerm] = useState('');

  // ── The one read (Requirement 4.1) ────────────────────────────────────────
  //
  // Same path, same limit, once on mount. `[]` resolves to `empty` and a 503 to `error`, so
  // the page's own `isLoading`/`error` pair and its one string are gone.
  const readLogs = useCallback(() => api.user.getSecurityLogs(100), []);
  const logs = usePanelState(readLogs, { deps: [] });
  const logsState = logs.state;

  /**
   * The rows, each field resolved once against its declaration.
   *
   * `id` keeps its random fallback because it is only a React key — the column is `NOT NULL`,
   * so the fallback is unreachable, and it is not rendered.
   */
  const rows = useMemo(() => {
    const body = logs.data?.data ?? logs.data;
    const raw = Array.isArray(body) ? body : (Array.isArray(body?.logs) ? body.logs : []);
    return raw.map((l) => {
      const recordedAt = rowField(l, FIELD.recordedAt);
      return {
        id: l?.id || Math.random().toString(36).substring(2),
        eventType: rowField(l, FIELD.eventType),
        ipAddress: rowField(l, FIELD.ipAddress),
        userAgent: rowField(l, FIELD.userAgent),
        // The exact same formatting call, on the exact same field. `fromNullable` over the
        // FORMATTED string so an unparseable timestamp is an absence and not `Invalid Date`.
        recordedAt: fromNullable(
          recordedAt.available ? formatRecordedAt(recordedAt.value) : null,
          FIELD.recordedAt.reason,
        ),
        // Declared unavailable: no column exists for either. The constants are gone.
        location: rowField(l, FIELD.location),
        outcome: rowField(l, FIELD.outcome),
      };
    });
  }, [logs.data]);

  /**
   * The filter, over the same four fields it always read.
   *
   * `location` stays in the expression even though it can never match now: it was matching the
   * constant `"Secure Session"` before, so every row answered to "secure" — removing the term
   * removes a match that was never about the account's data.
   */
  const filteredRows = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (term === '') return rows;
    const text = (reported) => (reported.available ? String(reported.value).toLowerCase() : '');
    return rows.filter(
      (row) =>
        text(row.eventType).includes(term)
        || text(row.ipAddress).includes(term)
        || text(row.location).includes(term)
        || text(row.userAgent).includes(term),
    );
  }, [rows, searchTerm]);

  /** The one summary figure that is really computable, as `pageFields` declares it. */
  const loginEventCount = useMemo(
    () => rows.filter((row) => isLoginEvent(row.eventType.available ? row.eventType.value : null)).length,
    [rows],
  );

  const handleExport = () => {
    const headers = ['Event', 'IP Address', 'Location', 'Device', 'Time', 'Status'];
    const csvContent = [
      headers.join(','),
      ...filteredRows.map((row) =>
        [row.eventType, row.ipAddress, row.location, row.userAgent, row.recordedAt, row.outcome]
          .map((reported) => `"${csvCell(reported)}"`)
          .join(','),
      ),
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `security_logs_${new Date().toISOString().split('T')[0]}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  /*
   * The six columns, unchanged in order and in heading. `render` is where the marker lives:
   * `ds/DataTable` renders a bare `—` for a blank cell, and Requirement 19.3's point is that
   * the reason is the load-bearing half, so every cell that can be absent renders through the
   * one marker with its declared sentence.
   */
  const columns = useMemo(
    () => [
      {
        key: 'eventType',
        header: FIELD.eventType.label,
        render: ({ value }) =>
          value.available ? (
            <span className="font-medium text-content-primary">{value.value}</span>
          ) : (
            <Marker field={FIELD.eventType} reason={value.reason} />
          ),
      },
      {
        key: 'ipAddress',
        header: FIELD.ipAddress.label,
        render: ({ value }) =>
          value.available ? (
            <span className="font-mono text-content-secondary">{value.value}</span>
          ) : (
            <Marker field={FIELD.ipAddress} reason={value.reason} />
          ),
      },
      {
        key: 'location',
        header: FIELD.location.label,
        // Declared UNAVAILABLE, so this arm is the only arm. The column stays because
        // Requirement 16.2 keeps it and the reason is what makes an empty column honest.
        render: ({ value }) => <Marker field={FIELD.location} reason={value.reason} />,
      },
      {
        key: 'userAgent',
        header: 'Device / Client',
        priority: 3,
        render: ({ value }) =>
          value.available ? (
            <span className="text-content-muted">{value.value}</span>
          ) : (
            <Marker field={FIELD.userAgent} reason={value.reason} />
          ),
      },
      {
        key: 'recordedAt',
        header: FIELD.recordedAt.label,
        render: ({ value }) =>
          value.available ? (
            <span className="text-content-muted">{value.value}</span>
          ) : (
            <Marker field={FIELD.recordedAt} reason={value.reason} />
          ),
      },
      {
        key: 'outcome',
        header: FIELD.outcome.label,
        // Was a green `SUCCESS` chip on every row, derived from a column that does not exist.
        render: ({ value }) => <Marker field={FIELD.outcome} reason={value.reason} />,
      },
    ],
    [],
  );

  const summaryPanelState =
    logsState === PANEL_STATES.EMPTY ? PANEL_STATES.READY : logsState;

  return (
    <div className="flex-1 overflow-y-auto p-5">
      <PageHeader
        title="Security Audit Logs"
        subtitle="Your account authentication records, API access events, and active session history"
        actions={
          <CommandButton
            intent="secondary"
            icon={RefreshCw}
            onClick={logs.refetch}
            loading={logsState === PANEL_STATES.LOADING || logsState === PANEL_STATES.REFRESHING}
            loadingLabel="Reading your security events"
          >
            Refresh
          </CommandButton>
        }
      />

      {/* Three of these four cards were not measurements. Each now renders the marker with the
          sentence `pageFields.js` declares, and the fourth states its real scope in its own
          label. `empty` is resolved to `ready` here on purpose: an account with no events has
          a summary — a login count of zero — and Requirement 19.1 says a zero is a reading. */}
      <Panel
        title="Account security summary"
        state={summaryPanelState}
        loading={{ kind: 'skeleton-metric', rows: 1, columns: 4, label: 'Loading your security summary' }}
        empty={{
          headline: 'No security events yet',
          body: 'Sign-in and access events appear here as they are recorded against your account.',
          action: { label: 'Read again', onClick: logs.refetch },
        }}
        error={{ error: logs.error, onRetry: logs.refetch }}
        unavailable={{ reason: 'Your security summary cannot be read right now.' }}
        unauthorised={{ error: logs.error }}
        className="mb-4"
      >
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {/* fontSize: 9 → --text-micro (label) and fontSize: 18 → the tier-2 figure step,
              both set by `ds/Metric` rather than by this page. */}
          <Metric
            label={FIELD.loginEventCount.label}
            value={loginEventCount}
            format="integer"
            tier={2}
            hint={FIELD.loginEventCount.tooltip}
          />
          <Metric
            label={FIELD.apiCallCount24h.label}
            unavailable
            unavailableReason={FIELD.apiCallCount24h.reason}
            tier={2}
          />
          <Metric
            label={FIELD.failedAttemptCount.label}
            unavailable
            unavailableReason={FIELD.failedAttemptCount.reason}
            tier={2}
          />
          <Metric
            label={FIELD.activeSessionCount.label}
            unavailable
            unavailableReason={FIELD.activeSessionCount.reason}
            tier={2}
          />
        </div>
      </Panel>

      <Panel
        title="Security events"
        state={logsState}
        loading={{ kind: 'skeleton-table', rows: 6, columns: 6, label: 'Loading your security events' }}
        empty={{
          headline: 'No security events are recorded',
          body: 'Every sign-in, sign-out and access event against this account is listed here '
            + 'once it has been recorded.',
          action: { label: 'Read again', onClick: logs.refetch },
        }}
        error={{ error: logs.error, onRetry: logs.refetch }}
        unavailable={{ reason: 'Your security events cannot be read right now.' }}
        unauthorised={{ error: logs.error }}
      >
        <div className="mb-3 flex items-center gap-2.5">
          <Search size={13} aria-hidden="true" className="shrink-0 text-content-muted" />
          {/* fontSize: 11 → --text-body (control text — Q3). The label was a placeholder,
              which is not an accessible name once the field has content. */}
          <label htmlFor="security-log-search" className="sr-only">
            Search security events
          </label>
          <input
            id="security-log-search"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search events, IP addresses, locations, user agents..."
            className="flex-1 border-none bg-transparent text-body text-content-primary outline-none placeholder:text-content-muted"
          />
          <CommandButton
            intent="secondary"
            icon={Download}
            onClick={handleExport}
            disabled={filteredRows.length === 0}
            disabledReason="There are no events in this view to export."
          >
            Export CSV
          </CommandButton>
        </div>

        <DataTable
          caption="Security audit events for this account"
          columns={columns}
          rows={filteredRows}
          pageSize={0}
          density="compact"
        />
      </Panel>
    </div>
  );
}
