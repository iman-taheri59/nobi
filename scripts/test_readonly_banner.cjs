// Viewer role notice and upstream health are independent UI states.
// Execute the production functions, not a copied implementation.
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const app = fs.readFileSync('nbx-app.js', 'utf8');
const index = fs.readFileSync('index.html', 'utf8');
const paint = app.match(/  function paintUser\(state\) \{[\s\S]*?\n  \}/);
const upstream = index.match(/function upShow\(on, detail\)\{[\s\S]*?\n\}/);
assert(paint && upstream, 'production paintUser/upShow functions must remain extractable');

for (const file of ['index.html', 'nobitex-mobile.html']) {
  const html = fs.readFileSync(file, 'utf8');
  assert(html.includes('id="roleBanner"'), `${file}: missing independent role banner`);
  assert(html.includes('id="roleBannerMsg"'), `${file}: missing role message`);
  assert(!/id="upBannerMsg"[^>]*data-i18n="auth\.readonly"/.test(html),
    `${file}: upstream message must not double as the role notice`);
}

const dom = new JSDOM(`<!doctype html><html><body>
  <span id="userChip" class="hidden"></span><span id="userName"></span><span id="userRole"></span>
  <button id="logoutBtn" class="hidden"></button><div id="accountNav" class="hidden"></div>
  <div id="operatorAuditPanel" class="hidden"></div>
  <div id="upBanner"><div id="upBannerMsg">upstream-original</div></div>
  <div id="roleBanner" class="hidden"><div id="roleBannerMsg"></div></div>
</body></html>`, { url: 'https://example.test/' });
const { window } = dom;
const byId = id => window.document.getElementById(id);
const context = {
  window,
  document: window.document,
  $: byId,
  byId,
  inFa: key => key,
  roleLabel: role => `auth.role.${role}`,
  t: key => key,
  refreshIcons: () => {},
};
vm.runInNewContext(`let auditAuthIdentity = null;\n${paint[0]}\n${upstream[0]}\nwindow.paintUser = paintUser; window.upShow = upShow;`, context);

window.paintUser({
  user: { username: 'viewer1', role: 'viewer' },
  can_mutate: false,
  capabilities: { signal_intelligence_review: false },
});
assert(!byId('roleBanner').classList.contains('hidden'), 'Viewer role notice is visible');
assert.strictEqual(byId('roleBannerMsg').textContent, 'auth.readonly');
assert.strictEqual(byId('upBannerMsg').textContent, 'upstream-original',
  'painting the role must not rewrite upstream health');
assert.strictEqual(window.document.documentElement.dataset.canMutate, '0');
assert(byId('operatorAuditPanel').classList.contains('hidden'));

// This exact sequence used to hide the only read-only notice after a healthy scan.
window.upShow(false);
assert(byId('upBanner').classList.contains('hidden'), 'healthy upstream alert is hidden');
assert(!byId('roleBanner').classList.contains('hidden'),
  'healthy upstream repaint must not hide the Viewer notice');
assert.strictEqual(byId('roleBannerMsg').textContent, 'auth.readonly');

// Both truthful states can coexist: exchange down AND this session is read-only.
window.upShow(true, 'fixture unreachable');
assert(!byId('upBanner').classList.contains('hidden'));
assert(!byId('roleBanner').classList.contains('hidden'));
assert(byId('upBannerMsg').textContent.includes('fixture unreachable'));
assert.strictEqual(byId('roleBannerMsg').textContent, 'auth.readonly');

window.paintUser({
  user: { username: 'owner1', role: 'owner' },
  can_mutate: true,
  capabilities: { signal_intelligence_review: true },
});
assert(byId('roleBanner').classList.contains('hidden'), 'Owner gets no Viewer notice');
assert.strictEqual(window.document.documentElement.dataset.canMutate, '1');
assert(!byId('operatorAuditPanel').classList.contains('hidden'));
assert(!byId('upBanner').classList.contains('hidden'), 'role paint does not hide upstream alert');

console.log('PASS — read-only role notice survives healthy/error upstream repaints; Owner stays unchanged');
