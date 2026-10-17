// Default scenario: round-robin over the endpoints declared in loadtest.yaml.
import http from 'k6/http';
import { check } from 'k6';

const ENDPOINTS = JSON.parse(__ENV.ENDPOINTS || '[]');
const BASE_URL = __ENV.BASE_URL;

export default function () {
  const ep = ENDPOINTS[__ITER % ENDPOINTS.length];
  const hasBody = ep.body !== undefined && ep.body !== null;
  const res = http.request(
    ep.method,
    `${BASE_URL}${ep.path}`,
    hasBody ? JSON.stringify(ep.body) : null,
    hasBody ? { headers: { 'Content-Type': 'application/json' } } : {},
  );
  check(res, { 'status < 400': (r) => r.status < 400 });
}
