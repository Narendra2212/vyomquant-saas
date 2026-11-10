/** MKT-04: the reviewer queue offers only the actions a state admits, and a reject needs a reason. */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const admin = vi.hoisted(() => ({
  listSubmissions: vi.fn(),
  getSubmission: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  publish: vi.fn(),
  suspend: vi.fn(),
  unpublish: vi.fn(),
}));
// The page never signs in; `isAuthenticated` is declared so the factory replaces the module whole.
vi.mock('../../../src/api', () => ({ api: { library: { admin } }, isAuthenticated: () => false }));

import AdminMarketplaceReview from '../../../src/pages/AdminMarketplaceReview';

const SUB = {
  id: 'sub-1',
  source_strategy_id: 'strat-1234-5678',
  version_id: 'v-1',
  owner_id: 'u-1',
  submission_state: 'SUBMITTED',
  eligibility_outcomes: [{ code: 'MP_OWNERSHIP', passed: true }],
};

beforeEach(() => {
  Object.values(admin).forEach((m) => m.mockReset());
  admin.listSubmissions.mockResolvedValue({ items: [{ ...SUB, submitted_at: '2026-11-10T10:00:00Z' }] });
  admin.getSubmission.mockResolvedValue({ submission: SUB, evidence: [], transitions: [] });
});

const renderPage = () => render(<MemoryRouter><AdminMarketplaceReview /></MemoryRouter>);

describe('AdminMarketplaceReview', () => {
  it('lists the SUBMITTED queue and offers approve/reject only', async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/Strategy strat-12/));
    await screen.findByTestId('admin-detail');
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Publish' })).toBeNull();
    expect(admin.listSubmissions).toHaveBeenCalledWith({ state: 'SUBMITTED', pageSize: 100 });
  });

  it('refuses to reject without a reason, then sends the reason', async () => {
    admin.reject.mockResolvedValue({ submission_state: 'REJECTED' });
    renderPage();
    fireEvent.click(await screen.findByText(/Strategy strat-12/));
    await screen.findByTestId('admin-detail');

    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    expect((await screen.findByTestId('admin-notice')).textContent).toMatch(/reason/);
    expect(admin.reject).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: '  Overfit on one regime  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(admin.reject).toHaveBeenCalledWith('sub-1', 'Overfit on one regime'));
  });

  it('an APPROVED submission offers Publish', async () => {
    admin.getSubmission.mockResolvedValue({ submission: { ...SUB, submission_state: 'APPROVED' }, evidence: [], transitions: [] });
    admin.publish.mockResolvedValue({ submission_state: 'PUBLISHED' });
    renderPage();
    fireEvent.click(await screen.findByText(/Strategy strat-12/));
    fireEvent.click(await screen.findByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(admin.publish).toHaveBeenCalledWith('sub-1'));
  });
});
