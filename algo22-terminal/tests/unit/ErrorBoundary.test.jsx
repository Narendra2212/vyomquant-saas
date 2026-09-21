/**
 * `ErrorBoundary` — vyomquant-ui-redesign task 6.25. Requirements 14.3, 14.4.
 *
 * The thing worth testing hardest is an absence. Until this task the boundary printed
 * `error.message`, `error.stack` and `errorInfo.componentStack` into the DOM and copied
 * all three to the clipboard, so every test here throws an error carrying a realistic
 * V8 stack and then asserts that not one frame, class name, file path or status code
 * reaches the screen or the clipboard — while the stack DOES reach Sentry.
 *
 * `@sentry/react` is stubbed rather than initialised: it is an outbound network SDK, and
 * stubbing it is what makes "the component stack goes to Sentry" an assertion instead of
 * a hope. Nothing else is mocked.
 */

import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const { captureException, getClient } = vi.hoisted(() => ({
  captureException: vi.fn(() => 'evt_9f3c81ab7d24'),
  getClient: vi.fn(() => ({ name: 'stub-client' })),
}));

vi.mock('@sentry/react', () => ({ captureException, getClient }));

import ErrorBoundary from '../../src/components/ErrorBoundary';
import { CONTEXT_COPY } from '../../src/design/errorCopy';

/** A real-shaped render crash: the message and stack that used to be rendered verbatim. */
const CRASH = new Error("Cannot read properties of undefined (reading 'unrealized_pnl')");
CRASH.stack = [
  "TypeError: Cannot read properties of undefined (reading 'unrealized_pnl')",
  '    at PositionsPanel (/src/pages/Dashboard.jsx:412:19)',
  '    at renderWithHooks (/node_modules/react-dom/cjs/react-dom.development.js:15486:18)',
].join('\n');

function Boom() {
  throw CRASH;
}

const mount = (child) =>
  render(
    <MemoryRouter>
      <ErrorBoundary>{child}</ErrorBoundary>
    </MemoryRouter>,
  );

/** Everything Requirement 14.4 forbids as user-facing content. */
const FORBIDDEN_ON_SCREEN = [
  /\bat\s+\S+\s*\([^)]*:\d+:\d+\)/, // a V8 stack frame
  /\n\s+at\s/, //                      a stack frame, minimal form
  /\b\w*(?:Error|Exception)\b/, //     TypeError, AxiosError, ApiError…
  /\b(?:404|429|500|503)\b/, //        a bare HTTP status
  /unrealized_pnl/, //                 the message body
  /Dashboard\.jsx|node_modules/, //    module paths from the stack
];

let clipboardWrites;

beforeEach(() => {
  captureException.mockClear();
  clipboardWrites = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: clipboardWrites },
    configurable: true,
  });
  // React logs every caught error, and the boundary logs its own. Silenced so a
  // passing run is quiet; the calls are still asserted below.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete navigator.clipboard;
});

describe('ErrorBoundary: it renders children until something throws', () => {
  it('renders its children untouched', () => {
    mount(<p>positions</p>);
    expect(screen.getByText('positions')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(captureException).not.toHaveBeenCalled();
  });
});

describe('ErrorBoundary: no stack reaches the screen (Requirement 14.4)', () => {
  it('renders translateError copy and nothing off the error', () => {
    mount(<Boom />);

    // The authored copy for the `default` context, which is what a render crash lands on.
    expect(screen.getByText(CONTEXT_COPY.default.headline)).toBeTruthy();
    expect(screen.getByText(CONTEXT_COPY.default.detail)).toBeTruthy();

    for (const pattern of FORBIDDEN_ON_SCREEN) {
      expect(document.body.textContent, `matched ${pattern}`).not.toMatch(pattern);
    }
  });

  it('leaks nothing for any of the shapes it can be handed', () => {
    const shapes = [
      CRASH,
      'a bare string',
      null,
      { status: 500, message: CRASH.message },
      { status: 503, category: 'SERVER_ERROR', data: { error: { code: 'PORTFOLIO_FETCH_FAILED' } } },
      { type: 'subscription_refused', channel: 'positions', code: 'CHANNEL_REFUSED_UNAUTHORISED' },
    ];

    for (const thrown of shapes) {
      const Throw = () => {
        throw thrown;
      };
      const { unmount } = mount(<Throw />);
      const text = document.body.textContent;
      expect(screen.getByRole('alert').textContent.length, JSON.stringify(thrown)).toBeGreaterThan(10);
      for (const pattern of FORBIDDEN_ON_SCREEN) {
        expect(text, `${JSON.stringify(thrown)} matched ${pattern}`).not.toMatch(pattern);
      }
      unmount();
    }
  });

  it('offers a reload and no duplicate retry', () => {
    mount(<Boom />);
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
  });
});

describe('ErrorBoundary: the stack goes to Sentry (Requirement 14.3)', () => {
  it('reports the error and the component stack, and shows the event id', () => {
    mount(<Boom />);

    expect(captureException).toHaveBeenCalledTimes(1);
    const [reported, hint] = captureException.mock.calls[0];
    expect(reported).toBe(CRASH);
    expect(hint.extra.componentStack).toContain('Boom');
    expect(hint.extra.route).toBe('/');

    // The reference a support conversation can quote — an opaque id, not a diagnostic.
    expect(screen.getByText('evt_9f3c81ab7d24')).toBeTruthy();
    expect(getClient).toHaveBeenCalled();
  });

  it('logs the error for the developer console, which is not user-facing', () => {
    mount(<Boom />);
    const logged = console.error.mock.calls.some((args) => args.includes(CRASH));
    expect(logged).toBe(true);
  });
});

describe('ErrorBoundary: the copy button copies a reference, not a stack', () => {
  it('copies exactly {eventId, timestamp, route}', async () => {
    mount(<Boom />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy report details' }));

    expect(clipboardWrites).toHaveBeenCalledTimes(1);
    const payload = clipboardWrites.mock.calls[0][0];

    for (const pattern of FORBIDDEN_ON_SCREEN) {
      expect(payload, `clipboard matched ${pattern}`).not.toMatch(pattern);
    }
    expect(payload).not.toContain('componentStack');

    const parsed = JSON.parse(payload);
    expect(Object.keys(parsed).sort()).toEqual(['eventId', 'route', 'timestamp']);
    expect(parsed.eventId).toBe('evt_9f3c81ab7d24');
    expect(parsed.route).toBe('/');
    expect(Number.isNaN(Date.parse(parsed.timestamp))).toBe(false);

    expect(await screen.findByRole('status')).toBeTruthy();
  });

  it('does not throw when the browser withholds the clipboard', () => {
    delete navigator.clipboard;
    mount(<Boom />);
    expect(() =>
      fireEvent.click(screen.getByRole('button', { name: 'Copy report details' })),
    ).not.toThrow();
    expect(screen.queryByRole('status')).toBeNull();
  });
});
