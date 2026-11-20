/*
 * LAUNCH AUDIT — "Not connected to the trading engine" on pages that never need a socket.
 * The client started in `disconnected` (error group), so the top bar's red strip showed on an
 * unsaved strategy in the Builder and every other page with no live channel. A client nobody
 * has asked to connect — or one released on purpose — is `idle`, which is neutral.
 */
import { describe, it, expect } from 'vitest';
import wsClient from '../../../src/websocketClient';
import { connectionPresentation, isConnectionDown } from '../../../src/components/shell/ConnectionStatusIndicator';

describe('an unused socket is idle, not down', () => {
  it('starts idle, and idle does not raise the down strip', () => {
    expect(wsClient.getStatus()).toBe('idle');
    expect(isConnectionDown('idle')).toBe(false);
    expect(connectionPresentation('idle').label).toBe('Idle');
  });

  it('a deliberate disconnect returns to idle and stays there', async () => {
    const closes = [];
    wsClient.ws = { close: () => closes.push(1), onclose: () => wsClient._setStatus('disconnected') };
    wsClient.disconnect();
    expect(closes).toHaveLength(1);
    await new Promise((r) => setTimeout(r, 0));
    expect(wsClient.getStatus()).toBe('idle');
  });

  it('a real drop or failure is still down', () => {
    for (const status of ['disconnected', 'error', 'failed']) {
      expect(isConnectionDown(status)).toBe(true);
    }
  });
});
