/*
 * EXCH-SAVE-01 — regression for the production report "Connection testing is working but
 * the SaaS failed to save API keys".
 *
 * Two page defects hid the cause from the trader:
 *
 *  1. `sanitizeErrorMessage` read only the axios shape (`err.response.status`,
 *     `err.response.data.detail`). Every call on this page goes through `apiClient`, whose
 *     interceptor rejects with an `ApiError` that carries `status` and `data` directly and
 *     has NO `.response`. So every refusal — including `require_aal2`'s 403 — became the
 *     generic "Failed to save exchange keys." These cases reject with a REAL `ApiError`,
 *     built the way the interceptor builds it, not a hand-rolled axios object.
 *
 *  2. The "Testnet / Sandbox Mode" checkbox was rendered and never sent, so a trader who
 *     ticked it had their key tested AND stored as a live credential.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import ExchangeManager from '../../src/pages/ExchangeManager';
import { api } from '../../src/api';
import { ApiError } from '../../src/apiClient';

vi.mock('../../src/api', () => ({
  // Required by tests/test_api_mock_factories_declare_isauthenticated.py: a vi.mock factory
  // replaces the whole module. This suite never signs in.
  isAuthenticated: () => false,
  api: {
    exchange: {
      list: vi.fn(),
      getSupported: vi.fn(),
      getAuthSchema: vi.fn(),
      testConnection: vi.fn(),
      saveKeys: vi.fn(),
      delete: vi.fn(),
      testStoredConnection: vi.fn(),
      reconnect: vi.fn(),
    },
  },
}));

const supported = {
  exchanges: [
    { id: 'bybit', display_name: 'Bybit', spot_support: true, futures_support: true, sandbox_support: true },
  ],
};

const bybitSchema = {
  exchange_id: 'bybit',
  display_name: 'Bybit',
  fields: [
    { name: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: 'Enter API Key' },
    { name: 'secret_key', label: 'Secret Key', type: 'password', required: true, placeholder: 'Enter Secret Key' },
    { name: 'sandbox', label: 'Testnet / Sandbox Mode', type: 'boolean', required: false, placeholder: 'Testnet Mode' },
  ],
};

// Exactly what apiClient's response interceptor rejects with.
const apiError = (status, data) =>
  new ApiError(
    (typeof data?.detail === 'string' ? data.detail : null) || 'Request failed',
    { url: '/api/exchanges/keys', method: 'post', status, data },
  );

async function openBybitForm() {
  render(<ExchangeManager />);
  fireEvent.click(await screen.findByText('Bybit'));
  fireEvent.change(await screen.findByPlaceholderText('Enter API Key'), { target: { value: 'k' } });
  fireEvent.change(screen.getByPlaceholderText('Enter Secret Key'), { target: { value: 's' } });
}

describe('EXCH-SAVE-01 ExchangeManager error shape and testnet toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.exchange.getSupported.mockResolvedValue(supported);
    api.exchange.list.mockResolvedValue([]);
    api.exchange.getAuthSchema.mockResolvedValue(bybitSchema);
  });
  afterEach(() => cleanup());

  it('an MFA 403 on save tells the trader to set up two-factor authentication', async () => {
    api.exchange.saveKeys.mockRejectedValue(
      apiError(403, {
        detail:
          'Two-factor authentication is required to access this resource. Please complete MFA verification and retry.',
      }),
    );
    await openBybitForm();
    fireEvent.click(screen.getByRole('button', { name: /Save Keys/i }));

    expect(await screen.findByText(/Two-factor authentication is required to save or remove exchange keys/i)).toBeDefined();
    expect(screen.queryByText('Failed to save exchange keys.')).toBeNull();
  });

  it('a structured server refusal reaches the trader instead of the generic fallback', async () => {
    api.exchange.saveKeys.mockRejectedValue(
      apiError(400, { detail: { error: 'EXCHANGE_KEY_WITHDRAW_ENABLED', message: 'This API key has withdrawal permission enabled.' } }),
    );
    await openBybitForm();
    fireEvent.click(screen.getByRole('button', { name: /Save Keys/i }));

    expect(await screen.findByText('This API key has withdrawal permission enabled.')).toBeDefined();
  });

  it('a non-MFA 403 still reads as access denied', async () => {
    api.exchange.saveKeys.mockRejectedValue(apiError(403, { detail: 'Account frozen' }));
    await openBybitForm();
    fireEvent.click(screen.getByRole('button', { name: /Save Keys/i }));

    expect(await screen.findByText('Access denied. Insufficient permissions.')).toBeDefined();
  });

  it('an internal leak inside an ApiError is still masked', async () => {
    api.exchange.saveKeys.mockRejectedValue(apiError(500, { detail: 'Traceback (most recent call last): SELECT * FROM vault' }));
    await openBybitForm();
    fireEvent.click(screen.getByRole('button', { name: /Save Keys/i }));

    expect(await screen.findByText('Exchange service temporarily unavailable. Please try again.')).toBeDefined();
    expect(screen.queryByText(/Traceback/)).toBeNull();
  });

  it('ticking Testnet sends sandbox:true on both Test Connection and Save', async () => {
    api.exchange.testConnection.mockResolvedValue({ status: 'CONNECTED' });
    api.exchange.saveKeys.mockResolvedValue({ status: 'ok' });
    await openBybitForm();
    fireEvent.click(screen.getByLabelText(/Testnet Mode/i));

    fireEvent.click(screen.getByRole('button', { name: /Test Connection/i }));
    await waitFor(() =>
      expect(api.exchange.testConnection).toHaveBeenCalledWith(expect.objectContaining({ sandbox: true })),
    );

    fireEvent.click(screen.getByRole('button', { name: /Save Keys/i }));
    await waitFor(() =>
      expect(api.exchange.saveKeys).toHaveBeenCalledWith(expect.objectContaining({ sandbox: true })),
    );
  });

  it('leaving Testnet unticked sends sandbox:false (live)', async () => {
    api.exchange.saveKeys.mockResolvedValue({ status: 'ok' });
    await openBybitForm();
    fireEvent.click(screen.getByRole('button', { name: /Save Keys/i }));
    await waitFor(() =>
      expect(api.exchange.saveKeys).toHaveBeenCalledWith(expect.objectContaining({ sandbox: false })),
    );
  });
});
