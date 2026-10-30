/* global console, process */
/* Integrated JSDOM audit UI test: minimal real DOM (audit panel from index.html),
 * real audit functions copy-pasted from the page source. Both languages.
 * Verifies escaping, actor/client, loaded/total, failure vs empty vs
 * not-configured vs standalone, scope rejection, late-reply rejection,
 * permission loss/restoration repaint. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');

let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
function expectedDigits(lang) { return lang === 'fa' ? '۱ / ۱' : '1 / 1'; }

async function runCase({ lang }) {
  // Minimal HTML containing only the audit panel (real markup from index.html).
  const html = `<!DOCTYPE html><html lang="${lang}"><body>
    <div id="operatorAuditPanel">
      <span id="auCount" class="chip num text-[10px] text-zinc-400">—</span>
      <select id="auScope"><option value="control">control</option><option value="all">all</option><option value="preview">preview</option></select>
      <span id="auTg" class="chip text-[10px] text-zinc-400 ms-auto">—</span>
      <button id="auRefresh">refresh</button>
      <table><tbody id="auBody"></tbody></table>
      <div id="auScopeNote">note</div>
    </div>
  </body></html>`;

  const dom = new JSDOM(html, { url: 'https://nobi.local/', pretendToBeVisual: true });
  const w = dom.window;
  const d = w.document;
  d.documentElement.dataset.canMutate = '1';

  // Globals needed by the audit functions.
  w.LANG = lang;
  try { w.localStorage.setItem('lang', lang); } catch (_) {}
  w.numLocale = () => lang === 'fa' ? 'fa-IR' : 'en-US';
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
    try { return date.toLocaleString(w.numLocale(), { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }); } catch (e) { return `${String(date.getMonth()+1).padStart(2,'0')}/${String(date.getDate()).padStart(2,'0')} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`; }
  };
  w.escText = v => String(v == null ? '' : v).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  w.byId = id => d.getElementById(id);
  const T = {
    fa: {
      'au.title':'لاگ حسابرسی (سمت سرور)','au.when':'زمان','au.action':'اقدام','au.actor':'عامل / کلاینت','au.result':'نتیجه','au.detail':'جزئیات',
      'au.refresh':'به‌روزرسانی','au.empty':'در این فیلتر رکوردی ثبت نشده است','au.loading':'در حال خواندن لاگ حسابرسی…',
      'au.error':'خواندن لاگ حسابرسی ناموفق بود؛ نبود رکورد فرض نشود.','au.standalone':'حالت مستقل مرورگر — لاگ حسابرسی سرور در دسترس نیست.',
      'au.note':'فیلتر پیش‌فرض فقط دو نوع پیش‌نمایش شناخته‌شده را کنار می‌گذارد؛ سایر رخدادها لزوماً عملیات کنترلی نیستند.',
      'au.tgOn':'تلگرام: تنظیم‌شده؛ ارسال تأیید نشده','au.tgOff':'تلگرام: تنظیم‌نشده','au.tgUnknown':'تلگرام: نامشخص',
      'au.tgBackoff':'تلگرام: وقفهٔ خطا','au.tgStandalone':'تلگرام: حالت مستقل'
    },
    en: {
      'au.title':'Audit log (server-side)','au.when':'When','au.action':'Action','au.actor':'Actor / client','au.result':'Result','au.detail':'Detail',
      'au.refresh':'Refresh','au.empty':'No audit records match this filter','au.loading':'Reading audit log…',
      'au.error':'Audit log read failed; do not assume the log is empty.','au.standalone':'Browser-standalone mode — server audit log unavailable.',
      'au.note':'The default excludes only two known preview types; remaining events are not necessarily control actions.',
      'au.tgOn':'Telegram: configured; delivery unverified','au.tgOff':'Telegram: not configured','au.tgUnknown':'Telegram: unknown',
      'au.tgBackoff':'Telegram: backoff','au.tgStandalone':'Telegram: standalone'
    }
  };
  w.t = k => String((T[lang] && T[lang][k]) || (T.fa && T.fa[k]) || k);
  w.toast = () => {};
  w.document = d;
  w.window = w;

  // Stub NBX.core.api.
  w.NBX = { core: { api: {} } };
  let currentAudit, currentNotify;
  w.NBX.core.api.getAudit = async (opts) => {
    const qs = new URLSearchParams(opts || {}).toString();
    const res = await w.fetch(`/api/audit?${qs}`, { cache: 'no-store' });
    return res.json();
  };
  w.NBX.core.api.getNotifyStatus = async () => {
    const res = await w.fetch('/api/notify/status', { cache: 'no-store' });
    return res.json();
  };

  // Stub fetch.
  const origFetch = w.fetch;
  w.fetch = async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/audit')) return { ok: true, status: 200, json: async () => currentAudit };
    if (u.includes('/api/notify/status')) return { ok: true, status: 200, json: async () => currentNotify };
    return origFetch ? origFetch(url, opts) : { ok: false, status: 404, json: async () => ({}) };
  };

  // Real audit functions from index.html (copy-pasted).
  let auEvents = null, auNotify = null, auLoading = false, auGeneration = 0, auScopeLoaded = null, auTotal = null, auStandalone = false;
  w.invalidateAuditServer = function () {
    ++auGeneration; auEvents = null; auNotify = null; auLoading = false; auScopeLoaded = null; auTotal = null; auStandalone = false;
    w.renderAuditServer();
  };
  w.renderAuditServer = function () {
    const body = d.getElementById('auBody'), chip = d.getElementById('auCount'), tg = d.getElementById('auTg');
    if (!body) return;
    const allowed = d.documentElement.dataset.canMutate === '1';
    if (!allowed) { auEvents = null; auNotify = null; auLoading = false; auScopeLoaded = null; auTotal = null; auStandalone = false; }
    const current = d.getElementById('auScope').value;
    const valid = allowed && auScopeLoaded === current;
    const message = auLoading ? w.t('au.loading') : auStandalone ? w.t('au.standalone') : !valid || auEvents === null ? w.t('au.error') : !auEvents.length ? w.t('au.empty') : null;
    if (message) body.innerHTML = `<tr><td colspan="5" class="py-4 text-zinc-400">${w.escText(message)}</td></tr>`;
    else body.innerHTML = auEvents.map(e => {
      let detail = '—';
      try { detail = e.detail == null ? '—' : typeof e.detail === 'string' ? e.detail : JSON.stringify(e.detail); } catch (_) {}
      return `<tr><td>${w.escText(w.fmtWhen(e.ts))}</td><td>${w.escText(e.action)}</td><td>${w.escText(e.actor || '—')} / ${w.escText(e.client || '—')}</td><td>${w.escText(e.result || '—')}</td><td>${w.escText(detail)}</td></tr>`;
    }).join('');
    if (chip) chip.textContent = valid && auEvents !== null && !auLoading ? `${w.fmtNum(auEvents.length)} / ${auTotal === null ? '—' : w.fmtNum(auTotal)}` : '—';
    if (tg) {
      let key = 'au.tgUnknown';
      if (auNotify && auNotify.standalone) key = 'au.tgStandalone';
      else if (auNotify && auNotify.ok !== false && typeof auNotify.configured === 'boolean') key = auNotify.configured ? (auNotify.backing_off ? 'au.tgBackoff' : 'au.tgOn') : 'au.tgOff';
      tg.textContent = w.t(key); tg.dataset.state = key;
    }
  };
  w.loadAudit = async function () {
    const seq = ++auGeneration, scope = d.getElementById('auScope').value;
    auEvents = null; auScopeLoaded = null; auTotal = null; auNotify = null; auStandalone = false;
    if (d.documentElement.dataset.canMutate !== '1') { auLoading = false; w.renderAuditServer(); return; }
    const api = w.NBX && w.NBX.core && w.NBX.core.api;
    auLoading = true; w.renderAuditServer();
    const [a, n] = await Promise.all([
      Promise.resolve().then(() => api && api.getAudit ? api.getAudit({ limit: 25, scope }) : null).catch(() => null),
      Promise.resolve().then(() => api && api.getNotifyStatus ? api.getNotifyStatus() : null).catch(() => null)
    ]);
    if (seq !== auGeneration || scope !== d.getElementById('auScope').value || d.documentElement.dataset.canMutate !== '1') return;
    auLoading = false; auNotify = n; auStandalone = !!(a && a.standalone);
    if (a && a.ok !== false && !a.unavailable && Array.isArray(a.events) && a.events.every(e => e && typeof e.action === 'string') && a.scope === scope) {
      auEvents = a.events; auScopeLoaded = scope; auTotal = Number.isSafeInteger(a.total) && a.total >= a.events.length ? a.total : null;
    }
    w.renderAuditServer();
  };

  const stop = () => { w.fetch = origFetch; };

  try {
    const byId = id => d.getElementById(id);
    const body = byId('auBody'); const chip = byId('auCount'); const tg = byId('auTg'); const scope = byId('auScope');

    // 1. Actor escaped, loaded/total, configured != delivery.
    currentAudit = { ok: true, scope: 'control', total: 1, events: [{ ts: 1700000000, action: 'afges', actor: '<script>', client: '<client>', result: 'ok', detail: '<payload>' }] };
    currentNotify = { ok: true, configured: true };
    await w.loadAudit();
    check(body.innerHTML.includes('&lt;script&gt;') && !body.innerHTML.includes('<script>'), `index.html ${lang}: actor escaped`);
    check(body.innerHTML.includes('&lt;client&gt;') && body.innerHTML.includes('&lt;payload&gt;'), `index.html ${lang}: client/detail escaped`);
    check(chip.textContent === expectedDigits(lang), `index.html ${lang}: loaded/total ${chip.textContent}`);
    check(tg.dataset.state === 'au.tgOn', `index.html ${lang}: configured is not delivery`);
    check(scope.value === 'control', `index.html ${lang}: default scope control`);
    check(d.documentElement.lang === lang, `index.html ${lang}: document lang`);

    // 2. All scope shows event.
    currentAudit = { ok: true, scope: 'all', total: 2, events: currentAudit.events };
    scope.value = 'all';
    await w.loadAudit();
    check(body.innerHTML.includes('afges') && !body.innerHTML.includes('<script>'), `index.html ${lang}: all scope shows event`);

    // 3. Preview empty.
    currentAudit = { ok: true, scope: 'preview', total: 0, events: [] };
    scope.value = 'preview';
    await w.loadAudit();
    check(body.innerHTML.includes(w.t('au.empty')) && !body.innerHTML.includes('afges'), `index.html ${lang}: preview empty`);

    // 4. Failure vs empty.
    currentAudit = { ok: false, unavailable: true };
    currentNotify = { ok: true, configured: null };
    await w.loadAudit();
    check(body.innerHTML.includes(w.t('au.error')) && !body.innerHTML.includes('afges'), `index.html ${lang}: failure differs from empty`);
    check(tg.dataset.state === 'au.tgUnknown', `index.html ${lang}: notify unknown on failure`);

    // 5. Successful empty vs not-configured.
    currentAudit = { ok: true, scope: 'control', total: 0, events: [] };
    currentNotify = { ok: true, configured: false };
    scope.value = 'control';
    await w.loadAudit();
    check(body.innerHTML.includes(w.t('au.empty')) && !body.innerHTML.includes('afges'), `index.html ${lang}: successful empty`);
    check(tg.dataset.state === 'au.tgOff', `index.html ${lang}: not configured is off`);

    // 6. Standalone distinct.
    currentAudit = { ok: false, standalone: true };
    currentNotify = { ok: false, standalone: true };
    await w.loadAudit();
    check(body.innerHTML.includes(w.t('au.standalone')) && tg.dataset.state === 'au.tgStandalone', `index.html ${lang}: standalone distinct`);

    // 7. Permission loss clears, restoration does not republish.
    d.documentElement.dataset.canMutate = '1';
    currentAudit = { ok: true, scope: 'control', total: 1, events: [{ ts: 1700000000, action: 'afges', actor: 'op', client: 'c', result: 'ok', detail: 'd' }] };
    await w.loadAudit();
    check(body.innerHTML.includes('op'), `index.html ${lang}: operator data visible`);
    d.documentElement.dataset.canMutate = '0';
    w.renderAuditServer();
    check(!body.innerHTML.includes('op') && chip.textContent === '—', `index.html ${lang}: permission loss clears audit panel`);
    check(!body.innerHTML.includes('<script>'), `index.html ${lang}: no unsanitized actor after permission loss`);
    d.documentElement.dataset.canMutate = '1';
    w.renderAuditServer();
    check(!body.innerHTML.includes('op'), `index.html ${lang}: restoration does not republish stale data`);

    // 8. Late reply after invalidation rejected.
    const p = new Promise(r => { windowOldResolve = r; });
    currentAudit = p;
    const first = w.loadAudit();
    await Promise.resolve();
    w.invalidateAuditServer();
    windowOldResolve({ scope: 'control', total: 1, events: [{ action: 'secret' }] });
    await first;
    check(!body.innerHTML.includes('secret'), `index.html ${lang}: late reply after invalidation rejected`);

    // 9. Old scope response rejected.
    const old = new Promise(r => { windowOldResolve2 = r; });
    currentAudit = old;
    const older = w.loadAudit();
    await Promise.resolve();
    scope.value = 'preview';
    currentAudit = { ok: true, scope: 'preview', total: 0, events: [] };
    await w.loadAudit();
    windowOldResolve2({ scope: 'control', total: 1, events: [{ action: 'old' }] });
    await older;
    check(body.innerHTML.includes(w.t('au.empty')) && !body.innerHTML.includes('old'), `index.html ${lang}: old scope response rejected`);

    // 10. Scope mismatch rejected.
    scope.value = 'control';
    currentAudit = { ok: true, scope: 'all', total: 1, events: [{ action: 'wrong' }] };
    await w.loadAudit();
    check(body.innerHTML.includes(w.t('au.error')) && !body.innerHTML.includes('wrong'), `index.html ${lang}: scope mismatch rejected`);

  } finally {
    stop();
  }
}

let windowOldResolve, windowOldResolve2;

async function run() {
  for (const lang of ['fa', 'en']) await runCase({ lang });
  console.log(`phase13 JSDOM integration UI: ${checks} checks passed`);
}

run().catch(e => { console.error(e); process.exitCode = 1; });
