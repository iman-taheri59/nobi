/* Local logos must work without a server, CDN or image requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');

for (const file of ['index.html', 'nobitex-mobile.html']) {
  const html = fs.readFileSync(file, 'utf8');
  const code = html.match(/<script id="nbx-coin-icons">([\s\S]*?)<\/script>/)[1];
  const dom = new JSDOM('', { url: 'https://dashboard.test/' });
  const context = { window: {}, document: dom.window.document, URL };
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
  const body = dom.window.document.body;
  body.innerHTML = icon('BTCIRT') + icon('TONIRT');
  const setMetadata = context.window.setCoinMetadata;
  setMetadata([
    {id: 'bitcoin', symbol: 'BTC', name: 'Bitcoin', updated_at: 123, image_url: '/api/coins/bitcoin/logo'},
    {id: 'the-open-network', symbol: 'TON', name: '<img onerror=bad>', updated_at: 123, image_url: '/api/coins/the-open-network/logo'},
    {id: 'bad', symbol: 'BAD', name: 'bad', image_url: 'https://evil.test/image.png'},
  ], '/api');
  assert.match(body.querySelector('[data-coin="BTC"]').src, /^https:\/\/dashboard.test\/api\/coins\/bitcoin\/logo\?v=123$/);
  assert.equal(body.querySelector('[data-coin="TON"]').tagName, 'IMG');
  assert.equal(body.querySelector('[data-coin="TON"]').getAttribute('title'), '<img onerror=bad>');
  assert.equal(body.querySelectorAll('[onerror]').length, 0);
  assert.equal(parse('BADIRT').tagName, 'SPAN');
  // Remote failure -> bundled image -> text if even the bundled image fails.
  body.querySelector('[data-coin="BTC"]').dispatchEvent(new dom.window.Event('error'));
  assert.match(body.querySelector('[data-coin="BTC"]').src, /^data:image/);
  body.querySelector('[data-coin="BTC"]').dispatchEvent(new dom.window.Event('error'));
  assert.equal(body.querySelector('[data-coin="BTC"]').tagName, 'SPAN');
  // A newly downloaded version is eligible again (different cache-busting URL).
  setMetadata([{id: 'bitcoin', symbol: 'BTC', name: 'Bitcoin', updated_at: 124, image_url: '/api/coins/bitcoin/logo'}], 'https://backend.test/api');
  assert.match(body.querySelector('[data-coin="BTC"]').src, /^https:\/\/backend.test\/api\//);
  // Exchange-only symbols still get authoritative names without any CG ID.
  setMetadata([
    {id: null, symbol: 'XYZ', name: 'Exchange-only asset', image_url: null},
    {id: 'bitcoin', symbol: 'BTC', name: 'نام نوبیتکس', updated_at: 124, image_url: '/api/coins/bitcoin/logo'},
  ], '/api');
  assert.equal(parse('XYZIRT').tagName, 'SPAN');
  assert.equal(parse('XYZIRT').title, 'Exchange-only asset');
  assert.equal(parse('BTCIRT').title, 'نام نوبیتکس');
  assert.equal(parse('XYZIRT').querySelector('img'), null);
  setMetadata(null, 'javascript:bad'); // unsupported/malformed data is harmless
  console.log(`${file}: PASS (${svgCount} embedded logos, pairs, safe fallback, size limits)`);
}
const app = fs.readFileSync('nbx-app.js', 'utf8');
assert.match(app, /window\.coinIcon\(s\.symbol\)/);
assert.match(app, /window\.coinIcon\(\w+\.symbol,\s*20\)/);
console.log('Signal and fundamentals rendering: PASS');
