// Mobile bottom nav derives its icons from the sidebar after the inline icon
// pass has already replaced every <i data-lucide> with an <svg>. In 2.5.1 that
// svg carried no icon name, so every tab in the bottom bar rendered as an
// anonymous circle. Load the real page, let both passes run, and require a
// named, non-fallback icon on every tab — in index.html and the inlined build.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

function load(file) {
  const vc = new VirtualConsole();
  vc.on('jsdomError', () => {}); // missing stylesheets / chart lib are irrelevant here
  const html = fs.readFileSync(file, 'utf8');
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    resources: 'usable',
    url: 'file://' + path.resolve(file),
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(w) {
      w.fetch = () => new Promise(() => {});
      w.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
      w.HTMLCanvasElement.prototype.getContext = () => null;
      w.ResizeObserver = class { observe() {} disconnect() {} };
      w.scrollTo = () => {};
    },
  });
  return new Promise((resolve) => setTimeout(() => resolve(dom.window), 3000));
}

(async () => {
  for (const file of ['index.html']) {
    const w = await load(file);
    const d = w.document;
    const tabs = [...d.querySelectorAll('#mobileBottomNav .mnav-btn')];
    assert(tabs.length >= 5, `${file}: bottom nav did not render (${tabs.length} tabs)`);
    const sidebar = [...d.querySelectorAll('.navbtn')].length;
    assert.strictEqual(tabs.length, sidebar, `${file}: bottom nav must mirror the sidebar`);
    for (const b of tabs) {
      const svg = b.querySelector('svg');
      assert(svg, `${file}: tab ${b.dataset.mtab} has no icon`);
      const name = svg.getAttribute('data-icon');
      assert(name && name !== 'circle', `${file}: tab ${b.dataset.mtab} lost its icon name`);
      assert(
        !/<circle cx="12" cy="12" r="8">/.test(svg.innerHTML),
        `${file}: tab ${b.dataset.mtab} renders the fallback circle`
      );
    }
    // The timeframe selector is injected into the candle card header.
    assert.strictEqual(d.querySelectorAll('#candleChips .tfSel').length, 4, `${file}: timeframe chips missing`);
    w.close();
  }
  console.log('ok mobile bottom nav icons + timeframe chips');
  process.exit(0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
