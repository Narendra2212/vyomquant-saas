/**
 * FE-08 -- the unguarded "Deploy Strategy" button is gone from `DeploymentConsole.jsx`.
 *
 * THE DEFECT
 *   `components/DeploymentConsole.jsx` (rendered as `StrategyDetail.jsx`'s "Deployments"
 *   tab) had a "Deploy Strategy" button in its header that posted directly to the legacy
 *   `POST /api/strategies/{id}/deploy`, with no confirmation dialog, no preflight review,
 *   and no real-funds acknowledgement -- unlike the other two deploy entry points this
 *   app has: the canonical, fully-reviewed flow (`lib/deployFlow.js` +
 *   `components/deploy/DeployConfirmation.jsx`, `POST /api/strategy-operations/.../deploy`)
 *   and `StrategyDetail.jsx`'s own legacy-endpoint call, which IS gated behind a
 *   `ds/ConfirmDialog` (`handleDeploy` only opens the dialog; the request only fires from
 *   its explicit confirm). RT-03 already made the legacy route itself paper-only
 *   server-side regardless of what this console's request body carried, so this control
 *   could not reach live capital -- but it still bypassed the one reviewed path for no
 *   reason, from the same page that offers it, and its own success check
 *   (`response.status === "deployed"`) could never even match what that route actually
 *   returns (`"running"`).
 *
 * THE FIX
 *   The button, its handler, and the dead `deploymentConfig`/`isDeploying` state were
 *   removed outright. The console still loads, displays, and manages (pause/resume/stop)
 *   EXISTING deployments -- nothing about reading or operating a running deployment
 *   changed.
 *
 * WHAT THIS FILE PROVES
 *   * no "Deploy Strategy" control exists anywhere in the rendered console;
 *   * the console never calls `POST /api/strategies/{id}/deploy` on mount or at any
 *     point during this test, proving the removal is of the control, not merely its label;
 *   * the console's real job -- listing deployments and pausing/resuming/stopping one --
 *     is unaffected.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const { mockPost, mockGet } = vi.hoisted(() => ({
  mockPost: vi.fn(async () => ({})),
  mockGet: vi.fn(async (url) => {
    if (String(url).endsWith('/deployments')) {
      return {
        deployments: [
          { deployment_id: 'd-1', status: 'paused', symbol: 'BTCUSDT', exchange: 'binance', environment: 'paper' },
        ],
      };
    }
    return {};
  }),
}));

vi.mock('../../src/api', () => ({ post: mockPost, get: mockGet }));

import DeploymentConsole from '../../src/components/DeploymentConsole';

describe('FE-08: DeploymentConsole has no unguarded deploy control', () => {
  it('renders with no "Deploy Strategy" button anywhere', async () => {
    render(<DeploymentConsole strategyId="s-1" versionId="v-1" />);

    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    expect(screen.queryByText('Deploy Strategy')).toBeNull();
    expect(screen.queryByText('Deploying...')).toBeNull();
  });

  it('never posts to the legacy deploy endpoint', async () => {
    render(<DeploymentConsole strategyId="s-1" versionId="v-1" />);

    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    const deployCalls = mockPost.mock.calls.filter(([url]) => String(url).endsWith('/s-1/deploy'));
    expect(deployCalls).toEqual([]);
  });

  it('still lists existing deployments', async () => {
    render(<DeploymentConsole strategyId="s-1" versionId="v-1" />);

    await screen.findByText('PAPER');
    expect(screen.getByText('paused')).toBeTruthy();
  });
});
