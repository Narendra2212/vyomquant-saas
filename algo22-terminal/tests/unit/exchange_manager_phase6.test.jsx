import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import ExchangeManager from '../../src/pages/ExchangeManager';
import { api } from '../../src/api';

vi.mock('../../src/api', () => ({
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
    }
  }
}));

describe('Phase 6B ExchangeManager Contract & Lifecycle Test Suite', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  const mockSupportedExchanges = {
    exchanges: [
      {
        id: 'binance',
        display_name: 'Binance',
        spot_support: true,
        futures_support: true,
        sandbox_support: true,
      },
      {
        id: 'gateio',
        display_name: 'Gate.io',
        spot_support: true,
        futures_support: true,
        sandbox_support: false,
      },
      {
        id: 'kucoin',
        display_name: 'KuCoin',
        spot_support: true,
        futures_support: true,
        sandbox_support: true,
      }
    ]
  };

  const mockConnectedExchanges = [
    {
      id: 'usr_123_binance',
      exchange_id: 'binance',
      name: 'BINANCE',
      credential_configured: true,
      masked_key: 'BIN••••••••••••••••••••••••CE',
      status: 'CONNECTED',
      permissions: ['Spot Trading', 'Read'],
      bot_count: 2,
      strategy_count: 3,
      health: 'healthy',
      connected_at: '2026-08-20T12:00:00Z',
    },
    {
      id: 'usr_123_gateio',
      exchange_id: 'gateio',
      name: 'GATEIO',
      credential_configured: true,
      masked_key: 'GAT••••••••••••••••••••••••IO',
      status: 'CONNECTED',
      permissions: ['Spot Trading'],
      bot_count: 0,
      strategy_count: 1,
      health: 'healthy',
      connected_at: '2026-08-22T10:00:00Z',
    }
  ];

  const mockBinanceSchema = {
    exchange_id: 'binance',
    display_name: 'Binance',
    fields: [
      { name: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: 'Enter API Key' },
      { name: 'secret_key', label: 'Secret Key', type: 'password', required: true, placeholder: 'Enter Secret Key' },
      { name: 'label', label: 'Connection Label', type: 'text', required: false, placeholder: 'e.g. Main' }
    ]
  };

  const mockKucoinSchema = {
    exchange_id: 'kucoin',
    display_name: 'KuCoin',
    fields: [
      { name: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: 'Enter API Key' },
      { name: 'secret_key', label: 'Secret Key', type: 'password', required: true, placeholder: 'Enter Secret Key' },
      { name: 'password', label: 'Passphrase', type: 'password', required: true, placeholder: 'Enter Passphrase' }
    ]
  };

  it('1. Loads and renders connected exchanges with authoritative bot counts and masked keys', async () => {
    api.exchange.getSupported.mockResolvedValue(mockSupportedExchanges);
    api.exchange.list.mockResolvedValue(mockConnectedExchanges);

    render(<ExchangeManager />);

    expect(await screen.findByText('BINANCE')).toBeDefined();
    expect(screen.getByText('GATEIO')).toBeDefined();
    expect(screen.getByText('BIN••••••••••••••••••••••••CE')).toBeDefined();
    expect(screen.getByText('GAT••••••••••••••••••••••••IO')).toBeDefined();
  });

  it('2. Resilient partial failure handling: loads connected exchanges even if supported list fails', async () => {
    api.exchange.getSupported.mockRejectedValue(new Error('Network error loading supported exchanges'));
    api.exchange.list.mockResolvedValue(mockConnectedExchanges);

    render(<ExchangeManager />);

    expect(await screen.findByText('BINANCE')).toBeDefined();
    expect(screen.getByText('BIN••••••••••••••••••••••••CE')).toBeDefined();
  });

  it('3. Dynamically renders credential schema for selected exchange', async () => {
    api.exchange.getSupported.mockResolvedValue(mockSupportedExchanges);
    api.exchange.list.mockResolvedValue([]);
    api.exchange.getAuthSchema.mockResolvedValue(mockKucoinSchema);

    render(<ExchangeManager />);

    expect(await screen.findByText('KuCoin')).toBeDefined();

    // Click KuCoin in supported directory
    fireEvent.click(screen.getByText('KuCoin'));

    expect(await screen.findByText('Passphrase')).toBeDefined();
    expect(screen.getByPlaceholderText('Enter API Key')).toBeDefined();
    expect(screen.getByPlaceholderText('Enter Secret Key')).toBeDefined();
    expect(screen.getByPlaceholderText('Enter Passphrase')).toBeDefined();
  });

  it('4. Successfully tests connection and renders success toast', async () => {
    api.exchange.getSupported.mockResolvedValue(mockSupportedExchanges);
    api.exchange.list.mockResolvedValue([]);
    api.exchange.getAuthSchema.mockResolvedValue(mockBinanceSchema);
    api.exchange.testConnection.mockResolvedValue({
      status: 'ok',
      usdt_balance: 1450.50,
      clock_sync: '12ms'
    });

    render(<ExchangeManager />);

    expect(await screen.findByText('Binance')).toBeDefined();
    fireEvent.click(screen.getByText('Binance'));

    expect(await screen.findByPlaceholderText('Enter API Key')).toBeDefined();

    fireEvent.change(screen.getByPlaceholderText('Enter API Key'), { target: { value: 'bin_key_123' } });
    fireEvent.change(screen.getByPlaceholderText('Enter Secret Key'), { target: { value: 'bin_sec_456' } });

    const testBtn = screen.getByRole('button', { name: /Test Connection/i });
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(api.exchange.testConnection).toHaveBeenCalledWith({
        exchange_id: 'binance',
        api_key: 'bin_key_123',
        secret_key: 'bin_sec_456',
        password: undefined,
        uid: undefined
      });
    });

    expect(await screen.findByText(/Connection verified! Balance: \$1450.5 USDT/i)).toBeDefined();
  });

  it('5. Successfully saves credentials and refreshes connected exchange list', async () => {
    api.exchange.getSupported.mockResolvedValue(mockSupportedExchanges);
    api.exchange.list.mockResolvedValueOnce([]).mockResolvedValueOnce(mockConnectedExchanges);
    api.exchange.getAuthSchema.mockResolvedValue(mockBinanceSchema);
    api.exchange.saveKeys.mockResolvedValue({ status: 'ok' });

    render(<ExchangeManager />);

    expect(await screen.findByText('Binance')).toBeDefined();
    fireEvent.click(screen.getByText('Binance'));

    expect(await screen.findByPlaceholderText('Enter API Key')).toBeDefined();

    fireEvent.change(screen.getByPlaceholderText('Enter API Key'), { target: { value: 'bin_key_123' } });
    fireEvent.change(screen.getByPlaceholderText('Enter Secret Key'), { target: { value: 'bin_sec_456' } });

    const saveBtn = screen.getByRole('button', { name: /Save Keys/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(api.exchange.saveKeys).toHaveBeenCalledWith({
        exchange_id: 'binance',
        api_key: 'bin_key_123',
        secret_key: 'bin_sec_456',
        password: undefined,
        uid: undefined,
        label: ''
      });
    });

    expect(await screen.findByText(/Exchange keys encrypted and stored securely/i)).toBeDefined();
  });

  it('6. Blocks deletion of exchange with active deployed bots', async () => {
    api.exchange.getSupported.mockResolvedValue(mockSupportedExchanges);
    api.exchange.list.mockResolvedValue(mockConnectedExchanges);

    render(<ExchangeManager />);

    expect(await screen.findByText('BINANCE')).toBeDefined();

    // Attempt to disconnect Binance (bot_count = 2)
    const deleteButtons = screen.getAllByTitle('Disconnect');
    fireEvent.click(deleteButtons[0]);

    expect(await screen.findByText(/Cannot delete: 2 active bot\(s\) running/i)).toBeDefined();
    expect(api.exchange.delete).not.toHaveBeenCalled();
  });

  it('7. Sanitizes raw backend exceptions in toast notifications', async () => {
    api.exchange.getSupported.mockResolvedValue(mockSupportedExchanges);
    api.exchange.list.mockResolvedValue([]);
    api.exchange.getAuthSchema.mockResolvedValue(mockBinanceSchema);
    api.exchange.testConnection.mockRejectedValue({
      response: {
        status: 500,
        data: {
          detail: 'Traceback (most recent call last): File "internal/db.py", line 42 in query: SELECT * FROM vault'
        }
      }
    });

    render(<ExchangeManager />);

    expect(await screen.findByText('Binance')).toBeDefined();
    fireEvent.click(screen.getByText('Binance'));

    expect(await screen.findByPlaceholderText('Enter API Key')).toBeDefined();

    fireEvent.change(screen.getByPlaceholderText('Enter API Key'), { target: { value: 'bad_key' } });
    fireEvent.change(screen.getByPlaceholderText('Enter Secret Key'), { target: { value: 'bad_sec' } });

    const testBtn = screen.getByRole('button', { name: /Test Connection/i });
    fireEvent.click(testBtn);

    expect(await screen.findByText('Exchange service temporarily unavailable. Please try again.')).toBeDefined();
    expect(screen.queryByText(/Traceback/i)).toBeNull();
    expect(screen.queryByText(/SELECT \* FROM/i)).toBeNull();
  });
});
