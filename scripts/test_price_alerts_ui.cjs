/* global console, process */
/* Offline ADR-0049 bell/management tests. No real session, network or order. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');

const moduleCode = fs.readFileSync('nbx-alerts.js', 'utf8');
let checks = 0;
function check(value, message) { assert.ok(value, message); checks += 1; }
function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
async function settle() {
  for (let i = 0; i < 12; i += 1) await new Promise(resolve => setTimeout(resolve, 0));
}
function rule(id = 1, overrides = {}) {
  return {
    id, symbol: 'BTCIRT', metric: 'last', operator: 'above', threshold: 100,
    enabled: true, armed: true, last_status: 'armed', last_value: 90,
    last_observed_at: 1700000000, ...overrides,
  };
}
function event() {
  return {
    id: 8, rule_id: null, symbol: 'BTCIRT', metric: 'last', operator: 'above',
    threshold: 100, previous_value: 90, value: 110, observed_at: 1700000001,
    source: '<script>alert(1)</script>', delivery_sent: false, delivery_reason: 'http-400',
  };
}
function server(window) {
  const api = {
    calls: [], rules: [rule()], events: [event()], failRules: false, defer: null,
  };
  window.fetch = async (url, options = {}) => {
    const path = String(url);
    const method = options.method || 'GET';
    api.calls.push({ path, method, options });
    check(path.startsWith('/api/alerts/'), `no non-alert request: ${path}`);
    if (path.startsWith('/api/alerts/rules?') && method === 'GET') {
      if (api.failRules) return response({ detail: 'fixture read failure' }, 500);
      if (api.defer) return api.defer.promise;
      return response({ ok: true, rules: api.rules, n: api.rules.length });
    }
    if (path.startsWith('/api/alerts/events?') && method === 'GET') {
      return response({ ok: true, events: api.events, n: api.events.length });
    }
    if (path === '/api/alerts/rules' && method === 'POST') {
      const body = JSON.parse(options.body);
      const created = rule(Math.max(0, ...api.rules.map(item => item.id)) + 1, {
        ...body, enabled: true, armed: false, last_status: 'arming', last_value: null,
        last_observed_at: null,
      });
      api.rules.unshift(created);
      return response({ ok: true, rule: created, audit_id: 10 }, 201);
    }
    const stateMatch = path.match(/^\/api\/alerts\/rules\/(\d+)$/);
    if (stateMatch && method === 'PATCH') {
      const id = Number(stateMatch[1]);
      const body = JSON.parse(options.body);
      const found = api.rules.find(item => item.id === id);
      if (!found) return response({ detail: 'missing' }, 404);
      found.enabled = body.enabled;
      found.last_status = body.enabled ? 'arming' : 'disabled';
      found.armed = false;
      return response({ ok: true, rule: found, audit_id: 11 });
    }
    if (stateMatch && method === 'DELETE') {
      const id = Number(stateMatch[1]);
      api.rules = api.rules.filter(item => item.id !== id);
      return response({ ok: true, deleted: true, rule_id: id, history_retained: true });
    }
    throw new Error(`unexpected fixture request ${method} ${path}`);
  };
  return api;
}
function bootstrap(file) {
  const dom = new JSDOM(fs.readFileSync(file, 'utf8'), {
    url: file.endsWith('mobile.html') ? 'https://alerts-mobile.test/' : 'https://alerts.test/',
    runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const { window } = dom;
  window.NBX = { config: { pythonApi: '/api' } };
  window.eval(moduleCode);
  window.NBXAlerts.init();
  const api = server(window);
  window.document.cookie = 'nbx_csrf=fixture-csrf';
  return { dom, window, document: window.document, api, alerts: window.NBXAlerts };
}
function user(role, name = role) {
  return { user: { id: role === 'owner' ? 1 : 2, username: name, role }, can_mutate: role !== 'viewer' };
}
async function viewerCase(file) {
  const ctx = bootstrap(file);
  const { window, document, api, alerts, dom } = ctx;
  const anonymousResult = await alerts.open();
  check(anonymousResult.skipped === true, `${file}: anonymous open is explicitly skipped`);
  check(api.calls.length === 0, `${file}: anonymous bell makes no request`);
  alerts.close();
  alerts.setUser(user('viewer'));
  check(api.calls.length === 0, `${file}: assigning Viewer makes no request`);
  const result = await alerts.open();
  check(result.skipped === true, `${file}: Viewer open is explicitly skipped`);
  check(api.calls.length === 0, `${file}: Viewer bell makes no forbidden GET`);
  check(!document.getElementById('alertsAccess').classList.contains('hidden'), `${file}: Viewer sees access explanation`);
  check(document.getElementById('alertsContent').classList.contains('hidden'), `${file}: Viewer management stays hidden`);
  check(document.getElementById('alertsAccessText').textContent.includes('Viewer'), `${file}: Viewer role is named`);
  alerts.close();
  check(document.getElementById('alertsModal').classList.contains('hidden'), `${file}: dialog closes`);
  dom.window.close();
}
async function ownerBasicCase(file) {
  const ctx = bootstrap(file);
  const { document, api, alerts, dom } = ctx;
  alerts.setUser(user('operator'));
  check(api.calls.length === 0, `${file}: Operator identity alone makes no request`);
  await alerts.open();
  check(api.calls.map(call => call.method).join(',') === 'GET,GET', `${file}: open reads rules and events only`);
  check(document.getElementById('alertsRules').textContent.includes('BTCIRT'), `${file}: rule rendered`);
  check(/100|۱۰۰/.test(document.getElementById('alertsRules').textContent), `${file}: threshold rendered in active locale`);
  check(document.getElementById('alertsEvents').textContent.includes('http-400'), `${file}: bounded delivery reason rendered`);
  check(document.getElementById('alertsEvents').querySelector('script') === null, `${file}: hostile source cannot create script`);
  check(document.getElementById('alertsEvents').innerHTML.includes('&lt;script&gt;'), `${file}: hostile source escaped`);
  check(document.getElementById('alertsBadge').textContent !== '', `${file}: enabled count visible after a successful read`);
  check(document.getElementById('alertsBadge').title.includes('خوانده‌نشده'), `${file}: badge explicitly denies unread-event semantics`);
  alerts.close();
  check(!document.getElementById('alertsBadge').classList.contains('hidden'), `${file}: enabled count survives close`);
  dom.window.close();
}
async function mutationAndRaceCase() {
  const { window, document, api, alerts, dom } = bootstrap('index.html');
  alerts.setUser(user('owner'));
  await alerts.open();
  document.getElementById('alertThreshold').value = '105';
  document.getElementById('alertSymbol').value = 'btcirt';
  document.getElementById('alertRuleForm').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  const created = api.calls.find(call => call.method === 'POST');
  check(created, 'create sends POST');
  check(created.options.headers['X-CSRF-Token'] === 'fixture-csrf', 'create carries CSRF cookie');
  check(created.options.body === JSON.stringify({ symbol: 'BTCIRT', metric: 'last', operator: 'above', threshold: 105 }), 'create payload normalized');
  check(document.getElementById('alertsStatus').textContent.includes('baseline'), 'create explains baseline');

  const toggle = document.querySelector('#alertsRules [data-alert-action="toggle"]');
  toggle.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
  const patched = api.calls.find(call => call.method === 'PATCH');
  check(patched && patched.options.headers['X-CSRF-Token'] === 'fixture-csrf', 'toggle sends CSRF PATCH');
  check(patched.options.body === JSON.stringify({ enabled: false }), 'toggle sends only enabled');

  const del = document.querySelector('#alertsRules [data-alert-action="delete"]');
  const deletesBefore = api.calls.filter(call => call.method === 'DELETE').length;
  del.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
  check(api.calls.filter(call => call.method === 'DELETE').length === deletesBefore, 'first delete click only asks confirmation');
  const confirm = document.querySelector('#alertsRules [data-alert-action="confirm-delete"]');
  check(confirm, 'confirmation control rendered');
  confirm.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await settle();
  const deleted = api.calls.find(call => call.method === 'DELETE');
  check(deleted && deleted.options.headers['X-CSRF-Token'] === 'fixture-csrf', 'confirmed delete sends CSRF');
  check(deleted.options.body == null, 'DELETE has no JSON body');
  check(document.getElementById('alertsEvents').textContent.includes('تاریخی'), 'historical event snapshot remains visible');

  const metric = document.getElementById('alertMetric');
  metric.value = 'premium_pct';
  metric.dispatchEvent(new window.Event('change', { bubbles: true }));
  const symbol = document.getElementById('alertSymbol');
  check(symbol.value === 'USDTIRT' && symbol.readOnly, 'premium locks the reference symbol');
  document.getElementById('alertThreshold').value = '-2.5';
  document.getElementById('alertRuleForm').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  await settle();
  const premiumPosts = api.calls.filter(call => call.method === 'POST');
  const premiumBody = JSON.parse(premiumPosts[premiumPosts.length - 1].options.body);
  check(premiumBody.symbol === 'USDTIRT' && premiumBody.metric === 'premium_pct' && premiumBody.threshold === -2.5, 'premium payload preserves signed threshold');

  document.documentElement.lang = 'en';
  alerts.render();
  check(document.getElementById('alertsTitle').textContent === 'Price and premium alerts', 'English static translation');
  check(document.getElementById('alertsRules').textContent.includes('Armed') || document.getElementById('alertsRules').textContent.includes('baseline'), 'English dynamic status translation');
  document.documentElement.lang = 'fa';

  const callsBeforeViewer = api.calls.length;
  alerts.setUser(user('viewer'));
  check(document.getElementById('alertsBadge').classList.contains('hidden'), 'role loss clears enabled badge');
  check(!document.getElementById('alertsRules').textContent.includes('BTCIRT'), 'role loss clears operational data');
  await alerts.refresh();
  check(api.calls.length === callsBeforeViewer, 'role loss cannot be bypassed through public refresh');

  alerts.setUser(user('owner', 'other-owner'));
  api.failRules = true;
  await alerts.open();
  check(document.getElementById('alertsStatus').textContent.includes('انجام نشد'), 'read failure is an error, not a fake empty state');
  check(document.getElementById('alertsRulesCount').textContent === '—', 'failed read clears counts');
  api.failRules = false;

  alerts.close();
  api.defer = deferred();
  const pendingOpen = alerts.open();
  await settle();
  alerts.close();
  api.defer.resolve(response({ ok: true, rules: [rule(99, { symbol: 'SECRETIRT' })], n: 1 }));
  await pendingOpen;
  check(!document.getElementById('alertsRules').textContent.includes('SECRETIRT'), 'late reply after close is discarded');
  api.defer = deferred();
  const rolePending = alerts.open();
  await settle();
  alerts.setUser(user('viewer'));
  api.defer.resolve(response({ ok: true, rules: [rule(100, { symbol: 'ROLELATEIRT' })], n: 1 }));
  await rolePending;
  check(!document.getElementById('alertsRules').textContent.includes('ROLELATEIRT'), 'late reply after role loss is discarded');
  api.defer = null;
  alerts.setUser(user('owner', 'final-owner'));

  await alerts.open();
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  check(document.getElementById('alertsModal').classList.contains('hidden'), 'Escape closes dialog');
  check(document.activeElement === document.getElementById('alertsBtn'), 'close restores bell focus');
  dom.window.close();
}

(async () => {
  const index = fs.readFileSync('index.html', 'utf8');
  check(index.includes('<script src="nbx-alerts.js"></script>'), 'canonical loads alert module');
  const mobile = fs.readFileSync('nobitex-mobile.html', 'utf8');
  const inline = mobile.match(/<script id="nbx-inline-nbx-alerts-js">\s*([\s\S]*?)\s*<\/script>/);
  check(inline && inline[1].trim() === moduleCode.trim(), 'standalone inlines exact alert module');
  for (const file of ['index.html', 'nobitex-mobile.html']) {
    const html = fs.readFileSync(file, 'utf8');
    for (const id of ['alertsBtn', 'alertsModal', 'alertsDialog', 'alertRuleForm', 'alertsRules', 'alertsEvents']) {
      check(html.includes(`id="${id}"`), `${file}: ${id} present`);
    }
    await viewerCase(file);
    await ownerBasicCase(file);
  }
  await mutationAndRaceCase();
  console.log(`Price alerts UI: ${checks} checks passed (canonical + standalone, no network/orders)`);
})().catch(error => { console.error(error); process.exitCode = 1; });
