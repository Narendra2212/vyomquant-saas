/**
 * The Strategies page's rename path, at the API layer (task 16.2).
 *
 * Requirement 2.6. `Strategies.jsx` had always issued `PUT /api/strategies/{id}/rename`
 * with `{name}` through a raw `fetch`, against a route that existed nowhere in the
 * backend — the page was right about which endpoint should exist, and task 5.3 made it
 * real. What is asserted here is the seam between the page and that endpoint:
 *
 * * **The request.** One PUT to `/api/strategies/{id}/rename` carrying `{name}` and
 *   nothing else, with the id encoded rather than interpolated raw.
 * * **The refusals.** The server's own 422 `STRATEGY_NAME_INVALID`, 409
 *   `STRATEGY_ARCHIVED` and 404 reach the caller as thrown errors, so a rejected rename
 *   cannot read as a success and the page can say why it was rejected.
 * * **The call site.** The handler goes through the shared `endpoints` module, not a raw
 *   `fetch` — which is what carries the auth header, the retry/circuit policy and the
 *   normalized error body every other strategy action already gets.
 *
 * The page's own prompt/reload behaviour is task 16.6's subject and is not duplicated here.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
}));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: (...args) => mockClient.del(...args),
}));

import { strategiesApi } from '../../src/api/modules/strategies';

/** The 200 body `rename_strategy` answers with: the updated strategy record. */
const renamed = (name) => ({
  id: 's-1',
  user_id: 'u-1',
  name,
  current_version: 'v1.0',
  updated_at: '2024-01-02T00:00:00Z',
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('strategiesApi.rename', () => {
  it('puts the new name to the real rename endpoint', async () => {
    mockClient.put.mockResolvedValue(renamed('Momentum v2'));

    const data = await strategiesApi.rename('s-1', 'Momentum v2');

    expect(mockClient.put).toHaveBeenCalledTimes(1);
    const [url, body] = mockClient.put.mock.calls[0];

    expect(url).toBe('/api/strategies/s-1/rename');
    expect(body).toEqual({ name: 'Momentum v2' });
    expect(data.name).toBe('Momentum v2');
  });

  it('sends the name and nothing else, so only the display name can be written', async () => {
    mockClient.put.mockResolvedValue(renamed('Renamed'));

    await strategiesApi.rename('s-1', 'Renamed');

    const [, body] = mockClient.put.mock.calls[0];
    expect(Object.keys(body)).toEqual(['name']);
    // Requirement 2.6: every version, backtest, deployment and signal record is left
    // unchanged, which holds by construction when the body names no other field.
    for (const forbidden of ['status', 'current_version', 'graph', 'dag', 'archived_at']) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it('encodes the strategy id rather than interpolating it raw', async () => {
    mockClient.put.mockResolvedValue(renamed('x'));

    await strategiesApi.rename('a/b?c', 'x');

    expect(mockClient.put.mock.calls[0][0]).toBe('/api/strategies/a%2Fb%3Fc/rename');
  });

  it('does not trim on the client — the server measures and stores the trimmed name', async () => {
    mockClient.put.mockResolvedValue(renamed('Padded'));

    await strategiesApi.rename('s-1', '  Padded  ');

    // One authority for Requirement 2.6's bounds, and it is the backend's. A client-side
    // trim here would mean two places deciding what "1-100 characters" measures.
    expect(mockClient.put.mock.calls[0][1]).toEqual({ name: '  Padded  ' });
  });

  it('propagates a refused name rather than reporting it as a rename', async () => {
    const refusal = Object.assign(new Error('A strategy name cannot be empty once trimmed.'), {
      status: 422,
      data: { error: 'STRATEGY_NAME_INVALID', details: { reason: 'empty_after_trim' } },
    });
    mockClient.put.mockRejectedValue(refusal);

    await expect(strategiesApi.rename('s-1', '   ')).rejects.toThrow(
      'A strategy name cannot be empty once trimmed.',
    );
  });

  it('propagates an archived strategy and a strategy that is not the caller\'s', async () => {
    mockClient.put.mockRejectedValueOnce(
      Object.assign(new Error('This strategy is archived.'), {
        status: 409,
        data: { error: 'STRATEGY_ARCHIVED' },
      }),
    );
    await expect(strategiesApi.rename('s-1', 'x')).rejects.toThrow('This strategy is archived.');

    mockClient.put.mockRejectedValueOnce(
      Object.assign(new Error('Strategy not found.'), { status: 404 }),
    );
    await expect(strategiesApi.rename('s-nope', 'x')).rejects.toThrow('Strategy not found.');
  });
});

describe('Strategies.jsx rename call site', () => {
  const source = readFileSync(
    path.resolve(__dirname, '../../src/pages/Strategies.jsx'),
    'utf-8',
  );

  it('goes through the shared endpoints module', () => {
    expect(source).toContain('endpoints.strategies.rename(');
  });

  it('no longer reaches the rename route through a raw fetch', () => {
    expect(source).not.toMatch(/fetch\([^)]*\/rename/);
  });
});
