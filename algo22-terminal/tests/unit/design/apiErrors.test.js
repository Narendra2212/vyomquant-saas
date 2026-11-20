import { describe, it, expect } from 'vitest';
import { apiErrorStatus, apiErrorDetail, apiErrorMessage } from '../../../src/design/apiErrors';

const apiError = (status, data) => Object.assign(new Error('Request failed'), { name: 'ApiError', status, data });

describe('apiErrors reads both ApiError and axios shapes', () => {
  it('reads status and detail off an ApiError (no .response)', () => {
    const err = apiError(403, { detail: { code: 'X', message: 'Not on your plan.' } });
    expect(apiErrorStatus(err)).toBe(403);
    expect(apiErrorDetail(err)).toEqual({ code: 'X', message: 'Not on your plan.' });
    expect(apiErrorMessage(err)).toBe('Not on your plan.');
  });

  it('reads the axios-native shape too', () => {
    const err = { response: { status: 400, data: { detail: 'Bad input.' } } };
    expect(apiErrorStatus(err)).toBe(400);
    expect(apiErrorMessage(err)).toBe('Bad input.');
  });

  it('never shows a leaked internal', () => {
    const err = apiError(500, { detail: 'Traceback (most recent call last): SELECT * FROM x' });
    expect(apiErrorMessage(err, 'Fallback.')).toBe('Fallback.');
  });

  it('falls back when nothing usable is present', () => {
    expect(apiErrorMessage(new Error(''), 'Fallback.')).toBe('Fallback.');
    expect(apiErrorStatus(new Error('x'))).toBeNull();
  });
});
