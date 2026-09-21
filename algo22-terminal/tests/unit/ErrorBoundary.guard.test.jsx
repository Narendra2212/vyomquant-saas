/**
 * `ErrorBoundary`'s own last resort — vyomquant-ui-redesign task 6.25. Requirement 14.4.
 *
 * A component that throws while rendering an error boundary's fallback is NOT caught by
 * that boundary: React hands the throw to the next boundary up, and there is none above
 * the application root. The page would unmount to blank — the one outcome the boundary
 * exists to prevent.
 *
 * The boundary therefore renders the copy inside a second, tiny boundary. This file is
 * the proof, and forcing `ds/ErrorState` to throw is the only way to reach that path:
 * `translateError` is total over its input and its Requirement 14.4 scrubber (which
 * throws in development) cannot be tripped by any real error, because every string it
 * can return is authored copy. So the failure is injected rather than provoked.
 *
 * It lives in its own file because the mock has to apply to the whole module graph.
 */

import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

vi.mock('../../src/components/ds/ErrorState', () => {
  const Broken = () => {
    throw new Error('the error-copy layer is broken');
  };
  return { ErrorState: Broken, default: Broken };
});

import ErrorBoundary from '../../src/components/ErrorBoundary';
import { CONTEXT_COPY } from '../../src/design/errorCopy';

function Boom() {
  const error = new Error("Cannot read properties of undefined (reading 'unrealized_pnl')");
  error.stack = 'TypeError: boom\n    at PositionsPanel (/src/pages/Dashboard.jsx:412:19)';
  throw error;
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ErrorBoundary: the fallback survives its own copy layer failing', () => {
  it('still says something, still announces it, and still offers a way out', () => {
    render(
      <MemoryRouter>
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>
      </MemoryRouter>,
    );

    // Not a blank page: the authored last-resort copy, which is `CONTEXT_COPY.default`
    // read directly rather than a second wording invented for this path.
    expect(screen.getByText(CONTEXT_COPY.default.headline)).toBeTruthy();
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Copy report details' })).toBeTruthy();

    // And the degraded path leaks no more than the healthy one.
    expect(document.body.textContent).not.toMatch(/\bat\s+\S+\s*\([^)]*:\d+:\d+\)/);
    expect(document.body.textContent).not.toMatch(/\b\w*(?:Error|Exception)\b/);
    expect(document.body.textContent).not.toContain('unrealized_pnl');
  });
});
