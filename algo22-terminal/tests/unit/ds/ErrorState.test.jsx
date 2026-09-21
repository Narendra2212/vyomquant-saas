/**
 * `ds/ErrorState` — vyomquant-ui-redesign task 6.1. Requirements 14.3, 14.4, 19.4.
 *
 * The one thing worth testing hardest is the absence: no branch of this component may
 * put `error.message` on screen. Every error below carries a distinctive message, and
 * every test asserts it is nowhere in the rendered output.
 */

import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { ErrorState } from '../../../src/components/ds/ErrorState';
import { translateError } from '../../../src/design/errorCopy';

const LEAK = 'TypeError: Cannot read properties of undefined (reading \'unrealized_pnl\') at line 412';

const mount = (props) => render(<MemoryRouter><ErrorState {...props} /></MemoryRouter>);

/** An `ApiError`-shaped rejection: the fields `errorCopy.js` actually reads. */
const apiError = ({ status, code, category, requestId, message = LEAK, retryable }) => ({
  status,
  category,
  requestId,
  message,
  data: code ? { error: { code, message } } : undefined,
  isRetryable: () => retryable === true,
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('ErrorState: it renders only translateError output (Requirements 14.3, 14.4)', () => {
  it('never renders error.message, even when that is all the error carries', () => {
    mount({ error: new Error(LEAK), context: 'positions' });
    expect(document.body.textContent).not.toContain('TypeError');
    expect(document.body.textContent).not.toContain('unrealized_pnl');
    expect(document.body.textContent).not.toContain('412');
    // …and it still says something useful.
    expect(screen.getByRole('alert').textContent.length).toBeGreaterThan(20);
  });

  it('renders the translation for a named backend code', () => {
    const error = apiError({ status: 503, code: 'PORTFOLIO_FETCH_FAILED', category: 'SERVER_ERROR', retryable: true });
    const copy = translateError(error, 'portfolio');
    mount({ error, context: 'portfolio', onRetry: () => {} });
    expect(screen.getByText(copy.headline)).toBeTruthy();
    expect(screen.getByText(copy.detail)).toBeTruthy();
    expect(document.body.textContent).not.toContain(LEAK);
  });

  it('renders no status code, class name or path for any of the shapes it is handed', () => {
    const shapes = [
      new Error(LEAK),
      'a bare string',
      null,
      undefined,
      { status: 500, message: LEAK },
      apiError({ status: 404, code: 'STRATEGY_NOT_FOUND', category: 'CLIENT_ERROR' }),
      apiError({ status: 429, category: 'CLIENT_ERROR', retryable: true }),
      { type: 'subscription_refused', channel: 'positions', code: 'CHANNEL_REFUSED_UNAUTHORISED' },
    ];
    for (const error of shapes) {
      const { unmount } = mount({ error, context: 'positions' });
      const text = screen.getByRole('alert').textContent;
      expect(text.length, JSON.stringify(error)).toBeGreaterThan(10);
      expect(text).not.toMatch(/\b(?:404|429|500|503)\b/);
      expect(text).not.toContain('TypeError');
      expect(text).not.toContain('/api/');
      unmount();
    }
  });
});

describe('ErrorState: retry is offered iff the translation says retryable (Requirement 19.4)', () => {
  it('offers retry for a retryable failure with a handler', () => {
    const onRetry = vi.fn();
    const error = apiError({ status: 503, code: 'PORTFOLIO_FETCH_FAILED', category: 'SERVER_ERROR', retryable: true });
    mount({ error, context: 'portfolio', onRetry });
    const button = screen.getByRole('button', { name: /try again/i });
    button.click();
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('alert').getAttribute('data-error-retryable')).toBe('true');
  });

  it('offers no retry for a permanent refusal, even when a handler is supplied', () => {
    // A retry button on `MARKETPLACE_NOT_SUBSCRIBED` invites a trader to keep pressing
    // something that will keep failing.
    const error = apiError({ status: 403, code: 'MARKETPLACE_NOT_SUBSCRIBED', category: 'CLIENT_ERROR' });
    mount({ error, context: 'marketplace', onRetry: () => {} });
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
    expect(screen.getByRole('alert').getAttribute('data-error-retryable')).toBe('false');
  });

  it('offers no retry when the failure is retryable but no handler was supplied', () => {
    const error = apiError({ status: 503, code: 'PORTFOLIO_FETCH_FAILED', category: 'SERVER_ERROR', retryable: true });
    mount({ error, context: 'portfolio' });
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
    // The translation's own answer is still published, so the absence is the caller's.
    expect(screen.getByRole('alert').getAttribute('data-error-retryable')).toBe('true');
  });

  it('renders the translation\'s own next action', () => {
    mount({ error: { category: 'AUTH_ERROR' }, context: 'dashboard' });
    expect(screen.getByRole('link', { name: 'Sign in' })).toBeTruthy();
  });
});

describe('ErrorState: the correlation reference', () => {
  it('renders a request id as a labelled reference', () => {
    const error = apiError({ status: 500, category: 'SERVER_ERROR', requestId: 'req_7f3c81ab' });
    mount({ error, context: 'positions' });
    expect(screen.getByText('req_7f3c81ab')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('Support reference');
  });

  it('renders the backend envelope\'s own request_id', () => {
    mount({
      error: { status: 500, category: 'SERVER_ERROR', data: { request_id: 'srv-99a2', error: { code: 'NOPE' } } },
      context: 'positions',
    });
    expect(screen.getByText('srv-99a2')).toBeTruthy();
  });

  it('renders no reference block when there is no reference', () => {
    mount({ error: new Error(LEAK), context: 'positions' });
    expect(screen.getByRole('alert').textContent).not.toContain('Support reference');
  });
});

describe('ErrorState: it is announced', () => {
  it('is an alert, because a failed read has just removed data from the screen', () => {
    mount({ error: new Error(LEAK), context: 'positions' });
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('stays an alert when compact', () => {
    mount({ error: new Error(LEAK), context: 'positions', compact: true });
    const alert = screen.getByRole('alert');
    expect(alert.className).toContain('py-4');
    expect(alert.className).not.toContain('py-8');
  });

  it('accepts no colour prop and takes the error hue from semantic.js', () => {
    mount({ error: new Error(LEAK), context: 'positions' });
    const icon = screen.getByRole('alert').querySelector('svg');
    expect(icon.getAttribute('aria-hidden')).toBe('true');
    // `#EF5350` is `--color-status-error`, reached through `statusToken('error')`.
    expect(icon.style.color).toBe('rgb(239, 83, 80)');
  });
});
