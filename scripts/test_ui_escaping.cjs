/* Regression gates for DOM XSS and the IIFE-scoped retry handler.

   The dashboard interpolates exchange metadata, journal events and upstream
   error strings into innerHTML. Those values are not trusted: a coin name or a
   crafted error detail must never become markup.
*/
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');

const PAYLOAD = '\"><img src=x onerror=\"window.__pwned=1\">';
const ATTR_PAYLOAD = 'BTC\" onmouseover=\"window.__pwned=2';
const APP = fs.readFileSync('nbx-app.js', 'utf8');
let checks = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); checks++; };

function boot(file) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (e) => errors.push(e.message));
  const dom = new JSDOM(fs.readFileSync(file, 'utf8'), {
    url: 'https://dashboard.test/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse(w) {
      w.setInterval = () => 0;
      w.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
      w.scrollTo = () => {};
      w.HTMLElement.prototype.scrollIntoView = () => {};
    },
  });
  return { dom, w: dom.window, errors };
}

for (const file of ['index.html', 'nobitex-mobile.html']) {
  const html = fs.readFileSync(file, 'utf8');
  // index.html loads nbx-app.js as an external file; the standalone build has
  // it inlined. Check the app layer in both cases.
  const combined = file === 'index.html' ? html + '\n' + APP : html;
  // 1. The retry button used to call scanNow(), which only exists inside the
  //    nbx-app.js IIFE. An inline onclick could not see it (ReferenceError).
  ok(!combined.includes('onclick="scanNow('), `${file}: no IIFE-scoped inline handler`);
  ok(combined.includes('data-retry-scan'), `${file}: retry uses a bound data attribute`);
  ok(combined.includes("querySelectorAll('[data-retry-scan]')"), `${file}: retry is bound`);
  // 2. The escaping helpers must be applied to server-provided strings.
  for (const marker of [
    '${escText(L(s.label))}', '${escText(L(n.txt))}', '${escText(a.name)}',
    '${escText(L(a.metric))}', '${escText(L(n.tag))}', '${esc(s.symbol)}',
    '${esc(String(live.unavailableDetail)',
  ]) {
    ok(combined.includes(marker), `${file}: missing ${marker}`);
  }

  const { dom, w, errors } = boot(file);
  ok(errors.length === 0, `${file}: boot errors: ${errors.join(' | ')}`);

  // 3. News feed: a malicious title/tag/source must stay text.
  w.eval(`
    news.length = 0;
    news.push({ sev: 'crit', txt: { fa: ${JSON.stringify(PAYLOAD)}, en: ${JSON.stringify(PAYLOAD)} },
                tag: { fa: ${JSON.stringify(PAYLOAD)}, en: 'tag' },
                src: { fa: 'src', en: ${JSON.stringify(PAYLOAD)} }, t: '12:00' });
    renderNews();
  `);
  const feed = w.document.getElementById('newsFeed');
  ok(feed.querySelectorAll('img, script').length === 0, `${file}: news injected an element`);
  ok(!w.__pwned, `${file}: news executed an inline handler`);
  ok(feed.textContent.includes('<img'), `${file}: payload must be visible as escaped text`);

  // 4. Fundamentals list: name/metric are provider strings.
  w.eval(`
    faAssets.length = 0;
    faAssets.push({ name: ${JSON.stringify(PAYLOAD)}, score: 50,
                    metric: { fa: ${JSON.stringify(ATTR_PAYLOAD)}, en: 'metric' } });
    renderFaList();
  `);
  const fa = w.document.getElementById('faList');
  ok(fa.querySelectorAll('img, script').length === 0, `${file}: FA injected an element`);
  ok(!w.__pwned, `${file}: FA executed an inline handler`);
  ok(fa.textContent.includes('<img'), `${file}: FA payload must be escaped text`);

  // 5. The retry scan entry point must actually be exported for the binding.
  //    Only the standalone build boots the app layer as inline script.
  if (file !== 'index.html') {
    ok(typeof w.NBX_APP?.scanNow === 'function', `${file}: NBX_APP.scanNow is exported`);
  }
  dom.window.close();
}

// 6. The esc() helper itself must escape both tag and attribute contexts.
const start = APP.indexOf('const esc =');
assert.ok(start >= 0, 'esc helper missing');
const end = APP.indexOf('\n  const faNum', start);
const helper = APP.slice(start, end > start ? end : start + 400);
const esc = new Function(`${helper}\n return esc;`)();
ok(esc(PAYLOAD) === '&quot;&gt;&lt;img src=x onerror=&quot;window.__pwned=1&quot;&gt;',
   'esc must escape quotes and angle brackets');
ok(!esc(ATTR_PAYLOAD).includes('"'), 'esc must not leave an unescaped double quote');

console.log(`UI escaping + retry binding PASS (${checks} checks)`);
