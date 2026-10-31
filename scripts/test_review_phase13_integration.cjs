/* global console, process */
/* Real-DOM audit UI integration for index.html and nobitex-mobile.html.
 * Both languages, real page scripts evaluated in a JSDOM window, no network,
 * no real credentials. Verifies escaping, actor/client, loaded/total,
 * failure vs empty vs not-configured vs standalone, scope rejection,
 * late-reply rejection, and permission loss/restoration repaint. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');

let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
function expectedDigits(lang) { return lang === 'fa' ? '۱ / ۱' : '1 / 1'; }

function extractScripts(html) {
  const scripts = [];
  const re = /<script([^>]*)>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html))) {
    const attrs = m[1] || '';
    let body = m[2];
    if (attrs.includes('src=')) {
      const srcMatch = attrs.match(/src="([^"]+)"/);
      if (!srcMatch) continue;
      const src = srcMatch[1];
      if (src.startsWith('https://unpkg.com/')) continue;
      if (src === 'nbx-core.js') body = fs.readFileSync('nbx-core.js', 'utf8');
      else if (src === 'nbx-app.js') body = fs.readFileSync('nbx-app.js', 'utf8');
      else continue;
    }
    // Do not start the live app in this offline UI test.
    body = body.replace(/if \(document\.readyState === 'loading'\) document\.addEventListener\('DOMContentLoaded', start\);\s*else start\(\);/g, '');
    scripts.push(body);
  }
  return scripts;
}

function globals(w, lang) {
  const d = w.document;
  const targetFa = lang === 'fa';
  w.LANG = lang;
  w.localStorage.setItem('lang', lang);
  w.numLocale = () => targetFa ? 'fa-IR' : 'en-US';
  w.fmtNum = (n, dec = 0) => {
    if (n == null || (typeof n === 'string' && !n.trim())) return '—';
    const v = Number(n);
    if (!Number.isFinite(v)) return '—';
    const d2 = Math.max(0, Math.min(20, Number(dec) || 0));
    return v.toLocaleString(w.numLocale(), { minimumFractionDigits: d2, maximumFractionDigits: d2 });
  };
  w.fmtWhen = ts => {
    if (!ts) return '—';
    const date = new Date(ts * 1000);
    try { return date.toLocaleString(w.numLocale(), { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }); } catch { return `${String(date.getMonth()+1).padStart(2,'0')}/${String(date.getDate()).padStart(2,'0')} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`; }
  };
  w.escText = v => String(v == null ? '' : v).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  w.byId = id => d.getElementById(id);
  w.t = k => {
    const T = w.T || {};
    return String((T[lang] && T[lang][k]) || (T.fa && T.fa[k]) || k);
  };
  w.toast = () => {};
  w.document = d;
  w.window = w;
}

function stubAudit(w, handler) {
  const orig = w.fetch;
  w.fetch = async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/audit') || u.includes('/api/notify/status')) return handler(u, opts);
    if (u.includes('/auth/me')) return { ok: true, status: 200, json: async () => ({ user: { username: 'op' }, role: 'operator', can_mutate: true, token: false }) };
    return orig ? orig(url, opts) : { ok: false, status: 404, json: async () => ({}) };
  };
  return () => { w.fetch = orig; };
}

function response(payload, status = 200) {
  return { ok: status === 200, status, json: async () => payload };
}

async function runCase({ file, lang }) {
  // An http origin, not file://: a file URL is an opaque origin, where
  // localStorage throws and the page silently falls back to 'fa' — which would
  // make the English half of this suite assert against Persian strings.
  // The server serves the mobile file over http too, and the one genuinely
  // file-only behaviour (api.standaloneMobile) is driven explicitly below.
  const url = file.endsWith('mobile.html') ? 'https://nobi-mobile.local/' : 'https://nobi.local/';
  // `outside-only` keeps JSDOM from executing the page's own inline scripts
  // while parsing. They run from this loop instead, in a controlled order, so
  // that (a) localStorage already holds the language when `let LANG` is first
  // evaluated and (b) NBX.core exists before any top-level statement that reads
  // it — the inlined mobile app calls initTicket() at top level and would
  // otherwise throw on NBX.core.engine before the test could intervene.
  const dom = new JSDOM(fs.readFileSync(file, 'utf8'), {
    url, runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  const d = w.document;
  d.documentElement.dataset.canMutate = '1';
  globals(w, lang);
  const bodies = extractScripts(fs.readFileSync(file, 'utf8'));
  // One eval, not one per script: `const`/`let` declared by eval code stay in
  // that eval's own scope, so splitting the scripts would leave `T`, `t` and
  // `LANG` invisible to the script that reads them. The core client is pulled
  // to the front so NBX.core can be built before any top-level statement that
  // reads it — the inlined mobile app calls initTicket() at top level and would
  // otherwise throw on NBX.core.engine.
  const coreAt = bodies.findIndex(b => b.includes('NBX.createCore'));
  const core = coreAt >= 0 ? bodies.splice(coreAt, 1)[0] : null;
  const source = [
    core,
    // The page boots NBX.core inside start(), which this offline test strips so
    // no timer or network call is started. Build the core as soon as the client
    // exists: it only constructs the API client and the market engine, neither
    // of which touches the network until a method is called.
    core ? ';window.NBX.core = window.NBX.createCore();' : '',
    ...bodies,
    // Re-export what this test reaches from the outside: `t` is a top-level
    // const, so it never becomes a window property on its own.
    ';window.__pageT = t;',
  ].filter(Boolean).join('\n');
  try {
    w.eval(source);
  } catch (e) {
    w.close();
    throw e;
  }
  // `t` and `T` are top-level `const`s in the page script, so they never become
  // window properties: the stub installed by globals() would answer every key
  // with the key itself. Delegate to the page's real translator instead — it
  // also runs the string through localizeText, which is part of the contract.
  w.t = k => w.__pageT(k);
  // Static i18n is applied by the same stripped boot, so the scope note is
  // still an empty placeholder. Run the real pass — it reads LANG and the page's
  // own translation table, exactly as a language switch does at runtime.
  w.applyStaticI18n();

  const stopFetch = stubAudit(w, (url) => {
    if (url.includes('/api/audit')) return response(currentAudit);
    if (url.includes('/api/notify/status')) return response(currentNotify);
    return response({});
  });
  try {
    const api = w.NBX && w.NBX.core && w.NBX.core.api;
    assert(api, 'core api available');
    const byId = id => d.getElementById(id);
    const t = k => w.t(k);
    const body = byId('auBody'); const chip = byId('auCount'); const tg = byId('auTg'); const scope = byId('auScope');
    assert(body && chip && tg && scope, 'audit elements present');

    currentAudit = { ok: true, scope: 'control', total: 1, events: [{ ts: 1700000000, action: 'afges', actor: '<script>', client: '<client>', result: 'ok', detail: '<payload>' }] };
    currentNotify = { ok: true, configured: true };
    await w.loadAudit();
    check(body.innerHTML.includes('&lt;script&gt;') && !body.innerHTML.includes('<script>'), `${file} ${lang}: actor escaped`);
    check(body.innerHTML.includes('&lt;client&gt;') && body.innerHTML.includes('&lt;payload&gt;'), `${file} ${lang}: client/detail escaped`);
    check(chip.textContent === expectedDigits(lang), `${file} ${lang}: loaded/total ${chip.textContent}`);
    check(tg.dataset.state === 'au.tgOn', `${file} ${lang}: configured is not delivery`);
    check(scope.value === 'control', `${file} ${lang}: default scope control`);
    check(d.documentElement.lang === lang || d.documentElement.lang.startsWith(lang), `${file} ${lang}: document lang`);
    check(byId('auScopeNote').textContent.includes(lang === 'fa' ? 'شناخته‌شده' : 'known preview types'), `${file} ${lang}: scope note localized`);

    scope.value = 'all';
    currentAudit = { ok: true, scope: 'all', total: 2, events: currentAudit.events };
    await w.loadAudit();
    check(body.innerHTML.includes('afges') && !body.innerHTML.includes('<script>'), `${file} ${lang}: all scope shows event`);

    scope.value = 'preview';
    currentAudit = { ok: true, scope: 'preview', total: 0, events: [] };
    await w.loadAudit();
    check(body.innerHTML.includes(t('au.empty')) && !body.innerHTML.includes('afges'), `${file} ${lang}: preview empty`);

    currentAudit = { ok: false, unavailable: true };
    currentNotify = { ok: true, configured: null };
    await w.loadAudit();
    check(body.innerHTML.includes(t('au.error')) && !body.innerHTML.includes('afges'), `${file} ${lang}: failure differs from empty`);
    check(tg.dataset.state === 'au.tgUnknown', `${file} ${lang}: notify unknown on failure`);

    scope.value = 'control';
    currentAudit = { ok: true, scope: 'control', total: 0, events: [] };
    currentNotify = { ok: true, configured: false };
    await w.loadAudit();
    check(body.innerHTML.includes(t('au.empty')) && !body.innerHTML.includes('afges'), `${file} ${lang}: successful empty`);
    check(tg.dataset.state === 'au.tgOff', `${file} ${lang}: not configured is off`);

    // Standalone is reached through the client, not through the payload: the
    // core API collapses any `ok:false` body into `unavailable`, so the only
    // way `auStandalone` becomes true is `api.standaloneMobile` (file:// mode).
    // Drive the real flag instead of a payload the client would rewrite.
    const api0 = w.NBX.core.api;
    const wasStandalone = api0.standaloneMobile;
    api0.standaloneMobile = true;
    currentAudit = { ok: false, standalone: true };
    currentNotify = { ok: false, standalone: true };
    await w.loadAudit();
    check(body.innerHTML.includes(t('au.standalone')) && tg.dataset.state === 'au.tgStandalone', `${file} ${lang}: standalone distinct`);
    api0.standaloneMobile = wasStandalone;

    d.documentElement.dataset.canMutate = '1';
    scope.value = 'control';
    currentAudit = { ok: true, scope: 'control', total: 1, events: [{ ts: 1700000000, action: 'afges', actor: 'op', client: 'c', result: 'ok', detail: 'd' }] };
    await w.loadAudit();
    check(body.innerHTML.includes('op'), `${file} ${lang}: operator data visible`);
    d.documentElement.dataset.canMutate = '0';
    w.renderAuditServer();
    check(!body.innerHTML.includes('op') && chip.textContent === '—', `${file} ${lang}: permission loss clears audit panel`);
    check(!body.innerHTML.includes('<script>'), `${file} ${lang}: no unsanitized actor after permission loss`);
    d.documentElement.dataset.canMutate = '1';
    w.renderAuditServer();
    check(!body.innerHTML.includes('op'), `${file} ${lang}: restoration does not republish stale data`);

    scope.value = 'control';
    const p = new Promise(r => { windowOldResolve = r; });
    currentAudit = p;
    const first = w.loadAudit();
    await Promise.resolve();
    w.invalidateAuditServer();
    windowOldResolve({ scope: 'control', total: 1, events: [{ action: 'secret' }] });
    await first;
    check(!body.innerHTML.includes('secret'), `${file} ${lang}: late reply after invalidation rejected`);

    const old = new Promise(r => { windowOldResolve2 = r; });
    currentAudit = old;
    const older = w.loadAudit();
    await Promise.resolve();
    scope.value = 'preview';
    currentAudit = { ok: true, scope: 'preview', total: 0, events: [] };
    await w.loadAudit();
    windowOldResolve2({ scope: 'control', total: 1, events: [{ action: 'old' }] });
    await older;
    check(body.innerHTML.includes(t('au.empty')) && !body.innerHTML.includes('old'), `${file} ${lang}: old scope response rejected`);

    scope.value = 'control';
    currentAudit = { ok: true, scope: 'all', total: 1, events: [{ action: 'wrong' }] };
    await w.loadAudit();
    check(body.innerHTML.includes(t('au.error')) && !body.innerHTML.includes('wrong'), `${file} ${lang}: scope mismatch rejected`);
  } finally {
    stopFetch();
    w.close();
  }
}

let windowOldResolve, windowOldResolve2, currentAudit, currentNotify;

async function run() {
  for (const file of ['index.html', 'nobitex-mobile.html']) {
    for (const lang of ['fa', 'en']) await runCase({ file, lang });
  }
  console.log(`phase13 integration UI: ${checks} checks passed`);
}

run().catch(e => { console.error('FAILED:', e && e.stack ? e.stack : e); process.exitCode = 1; });
