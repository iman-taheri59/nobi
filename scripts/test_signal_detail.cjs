/* Offline regression: a journal detail row must open, close and reopen.
 * Tests real DOM nodes and the canonical function from both generated forms.
 * Fetching is replaced with an explicit fixture; no trading server is contacted.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');

async function run(file) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(fs.readFileSync(file, 'utf8'), {
    url: 'https://dashboard.test/', runScripts: 'dangerously',
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.setInterval = () => 0;
      w.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
      w.scrollTo = () => {};
    },
  });
  const w = dom.window;
  try {
    if (w.document.readyState !== 'complete') {
      await new Promise(resolve => w.addEventListener('load', resolve, { once: true }));
    }
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(errors.length, 0, `${file}: boot errors`);
    w.eval(`
      window.__calls = 0;
      window.NBX = window.NBX || { core: { api: {} } };
      window.NBX.core.api.getJournalSignal = async id => {
        window.__calls++;
        return { signal: { id, symbol: 'BTCIRT', components: {} }, path: [] };
      };
      detailHtml = d => '<div data-detail-fixture>' + d.signal.id + '</div>';
    `);
    const body = w.document.getElementById('perfBody');
    body.innerHTML = '<tr data-sig="101"><td>first</td></tr><tr data-sig="102"><td>second</td></tr>';
    const [first, second] = body.querySelectorAll('tr');
    await w.toggleSignalRow(first);
    assert.ok(first.nextElementSibling.classList.contains('sig-detail'));
    assert.ok(first.classList.contains('bg-white/[.04]'));
    assert.equal(w.__calls, 1);
    await w.toggleSignalRow(first);
    assert.equal(body.querySelectorAll('.sig-detail').length, 0);
    assert.equal(first.nextElementSibling, second, 'closing must not remove another signal');
    assert.ok(!first.classList.contains('bg-white/[.04]'));
    await w.toggleSignalRow(first);
    assert.equal(w.__calls, 1, 'reopen uses detail cache');
    await w.toggleSignalRow(second);
    assert.equal(body.querySelectorAll('.sig-detail').length, 1);
    assert.ok(second.nextElementSibling.classList.contains('sig-detail'));
    assert.ok(!first.classList.contains('bg-white/[.04]'));
    await w.toggleSignalRow(second);
    assert.equal(body.querySelectorAll('.sig-detail').length, 0);
    // Exercise the delegated click listener too (not only direct calls).
    first.firstElementChild.click();
    await Promise.resolve(); await Promise.resolve();
    assert.equal(body.querySelectorAll('.sig-detail').length, 1);
    first.firstElementChild.click();
    await Promise.resolve();
    assert.equal(body.querySelectorAll('.sig-detail').length, 0);
    assert.equal(errors.length, 0, `${file}: runtime errors`);
    console.log(`${file}: signal detail open/close/reopen, switch, cache and delegated clicks PASS`);
  } finally {
    w.close();
  }
}
(async () => {
  for (const file of ['index.html', 'nobitex-mobile.html']) await run(file);
})().catch(e => { console.error(e); process.exitCode = 1; });
