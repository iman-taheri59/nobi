/*
 * capoy.ir read-only audit export.
 * Run only in the browser console of an authenticated capoy.ir Viewer session.
 * This script makes GET requests to the same origin only. It has no POST, order,
 * settings, logout, cookie-reading, localStorage, or third-party network code.
 * Paper positions/metrics are deliberately excluded because those GET handlers
 * call evaluate_positions() and may update paper state.
 */
(async () => {
  'use strict';
  if (location.origin !== 'https://capoy.ir' && location.origin !== 'http://capoy.ir') {
    throw new Error('Open capoy.ir first; this exporter only runs on the capoy.ir origin.');
  }
  const paths = [
    '/api/auth/me',
    '/api/health',
    '/api/universe',
    '/api/signals?tf=15m',
    '/api/market/overview',
    '/api/risk/state',
    '/api/paper/orders?limit=100',
    '/api/journal/summary?days=90&limit=60',
    '/api/journal/stage',
    '/api/journal/msi?window_days=30&baseline_days=180&min_samples=20',
    '/api/journal/signals?days=30&limit=200&offset=0',
    '/api/signal-intelligence/summary',
    '/api/backtest/reports?limit=10',
    '/api/backtest/promotion-gate',
    '/api/live/status',
    '/api/live/evidence',
    '/api/ws/status'
  ];
  const output = {
    schema: 'capoy-readonly-audit-v1',
    exported_at: new Date().toISOString(),
    origin: location.origin,
    method: 'GET-only; same-origin; no credentials or cookies exported',
    excluded: [
      '/api/paper/positions (may evaluate/update positions)',
      '/api/paper/metrics (may evaluate/update positions)'
    ],
    results: {}
  };
  for (const path of paths) {
    try {
      const response = await fetch(path, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' }
      });
      const text = await response.text();
      let body;
      try { body = JSON.parse(text); } catch { body = text.slice(0, 20000); }
      output.results[path] = { status: response.status, ok: response.ok, body };
    } catch (error) {
      output.results[path] = { status: null, ok: false, error: String(error) };
    }
  }
  const blob = new Blob([JSON.stringify(output, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `capoy-readonly-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  console.log('Read-only export downloaded. Review it for private data before sharing.', output);
})();
