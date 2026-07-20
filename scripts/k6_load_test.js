import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '2m', target: 200 }, // Ramp-up to 200 users over 2 minutes
    { duration: '5m', target: 1000 }, // Ramp-up to target 1000 users over 5 minutes
    { duration: '10m', target: 1000 }, // Stay at 1000 users for 10 minutes (Soak Test)
    { duration: '3m', target: 0 }, // Ramp-down to 0 users
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% of requests should be below 500ms
    http_req_failed: ['rate<0.01'], // Less than 1% of requests should fail
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // Test Health Endpoint
  const res = http.get(`${BASE_URL}/health`);
  check(res, {
    'health status is 200': (r) => r.status === 200,
  });

  sleep(1);
}
