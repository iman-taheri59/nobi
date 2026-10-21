/* Local logos must work without a server, CDN or image requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

for (const file of ['index.html', 'nobitex-mobile.html']) {
  const html = fs.readFileSync(file, 'utf8');
  const code = html.match(/<script id="nbx-coin-icons">([\s\S]*?)<\/script>/)[1];
  const context = { window: {} };
  vm.runInNewContext(code, context);
  const icon = context.window.coinIcon;
  const parse = (symbol, size) => new JSDOM(icon(symbol, size)).window.document.body.firstElementChild;
  for (const symbol of ['BTCIRT', 'ETHUSDT', 'USDTIRT', 'BTC/IRT', 'btc-irt', 'BTC_IRT', 'BTC', 'USDT', 'SOLIRT']) {
    const el = parse(symbol);
    assert.equal(el.tagName, 'IMG', symbol);
    assert.match(el.src, /^data:image\/svg\+xml;base64,/);
    const svg = Buffer.from(el.src.split(',')[1], 'base64').toString();
    assert.match(svg, /<svg/);
    assert.doesNotMatch(svg.replace(/xmlns="[^"]*"/g, ''), /<script|https?:\/\//i);
    assert.equal(el.getAttribute('alt'), '');
    assert.equal(el.width, 24);
  }
  assert.equal(parse('USDTIRT').title, 'USDT');
  assert.equal(parse('WBTCIRT').tagName, 'SPAN'); // never mislabel wrapped BTC
  assert.equal(parse('NEWCOINUSDT').textContent, 'NEW');
  assert.equal(parse('NEWCOINUSDT').getAttribute('aria-label'), 'NEWCOIN');
  for (const input of ['', null, '<img src=x onerror=alert(1)>', '"><script>alert(1)</script>']) {
    const el = parse(input);
    assert.equal(el.tagName, 'SPAN');
    assert.equal(el.querySelectorAll('script,img').length, 0);
  }
  assert.equal(parse('BTCIRT', 999).width, 48);
  assert.equal(parse('BTCIRT', -99).width, 16);
  assert.equal(parse('BTCIRT', 'oops').width, 24);
  const svgCount = fs.readdirSync('assets/coins').filter(x => x.endsWith('.svg')).length;
  assert.equal((code.match(/data:image\/svg\+xml;base64,/g) || []).length, svgCount);
  console.log(`${file}: PASS (${svgCount} embedded logos, pairs, safe fallback, size limits)`);
}
const app = fs.readFileSync('nbx-app.js', 'utf8');
assert.match(app, /window\.coinIcon\(s\.symbol\)/);
assert.match(app, /window\.coinIcon\(x\.symbol, 20\)/);
console.log('Signal and fundamentals rendering: PASS');
