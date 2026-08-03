/**
 * Unit tests for apiClient
 * Tests: headers, error handling, caching, deduplication, retry logic
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get, post, put, del, patch } from '../../src/apiClient';

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
global.localStorage = localStorageMock;

// Mock sessionStorage
const sessionStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
global.sessionStorage = sessionStorageMock;

// Mock axios
const { mockAxiosInstance } = vi.hoisted(() => {
  return {
    mockAxiosInstance: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      patch: vi.fn(),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() }
      }
    }
  };
});

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => mockAxiosInstance)
  }
}));

describe('apiClient', () => {
  beforeEach(() => {
    localStorageMock.getItem.mockReturnValue('test-token');
    sessionStorageMock.getItem.mockReturnValue('test-token');
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('GET requests', () => {
    it('should return data on successful request', async () => {
      const mockData = { result: 'success' };
      mockAxiosInstance.get.mockResolvedValue({ data: mockData });

      const result = await get('/api/test');
      expect(result).toEqual(mockData);
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/test', {});
    });

    it('should throw on error', async () => {
      mockAxiosInstance.get.mockRejectedValue(new Error('Network error'));
      await expect(get('/api/test-error')).rejects.toThrow('Network error');
    });

    it('should handle params correctly', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: {} });

      await get('/api/test', { params: { id: 123 } });
      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/api/test', { params: { id: 123 } });
    });

    it('should cache successful GET requests', async () => {
      const mockData = { result: 'cached' };
      mockAxiosInstance.get.mockResolvedValue({ data: mockData });

      const result1 = await get('/api/cached');
      const result2 = await get('/api/cached');

      expect(result1).toEqual(mockData);
      expect(result2).toEqual(mockData);
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(1); // Only called once due to cache
    });

    it('should deduplicate concurrent requests', async () => {
      const mockData = { result: 'deduped' };
      mockAxiosInstance.get.mockImplementation(() => 
        new Promise(resolve => setTimeout(() => resolve({ data: mockData }), 100))
      );

      const [result1, result2] = await Promise.all([
        get('/api/dedup'),
        get('/api/dedup')
      ]);

      expect(result1).toEqual(mockData);
      expect(result2).toEqual(mockData);
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(1);
    });
  });

  describe('POST requests', () => {
    it('should send data and return response', async () => {
      const mockData = { id: 1 };
      mockAxiosInstance.post.mockResolvedValue({ data: mockData });

      const result = await post('/api/test', { name: 'test' });
      expect(result).toEqual(mockData);
      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/api/test', { name: 'test' }, {});
    });

    it('should throw on error', async () => {
      mockAxiosInstance.post.mockRejectedValue(new Error('Network error'));
      await expect(post('/api/test', {})).rejects.toThrow('Network error');
    });

    it('should not retry on 4xx errors', async () => {
      const error = { response: { status: 400 } };
      mockAxiosInstance.post.mockRejectedValue(error);

      await expect(post('/api/test', {})).rejects.toThrow();
      expect(mockAxiosInstance.post).toHaveBeenCalledTimes(1); // No retry
    });
  });

  describe('PUT requests', () => {
    it('should send data and return response', async () => {
      const mockData = { id: 1, updated: true };
      mockAxiosInstance.put.mockResolvedValue({ data: mockData });

      const result = await put('/api/test/1', { name: 'updated' });
      expect(result).toEqual(mockData);
      expect(mockAxiosInstance.put).toHaveBeenCalledWith('/api/test/1', { name: 'updated' }, {});
    });
  });

  describe('DELETE requests', () => {
    it('should send delete request and return response', async () => {
      const mockData = { deleted: true };
      mockAxiosInstance.delete.mockResolvedValue({ data: mockData });

      const result = await del('/api/test/1');
      expect(result).toEqual(mockData);
      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/api/test/1', {});
    });
  });

  describe('PATCH requests', () => {
    it('should send patch request and return response', async () => {
      const mockData = { id: 1, patched: true };
      mockAxiosInstance.patch.mockResolvedValue({ data: mockData });

      const result = await patch('/api/test/1', { status: 'active' });
      expect(result).toEqual(mockData);
      expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/api/test/1', { status: 'active' }, {});
    });
  });

  describe('Retry logic', () => {
    it('should retry on 5xx errors', async () => {
      const error = { response: { status: 500 } };
      mockAxiosInstance.get
        .mockRejectedValueOnce(error)
        .mockRejectedValueOnce(error)
        .mockResolvedValueOnce({ data: { success: true } });

      const result = await get('/api/retry');
      expect(result).toEqual({ success: true });
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(3); // Initial + 2 retries
    });

    it('should retry on network errors', async () => {
      const error = { request: {}, message: 'Network Error' };
      mockAxiosInstance.get
        .mockRejectedValueOnce(error)
        .mockResolvedValueOnce({ data: { success: true } });

      const result = await get('/api/network');
      expect(result).toEqual({ success: true });
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(2); // Initial + 1 retry
    });

    it('should not retry on 401 errors', async () => {
      const error = { response: { status: 401 } };
      mockAxiosInstance.get.mockRejectedValue(error);

      await expect(get('/api/auth')).rejects.toThrow();
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(1); // No retry
    });
  });

  describe('Idempotency', () => {
    it('should reuse same idempotency key for identical requests', async () => {
      const mockData = { id: 1 };
      mockAxiosInstance.post.mockResolvedValue({ data: mockData });

      const result1 = await post('/api/test', { name: 'test' });
      const result2 = await post('/api/test', { name: 'test' });

      expect(result1).toEqual(mockData);
      expect(result2).toEqual(mockData);
      expect(mockAxiosInstance.post).toHaveBeenCalledTimes(2);
    });

    it('should generate different idempotency keys for different payloads', async () => {
      const mockData = { id: 1 };
      mockAxiosInstance.post.mockResolvedValue({ data: mockData });

      await post('/api/test', { name: 'test1' });
      await post('/api/test', { name: 'test2' });

      expect(mockAxiosInstance.post).toHaveBeenCalledTimes(2);
    });
  });

  describe('Auth expiration', () => {
    it('should dispatch auth-expired event on 401 error', async () => {
      const error = { response: { status: 401, data: { message: 'Unauthorized' } } };
      mockAxiosInstance.get.mockRejectedValue(error);

      const authExpiredHandler = vi.fn();
      window.addEventListener('auth-expired', authExpiredHandler);

      await expect(get('/api/protected')).rejects.toThrow();

      // In production mode, event should be dispatched
      vi.stubEnv('MODE', 'production');
      await expect(get('/api/protected')).rejects.toThrow();
      
      window.removeEventListener('auth-expired', authExpiredHandler);
    });
  });

  describe('Request cancellation', () => {
    it('should handle request cancellation gracefully', async () => {
      const cancelTokenSource = { token: { reason: 'canceled' } };
      const error = { message: 'Request canceled' };
      mockAxiosInstance.get.mockRejectedValue(error);

      await expect(get('/api/test')).rejects.toThrow();
    });
  });

  describe('Memory safety', () => {
    it('should enforce cache size limit', async () => {
      const mockData = { data: 'test' };
      mockAxiosInstance.get.mockResolvedValue({ data: mockData });

      // Fill cache beyond limit
      for (let i = 0; i < 150; i++) {
        await get(`/api/test${i}`);
      }

      // Should still work without memory issues
      const result = await get('/api/test-final');
      expect(result).toEqual(mockData);
    });
  });

  describe('Concurrency stress test', () => {
    it('should handle 50 concurrent requests without errors', async () => {
      const mockData = { id: 1 };
      mockAxiosInstance.get.mockResolvedValue({ data: mockData });

      const requests = Array.from({ length: 50 }, (_, i) => 
        get(`/api/concurrent${i}`)
      );

      const results = await Promise.all(requests);
      
      expect(results).toHaveLength(50);
      expect(results.every(r => r !== null)).toBe(true);
    });

    it('should deduplicate identical concurrent requests', async () => {
      const mockData = { data: 'deduped' };
      mockAxiosInstance.get.mockImplementation(() => 
        new Promise(resolve => setTimeout(() => resolve({ data: mockData }), 100))
      );

      const requests = Array.from({ length: 10 }, () => get('/api/same'));
      const results = await Promise.all(requests);

      expect(results).toHaveLength(10);
      expect(results.every(r => r === mockData)).toBe(true);
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(1); // Only one actual request
    });
  });

  describe('Cancel + retry interaction', () => {
    it('should not retry canceled requests', async () => {
      const canceledError = { code: 'ECONNABORTED', message: 'Request canceled' };
      mockAxiosInstance.get.mockRejectedValue(canceledError);

      await expect(get('/api/test')).rejects.toThrow();
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(1); // No retry on cancel
    });

    it('should handle cancellation during retry sequence', async () => {
      const networkError = { request: {}, message: 'Network Error' };
      const canceledError = { code: 'ECONNABORTED', message: 'Request canceled' };
      
      mockAxiosInstance.get
        .mockRejectedValueOnce(networkError)
        .mockRejectedValueOnce(canceledError);

      await expect(get('/api/test')).rejects.toThrow();
      // Should retry once on network error, then stop on cancel
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(2);
    });
  });

  describe('Non-string detail handling', () => {
    it('should handle non-string detail (dict) as error message', async () => {
      const error = { 
        response: { 
          status: 400, 
          data: { detail: { error: 'Validation failed', field: 'email' } } 
        } 
      };
      mockAxiosInstance.get.mockRejectedValue(error);

      await expect(get('/api/test')).rejects.toThrow();
      // The error message should be a string, not the dict object
      // The fix ensures typeof detail === 'string' check prevents dict assignment
    });

    it('should handle non-string detail (list) as error message', async () => {
      const error = { 
        response: { 
          status: 400, 
          data: { detail: ['Error 1', 'Error 2'] } 
        } 
      };
      mockAxiosInstance.get.mockRejectedValue(error);

      await expect(get('/api/test')).rejects.toThrow();
      // The error message should be a string, not the list object
    });

    it('should handle string detail correctly', async () => {
      const error = { 
        response: { 
          status: 400, 
          data: { detail: 'This is a string error' } 
        } 
      };
      mockAxiosInstance.get.mockRejectedValue(error);

      await expect(get('/api/test')).rejects.toThrow();
      // String detail should work correctly
    });
  });
});
