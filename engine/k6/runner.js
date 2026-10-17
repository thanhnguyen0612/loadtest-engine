// Platform-owned k6 entrypoint. Developers never set options; they only supply a scenario.
import scenario from './scenario.js';

const PHASE = __ENV.PHASE || 'load';
const STAGES = JSON.parse(__ENV.STAGES || '[]');
const P95_MS = Number(__ENV.P95_MS || 500);
const ERROR_RATE = Number(__ENV.ERROR_RATE || 0.01);
const MAX_RPS = Number(__ENV.MAX_RPS || 500);

// Platform cap on requests/s (not iterations/s): a custom scenario that sends several
// requests per iteration is aborted once its average request rate exceeds the cap.
const rateGuard = { threshold: `rate<=${MAX_RPS}`, abortOnFail: true, delayAbortEval: '10s' };

const smoke = {
  scenarios: {
    smoke: {
      executor: 'constant-arrival-rate',
      rate: 5,
      timeUnit: '1s',
      duration: '15s',
      preAllocatedVUs: 5,
      maxVUs: 20,
    },
  },
  thresholds: { http_req_failed: ['rate<0.05'], http_reqs: [rateGuard] },
};

const load = {
  scenarios: {
    load: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 500,
      // k6 rejects zero-duration stages, so skip them.
      stages: STAGES.filter((s) => s.duration_s > 0).map((s) => ({
        duration: `${s.duration_s}s`,
        target: s.target_rps,
      })),
    },
  },
  thresholds: {
    http_req_duration: [`p(95)<${P95_MS}`],
    http_req_failed: [`rate<${ERROR_RATE}`],
    http_reqs: [rateGuard],
  },
};

export const options = {
  ...(PHASE === 'smoke' ? smoke : load),
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

export default scenario;

export function handleSummary(data) {
  return {
    stdout:
      '===LOADTEST_SUMMARY_BEGIN===\n' + JSON.stringify(data) + '\n===LOADTEST_SUMMARY_END===\n',
  };
}
