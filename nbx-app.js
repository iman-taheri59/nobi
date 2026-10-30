/* ============================================================
 * nobiTrader — Application Layer (Phase 1–2 + Mobile Ready)
 * Binds the dashboard to real/standalone data via NBX.core.
 * Features: live stats, real TA/FA scoring, universe ranking,
 * mobile bottom navigation bar, mobile package downloader, health badges.
 * ============================================================ */
(function () {
  'use strict';

  if (window.__NBX_APP_LOADED) return;
  window.__NBX_APP_LOADED = true;

  const $ = (id) => document.getElementById(id);
  /* Server- and exchange-provided strings reach innerHTML and HTML
   * attributes below. Escape once, here, instead of trusting each
   * call site to remember. */
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (ch) =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  const faNum = (n, d = 0) => window.NBXDisplay.number(n, d);
  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
  const inFa = (k) => window.NBXDisplay.text((T[LANG] && T[LANG][k]) || T.fa[k] || k);
  const goodPct = (v, d = 1) => window.NBXDisplay.percent(v, d);
  const fmtPrice = (v, sym) => window.NBXDisplay.price(v, sym);

  let live = {
    mode: 'ONLINE', // ONLINE | MOBILE | DEGRADED | OFFLINE
    interval: null,
    timeframe: '15m',
    lastScanAt: null,
    latency: null,   // no invented latency: a number appears only from a real call
    ranked: [],
    activeSymbol: 'BTCIRT',
    candleSeriesData: null,
    source: null,
    upstream: null,
    risk: null,
    //: `/api/ws/status`: the transport actually in use and what arrived on it.
    wsTransport: null,
    wsChannels: [],
    wsStats: 0,
    wsCandle: 0,
  };
  window.live = live;

  /* ---------- single source of truth for a price ----------
     The desk ticket used to carry a hard-coded table of eight prices. Anything
     that needs a price now asks here, and gets null when no live row exists. */
  window.livePrice = function livePrice(sym) {
    if (!sym || live.unavailable) return null;
    const unis = (NBX.core.engine && NBX.core.engine.universe) || [];
    const u = unis.find((x) => x && x.symbol === sym);
    if (u) {
      const px = [u.last_price, u.lastPrice, u.latest, u.price, u.close]
        .find((v) => Number.isFinite(Number(v)) && Number(v) > 0);
      if (px !== undefined) return Number(px);
    }
    const sig = (live.ranked || []).find((x) => x && x.symbol === sym);
    const sp = sig && [sig.price, sig.last_price, sig.last]
      .find((v) => Number.isFinite(Number(v)) && Number(v) > 0);
    return sp !== undefined ? Number(sp) : null;
  };

  /* ---------- UI helpers for live & mobile widgets ---------- */
  function addLiveStylesOnce() {
    if ($('nbx-live-styles')) return;
    const st = document.createElement('style');
    st.id = 'nbx-live-styles';
    st.textContent = `
      .nbx-mini-hbar{height:6px;border-radius:999px;background:rgba(255,255,255,.07);overflow:hidden}
      .nbx-mini-hbar > i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#34d399,#38bdf8)}
      #candleChips{scrollbar-width:none}
      #candleChips::-webkit-scrollbar{display:none}
      .meta-row{display:flex;justify-content:space-between;font-size:11px;color:#a1a1aa;padding:5px 0;border-bottom:1px dashed rgba(255,255,255,.05)}
      #mobileBottomNav{position:fixed;bottom:0;left:0;right:0;z-index:75;background:rgba(9,9,11,.94);
        backdrop-filter:blur(16px);border-top:1px solid rgba(255,255,255,.09);
        padding:4px 6px max(4px, env(safe-area-inset-bottom));}
      @media (min-width:1024px){ #mobileBottomNav{display:none !important;} }
      /* Ten tabs cannot share 390px: one label every 34px overlapped its
         neighbours and the bar read as gibberish. The row scrolls instead. */
      #mobileBottomNav .mnav-row{display:flex;align-items:stretch;gap:2px;overflow-x:auto;overflow-y:hidden;
        scroll-snap-type:x proximity;scrollbar-width:none;-webkit-overflow-scrolling:touch;}
      #mobileBottomNav .mnav-row::-webkit-scrollbar{display:none}
      .mnav-btn{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
        padding:4px 6px;border-radius:10px;font-size:9.5px;color:#a1a1aa;transition:all .2s ease;border:none;
        background:transparent;cursor:pointer;flex:0 0 auto;min-width:64px;max-width:82px;scroll-snap-align:center;}
      .mnav-btn span{display:block;width:100%;max-width:100%;overflow:hidden;text-overflow:ellipsis;
        white-space:nowrap;text-align:center;}
      .mnav-btn.active{color:#6ee7b7;background:rgba(52,211,153,.12);font-weight:700;}
    `;
    document.head.appendChild(st);
  }

  function renderLiveBadge() {
    let el = $('liveBadge');
    if (!el) {
      el = document.createElement('div');
      el.id = 'liveBadge';
      el.className = 'live-badge';
      // Reserve real space above the page, including the expanded details.
      // Keep this out of the sticky sidebar and the mobile navigation layer.
      const main = document.querySelector('main');
      const banner = $('upBanner');
      if (main && banner && banner.parentElement === main) banner.after(el);
      else (main || document.body).prepend(el);
      el.innerHTML = `
        <button id="liveBadgeBtn" type="button" aria-expanded="false" aria-controls="apiStatusPop"></button>
        <div id="apiStatusPop" hidden></div>
      `;
      const btn = $('liveBadgeBtn');
      const pop = $('apiStatusPop');
      btn.onclick = () => {
        pop.hidden = !pop.hidden;
        btn.setAttribute('aria-expanded', String(!pop.hidden));
      };
      el.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !pop.hidden) {
          pop.hidden = true;
          btn.setAttribute('aria-expanded', 'false');
          btn.focus();
          event.preventDefault();
          event.stopPropagation();
        }
      });
    }
    const m = live.mode;
    const map = {
      ONLINE: ['#34d399', LANG === 'fa' ? 'داده زنده نوبیتکس' : 'Live Nobitex data'],
      MOBILE: ['#f87171', LANG === 'fa' ? 'سرور نوبیتکس در دسترس نیست' : 'Nobitex unreachable'],
      DEGRADED: ['#f87171', LANG === 'fa' ? 'سرور نوبیتکس در دسترس نیست' : 'Nobitex unreachable'],
      OFFLINE: ['#f87171', LANG === 'fa' ? 'آفلاین — بدون داده' : 'Offline — no data'],
    };
    const [color, text] = map[m] || map.MOBILE;
    const latency = live.latency !== null && live.latency !== undefined ? Math.round(live.latency) + 'ms' : '—';
    const onWs = live.wsTransport === 'ws';
    const wsCh = Array.isArray(live.wsChannels) ? live.wsChannels.length : 0;
    // Keep the button node and its focus, plus disclosure state, across polls.
    $('liveBadgeBtn').innerHTML = `
      <span class="dot" aria-hidden="true" style="background:${color};box-shadow:0 0 0 4px ${color}22"></span>
      <span class="live-badge-label">${esc(text)}</span>
      <span class="live-badge-latency num">${esc(latency)}</span>
      ${onWs && wsCh ? `<span class="live-badge-ws num" style="color:#818cf8">WS x${esc(faNum(wsCh))}</span>` : ''}
      <svg class="live-badge-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
    `;
    $('apiStatusPop').innerHTML = [
      [LANG === 'fa' ? 'حالت اتصال' : 'Connection', `${m} · ${latency}`],
      [LANG === 'fa' ? 'نمادهای فعال' : 'Active Symbols', live.ranked.length ? faNum(live.ranked.length) : '—'],
      [LANG === 'fa' ? 'تایم‌فریم' : 'Timeframe', live.timeframe],
      [LANG === 'fa' ? 'آخرین دریافت صفحه' : 'Last page update', live.lastScanAt ? faNum(Math.round((Date.now() - live.lastScanAt) / 1000)) + (LANG === 'fa' ? ' ثانیه پیش' : 's ago') : '—'],
      [LANG === 'fa' ? 'مسیر قیمت' : 'Price path', onWs ? (LANG === 'fa' ? 'وب‌سوکت' : 'WebSocket') : (LANG === 'fa' ? 'REST — دوره‌ای' : 'REST — polled')],
      [LANG === 'fa' ? 'کانال‌های وب‌سوکت' : 'WS channels', onWs ? faNum(wsCh) : '—'],
      [LANG === 'fa' ? 'پیام آمار بازار' : 'Market-stat frames', onWs ? faNum(live.wsStats || 0) : '—'],
      [LANG === 'fa' ? 'کندل وب‌سوکت' : 'WS candles', onWs ? faNum(live.wsCandle || 0) : '—'],
    ].map(([k, v]) => `<dl class="live-status-row"><dt>${esc(k)}</dt><dd class="num">${esc(v)}</dd></dl>`).join('') +
      `<div class="live-status-note">${LANG === 'fa' ? 'زمان دریافت صفحه، گزارش چرخهٔ اسکن مستقلِ سرور نیست.' : 'The page update time is not the background server scan status.'}</div>`;
  }

  /* ---------- Mobile Bottom Navigation Bar & Header Download Modal ---------- */
  function injectMobileNavAndDownload() {
    if (!$('mobileBottomNav')) {
      const nav = document.createElement('div');
      nav.id = 'mobileBottomNav';
      // Derived from the sidebar, never a second hardcoded list. The previous
      // copy here silently missed `settings`, so on a phone — where this bar
      // *is* the navigation and the sidebar is off-canvas — the tab existed in
      // the DOM but nowhere a user could tap it. Reading the DOM means a tab
      // added to index.html shows up here by construction.
      const navItems = () =>
        [...document.querySelectorAll('.navbtn')].map((b) => ({
          tab: b.dataset.tab,
          // After the first icon pass the placeholder <i> is gone and only the
          // <svg class="lucide lucide-x"> remains; without this fallback every
          // bottom-nav icon became an anonymous circle.
          icon: (function () {
            const i = b.querySelector('i[data-lucide]');
            if (i) return i.getAttribute('data-lucide');
            const svg = b.querySelector('svg');
            const cls = svg ? (svg.getAttribute('class') || '') : '';
            const m = cls.match(/lucide-([a-z0-9-]+)/);
            return m ? m[1] : 'circle';
          })(),
          // label is re-read on every render, so a language switch (which
          // rewrites the sidebar text) carries over without a separate table.
          label: (b.querySelector('[data-i18n]')?.textContent || b.textContent || '').trim(),
        }));
      const renderMNav = () => {
        const activeTab = document.querySelector('.navbtn.active')?.dataset?.tab || 'overview';
        nav.innerHTML = `<div class="mnav-row">
          ${navItems()
            .map(
              (it) => `
            <button data-mtab="${it.tab}" class="mnav-btn ${activeTab === it.tab ? 'active' : ''}" title="${it.label}">
              <i data-lucide="${it.icon}" class="w-4 h-4"></i>
              <span>${it.label}</span>
            </button>`
            )
            .join('')}
        </div>`;
        nav.querySelectorAll('[data-mtab]').forEach((b) => {
          b.onclick = () => {
            const target = document.querySelector(`.navbtn[data-tab="${b.dataset.mtab}"]`);
            if (target) target.click();
            renderMNav();
          };
        });
        if (typeof refreshIcons === 'function') refreshIcons();
      };
      document.body.appendChild(nav);
      renderMNav();
      document.querySelectorAll('.navbtn').forEach((btn) => {
        btn.addEventListener('click', () => setTimeout(renderMNav, 20));
      });
      ['langFa', 'langEn', 'mLangFa', 'mLangEn'].forEach((id) => {
        const el = $(id);
        if (el) el.addEventListener('click', () => setTimeout(renderMNav, 40));
      });
    }

    /* Inject Mobile Package button in header */
    const clockEl = $('clock');
    if (clockEl && !$('mobilePkgBtn')) {
      const btn = document.createElement('button');
      btn.id = 'mobilePkgBtn';
      btn.className = 'chip text-sky-300 border-sky-400/30 hover:bg-sky-400/10 cursor-pointer px-2.5 py-1.5';
      btn.innerHTML = `<i data-lucide="smartphone" class="w-3.5 h-3.5"></i> <span id="mobilePkgBtnTxt" class="hidden sm:inline">${LANG === 'fa' ? 'پکیج موبایل' : 'Mobile Pkg'}</span>`;
      clockEl.parentElement.insertBefore(btn, clockEl);
      btn.onclick = openMobileModal;
    }
  }

  function openMobileModal() {
    let m = $('mobilePkgModal');
    if (!m) {
      m = document.createElement('div');
      m.id = 'mobilePkgModal';
      m.className = 'fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm';
      document.body.appendChild(m);
    }
    const isServer = location.protocol === 'http:' || location.protocol === 'https:';
    m.classList.remove('hidden');
    m.classList.add('flex');
    m.innerHTML = `
      <div class="glass max-w-lg w-full p-5 sm:p-6 space-y-4 border border-sky-400/30">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <div class="w-9 h-9 rounded-xl bg-sky-400/15 border border-sky-300/30 flex items-center justify-center text-sky-300">
              <i data-lucide="smartphone" class="w-5 h-5"></i>
            </div>
            <div>
              <div class="font-extrabold text-sm sm:text-base text-sky-300">${LANG === 'fa' ? 'دریافت و اجرای پیش‌نمایش روی گوشی موبایل' : 'Mobile Preview Package'}</div>
              <div class="text-[11px] text-zinc-400">${LANG === 'fa' ? 'بدون نیاز به نصب پایتون یا سرور — ۱۰۰٪ مستقل و آفلاین/آنلاین' : 'Zero server required — 100% standalone on Android & iOS'}</div>
            </div>
          </div>
          <button id="closeMobileModal" class="p-1.5 rounded-lg hover:bg-white/10 text-zinc-400"><i data-lucide="x" class="w-4.5 h-4.5"></i></button>
        </div>

        <div class="rounded-xl bg-white/[.03] border border-white/10 p-3.5 text-xs space-y-2 leading-6 text-zinc-300">
          <div class="font-bold text-emerald-300">${LANG === 'fa' ? 'قابلیت‌های نسخه موبایل:' : 'Mobile Package Highlights:'}</div>
          <ul class="list-disc ps-4 space-y-1 text-[11px] text-zinc-400">
            <li>${LANG === 'fa' ? 'تمامی استایل‌ها، آیکون‌ها، چارت‌ها و موتورهای TA، FA، مدیریت ریسک، بک‌تست Walk-Forward و گرید سرچ درون خود فایل تعبیه شده‌اند.' : 'All styles, charts, TA/FA engines, Risk, Walk-Forward Backtest & Grid Search are embedded.'}</li>
            <li>${LANG === 'fa' ? 'سازگار با باز شدن مستقیم از فایل‌منیجر گوشی (Android / iOS Files)، تلگرام، کروم و سافاری حتی در حالت آفلاین.' : 'Works directly from Android/iOS Files, Telegram, Chrome, and Safari even offline.'}</li>
          </ul>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <a id="dlSingleHtmlBtn" href="${isServer ? '/download/nobitex-mobile.html' : '#'}" download="nobiTrader-mobile.html"
             class="flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-xs font-bold bg-emerald-400/15 border border-emerald-300/40 text-emerald-300 hover:bg-emerald-400/25 transition text-center">
            <i data-lucide="download" class="w-4 h-4"></i>
            <span>${LANG === 'fa' ? 'دانلود تک‌فایل موبایل (.html)' : 'Download Single-File (.html)'}</span>
          </a>
          ${
            isServer
              ? `<a href="/download/nobitex-trader-robat.zip" download="nobitex-trader-robat.zip"
                   class="flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-xs font-bold bg-sky-400/15 border border-sky-300/40 text-sky-300 hover:bg-sky-400/25 transition text-center">
                  <i data-lucide="download" class="w-4 h-4"></i>
                  <span>${LANG === 'fa' ? 'دانلود کل پکیج (.zip)' : 'Download Full ZIP (.zip)'}</span>
                </a>`
              : `<button id="dlLocalBlobBtn" class="flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-xs font-bold bg-sky-400/15 border border-sky-300/40 text-sky-300">
                  <i data-lucide="download" class="w-4 h-4"></i>
                  <span>${LANG === 'fa' ? 'ذخیره کپی آفلاین (.html)' : 'Save Offline Copy (.html)'}</span>
                </button>`
          }
        </div>
      </div>
    `;
    if (typeof refreshIcons === 'function') refreshIcons();
    $('closeMobileModal').onclick = () => {
      m.classList.add('hidden');
      m.classList.remove('flex');
    };
    m.onclick = (e) => {
      if (e.target === m) {
        m.classList.add('hidden');
        m.classList.remove('flex');
      }
    };
    if (!isServer) {
      const saveFn = (e) => {
        e.preventDefault();
        m.classList.add('hidden');
        const htmlContent = '<!DOCTYPE html>\n' + document.documentElement.outerHTML;
        const blob = new Blob([htmlContent], { type: 'text/html;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'nobiTrader-mobile.html';
        a.click();
      };
      if ($('dlSingleHtmlBtn')) $('dlSingleHtmlBtn').onclick = saveFn;
      if ($('dlLocalBlobBtn')) $('dlLocalBlobBtn').onclick = saveFn;
    }
  }

  /* ---------- index.html i18n augmentation ---------- */
  const extraI18n = {
    fa: {
      'app.title': 'nobiTrader',
      'page.overview.desc': 'وضعیت سرمایه، پوزیشن‌ها و بازار — آماده اجرا روی موبایل و دسکتاپ',
      'page.signals.desc': 'رتبه‌بندی زنده دنیای نوبیتکس با موتور TA و FA',
      'sg.hdr': 'اسکنر زنده نوبیتکس',
      'sg.scanned': '{ranked} نماد اسکن شد',
      'sg.faCoverage': 'برچسب live سرور: {n} از {t}',
      'sg.faScored': 'بنیادی: {n} از {t}',
      'sg.faCoverageTitle': 'گزارش سرور؛ نه تأیید مستقل دریافت: محاسبه‌شده {s} · fallback گزارش‌شده {f} (نه لزوماً ثابت) · ناموفق {x}',
      'sg.basisNoFa': 'بدون بنیادی',
      'sg.basisTitle': 'امتیاز ترکیبی فقط از TA و نقدینگی محاسبه شده است',
      'sg.cycle': 'چرخه: {tf} — auto {m}m',
      'sg.last': 'آخرین چرخه: {s} ثانیه پیش',
      'sg.showAll': 'نمایش همه ({n})',
      'sg.showLess': 'نمایش کمتر',
      'ov.kpi1sub': 'نسبت به سرمایه اولیه',
      't.connected': 'موتور تحلیلی آماده است',
      't.connectedM': 'دنیای نمادها بارگذاری و با موتور تکنیکال و فاندامنتال امتیازدهی شد.',
      't.degraded': 'حالت پیش‌نمایش مستقل موبایل',
      't.degradedM': 'موتور تحلیل تکنیکال، فاندامنتال، ریسک و بک‌تست به‌صورت مستقیم روی مرورگر گوشی فعال است.',
    },
    en: {
      'app.title': 'nobiTrader',
      'page.overview.desc': 'Equity, positions and market state — mobile & desktop ready',
      'page.signals.desc': 'Nobitex universe ranking with TA & FA engines',
      'sg.hdr': 'Live Nobitex Scanner',
      'sg.scanned': '{ranked} symbols scanned',
      'sg.faCoverage': 'Server-labelled live FA: {n} of {t}',
      'sg.faScored': 'FA: {n} of {t}',
      'sg.faCoverageTitle': 'Server report; receipt not independently verified: scored {s} · reported fallback {f} (not necessarily static) · failed {x}',
      'sg.basisNoFa': 'no FA',
      'sg.basisTitle': 'Composite computed from TA and liquidity only',
      'sg.cycle': 'Cycle: {tf} — auto {m}m',
      'sg.last': 'Last cycle: {s}s ago',
      'sg.showAll': 'show all ({n})',
      'sg.showLess': 'show fewer',
      't.connected': 'Analytics engine ready',
      't.connectedM': 'Universe loaded and scored by the TA & FA engines.',
      't.degraded': 'Standalone Mobile Preview Active',
      't.degradedM': 'TA, FA, Risk, Paper Trading & Backtest engines are running locally in your browser.',
    },
  };
  Object.keys(extraI18n).forEach((l) => Object.assign(T[l] || {}, extraI18n[l]));

  function setLiveMode(m, latency) {
    live.mode = m;
    live.latency = latency || live.latency;
    renderLiveBadge();
    // The overview header used to claim "live" on every load, including when
    // the exchange was down. Keep the two statements from disagreeing.
    // The latency chip in the header used to print a hard-coded "API: 124ms".
    const api = document.getElementById('apiChip');
    if (api) {
      const ok = m === 'ONLINE' && live.latency !== null && live.latency !== undefined;
      api.className = 'hidden 2xl:inline-flex chip num shrink-0 ' +
        (ok ? 'text-emerald-300 border-emerald-300/30' : 'text-red-300 border-red-300/40');
      api.innerHTML = `<i data-lucide="${ok ? 'wifi' : 'wifi-off'}" class="w-3.5 h-3.5"></i> `
        + `<span>${ok ? (LANG==='fa'?'ارتباط: ':'API: ') + faNum(live.latency) + (LANG==='fa'?' میلی‌ثانیه':' ms') : (LANG === 'fa' ? 'ارتباط: بدون پاسخ' : 'API: no reply')}</span>`;
    }
    const chip = document.querySelector('[data-i18n-scope="chip.live"]');
    if (chip) {
      const ok = m === 'ONLINE';
      chip.textContent = ok
        ? inFa('chip.live')
        : (LANG === 'fa' ? 'بدون دادهٔ زنده' : 'no live data');
      chip.className = 'chip num shrink-0 ' + (ok ? 'text-emerald-300' : 'text-red-300 border-red-300/40');
    }
  }

  /* ---------- Overview updates: chart + positions ---------- */
  injectCandleSelector();

  function injectCandleSelector() {
    if ($('candleChips')) return;
    const anchor = $('candleChart')?.previousElementSibling;
    if (!anchor) return;
    const div = document.createElement('div');
    div.id = 'candleChips';
    div.className = 'flex gap-1.5 overflow-x-auto pb-1';
    div.innerHTML = ['15m', '1h', '4h', 'D']
      .map(
        (tf) => `
      <button data-tf="${tf}" class="tfSel px-2.5 py-1 rounded-lg text-[10px] font-bold border transition ${
          tf === '15m' ? 'bg-emerald-400/15 text-emerald-300 border-emerald-300/30' : 'bg-white/5 text-zinc-400 border-white/10'
        }">${window.NBXDisplay.timeframe(tf)}</button>
    `
      )
      .join('');
    anchor.appendChild(div);
    document.querySelectorAll('.tfSel').forEach(
      (b) =>
        (b.onclick = () => {
          document.querySelectorAll('.tfSel').forEach((x) => (x.className = 'tfSel px-2.5 py-1 rounded-lg text-[10px] font-bold border transition bg-white/5 text-zinc-400 border-white/10'));
          b.className = 'tfSel px-2.5 py-1 rounded-lg text-[10px] font-bold border transition bg-emerald-400/15 text-emerald-300 border-emerald-300/30';
          live.timeframe = b.dataset.tf;
          const ovTf = document.querySelector('[data-i18n="ov.tf"]');
          if (ovTf && typeof window.NBXDisplay === 'object') ovTf.textContent = (LANG === 'fa' ? 'تایم‌فریم ' : '') + window.NBXDisplay.timeframe(live.timeframe) + (LANG === 'fa' ? '' : ' timeframe');
          if (typeof window.loadMarketOverview === 'function') window.loadMarketOverview(live.timeframe);
          scanNow(true);
        })
    );
  }

  let paperSnapshot = null;
  let paperRefreshId = 0;
  async function refreshPaperState() {
    const refreshId = ++paperRefreshId;
    const controlEpoch = window.paperControlEpoch || 0;
    const api = NBX.core.api;
    let posRes = null;
    let metRes = null;
    let riskRes = null;
    try { posRes = await api.getPaperPositions(); } catch (e) {}
    try { metRes = await api.getPaperMetrics(); } catch (e) {}
    try { riskRes = await api.getBackendRiskState(); } catch (e) {}
    if (refreshId !== paperRefreshId) return;
    paperSnapshot = [posRes, metRes, riskRes, controlEpoch];
    paintPaperState(...paperSnapshot);
  }

  function paintPaperState(posRes, metRes, riskRes, controlEpoch = window.paperControlEpoch || 0) {
    const localMode = !!NBX.core.api.standaloneMobile;
    const usable = r => !!r && r.ok !== false && !r.unavailable && (localMode ? r.source === 'browser-local' : r.source !== 'browser-local');
    const positionsKnown = usable(posRes) && Array.isArray(posRes.positions);
    window.__NBX_PAPER_POSITIONS_KNOWN = positionsKnown;
    const rows = positionsKnown ? posRes.positions : [];
    const openRows = rows.filter((p) => p.state !== 'CLOSED');
    window.__NBX_PAPER_OPEN_TOTAL = positionsKnown ? openRows.length : null;
    const list = openRows
      .slice(0, 8)
      .map((p) => {
        const mark = Number(p.last_price || p.entry_price || 0);
        const pnl = Number.isFinite(p.pnl_pct)
          ? p.pnl_pct
          : p.entry_price
          ? ((mark / p.entry_price) - 1) * 100
          : 0;
        return {
          sym: p.symbol,
          label: { fa: p.symbol.replace('IRT', '/تومان'), en: p.symbol },
          entry: p.entry_price,
          stop_pct: p.stop_pct,
          side: p.side,
          price: mark,
          size: p.filled_qty ?? p.qty,
          sl: window.NBXDisplay.percent(p.stop_pct == null ? null : -Math.abs(p.stop_pct), 1),
          tp1: window.NBXDisplay.percent(p.tp1 && p.entry_price ? ((p.tp1 - p.entry_price) / p.entry_price) * 100 : 0, 1),
          state: {
            fa: p.state === 'PARTIAL' ? 'هدف اول محقق شد' : 'باز',
            en: p.state === 'PARTIAL' ? 'TP1 filled' : 'Open',
          },
          hot: !!p.tp1_filled,
          pnl,
        };
      });

    if (typeof positions !== 'undefined' && Array.isArray(positions)) {
      positions.length = 0;
      positions.push(...list);
      if (typeof renderPositions === 'function') renderPositions();
    }

    const validMetrics = m => !!m && m.equity != null && typeof m.equity !== 'boolean' && String(m.equity).trim() !== '' && Number.isFinite(Number(m.equity));
    const metricsKnown = usable(metRes) && validMetrics(metRes.metrics);
    const m = (metricsKnown && metRes.metrics) || (usable(posRes) && validMetrics(posRes.metrics) && posRes.metrics) || null;
    // Never resurrect an unlabelled, previously cached risk read after failure.
    const rs = controlEpoch === (window.paperControlEpoch || 0) && usable(riskRes) ? riskRes : null;
    const notice = $('paperStateNotice');
    if (notice) {
      notice.dataset.source = localMode ? 'browser-local' : 'server';
      notice.dataset.state = !m || !positionsKnown || !rs ? 'partial-or-unavailable' : 'current-read';
      notice.textContent = localMode
        ? (LANG === 'fa' ? 'منبع: دفتر مستقل مرورگر؛ این داده‌ها حساب سرور نیستند.' : 'Source: browser-local paper book; not the server account.')
        : (LANG === 'fa' ? 'منبع: دفتر کاغذی سرور.' : 'Source: server paper book.');
      if (!m || !positionsKnown || !rs) notice.textContent += LANG === 'fa'
        ? ' بعضی داده‌ها خوانده نشد؛ نامشخص به معنی صفر یا قطعی صرافی نیست.'
        : ' Some reads failed; unknown does not mean zero or an exchange outage.';
      else if (!metricsKnown) notice.textContent += LANG === 'fa'
        ? ' معیارها از پاسخ پوزیشن‌ها؛ منحنی سرمایه خوانده نشد.'
        : ' Metrics from positions response; equity curve read unavailable.';
    }
    ['paperRiskNotice','paperDeskNotice'].forEach(id => {
      const el=$(id);if (!el) return;
      el.dataset.state=rs ? 'read' : 'unknown';
      el.textContent=!rs
        ? (LANG === 'fa' ? 'خواندن وضعیت ریسک ناموفق بود؛ مقادیر قبلی ممکن است قدیمی باشند. این وضعیت تأیید ایمنی نیست؛ کنترل نهایی هر سفارش با سرور است.' : 'Risk read failed; previous values may be stale. This is not a safety approval; the server owns order preflight.')
        : localMode
        ? (LANG === 'fa' ? 'وضعیت ریسک دفتر مستقل مرورگر؛ نه حساب سرور.' : 'Risk state of the browser-local book, not the server account.')
        : (LANG === 'fa' ? 'وضعیت ریسک از پاسخ سرور؛ کنترل نهایی هر سفارش جداگانه انجام می‌شود.' : 'Risk state read from server; each order has a separate server preflight.');
    });
    const eq = m && m.equity != null && m.equity !== '' && typeof m.equity !== 'boolean' && Number.isFinite(Number(m.equity)) ? Number(m.equity) : null;
    const startEq = m && m.start_equity != null && m.start_equity !== '' && typeof m.start_equity !== 'boolean' && Number.isFinite(Number(m.start_equity)) ? Number(m.start_equity) : null;
    const pct = eq !== null && startEq ? ((eq - startEq) / startEq) * 100 : null;

    if ($('kEquity')) $('kEquity').textContent = eq !== null ? window.NBXDisplay.money(eq, 'USD') : '\u2014';
    if ($('kToday')) {
      $('kToday').textContent = pct !== null ? window.NBXDisplay.percent(pct, 2) : (LANG === 'fa' ? 'آمار کارایی ثبت نشده است' : 'Performance data not recorded');
      $('kToday').className = 'num kpi-num ' + (pct === null ? 'text-zinc-400' : pct < 0 ? 'text-red-400' : 'text-emerald-400');
    }
    if ($('kTodayV')) {
      $('kTodayV').textContent = eq !== null && startEq !== null ? window.NBXDisplay.money(eq - startEq, 'USD', 2, true) : '';
    }
    if ($('kRisk')) $('kRisk').textContent = rs ? window.NBXDisplay.percent(rs.openHeat, 2, false) : '\u2014';

    // The header badge must reflect the version the backend actually reports,
    // not a hard-coded marketing string.
    if (live.backendVersion) {
      const chip = $('buildChip');
      if (chip) {
        chip.textContent = 'v' + live.backendVersion;
        chip.title = 'back-end ' + live.backendVersion + ' · ' + (live.authMode || 'auth ?');
      }
    }

    // Only a current read may repaint the control badge. In-flight/unknown
    // command handling owns its own lock and cannot be cleared by a poll.
    if(typeof window.paintPaperControl === 'function') window.paintPaperControl(rs && rs.halted,localMode?'browser-local':'server',controlEpoch);
    if ($('kPf')) {
      const pfCard = $('kPf').closest('.glass');
      const pfSub = pfCard && pfCard.querySelector('[data-i18n="ov.kpi4sub"]');
      const events = m && m.trades != null && m.trades !== '' && typeof m.trades !== 'boolean' && Number.isSafeInteger(Number(m.trades)) && Number(m.trades) >= 0 ? Number(m.trades) : null;
      const pf = m && m.profit_factor;
      const validPf = m && m.profit_factor_defined !== false && pf !== null && pf !== undefined && pf !== '' && typeof pf !== 'boolean' && Number.isFinite(Number(pf));
      $('kPf').textContent = events > 0 && validPf ? (m.profit_factor_is_capped ? '≥' : '') + faNum(Number(pf), 2) : (LANG === 'fa' ? 'ضریب سود ثبت نشده است' : 'Profit factor not recorded');
      if (pfSub) pfSub.textContent = !m
        ? (LANG === 'fa' ? 'دفتر کاغذی خوانده نشد؛ علت قطعی صرافی تأیید نشده است.' : 'Paper book read unavailable; exchange outage not established.')
        : events === null
        ? (LANG === 'fa' ? 'شمارش خروج‌ها نامشخص؛ PF تأیید نشده است.' : 'Exit-event count unknown; PF not established.')
        : !events
        ? (LANG === 'fa' ? 'رویداد خروج ثبت نشده؛ PF تعریف نشده است.' : 'No recorded exit events; PF is undefined.')
        : m.profit_factor_defined === false
        ? (LANG === 'fa' ? 'فقط خروج سربه‌سر ثبت شده؛ PF تعریف نشده است.' : 'Only break-even exits recorded; PF is undefined.')
        : localMode
        ? (LANG === 'fa' ? 'PF دفتر محلی مرورگر؛ مستقل از حساب سرور.' : 'Browser-local PF; independent of the server account.')
        : (LANG === 'fa' ? 'PF رویدادهای خروج نگهداری‌شده؛ خروج جزئی جدا شمرده می‌شود، بدون کارمزد ورود؛ بدون فیلتر زمانی؛ سقف نمایشی ۹۹٫۹۹.' : 'Retained exit-event PF; partial exits counted separately, excludes entry fees; no time filter; display cap 99.99.');
      if (pfCard) pfCard.toggleAttribute('data-unavailable', !m);
    }
    const bar = document.querySelector('#page-overview .bar-fill[data-w]');
    if (bar) {
      const heat = rs && rs.openHeat, cap = rs && rs.caps && rs.caps.maxHeatPct;
      const known = heat != null && Number.isFinite(Number(heat)) && Number(cap) > 0;
      bar.style.width = known ? Math.min(100, Math.max(0, (Number(heat) / Number(cap)) * 100)) + '%' : '0%';
      bar.dataset.w = known ? String(Math.min(100, Math.max(0, (Number(heat) / Number(cap)) * 100))) : '0';
      bar.parentElement.setAttribute('aria-label', known ? String(heat) + ' / ' + String(cap) : 'unknown');
      bar.parentElement.dataset.state = known ? 'known' : 'unknown';
    }

    const capLabel = document.querySelector('[data-i18n="ov.kpi3cap"]');
    const heatCap = rs && rs.caps && rs.caps.maxHeatPct;
    if (capLabel) capLabel.textContent = heatCap != null && Number(heatCap) > 0
      ? (LANG === 'fa' ? 'از سقف ' : 'of cap ') + window.NBXDisplay.percent(heatCap, 2, false)
      : (LANG === 'fa' ? 'سقف ریسک نامشخص' : 'Risk cap unknown');

    // Keep the sizing preview (Desk page) on the same equity as the backend.
    const allocEq = rs && typeof rs.equity === 'number' && Number.isFinite(rs.equity) && rs.equity > 0
      ? Number(rs.equity)
      : (eq !== null && eq > 0 ? eq : null);
    window.__NBX_PAPER_EQUITY_KNOWN = allocEq !== null;
    if (allocEq !== null) {
      try { EQUITY = allocEq; } catch (e) {}
    }
    if (typeof window.renderAlloc === 'function') window.renderAlloc();

    // The ticket's preview compares against these numbers; it must be the same
    // object the server gates on, not a second literal set.
    if (typeof RISK_LIMITS !== 'undefined') {
      const c=rs && rs.caps;
      const valid=v=>typeof v==='number' && Number.isFinite(v) && v>=0;
      RISK_LIMITS=c && ['riskPct','maxSingleAssetPct','minRR'].every(k=>valid(c[k])) ? {
        riskPct:c.riskPct,maxSingleAssetPct:c.maxSingleAssetPct,minRR:c.minRR,
      } : null;
    }

    // Missing fields must not turn into green zeros or fabricated defaults.
    const numeric=v=>typeof v==='number' && Number.isFinite(v) && v>=0 ? v : null;
    const pctText=v=>v===null?'—':window.NBXDisplay.percent(v,2,false);
    const same=v=>({fa:v,en:v});
    if (typeof caps !== 'undefined' && Array.isArray(caps)) {
      const c=rs && rs.caps || {};
      const concurrent=numeric(c.maxConcurrent),heat=numeric(rs && rs.openHeat),maxHeat=numeric(c.maxHeatPct),single=numeric(c.maxSingleAssetPct),minCash=numeric(c.minCashPct);
      const count=positionsKnown?posRes.positions.filter(p=>p.state!=='CLOSED').length:null;
      const cash=m && numeric(m.cash)!==null && numeric(m.equity)>0 ? m.cash/m.equity*100 : null;
      const ratio=(v,max)=>v!==null && max!==null && max>0 ? Math.min(100,Math.max(0,v/max*100)) : 0;
      caps.length=0;caps.push(
        {name:{fa:'پوزیشن‌های همزمان',en:'Concurrent positions'},val:same((count===null?'—':faNum(count))+' / '+(concurrent===null?'—':faNum(concurrent))),pct:ratio(count,concurrent),tone:'sky'},
        {name:{fa:'دمای کل سبد',en:'Portfolio heat'},val:same(pctText(heat)+' / '+pctText(maxHeat)),pct:ratio(heat,maxHeat),tone:'amber'},
        {name:{fa:'سقف نامینال هر سفارش (نه تجمع نماد)',en:'Per-order nominal cap (not cumulative symbol)'},val:same(pctText(single)),pct:single===null?0:Math.min(100,single),tone:'indigo'},
        {name:{fa:'بافر نقد',en:'Cash buffer'},val:same(pctText(cash)+' ('+(LANG==='fa'?'حداقل ':'min ')+pctText(minCash)+')'),pct:cash===null?0:Math.min(100,cash),tone:'zinc'}
      );
      if(typeof renderCaps==='function') renderCaps();
    }
    if (typeof kills !== 'undefined' && Array.isArray(kills)) {
      const up=live.upstream || {},h=rs && typeof rs.halted==='boolean'?rs.halted:null;
      // Engine daily_pnl is not a reconciled day/session ledger. Never show a
      // green automatic daily-stop guarantee from this raw scalar.
      const daily=rs && typeof rs.dailyPnl==='number' && Number.isFinite(rs.dailyPnl)?rs.dailyPnl:null;
      kills.length=0;kills.push(
        {n:{fa:'توقف ورود جدید',en:'New-entry halt'},v:same(h===null?(LANG==='fa'?'نامشخص':'unknown'):h?(LANG==='fa'?'مسلح':'Armed'):(LANG==='fa'?'غیرفعال':'Off')),ok:h===null?null:!h},
        {n:{fa:'PnL خام موتور؛ روز تطبیق‌نیافته',en:'Raw engine P&L; day unreconciled'},v:same(daily===null?'—':window.NBXDisplay.percent(daily,2)),ok:null},
        {n:{fa:'خوراک بازار؛ نه سلامت کنترل‌ها',en:'Market feed; not control health'},v:same(up.ok===true?(LANG==='fa'?'پاسخ موفق گزارش شد':'successful read reported'):up.ok===false?(LANG==='fa'?'خطای خوراک گزارش شد':'feed failure reported'):(LANG==='fa'?'نامشخص':'unknown')),ok:up.ok===true?true:up.ok===false?false:null}
      );
      if(typeof renderKills==='function') renderKills();
    }

    live.risk = rs;
    // Store the response *envelope*, not m: drawEquity() reads `.curve` from
    // here, and assigning the inner metrics object left that key undefined —
    // so the equity chart painted "Nobitex unreachable" forever, live or not.
    live.paperMetrics = metricsKnown ? Object.assign({}, metRes, { metrics: m })
                                     : { metrics: m, curve: [], unavailable: true };
    if (!metricsKnown || !Array.isArray(metRes.curve) || !metRes.curve.length) {
      if (typeof equitySeries !== 'undefined' && equitySeries) { try { equitySeries.setData([]); } catch (e) {} }
      if (typeof window.drawEquity === 'function') window.drawEquity(0);
    }
    if (metricsKnown && Array.isArray(metRes.curve) && metRes.curve.length) {
      try {
        // The first paint happened before this response and wrote "Nobitex
        // unreachable" into the slot; it created no series, so the update below
        // had nothing to write to and the sentence stayed on screen forever.
        // Rebuild the chart now that there is a curve to put in it.
        if (typeof equitySeries === 'undefined' || !equitySeries) {
          if (typeof window.drawEquity === 'function') window.drawEquity(0);
        }
        // sanitised: fractional/duplicate timestamps make the library throw
        const eq = (typeof window.seriesData === 'function' ? window.seriesData(metRes.curve) : []);
        if (eq.length && typeof equitySeries !== 'undefined' && equitySeries) equitySeries.setData(eq);
      } catch (e) {}
    }
  }

  async function updateOverviewFromLive() {
    const engine = NBX.core.engine;
    const ranked = engine.rankSignals();
    live.ranked = ranked;
    const top = ranked.find((x) => x.symbol === live.activeSymbol) || ranked.find((x) => x.composite >= 60) || ranked[0];
    if (top) live.activeSymbol = top.symbol;

    try {
      let rows = engine.candles[live.activeSymbol + ':' + live.timeframe] || engine.candles[live.activeSymbol];
      if ((!rows || !rows.length) && NBX.core.api && typeof NBX.core.api.getBackendCandles === 'function') {
        const cRes = await NBX.core.api.getBackendCandles(live.activeSymbol, live.timeframe);
        if (cRes && Array.isArray(cRes.rows)) {
          rows = cRes.rows;
          engine.candles[live.activeSymbol] = rows;
          engine.candles[live.activeSymbol + ':' + live.timeframe] = rows;
          // Record provenance on this path too. Otherwise the same synthetic
          // data painted one colour here and another in the list, purely
          // because a different function fetched it.
          engine.candleSource = engine.candleSource || {};
          engine.candleSource[live.activeSymbol] = cRes.source || null;
        }
      }
      if (rows && rows.length && typeof candleSeries !== 'undefined' && candleSeries) {
        // Normalised: candles can carry duplicate or fractional timestamps
        // (synthetic generators and exchange payloads both do), which makes
        // lightweight-charts throw and blanks the whole overview.
        const bars = (typeof window.seriesBars === 'function' ? window.seriesBars(rows) : []);
        if (bars.length) candleSeries.setData(bars);
        lastCandle = { time: rows[rows.length - 1].t, open: rows[rows.length - 1].o, high: rows[rows.length - 1].h, low: rows[rows.length - 1].l, close: rows[rows.length - 1].c };
        if ($('lastPrice')) $('lastPrice').textContent = fmtPrice(lastCandle.close, live.activeSymbol);
        // Depth comes from its own route; a last-trade price is not a book.
        if (typeof window.refreshOrderbook === 'function') window.refreshOrderbook(live.activeSymbol);
      }
    } catch (e) {}

    const unit = engine.universe.find((x) => x.symbol === live.activeSymbol);
    if (unit && unit.change24 !== undefined) {
      const ce = $('lastChg');
      if (ce) {
        ce.textContent = goodPct(unit.change24, 1);
        ce.className = 'chip num ' + (unit.change24 >= 0 ? 'text-emerald-300' : 'text-red-300');
      }
    }

    // Real paper positions + KPIs (previously this block fabricated 3 rows).
    await refreshPaperState();
  }

  /* ---------- Signals page from ranking ---------- */
  /* ---------- sparkline data ----------
   * In backend mode the browser never fetches per-symbol candles: the scan
   * loop that fills `engine.candles` only runs in the offline path, and the
   * overview fetches exactly one symbol for the big chart. So every row but
   * the active one rendered the "—" placeholder — the charts were not broken,
   * they never had data. Fetch the rows we actually display, four at a time,
   * and repaint once. */
  const sparkState = { tf: null, at: 0, inflight: false };

  const sparkRows = (sym, tf) => {
    const eng = NBX.core && NBX.core.engine;
    if (!eng) return null;
    return eng.candles[sym + ':' + tf] || null;
  };

  async function prefetchSparkCandles(symbols, tf, force) {
    const api = NBX.core && NBX.core.api;
    if (!api || typeof api.getBackendCandles !== 'function') return 0;
    const list = [...new Set((symbols || []).filter(Boolean))];
    const missing = list.filter((s) => {
      const rows = sparkRows(s, tf);
      return !rows || rows.length < 8;
    });
    if (!missing.length || sparkState.inflight) return 0;
    const fresh = sparkState.tf === tf && Date.now() - sparkState.at < 90_000;
    if (fresh && !force) return 0;

    sparkState.inflight = true;
    sparkState.tf = tf;
    sparkState.at = Date.now();
    const queue = missing.slice();
    let fetched = 0;
    const worker = async () => {
      while (queue.length) {
        const sym = queue.shift();
        try {
          const r = await api.getBackendCandles(sym, tf);
          if (r && Array.isArray(r.rows) && r.rows.length >= 8) {
            // remember where it came from: the honesty rules elsewhere in the
            // dashboard depend on never calling synthetic data "live".
            NBX.core.engine.candles[sym] = r.rows;
            NBX.core.engine.candles[sym + ':' + tf] = r.rows;
            NBX.core.engine.candleSource = NBX.core.engine.candleSource || {};
            NBX.core.engine.candleSource[sym] = r.source || null;
            fetched++;
          }
        } catch (e) { /* one symbol must not stop the rest */ }
      }
    };
    await Promise.all([worker(), worker(), worker(), worker()]);
    sparkState.inflight = false;
    if (fetched) renderSignalsLive();   // second pass: charts now have data
    return fetched;
  }

  function bindSignalRows() {
    const list = $('sigList');
    if (!list || list.dataset.bound === '1') return;
    list.dataset.bound = '1';
    list.addEventListener('click', (ev) => {
      if (ev.target.closest('[data-close-detail], a, button')) return;
      const row = ev.target.closest('.sig-row');
      if (row && typeof window.toggleSignalDetail === 'function') window.toggleSignalDetail(row);
    });
    list.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      const row = ev.target.closest('.sig-row');
      if (row && typeof window.toggleSignalDetail === 'function') {
        ev.preventDefault();
        window.toggleSignalDetail(row);
      }
    });
  }

  function signalSnapshotMeta(reply,tf){
    return {rows:reply.scored,tf:typeof reply.tf==='string'?reply.tf:tf,received:Date.now(),
      responseAt:typeof reply.ts==='string' && Number.isFinite(Date.parse(reply.ts))?reply.ts:null,
      age:typeof reply.age_s==='number' && Number.isFinite(reply.age_s) && reply.age_s>=0?reply.age_s:null,
      stale:typeof reply.stale==='boolean'?reply.stale:null};
  }
  function signalFaState(s){
    if(!faNumber(s.fa_score)) return 'absent';
    const record=faRecord(s.symbol,s.fa,'scan-snapshot');
    return !record?'score-only':record.score!==s.fa_score?'mismatch':record.state;
  }
  function signalFaLabel(state){
    const labels={server:['برچسب سرور؛ دریافت اختصاصی تأییدنشد','server-labelled; receipt unverified'],
      cached:['کش گزارش‌شده؛ سن منبع نامشخص','reported cache; source age unknown'],
      static:['پروفایل ثابت؛ نه دادهٔ امروز','static profile; not today’s data'],
      unmapped:['بدون نگاشت؛ عدد پیش‌فرض مدل','unmapped; model default'],
      'score-only':['فقط عدد اسکن؛ منشأ نامشخص','scan number only; origin unknown'],
      mismatch:['اختلاف عدد مؤلفه و metadata؛ منشأ نامشخص','component/metadata mismatch; origin unknown'],
      absent:['مؤلفهٔ FA ناموجود/نامعتبر','FA component absent/invalid']};
    const value=labels[state]||labels.absent;return faText(value[0],value[1]);
  }
  function signalGateRow(symbol){
    const g=live.gates,v=g && g.verdicts && Object.prototype.hasOwnProperty.call(g.verdicts,symbol)?g.verdicts[symbol]:null;
    if(!g || g.mode!=='shadow' || g.applied!==false || !v || typeof v.code!=='string') return faText('گیت سایه: نامشخص','Shadow gate: unknown');
    const status=v.code==='ok'?faText('بدون اعتراض گزارش‌شده؛ نه پذیرش سفارش','no reported objection; not order approval'):v.code.slice(0,120);
    const input=v.inputs||{},num=x=>typeof x==='number' && Number.isFinite(x) && x>=0?faNum(x,1):'—';
    return faText('گیت سایه: ','Shadow gate: ')+status+' · spread '+num(input.spread_bps)+' bps · depth '+num(input.depth_usd)+' USD'
      +faText(' · سن دفترِ این نماد گزارش نشده',' · per-symbol book age not reported');
  }
  function signalProvenanceSummary(){
    const node=$('sgFaProvenanceSummary');if(!node)return;
    const counts={};for(const s of live.ranked){const state=signalFaState(s);counts[state]=(counts[state]||0)+1;}
    node.textContent=faText('تمام ردیف‌های همین اسکن: ','All rows in this scan: ')+faNum(live.ranked.length)+' · '+Object.entries(counts).map(([state,n])=>signalFaLabel(state)+': '+faNum(n)).join(' · ');
  }

  /* The strip above the signal list is rewritten by applyStaticI18n() on every
     tab switch, which used to restore "Scanning…" and a fixed "last cycle: 23s
     ago" even with the exchange down. This function is the single writer. */
  function syncSignalsHeader() {
    const sh=$('page-signals');if(!sh)return;
    const noData=!!live.unavailable || !live.ranked.length;
    const set=(sel,txt,cls)=>{const el=sh.querySelector(sel);if(el){el.textContent=txt;if(cls)el.className=cls;}};
    const sec=scanInterval();
    const cadence=Number.isFinite(sec) && sec>0
      ? faText('دریافت صفحه هر ','page fetch every ')+faNum(sec)+faText(' ثانیه',' seconds')
      : sec===0?faText('دریافت دستی صفحه','manual page fetch'):faText('دورهٔ دریافت نامشخص','page fetch interval unknown');
    set('[data-i18n="sg.cycle"]',faText('کندل: ','Candle: ')+window.NBXDisplay.timeframe(live.timeframe)+' · '+cadence,'chip');
    set('[data-i18n="sg.scanned"]',noData?faText('اسکن قابل استفاده نیست','No usable scan'):inFa('sg.scanned').replace('{ranked}',faNum(live.ranked.length)), 'chip num text-zinc-300');
    const meta=!noData && live.signalSnapshot && live.signalSnapshot.rows===live.ranked?live.signalSnapshot:null;
    const elapsed=meta && typeof meta.received==='number'?(Date.now()-meta.received)/1000:null;
    const age=elapsed!==null && Number.isFinite(elapsed) && elapsed>=0?faNum(Math.floor(elapsed)): '—';
    set('[data-i18n="sg.last"]',faText('دریافت صفحه: ','Page received: ')+age+faText(' ثانیه پیش؛ نه زمان تولید اسکن',' seconds ago; not scan generation time'),'text-[10px] sm:text-[11px] text-zinc-400 self-center');
    const note=$('sgReceiptSummary');if(note){
      note.textContent=noData?faText('اسکن موجود نیست یا دریافت ناموفق بوده؛ این وضعیت به‌تنهایی قطع همهٔ بازارها را ثابت نمی‌کند.','No usable scan or scan read failed; this alone does not prove all markets are down.')
        : meta?faText('سن cache در پاسخ سرور: ','Server-reported cache age at response: ')+(meta.age===null?'—':faNum(meta.age,1)+faText(' ثانیه',' seconds'))
          +' · '+(meta.stale===null?faText('وضعیت cache نامشخص','cache state unknown'):meta.stale?faText('cache کهنه گزارش شده','cache reported stale'):faText('cache کهنه گزارش نشده؛ نه تأیید سن کندل','cache not reported stale; not candle-age verification'))
          +(meta.tf!==live.timeframe?faText(' · هشدار: گزارش برای ',' · Warning: report is for ')+meta.tf+faText('؛ انتخاب فعلی ','; current selection ')+live.timeframe:'')
          +(meta.responseAt?faText(' · زمان پاسخ API (نه تولید اسکن): ',' · API response time (not scan generation): ')+new Date(meta.responseAt).toLocaleString(window.NBXDisplay.dateLocale()):'')
        : faText('زمان دریافت و سن cache این snapshot نامشخص است.','This snapshot’s receipt time and cache age are unknown.');
      const f=!noData && live.scanFreshness;
      note.textContent+=' · '+(f && typeof f.stale==='boolean'?faText('تازگی کندل طبق بلوک سرور؛ نه تأیید مستقل دریافت','candle age per server block; receipt not independently verified'):faText('تازگی کندل نامشخص','Candle freshness unknown'));
    }
    if(noData){
      set('#sgFaCoverage','—','chip num text-zinc-300');const fa=$('sgFaCoverage');if(fa)fa.title='';
      const provenance=$('sgFaProvenanceSummary');if(provenance)provenance.textContent=faText('پوشش FA این نشست نامشخص است.','FA coverage in this session is unknown.');
    }else signalProvenanceSummary();
    if(typeof window.sgRenderGates==='function')window.sgRenderGates(noData?null:live.gates);
    if(typeof window.sgRenderFresh==='function')window.sgRenderFresh(noData?null:live.scanFreshness);
  }
  window.syncSignalsHeader = syncSignalsHeader;

  /* How many ranked rows the page draws at once.
   * This used to be a hard-coded `.slice(0, 12)` inside the renderer: the strip
   * above said "39 symbols scanned" while the list stopped at twelve, and rows
   * 13..39 had no row, no detail and no way to be seen even though the gates had
   * already judged them. The number is a *first paint* budget, not the size of
   * the scan, so it lives here with a name and can be lifted by one click. */
  const SIG_ROW_LIMIT = 15;

  window.sigShowAll = function sigShowAll(show) {
    live.showAll = !!show;
    renderSignalsLive();
    // Rows beyond the first page never had their sparkline prefetched, so the
    // charts were requested only for the visible twelve. Ask for the rest when
    // they become visible — a row with an empty chart is a half-drawn row.
    const rest = (live.ranked || []).slice(SIG_ROW_LIMIT).map((s) => s.symbol);
    if (show && rest.length && typeof prefetchSparkCandles === 'function') {
      prefetchSparkCandles(rest, live.timeframe);
    }
  };

  function renderSignalsLive() {
    const list = $('sigList');
    if (!list) return;
    const ranked = live.ranked;
    if (!ranked.length) {
      // Two very different reasons for an empty list: a scan still running, or
      // an exchange we cannot reach. The second one must look like an error,
      // not like "nothing interesting today".
      if (live.unavailable) {
        list.innerHTML = `<div data-unavailable="1" class="glass p-5 sm:p-6 border-red-400/40 bg-red-500/10 text-center">
          <i data-lucide="plug-zap" class="w-5 h-5 text-red-300 mx-auto mb-2"></i>
          <div class="text-[12px] sm:text-sm font-bold text-red-200">${
            LANG === 'fa' ? 'دریافت اسکن ناموفق یا داده ناموجود است' : 'Scan read failed or data unavailable'}</div>
          <div class="text-[10px] sm:text-[11px] text-red-200/80 leading-5 mt-1">${
            LANG === 'fa'
              ? 'خطای این مسیر، خرابی کلید حساب یا قطع قطعی همهٔ بازارها را ثابت نمی‌کند. برای خواندن دوباره تلاش کنید.'
              : 'This read failure does not prove account-key failure or an outage of every market. Retry the read.'}</div>
          ${live.unavailableDetail ? `<div class="text-[9px] text-red-200/60 mt-1.5 num" dir="ltr">${esc(String(live.unavailableDetail).slice(0,120))}</div>` : ''}
          <button type="button" data-retry-scan="1" class="mt-3 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-red-400/15 text-red-200 border border-red-300/40">${
            LANG === 'fa' ? 'تلاش دوباره' : 'Retry'}</button>
        </div>`;
        list.querySelectorAll('[data-retry-scan]').forEach((btn) => {
          btn.onclick = () => scanNow(true);
        });
        syncSignalsHeader();
        if (typeof refreshIcons === 'function') refreshIcons();
      } else {
        list.innerHTML = `<div class="glass p-6 text-center text-[11px] text-zinc-500">${
          LANG === 'fa' ? 'اسکن قابل استفاده‌ای در این نشست بارگذاری نشده است.' : 'No usable scan loaded in this session.'}</div>`;
      }
      syncSignalsHeader();
      return;
    }
    syncSignalsHeader();
    const shdr = $('page-signals');
    if (shdr) {
      const faEl = shdr.querySelector('#sgFaCoverage') || shdr.querySelector('[data-i18n="sg.faCoverage"]');
      if (faEl) {
        const c = live.faCoverage;
        if (!c) {
          faEl.textContent = '—';faEl.title='';faEl.className='chip num text-zinc-300';
        } else {
          // These are backend labels, not proof of successful per-coin fetches.
          // The engine may combine cached/default/context components.
          // An older backend reports only `scored`; do not relabel that as "live".
          const hasLive = c.live !== undefined;
          const liveN = hasLive ? c.live : c.scored;
          faEl.textContent = inFa(hasLive ? 'sg.faCoverage' : 'sg.faScored')
            .replace('{n}', faNum(liveN))
            .replace('{t}', faNum(c.total));
          faEl.className = 'chip num text-zinc-300 border-zinc-300/30';
          faEl.title = inFa('sg.faCoverageTitle')
            .replace('{s}', faNum(c.scored))
            .replace('{f}', Number.isInteger(c.fallback) && c.fallback>=0?faNum(c.fallback):'—')
            .replace('{x}', Number.isInteger(c.failed) && c.failed>=0?faNum(c.failed):'—');
        }
      }
    }

    const hidden = Math.max(0, ranked.length - SIG_ROW_LIMIT);
    const shown = live.showAll ? ranked : ranked.slice(0, SIG_ROW_LIMIT);
    list.innerHTML = shown
      .map((s, i) => {
        const composite=faNumber(s.composite)?s.composite:null;
        const col = composite===null?'zinc':composite >= 60 ? 'emerald' : composite >= 45 ? 'amber' : 'red';
        const vetoes=Array.isArray(s.fa && s.fa.vetoes)?s.fa.vetoes.filter(v=>typeof v==='string').slice(0,32):[];
        const comp = s.components || {};
        const dir = s.direction === 'LONG' ? 'text-emerald-300' : s.direction === 'EXIT' ? 'text-red-300' : 'text-zinc-300';
        const faState=signalFaState(s);
        const gateText=signalGateRow(s.symbol);
        const symLabel = LANG === 'fa' ? s.symbol.replace('IRT', '/تومان') : s.symbol;
        return `
      <div data-sig-sym="${esc(s.symbol)}" data-sig-idx="${i}" tabindex="0" role="button"
           class="sig-row glass p-3.5 sm:p-4 flex flex-col md:flex-row md:items-center gap-3 sm:gap-4 cursor-pointer">
        <div class="flex items-center gap-3 md:w-44 shrink-0">
          <div class="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center font-extrabold text-sm num ${
            i < 3 ? 'text-emerald-300' : 'text-zinc-500'
          }">${faNum(i + 1)}</div>
          <div class="min-w-0"><div class="font-bold text-[13px] sm:text-sm">${window.coinIcon(s.symbol)}${esc(symLabel)}</div>
            <span class="chip ${dir}"${
          s.direction_raw && s.direction_raw !== s.direction
            ? ` title="${esc(LANG === 'fa'
                ? `${s.direction_raw} بود؛ وتوی فاندامنتال (${vetoes.join('، ')}) سقف ${faNum(+s.veto_cap)} گذاشت`
                : `was ${s.direction_raw}; a fundamental veto (${vetoes.join(', ')}) capped it at ${+s.veto_cap}`)}"`
            : ''
        }><span class="dot ${
          s.direction === 'LONG' ? 'bg-emerald-400' : s.direction === 'EXIT' ? 'bg-red-400' : 'bg-zinc-400'
        }"></span> ${esc(window.NBXDisplay.label(s.direction))}</span>${
          s.direction_raw && s.direction_raw !== s.direction
            ? `<span class="chip text-[9px] text-amber-300 border-amber-300/30 ms-1">${LANG === 'fa' ? 'وتو' : 'veto'}</span>`
            : ''
        }
          </div>
          <span class="text-[10px] text-zinc-500 ms-auto md:hidden">${esc(window.NBXDisplay.label(s.regime || ''))}</span>
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 sm:gap-3 mb-1.5">
            <span class="text-[10px] text-zinc-500 hidden sm:block w-24">${inFa('sg.score')}</span>
            <div class="nbx-mini-hbar flex-1"><i style="width:${composite===null?0:clamp(composite,0,100)}%"></i></div>
            <span class="num font-extrabold text-${col}-300 w-8 text-start">${composite===null?'—':faNum(composite,0)}</span>
            ${s.composite_basis && s.composite_basis !== 'ta+fa+liq'
              ? `<span class="chip text-[9px] text-amber-300 border-amber-300/30" title="${esc(inFa('sg.basisTitle'))}">${inFa('sg.basisNoFa')}</span>`
              : ''}
          </div>
          <p data-signal-gate class="text-[10px] text-zinc-400 leading-5">${esc(gateText)}</p>
          <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
            <span class="chip">${LANG==='fa'?'تکنیکال':'TA'} <b class="num">${faNumber(s.score)?faNum(s.score,0):'—'}</b></span>
            <span class="chip" data-signal-fa="${faState}" title="${esc(s.composite_basis || '')}">${inFa('sg.faN')} <b class="num">${faNumber(s.fa_score) ? faNum(Math.round(s.fa_score)) : '—'}</b> · ${esc(signalFaLabel(faState))}</span>
            <span class="chip" data-signal-liquidity>${inFa('sg.liq')} <b class="num">${faNumber(s.liquidity_score)?faNum(s.liquidity_score):'—'}</b></span>
            <span class="chip">${window.NBXDisplay.label('trend')} <b class="num">${faNum(+(comp.trend || 0))}</b></span>
            <span class="chip">${window.NBXDisplay.label('momentum')} <b class="num">${faNum(+(comp.momentum || 0))}</b></span>
            <span class="chip ${s.regime === 'TREND' ? 'text-emerald-300' : 'text-zinc-500'}">${esc(window.NBXDisplay.label(s.regime || ''))}</span>
            ${s.risk && s.risk.stopPct ? `<span class="chip">${window.NBXDisplay.label('stop')} <b class="num">${window.NBXDisplay.percent(s.risk.stopPct, 2, false)}</b></span>` : ''}
          </div>
        </div>
        <div class="w-full md:w-48 lg:w-60 xl:w-72 2xl:w-80 shrink-0">
          ${typeof spark === 'function' ? spark(false, s.symbol, {big: true, neutral:true, strictTF:true}) : ''}
          <div class="text-[9px] text-zinc-600 text-center mt-0.5">${t('sg.expand')}</div>
        </div>
      </div>`;
      })
      .join('')
      + (hidden
        ? `<button type="button" class="w-full glass p-2.5 text-[11px] font-bold text-zinc-300 hover:text-white"
                   onclick="window.sigShowAll(${live.showAll ? 'false' : 'true'})">${
            live.showAll
              ? inFa('sg.showLess')
              : inFa('sg.showAll').replace('{n}', faNum(ranked.length))}</button>`
        : '');
    bindSignalRows();
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------- Fundamentals: scan-bound coverage, not provider-health guesses ---------- */
  const FA_REQUEST_BUDGET=6;
  let faGeneration=0;
  const faView={rows:[],context:null,loading:false,showAll:false,received:null,premium:null};
  const faNumber=v=>typeof v==='number' && Number.isFinite(v) && v>=0 && v<=100;
  const faText=(fa,en)=>LANG==='fa'?fa:en;
  function faContextCurrent(context){
    return !!context && context.ranked===live.ranked && context.tf===live.timeframe && context.scan===live.lastScanAt && !live.unavailable;
  }
  function faRecord(symbol,f,origin){
    if(!f || f.ok===false || f.unavailable || !faNumber(f.score) || (f.symbol!==undefined && f.symbol!==symbol)) return null;
    const profile=f.profile || f.evidence && f.evidence.profile;
    const state=f.source==='no-provider' || profile==='none' ? 'unmapped'
      : f.source==='fallback' || profile==='fallback' ? 'static'
      : f.cached===true || f.source==='cached' ? 'cached' : 'server';
    const vetoes=Array.isArray(f.vetoes) && f.vetoes.length<=32 && f.vetoes.every(v=>typeof v==='string' && v.length<=256)
      ? Array.from(new Set(f.vetoes)) : null;
    const tvl=f.evidence && f.evidence.tvl;
    return {symbol,state,score:f.score,scanScore:null,vetoes,origin,cached:f.cached===true || f.source==='cached',
      tvl:typeof tvl==='number' && Number.isFinite(tvl) && tvl>=0?tvl:null};
  }
  function invalidateFaPreview(){
    ++faGeneration;faView.rows=[];faView.context=null;faView.loading=false;faView.received=null;faView.premium=null;renderFaPreview();
  }
  async function fetchBackendFA(records,generation,context){
    const api=NBX.core && NBX.core.api;
    for(const record of records){
      if(generation!==faGeneration || !faContextCurrent(context)) return;
      let reply=null;
      try{if(api && !api.standaloneMobile && api.getBackendFA) reply=await api.getBackendFA(record.symbol);}catch(_){}
      if(generation!==faGeneration || !faContextCurrent(context)) return;
      const value=faRecord(record.symbol,reply,'detail-response');
      if(value) Object.assign(record,value);
      else{record.state='failed';record.error=reply && reply.auth_error ? 'auth' : 'request';}
    }
  }
  async function updateFaPreview(){
    const generation=++faGeneration;
    const context={ranked:live.ranked,tf:live.timeframe,scan:live.lastScanAt};
    faView.context=context;faView.rows=[];faView.received=null;faView.premium=null;faView.loading=false;
    if(!Array.isArray(live.ranked) || !live.ranked.length || live.unavailable){renderFaPreview();return;}
    const seen=new Set(),missing=[];
    for(const x of live.ranked){
      if(!x || typeof x.symbol!=='string' || seen.has(x.symbol)) continue;
      seen.add(x.symbol);
      const known=/^[A-Z0-9_]{2,40}$/.test(x.symbol)?faRecord(x.symbol,x.fa,'scan-snapshot'):null;
      if(known){faView.rows.push(known);continue;}
      const record={symbol:x.symbol,state:'not-requested',score:null,scanScore:faNumber(x.fa_score)?x.fa_score:null,vetoes:null,origin:'scan-partial'};
      if(!/^[A-Z0-9_]{2,40}$/.test(x.symbol)) record.state='invalid-symbol';
      else missing.push(record);
      faView.rows.push(record);
    }
    // The scan already contains full FA for modern backends. Fetch only missing
    // details, sequentially, within the existing six-request budget.
    const picked=missing.slice(0,FA_REQUEST_BUDGET);
    picked.forEach(r=>{r.state='pending';});faView.loading=!!picked.length;
    const premium=NBX.core && NBX.core.engine && NBX.core.engine.premium_pct;
    faView.premium=typeof premium==='number' && Number.isFinite(premium)?premium:null;
    renderFaPreview();
    await fetchBackendFA(picked,generation,context);
    if(generation!==faGeneration) return;
    if(!faContextCurrent(context)){invalidateFaPreview();return;}
    faView.loading=false;faView.received=Date.now();renderFaPreview();
  }
  function renderFaPreview(){
    const current=faContextCurrent(faView.context),rows=current?faView.rows:[];
    const covered=rows.filter(r=>faNumber(r.score) && r.state!=='unmapped');
    const evaluated=rows.filter(r=>r.vetoes!==null);
    const vetoed=evaluated.filter(r=>r.vetoes.length>0).length;
    const reasons=evaluated.reduce((n,r)=>n+r.vetoes.length,0);
    const average=covered.length?covered.reduce((n,r)=>n+r.score,0)/covered.length:null;
    const avg=$('faAvg'),veto=$('faVetoes'),prem=$('faPrem');
    if(avg) avg.textContent=average===null?'—':faNum(average,1);
    const bar=$('faAvgBar');if(bar){bar.dataset.w=average===null?'0':String(average);bar.style.width=(average===null?0:average)+'%';bar.parentElement.setAttribute('aria-label',average===null?'unknown':String(average));}
    const counts=state=>rows.filter(r=>r.state===state).length;
    const summary=$('faAvgSub');
    if(summary) summary.textContent=faText('میانگین: ','Mean: ')+faNum(covered.length)+' / '+faNum(rows.length)+faText(' نماد؛ ثابت: ',' symbols; static: ')+faNum(counts('static'))+faText('؛ کش: ','; cached: ')+faNum(counts('cached'))+faText('؛ منشأ دریافت تأییدنشد: ','; receipt unverified: ')+faNum(counts('server'))+faText('؛ بدون نگاشت: ','; unmapped: ')+faNum(counts('unmapped'))+faText('؛ خطای درخواست: ','; request failed: ')+faNum(counts('failed'))+faText('؛ درخواست‌نشده: ','; not requested: ')+faNum(counts('not-requested'))+faText('؛ در انتظار: ','; pending: ')+faNum(counts('pending'))+faText('؛ نماد نامعتبر: ','; invalid symbol: ')+faNum(counts('invalid-symbol'));
    if(veto) veto.textContent=evaluated.length?faNum(vetoed)+' / '+faNum(evaluated.length):'—';
    const vetoSub=$('faVetoSub');if(vetoSub) vetoSub.textContent=faText('نماد دارای وتو / پاسخ دارای فهرست وتو؛ علت در نماد: ','Vetoed symbols / replies with veto list; reason instances: ')+faNum(reasons)+faText('؛ نامعلوم: ','; unknown: ')+faNum(rows.length-evaluated.length)+faText('؛ نه تأیید ایمنی.','; not safety approval.');
    const premium=current?faView.premium:null;
    if(prem){prem.textContent=premium===null?'—':window.NBXDisplay.percent(premium,2);prem.title=faText('مرجع متقاطع BTCIRT، BTCUSDT و USDTIRT؛ سن مستقل هر سه منبع مشخص نیست.','BTCIRT/BTCUSDT/USDTIRT cross reference; independent age of all three legs is unknown.');}
    for(const id of ['faAvg','faVetoes','faPrem']){
      const card=$(id) && $(id).closest('.glass');if(card){card.dataset.state=!current?'unknown':faView.loading?'partial-loading':'read';card.removeAttribute('data-unavailable');}
    }
    const status=$('faCoverageNote');if(status){status.dataset.state=!current?'unknown':faView.loading?'loading':'read';status.textContent=!rows.length
      ? faText('اسکن قابل استفاده‌ای برای نمایش بنیادی نیست؛ علت، قطع بودن ارائه‌دهنده فرض نمی‌شود.','No usable scan for Fundamentals; this does not establish a provider outage.')
      : faText('فهرستِ همین اسکن و تایم‌فریم ','List from this scan and timeframe ')+faView.context.tf+faText('؛ امتیاز خنثیِ بدون نگاشت و مقدار صرفاً عددی اسکن در میانگین نیست. دریافت تکمیلی حداکثر ۶ نماد و جدا از snapshot تصمیم؛ تازه‌بودن اختصاصی ارائه‌دهنده تأیید نشده.','; unmapped defaults and scan-only numbers are excluded from the mean. At most 6 detail requests, separate from the decision snapshot; provider-specific freshness is unverified.')
        +(faView.received?faText(' زمان دریافت صفحه (نه مشاهدهٔ منبع): ',' Page received time (not source observation): ')+new Date(faView.received).toLocaleTimeString(window.NBXDisplay.dateLocale()):'');}
    const labels={static:['پروفایل ثابت؛ نه دادهٔ امروز','static profile; not today’s data'],cached:['کش گزارش‌شده؛ سن منبع نامشخص','reported cache; source age unknown'],server:['امتیاز سرور؛ دریافت اختصاصی تأیید نشده','server score; provider receipt unverified'],unmapped:['بدون نگاشت؛ پیش‌فرض خارج از میانگین','unmapped; default excluded from mean'],failed:['خواندن تکمیلی ناموفق؛ نه اثبات خرابی ارائه‌دهنده','detail read failed; not proof of provider outage'],'not-requested':['درخواست تکمیلی نشده','detail not requested'],pending:['در انتظار پاسخ تکمیلی','detail reply pending'],'invalid-symbol':['شناسهٔ نامعتبر؛ درخواست ارسال نشد','invalid symbol; no request sent']};
    const list=$('faList');if(list){
      list.innerHTML=rows.length?rows.slice(0,faView.showAll?rows.length:SIG_ROW_LIMIT).map(r=>{
        const score=faNumber(r.score) && r.state!=='unmapped'?r.score:null;
        const label=r.state==='failed' && r.error==='auth' ? ['خطای دسترسی در دریافت تکمیلی؛ نه خرابی ارائه‌دهنده','detail access denied; not a provider outage'] : labels[r.state] || labels.failed;
        const partial=score===null && r.scanScore!==null ? faText(' · فقط عدد اسکن، خارج میانگین: ',' · scan-only score, excluded: ')+faNum(r.scanScore,1):'';
        const details=r.vetoes===null?faText('وتو نامشخص','veto unknown'):r.vetoes.length?r.vetoes.join(' · '):faText('فهرست وتوی پاسخ خالی است؛ نه تأیید ایمنی','reply veto list empty; not safety approval');
        const tvl=r.tvl!==undefined && r.tvl!==null ? ' · TVL '+window.NBXDisplay.money(r.tvl,'USD',0)+faText(' (زمان مشاهده تأییدنشد)',' (observation time unverified)'):'';
        return `<div data-fa-symbol="${esc(r.symbol)}" data-fa-state="${r.state}" class="rounded-xl border border-white/5 p-2.5 text-[11px]">
          <div class="flex flex-wrap items-center gap-2"><span class="font-bold">${window.coinIcon(r.symbol, 20)}${esc(r.symbol)}</span><span data-fa-score class="num font-bold">${score===null?'—':faNum(score,1)}</span><span class="text-zinc-400">${esc(faText(label[0],label[1])+partial)}</span></div>
          ${score===null?'':`<div class="nbx-mini-hbar mt-1"><i style="width:${score}%"></i></div>`}
          <div class="text-[10px] text-zinc-500 leading-5">${esc((r.origin==='scan-snapshot'?faText('از snapshot اسکن','from scan snapshot'):r.origin==='detail-response'?faText('پاسخ FA جداگانه؛ نه snapshot تصمیم','separate FA reply; not decision snapshot'):faText('دادهٔ جزئی اسکن؛ بدون پاسخ تکمیلی','partial scan data; no detail reply'))+(r.cached && r.state!=='cached'?faText(' · کش گزارش‌شده',' · reported cache'):'')+' · '+details+tvl)}</div></div>`;
      }).join(''):'<div class="text-zinc-400 text-[11px] py-4">'+esc(faText('دادهٔ بنیادی خوانده نشده یا اسکن ناموجود است.','Fundamentals not read or scan unavailable.'))+'</div>';
      if(rows.length>SIG_ROW_LIMIT){const button=document.createElement('button');button.type='button';button.dataset.faShowAll='1';button.className='w-full mt-2 text-[11px] text-zinc-300';button.textContent=faView.showAll?inFa('sg.showLess'):inFa('sg.showAll').replace('{n}',faNum(rows.length));button.onclick=()=>{faView.showAll=!faView.showAll;renderFaPreview();};list.appendChild(button);}
    }
    if($('faRefresh')) $('faRefresh').disabled=faView.loading && current;
  }
  if($('faRefresh')) $('faRefresh').addEventListener('click',updateFaPreview);

  /* Surfaces that cannot show a number when the exchange is down. Each one
   * gets the sentence, in the same red style, instead of a blank box. */
  async function renderUnavailablePanels() {
    const on = !!live.unavailable;
    const html = `<div class="glass p-4 border-red-400/40 bg-red-500/10 text-center">
      <i data-lucide="plug-zap" class="w-4 h-4 text-red-300 mx-auto mb-1"></i>
      <div class="text-[11px] font-bold text-red-200">${LANG === 'fa'
        ? 'سرور نوبیتکس در دسترس نیست' : 'The Nobitex server is unreachable'}</div>
      <div class="text-[10px] text-red-200/75 leading-5 mt-0.5">${LANG === 'fa'
        ? 'قیمت، نمودار و سیگنال به دادهٔ زنده نیاز دارند؛ چیزی جای‌گزین نمی‌شود.'
        : 'Price, chart and signals need live data; nothing is substituted.'}</div>
    </div>`;
    if (!on) return;
    // Backtest lab: the KPIs are already em-dashes, the chart area carries the
    // sentence, and the gate bar stops advertising a promotion verdict.
    if ($('btChart')) $('btChart').innerHTML = html;
    if ($('btGateBar')) $('btGateBar').setAttribute('data-unavailable', '1');
    if ($('btGateMsg')) {
      $('btGateMsg').textContent = LANG === 'fa'
        ? 'بدون کندل زنده هیچ نتیجهٔ بک‌تستی وجود ندارد.'
        : 'without live candles there is no backtest result.';
    }
    if ($('btGate')) { $('btGate').textContent = '—'; $('btGate').className = 'chip text-red-300 border-red-300/30'; }
    if ($('btBody') && !$('btBody').children.length) {
      $('btBody').innerHTML = `<tr><td colspan="9" class="text-center text-[11px] text-red-200/80 py-3">
        ${LANG === 'fa' ? 'سرور نوبیتکس در دسترس نیست — پنجره‌ای برای نمایش نیست.' : 'Nobitex unreachable — no windows to show.'}</td></tr>`;
    }
    if (typeof refreshIcons === 'function') refreshIcons();
  }
  window.renderUnavailablePanels = renderUnavailablePanels;

  /* ---------- Scan scheduler (Backend first → Mobile Standalone Engine) ---------- */
  async function scanNow(force) {
    invalidateFaPreview();
    // A slow server must not leave the panel blank.
    try { renderSignalsLive(); } catch (e) {}
    try {
      const api = NBX.core.api;
      const backend = await api.checkBackend();
      if (backend && backend.backend) {
        // `upstream.ok` is three-valued: true, false, and null on the first call
        // of a fresh process (the probe is fired in the background and answered
        // with "unknown"). Reading null as false painted a red "the exchange is
        // unreachable" badge over a board that was feeding live prices, and it
        // stayed red until the next scheduled scan.
        const upOk = backend.upstream ? backend.upstream.ok : null;
        setLiveMode(upOk === false ? 'DEGRADED' : 'ONLINE',
                    backend.upstream?.latency_ms ?? backend.stats?.last_latency_ms ?? null);
        if (upOk === null) {
          setTimeout(() => { if (live.mode !== 'OFFLINE') scanNow(false); }, 2500);
        }
        live.upstream = backend.upstream || null;
        live.backendVersion = backend.version || null;
        live.authMode = (backend.auth && backend.auth.mode) || null;
        const uni = await api.getBackendUniverse();
        NBX.core.engine.universe = uni.symbols || [];
        NBX.core.engine.premium = uni.premium_usdtirt;
        NBX.core.engine.premium_pct = uni.premium_pct;
        live.source = uni.source || null;
        live.universeUnavailable = !!uni.unavailable;
        const signalTf=live.timeframe;
        const sig = await api.getBackendSignals(signalTf);
        live.signalSnapshot=signalSnapshotMeta(sig,signalTf);
        live.faCoverage = sig.fa_coverage || null;
        live.source = sig.source || live.source;
        live.dataAge = sig.age_s;
        live.dataStale = !!sig.stale;
        // 1.0.23 — the server measures the age of *each* symbol's candles and
        // reports the worst; keeping the block means the scan card can name the
        // symbol whose feed is behind instead of averaging it away.
        live.scanFreshness = sig.freshness || null;
        // ADR-0022 stage 1 — the gates' verdicts, always `applied: false`. Kept so
        // the card can say what *would* have happened without implying it did.
        live.gates = sig.gates || null;
        // ADR-0025 — each coin's own Toman premium. A measurement shown beside the
        // ranking, never inside it.
        live.pairPremium = sig.pair_premium || null;
        live.unavailable = !!(uni.unavailable || sig.unavailable) || !(sig.scored || []).length;
        live.unavailableMsg = (sig.unavailable && sig.message) || (uni.unavailable && uni.message) || '';
        live.unavailableDetail = (sig.detail || uni.detail || '').slice(0, 160);
        const bySym = {};
        sig.scored.forEach((s) => {
          bySym[s.symbol] = s;
        });
        NBX.core.engine.signals = bySym;
        NBX.core.engine.rankSignals = function () {
          return sig.scored;
        };
        live.lastScanAt = Date.now();
        await updateOverviewFromLive();
        renderSignalsLive();
        if (typeof window.upShow === 'function') window.upShow(live.unavailable, live.unavailableDetail);
        if (typeof window.fillTicketFromLive === 'function') window.fillTicketFromLive();
        window.dispatchEvent(new CustomEvent('nbx:scan', { detail: { unavailable: live.unavailable } }));
        // fire and forget: the rows paint immediately with a placeholder and
        // fill in when their candles arrive (usually a few hundred ms).
        prefetchSparkCandles((sig.scored || []).slice(0, SIG_ROW_LIMIT).map((s) => s.symbol), live.timeframe);
        await updateFaPreview();
        if (force && typeof toast === 'function') {
          if (upOk !== false) {
            toast(inFa('t.connected'), inFa('t.connectedM') + ' (FastAPI)', 'ok');
          } else {
            toast(
              LANG === 'fa' ? 'سرور نوبیتکس در دسترس نیست' : 'The Nobitex server is unreachable',
              LANG === 'fa' ? 'دادهٔ زنده دریافت نشد؛ هیچ عددی جای‌گزین نمی‌شود.' : 'No live data was received; nothing is substituted.',
              'err'
            );
          }
        }
        return;
      }

      /* Mobile / standalone: there is no data source. Say that, don't invent one. */
      const chip = $('buildChip');
      if (chip) {
        chip.textContent = LANG === 'fa' ? 'آفلاین · بدون داده' : 'offline · no data';
        chip.title = LANG === 'fa'
          ? 'بدون بک‌اند و بدون دسترسی به نوبیتکس: هیچ قیمتی تولید نمی‌شود'
          : 'No backend and no exchange access: no prices are generated';
      }
      setLiveMode('MOBILE', null);
      live.source = 'unavailable';
      live.unavailable = true;
      live.unavailableDetail = 'no backend / no exchange';
      live.ranked = [];
      NBX.core.engine.universe = [];
      NBX.core.engine.signals = {};
      live.lastScanAt = Date.now();
      await updateOverviewFromLive();
      renderSignalsLive();
      await updateFaPreview();
      await renderUnavailablePanels();
      if (typeof window.fillTicketFromLive === 'function') window.fillTicketFromLive();
      window.dispatchEvent(new CustomEvent('nbx:scan', { detail: { unavailable: true } }));
      if (typeof window.upShow === 'function') window.upShow(true, live.unavailableDetail);
      if (force && typeof toast === 'function') {
        toast(LANG === 'fa' ? 'سرور نوبیتکس در دسترس نیست' : 'The Nobitex server is unreachable',
              LANG === 'fa' ? 'دادهٔ زنده دریافت نشد؛ هیچ عددی جای‌گزین نمی‌شود.' : 'No live data was received; nothing is substituted.',
              'err');
      }
    } catch (e) {
      // A thrown scan (timeout, 500, a request waiting on a server lock) is an
      // outcome too. It used to fall through with an empty list and a badge
      // that implied a working browser engine.
      live.unavailable = true;
      live.unavailableMsg = LANG === 'fa'
        ? 'دسترسی به سرور نوبیتکس امکان‌پذیر نیست؛ دادهٔ زنده دریافت نشد.'
        : 'The Nobitex server is unreachable; no live data was received.';
      live.unavailableDetail = String((e && (e.code || e.message)) || 'scan failed').slice(0, 160);
      live.ranked = [];
      live.lastScanAt = Date.now();
      setLiveMode('OFFLINE', null);
      try { renderSignalsLive(); } catch (_) {}
      try { await updateFaPreview(); } catch (_) {}
      try { await renderUnavailablePanels(); } catch (_) {}
      try { if (typeof window.fillTicketFromLive === 'function') window.fillTicketFromLive(); } catch (_) {}
      try { if (typeof window.upShow === 'function') window.upShow(true, live.unavailableDetail); } catch (_) {}
      try { window.dispatchEvent(new CustomEvent('nbx:scan', { detail: { unavailable: true } })); } catch (_) {}
      if (force && typeof toast === 'function') toast(live.unavailableMsg, live.unavailableDetail, 'err');
    }
  }

  // 0 = manual only. Reading the preference here (instead of a literal) is what
  // makes Settings → "دورهٔ اسکن" a real control rather than decoration.
  const scanInterval = () =>
    typeof window.pref === 'function' ? Number(window.pref('scanSec')) : 120;

  function schedule() {
    if (live.interval) { clearInterval(live.interval); live.interval = null; }
    const sec = scanInterval();
    if (sec > 0) live.interval = setInterval(() => scanNow(false), sec * 1000);
    return sec;
  }

  /* The price feed's transport, read from the server every few seconds.
     This block used to sit *after* the `return` in `schedule()`: it was
     unreachable, so the page never learned whether the socket was up and the
     status chip had nothing to read. Dead code that looks like a feature is
     worse than a missing feature — it answers questions it never asked. */
  let wsPollTimer = null;
  async function wsPoll() {
    // The guard has to survive `NBX` itself being absent: if nbx-core.js never
    // loaded, `!NBX.core` is not false, it is a TypeError — thrown from a timer,
    // so nothing the page owns can catch it and the whole bundle looks dead.
    if (!(window.NBX && NBX.core && NBX.core.api && typeof NBX.core.api.getWsStatus === 'function')) return;
    try {
      const st = await NBX.core.api.getWsStatus();
      if (st && st.enabled) {
        live.wsMid = st.books;
        live.wsTransport = st.transport || 'rest-poll';
        live.wsChannels = st.channels || [];
        live.wsStats = st.stats_frames || 0;
        live.wsCandle = st.candle_frames || 0;
        renderLiveBadge();
      }
    } catch (e) {}
  }
  function startWsPolling() {
    if (wsPollTimer) return;
    wsPoll();
    wsPollTimer = setInterval(wsPoll, 5 * 1000);
  }

  function bindApiHealth() {
    const api = NBX.core.api;
    api.onHealth(({ ok, latency }) => {
      const el = document.querySelector('[data-i18n="hdr.api"]');
      if (el) el.textContent = (LANG==='fa'?'ارتباط: ':'API: ') + (ok ? faNum(latency) + (LANG==='fa'?' میلی‌ثانیه':' ms') : (LANG==='fa'?'محلی':'local'));
    });
  }


  /* ============================================================
   * Accounts (ADR-0018)
   *
   * The page asks "who am I?" before it asks for anything else. If the server
   * says accounts exist and there is no session, the dashboard is not booted at
   * all — a half-loaded dashboard behind a login form would still be polling
   * and still showing an empty shell, which reads as "broken" rather than
   * "signed out".
   *
   * Every mutation carries X-CSRF-Token (see `_authHeaders` in nbx-core.js).
   * ============================================================ */
  const AUTH = {
    cookie(name) {
      try {
        const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return m ? decodeURIComponent(m[1]) : '';
      } catch (e) { return ''; }
    },
    async call(path, body, method) {
      const headers = { 'Content-Type': 'application/json' };
      const csrf = this.cookie('nbx_csrf');
      if (csrf) headers['X-CSRF-Token'] = csrf;
      try {
        const res = await fetch(NBX.config.pythonApi + path, {
          method: method || 'POST', headers, credentials: 'same-origin',
          ...((method === 'DELETE' || method === 'GET')
            ? {} : { body: JSON.stringify(body || {}) }),
        });
        let data = null;
        try { data = await res.json(); } catch (e) {}
        return { ok: res.ok, status: res.status, data: data || {} };
      } catch (e) {
        return { ok: false, status: 0, data: {} };
      }
    },
    async me() {
      try {
        const res = await fetch(NBX.config.pythonApi + '/auth/me', { credentials: 'same-origin' });
        if (!res.ok) return null;
        return await res.json();
      } catch (e) { return null; }
    },
    async logout() {
      await this.call('/auth/logout');
      location.reload();
    },
  };

  function authSay(kind, text) {
    const el = $('authMsg');
    if (!el) return;
    el.className = 'auth-msg ' + kind;
    el.textContent = text;
  }

  function roleLabel(role) {
    return inFa('auth.role.' + role);
  }

  let auditAuthIdentity = null;
  function paintUser(state) {
    const chip = $('userChip'), name = $('userName'), role = $('userRole'), out = $('logoutBtn');
    if (!state || !chip) return;
    const identity = JSON.stringify([state.user && state.user.username, state.user && state.user.role, !!state.can_mutate, !!state.token]);
    if (identity !== auditAuthIdentity) {
      auditAuthIdentity = identity;
      if (typeof window.invalidateAuditServer === 'function') window.invalidateAuditServer();
    }
    const accountNav = $('accountNav');
    if (accountNav) accountNav.classList.toggle('hidden', !(state && state.user));
    if (state.user) {
      name.textContent = state.user.username;
      role.textContent = '· ' + roleLabel(state.user.role);
      chip.classList.remove('hidden');
      chip.classList.add('inline-flex');
      chip.title = state.user.username + ' — ' + roleLabel(state.user.role);
      document.documentElement.dataset.canMutate = state.can_mutate ? '1' : '0';
      document.documentElement.dataset.signalReview =
        state.capabilities && state.capabilities.signal_intelligence_review ? '1' : '0';
      const auditPanel = $('operatorAuditPanel');
      if (auditPanel) auditPanel.classList.toggle('hidden', !state.can_mutate);
      if (out) {
        out.classList.remove('hidden');
        out.title = inFa('auth.logout');
      }
      if (!state.can_mutate) {
        // Say it once, in the banner the app already uses for "something is
        // different about this session" — silently ignoring a click is worse.
        const banner = $('upBanner'), msg = $('upBannerMsg');
        if (banner && msg) {
          banner.classList.remove('hidden');
          msg.textContent = inFa('auth.readonly');
        }
      }
    } else if (state.token) {
      document.documentElement.dataset.canMutate = '0';
      const auditPanel = $('operatorAuditPanel');
      if (auditPanel) auditPanel.classList.add('hidden');
      name.textContent = 'token';
      role.textContent = '';
      chip.classList.remove('hidden');
      chip.classList.add('inline-flex');
    }
  }

  function showSetupGate() {
    const gate = $('authGate'), form = $('setupOwnerForm');
    if (!gate || !form) return;
    gate.hidden = false;
    $('authLoginForm').hidden = true;
    $('authRegisterForm').hidden = true;
    form.hidden = false;
    const alt = $('authToRegister');
    if (alt && alt.parentElement) alt.parentElement.style.display = 'none';
    $('authTitle').textContent = inFa('setup.title');
    const sub = gate.querySelector('p.sub');
    if (sub) sub.textContent = inFa('setup.sub');
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      const password = $('setupPass').value;
      if (password !== $('setupConfirm').value) {
        authSay('bad', inFa('setup.mismatch'));
        return;
      }
      const btn = $('setupClaimBtn');
      if (btn) btn.disabled = true;
      const r = await AUTH.call('/setup/claim', {
        setup_token: $('setupToken').value.trim(),
        username: $('setupUser').value.trim(),
        password,
      });
      if (r.ok) { location.reload(); return; }
      if (btn) btn.disabled = false;
      authSay('bad', (r.data && r.data.detail) || inFa('auth.err.net'));
    };
    setTimeout(() => $('setupToken').focus(), 30);
  }

  function showAuthGate(state) {
    const gate = $('authGate');
    if (!gate) return;
    gate.hidden = false;
    const loginForm = $('authLoginForm'), regForm = $('authRegisterForm');
    const toReg = $('authToRegister'), back = $('authBackBtn');

    function mode(kind) {
      const register = kind === 'register';
      if (loginForm) loginForm.hidden = register;
      if (regForm) regForm.hidden = !register;
      if (toReg) toReg.parentElement.style.display = register ? 'none' : '';
      authSay('info', '');
      const focus = $(register ? 'authCode' : 'authUser');
      if (focus) setTimeout(() => focus.focus(), 30);
    }
    if (toReg) toReg.onclick = () => mode('register');
    if (back) back.onclick = () => mode('login');
    mode('login');

    if (!state.auth_required || state.auth_required === false) {
      authSay('info', inFa('auth.err.noowner'));
    }

    if (loginForm) {
      loginForm.onsubmit = async (ev) => {
        ev.preventDefault();
        const btn = $('authLoginBtn');
        if (btn) btn.disabled = true;
        const r = await AUTH.call('/auth/login', {
          username: $('authUser').value.trim(),
          password: $('authPass').value,
        });
        if (r.ok) { location.reload(); return; }
        if (btn) btn.disabled = false;
        if (r.status === 401) authSay('bad', (r.data && r.data.detail) || inFa('auth.err.bad'));
        else if (r.status === 429) authSay('bad', (r.data && r.data.detail) || inFa('auth.err.bad'));
        else authSay('bad', (r.data && r.data.detail) || inFa('auth.err.net'));
      };
    }

    if (regForm) {
      regForm.onsubmit = async (ev) => {
        ev.preventDefault();
        const btn = $('authRegBtn');
        if (btn) btn.disabled = true;
        const r = await AUTH.call('/auth/register', {
          code: $('authCode').value.trim(),
          username: $('authNewUser').value.trim(),
          password: $('authNewPass').value,
        });
        if (r.ok) { location.reload(); return; }
        if (btn) btn.disabled = false;
        authSay('bad', (r.data && r.data.detail) || inFa('auth.err.invite'));
      };
    }
  }


  /* ============================================================
   * Settings → users and access.
   *
   * Only the owner sees the controls; everyone sees who they are and what
   * their role allows. The invite code is shown once and is not recoverable —
   * the server keeps a hash, so "show it again" is not an option and the UI
   * must not pretend otherwise.
   * ============================================================ */
  async function renderAccount() {
    const profile = $('accountProfile');
    if (!profile) return;
    const esc = (v) => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
    const me = await AUTH.me();
    if (!me || !me.user) return;
    const u = me.user, caps = me.capabilities || {}, policy = me.live_policy, usage = me.live_usage || {};
    profile.innerHTML = `<div class="grid grid-cols-2 lg:grid-cols-6 gap-3 text-[10px]"><div><span class="text-zinc-500">ID</span><b class="block num">${u.id}</b></div><div><span class="text-zinc-500">username</span><b class="block num">${esc(u.username)}</b></div><div><span class="text-zinc-500">role</span><b class="block">${esc(roleLabel(u.role))}</b></div><div><span class="text-zinc-500">status</span><b class="block">${u.active ? 'active' : 'disabled'}</b></div><div><span class="text-zinc-500">created</span><b class="block num">${new Date(u.created_at*1000).toLocaleString()}</b></div><div><span class="text-zinc-500">last login</span><b class="block num">${u.last_login ? new Date(u.last_login*1000).toLocaleString() : '—'}</b></div></div>`;
    $('accountCaps').innerHTML = Object.entries(caps).map(([key, value]) => `<div class="flex items-center gap-2 rounded-lg bg-white/[.03] px-2 py-1.5 text-[10px]"><span class="${value ? 'text-emerald-300' : 'text-zinc-600'}">${value ? '✓' : '—'}</span><span class="num">${esc(key)}</span></div>`).join('');
    $('accountLivePolicy').innerHTML = policy ? `<div class="grid grid-cols-2 gap-2"><span>live_place: <b>${policy.enabled ? 'enabled' : 'disabled'}</b></span><span>order: <b>${policy.max_order_usdt} USDT</b></span><span>daily: <b>${usage.notional_usdt || 0} / ${policy.max_daily_usdt} USDT</b></span><span>count: <b>${usage.orders || 0} / ${policy.max_daily_orders}</b></span><span class="col-span-2">symbols: <b>${esc((policy.allowed_symbols || []).join(', '))}</b></span></div>` : inFa('account.noPolicy');
    const sessionsRes = await AUTH.call('/auth/me/sessions', null, 'GET');
    const sessions = (sessionsRes.ok && sessionsRes.data.sessions) || [];
    $('accountSessions').innerHTML = sessions.map((s) => `<div class="flex flex-wrap items-center gap-2 rounded-lg bg-white/[.03] px-3 py-2 text-[10px]"><span class="num">${new Date(s.last_seen*1000).toLocaleString()}</span><span class="text-zinc-500">${esc(s.ip)}</span><span class="truncate max-w-sm text-zinc-500">${esc(s.ua)}</span>${s.current ? `<span class="chip text-emerald-300">${inFa('account.current')}</span>` : ''}<button data-my-session="${s.id}" class="ms-auto text-red-300">${inFa('account.revoke')}</button></div>`).join('');
    $('accountSessions').querySelectorAll('[data-my-session]').forEach((button) => { button.onclick = async () => { const r = await AUTH.call('/auth/me/sessions/' + button.dataset.mySession, null, 'DELETE'); if (r.ok && r.data.reload) location.reload(); else renderAccount(); }; });
    const prefs = me.preferences || {};
    $('accountLanguage').value = prefs.language || 'fa'; $('accountTimezone').value = prefs.timezone || 'Asia/Tehran'; $('accountDefaultPage').value = prefs.default_page || 'overview';
    await renderMyActivity();
  }

  async function renderMyActivity() {
    const body = $('accountActivity'); if (!body) return;
    const res = await AUTH.call('/auth/me/activity?limit=100', null, 'GET');
    const rows = (res.ok && res.data.events) || [];
    body.innerHTML = rows.map((e) => `<div class="grid grid-cols-[1fr_auto] gap-2 rounded-lg bg-white/[.03] px-3 py-2 text-[10px]"><span><b>${esc(e.action || '')}</b>${e.detail ? ' · ' + esc(e.detail) : ''}</span><span class="num text-zinc-500">${new Date(e.created_at*1000).toLocaleString()}</span></div>`).join('');
  }

  async function renderAccess() {
    const box = $('setAccess');
    const body = $('setAccessBody');
    const who = $('setAccessWho');
    if (!box || !body) return;
    const st = await AUTH.me();
    const usersNav = $('usersNav'), errorsNav = $('errorsNav'), settingsNav = $('settingsNav');
    if (usersNav) usersNav.classList.toggle('hidden', !(st && st.is_owner));
    if (errorsNav) errorsNav.classList.toggle('hidden', !(st && st.is_owner));
    if (settingsNav) settingsNav.classList.toggle('hidden', !(st && st.is_owner));
    if (!st || !st.user || !st.is_owner) { box.hidden = true; return; }
    box.hidden = false;
    if (who) who.textContent = st.user.username + ' · ' + roleLabel(st.user.role);

    if (!st.is_owner) {
      body.innerHTML = `<div class="text-[10px] sm:text-[11px] text-zinc-400 leading-6">${
        inFa('set.accessNotOwner')}</div>`;
      return;
    }

    const [usersRes, invRes] = await Promise.all([
      AUTH.call('/auth/users', null, 'GET') || Promise.resolve({ data: { users: [] } }),
      fetch(NBX.config.pythonApi + '/auth/invites', { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.json() : { invites: [] })).catch(() => ({ invites: [] })),
    ]);
    const users = (usersRes && usersRes.data && usersRes.data.users) || [];
    const invites = invRes.invites || [];

    const roleOptions = ['viewer', 'operator', 'owner']
      .map((r) => `<option value="${r}">${inFa('auth.role.' + r)}</option>`).join('');

    body.innerHTML = `
      <div class="space-y-1.5">
        ${users.map((u) => `
          <div class="flex flex-wrap items-center gap-2 rounded-xl bg-white/[.03] border border-white/10 px-3 py-2">
            <span class="num text-[11px] font-bold text-zinc-200">${u.username}</span>
            <span class="text-[9px] text-zinc-500">${u.last_login ? new Date(u.last_login * 1000).toLocaleString() : '—'}</span>
            ${u.active ? '' : `<span class="chip text-[9px] text-red-300 border-red-400/30">${inFa('set.accessOff')}</span>`}
            <select data-role="${u.id}" class="ms-auto text-[10px] bg-white/5 border border-white/10 rounded-lg px-2 py-1 text-zinc-300">
              ${['viewer', 'operator', 'owner'].map((r) =>
                `<option value="${r}" ${r === u.role ? 'selected' : ''}>${inFa('auth.role.' + r)}</option>`).join('')}
            </select>
            <button data-toggle="${u.id}" data-active="${u.active ? 1 : 0}"
              class="text-[10px] px-2 py-1 rounded-lg bg-white/5 border border-white/10 text-zinc-300">
              ${u.active ? inFa('set.accessDisable') : inFa('set.accessEnable')}</button>
            <button data-kill="${u.id}"
              class="text-[10px] px-2 py-1 rounded-lg bg-amber-400/10 border border-amber-300/30 text-amber-200">
              ${inFa('set.accessKill')}</button>
            ${u.role === 'operator' ? (() => { const p = u.live_policy || { enabled:false, max_order_usdt:25, max_daily_usdt:100, max_daily_orders:5, allowed_symbols:['BTCUSDT','ETHUSDT'] }; return `
              <div class="basis-full grid grid-cols-2 lg:grid-cols-6 gap-1.5 pt-2 border-t border-white/5">
                <label class="text-[9px] text-zinc-500"><input data-live-enabled="${u.id}" type="checkbox" ${p.enabled ? 'checked' : ''}> live_place</label>
                <input data-live-order="${u.id}" class="auth-input" type="number" min="1" value="${p.max_order_usdt}" placeholder="max/order USDT">
                <input data-live-day="${u.id}" class="auth-input" type="number" min="1" value="${p.max_daily_usdt}" placeholder="max/day USDT">
                <input data-live-count="${u.id}" class="auth-input" type="number" min="1" value="${p.max_daily_orders}" placeholder="orders/day">
                <input data-live-symbols="${u.id}" class="auth-input" value="${(p.allowed_symbols || []).join(',')}" placeholder="BTCUSDT,ETHUSDT">
                <button data-live-save="${u.id}" class="text-[10px] rounded-lg bg-rose-400/10 border border-rose-300/30 text-rose-200">${inFa('set.livePolicySave')}</button>
              </div>`; })() : ''}
          </div>`).join('')}
      </div>

      <div class="rounded-xl bg-white/[.03] border border-white/10 p-3 space-y-2">
        <div class="text-[11px] font-bold text-zinc-200">${inFa('set.inviteNew')}</div>
        <div class="flex flex-wrap items-center gap-2">
          <select id="invRole" class="text-[10px] bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-zinc-300">${roleOptions}</select>
          <input id="invNote" maxlength="120" placeholder="${inFa('set.inviteNote')}"
                 class="flex-1 min-w-[8rem] text-[10px] bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-zinc-200">
          <button id="invGo" class="text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-violet-400/15 text-violet-200 border border-violet-300/30">
            ${inFa('set.inviteMake')}</button>
        </div>
        <div id="invOut" class="text-[10px] leading-6 text-zinc-400"></div>
        ${invites.length ? `<div class="border-t border-white/5 pt-2 space-y-1">
          ${invites.map((i) => `<div class="flex items-center gap-2 text-[10px] text-zinc-500">
            <span class="num">#${i.id}</span>
            <span>${inFa('auth.role.' + i.role)}</span>
            <span class="chip text-[9px] ${i.state === 'open' ? 'text-emerald-300 border-emerald-300/30' : 'text-zinc-500 border-white/10'}">${window.NBXDisplay.label(i.state)}</span>
            <span class="truncate">${i.note || ''}</span>
            ${i.state === 'open' ? `<button data-revoke="${i.id}" class="ms-auto text-red-300">${inFa('set.inviteRevoke')}</button>` : ''}
          </div>`).join('')}
        </div>` : ''}
      </div>`;

    body.querySelectorAll('select[data-role]').forEach((el) => {
      el.onchange = async () => {
        const r = await AUTH.call('/auth/users/' + el.dataset.role, { role: el.value }, 'PATCH');
        if (!r.ok) el.value = el.dataset.prev || el.value;
        renderAccess();
      };
    });
    body.querySelectorAll('button[data-toggle]').forEach((el) => {
      el.onclick = async () => {
        await AUTH.call('/auth/users/' + el.dataset.toggle,
                        { active: el.dataset.active !== '1' }, 'PATCH');
        renderAccess();
      };
    });
    body.querySelectorAll('button[data-kill]').forEach((el) => {
      el.onclick = async () => { await AUTH.call('/auth/users/' + el.dataset.kill + '/logout'); };
    });
    body.querySelectorAll('button[data-live-save]').forEach((el) => {
      el.onclick = async () => {
        const id = el.dataset.liveSave;
        const policy = {
          enabled: body.querySelector(`[data-live-enabled="${id}"]`).checked,
          max_order_usdt: Number(body.querySelector(`[data-live-order="${id}"]`).value),
          max_daily_usdt: Number(body.querySelector(`[data-live-day="${id}"]`).value),
          max_daily_orders: Number(body.querySelector(`[data-live-count="${id}"]`).value),
          allowed_symbols: body.querySelector(`[data-live-symbols="${id}"]`).value.split(',').map((v) => v.trim().toUpperCase()).filter(Boolean),
          allowed_execution: ['limit'],
        };
        const r = await AUTH.call('/auth/users/' + id + '/live-policy', policy, 'PATCH');
        if (!r.ok) alert((r.data && r.data.detail) || 'Policy rejected');
        else renderAccess();
      };
    });
    body.querySelectorAll('button[data-revoke]').forEach((el) => {
      el.onclick = async () => {
        await AUTH.call('/auth/invites/' + el.dataset.revoke, null, 'DELETE');
        renderAccess();
      };
    });
    const go = $('invGo');
    if (go) {
      go.onclick = async () => {
        go.disabled = true;
        const r = await AUTH.call('/auth/invites', {
          role: ($('invRole') || {}).value || 'viewer',
          note: ($('invNote') || {}).value || '',
        });
        go.disabled = false;
        const out = $('invOut');
        if (!out) return;
        out.innerHTML = r.ok
          ? `<div class="text-zinc-400">${inFa('set.inviteOnce')}</div>
             <code class="block mt-1 select-all break-all text-emerald-300">${r.data.code}</code>`
          : `<div class="text-red-300">${(r.data && r.data.detail) || inFa('auth.err.net')}</div>`;
      };
    }
  }

  async function renderUserActivity() {
    const body = $('usersActivity');
    if (!body) return;
    const st = await AUTH.me();
    if (!st || !st.is_owner) { body.innerHTML = ''; return; }
    const res = await AUTH.call('/auth/activity?limit=200', null, 'GET');
    const events = (res.ok && res.data && res.data.events) || [];
    body.innerHTML = events.length ? events.map((e) => `<div class="grid grid-cols-[auto_1fr_auto] gap-2 rounded-lg bg-white/[.03] border border-white/5 px-3 py-2 text-[10px]">
      <span class="num text-violet-300">${String(e.username || '—').replaceAll('&','&amp;').replaceAll('<','&lt;')}</span>
      <span class="text-zinc-300"><b>${String(e.action || '').replaceAll('&','&amp;').replaceAll('<','&lt;')}</b>${e.detail ? ` · ${String(e.detail).replaceAll('&','&amp;').replaceAll('<','&lt;')}` : ''}</span>
      <span class="num text-zinc-500">${new Date((e.created_at || 0) * 1000).toLocaleString()}</span>
    </div>`).join('') : `<div class="text-[10px] text-zinc-500">${inFa('users.noActivity')}</div>`;
  }

  async function renderDeskLive() {
    const panel = $('deskLivePanel'), button = $('deskLivePlace');
    if (!panel || !button) return;
    const me = await AUTH.me();
    const policy = me && me.live_policy;
    const eligibleRole = !!(me && (me.is_owner || (me.user && me.user.role === 'operator' && policy && policy.enabled)));
    if (!eligibleRole) { panel.classList.add('hidden'); return; }
    panel.classList.remove('hidden');
    const [status, evidence] = await Promise.all([
      AUTH.call('/live/status', null, 'GET'), AUTH.call('/live/evidence', null, 'GET'),
    ]);
    const ready = !!(status.ok && status.data.placement_enabled && evidence.ok && evidence.data.ready);
    button.disabled = !ready;
    const policyChip = $('deskLivePolicy');
    if (policyChip) policyChip.textContent = me.is_owner ? 'owner' : `${policy.max_order_usdt} / ${policy.max_daily_usdt} USDT`;
    button.onclick = async () => {
      const key = `operator-${Date.now()}-${crypto.getRandomValues(new Uint32Array(1))[0].toString(16)}`;
      const out = await AUTH.call('/live/place', {
        symbol: $('deskLiveSymbol').value.trim().toUpperCase(), side: $('deskLiveSide').value,
        entry: Number($('deskLiveEntry').value), stop: Number($('deskLiveStop').value),
        size_quote: Number($('deskLiveSize').value), tf: '15m', idempotency_key: key,
        confirmation: $('deskLiveConfirm').value,
      });
      const msg = $('deskLiveMsg'), detail = out.data && out.data.detail;
      if (msg) { msg.textContent = out.ok ? `${out.data.execution.state} · ${out.data.execution.client_order_id}` : (typeof detail === 'string' ? detail : JSON.stringify(detail || 'Failed')); msg.className = `text-[10px] mt-2 ${out.ok ? 'text-emerald-300' : 'text-red-300'}`; }
    };
  }

  async function renderInstall() {
    const box = $('setInstall'), body = $('setInstallBody');
    if (!box || !body) return;
    const st = await AUTH.me();
    if (!st || !st.is_owner) { box.hidden = true; return; }
    const res = await AUTH.call('/install/settings', null, 'GET');
    if (!res.ok) { box.hidden = true; return; }
    box.hidden = false;
    const data = res.data || {};
    const values = data.values || {};
    const details = data.fields || {};
    const fields = data.allowed || Object.keys(values);
    const baseline = Object.fromEntries(fields.map(key => [key,
      String(values[key] ?? (details[key] || {}).effective ?? '')]));
    const fa = LANG === 'fa';
    body.innerHTML = `<div class="grid grid-cols-1 lg:grid-cols-2 gap-2">${fields.map(key => {
      const info = details[key] || {};
      const pending = info.pending_or_overridden ? (fa ? ' · در انتظار اعمال یا overridden' : ' · pending or overridden') : '';
      return `<label class="auth-field !mb-0"><span class="num">${esc(key)}</span>
        <input data-install="${esc(key)}" value="${esc(baseline[key])}">
        <span class="text-[10px] text-zinc-500 num">${fa ? 'مؤثر' : 'effective'}: ${esc(info.effective ?? '—')} · ${esc(info.source || 'unknown')}${esc(pending)}</span>
        <span class="text-[10px] text-zinc-500 num">managed: ${esc(info.managed ?? '—')} · default: ${esc(info.default ?? '—')}</span>
      </label>`;
    }).join('')}</div>
      <div class="flex flex-wrap justify-end gap-2 pt-2 border-t border-white/5">
        <span id="installMsg" class="text-[10px] text-zinc-400 me-auto"></span>
        <button id="installProbe" class="auth-btn !flex-none" type="button">${inFa('install.probe')}</button>
        <button id="installSave" class="auth-btn !flex-none" type="button">${inFa('install.save')}</button>
      </div>`;
    const payload = () => Object.fromEntries([...body.querySelectorAll('[data-install]')]
      .filter(el => el.value.trim() !== baseline[el.dataset.install])
      .map(el => [el.dataset.install, el.value.trim()]));
    const run = async (path) => {
      const msg = $('installMsg');
      const edits = payload();
      if (!Object.keys(edits).length) {
        if (msg) msg.textContent = fa ? 'تغییری برای ارسال نیست' : 'No edits to submit';
        return;
      }
      if (path.endsWith('settings') && Object.keys(edits).some(key => /_DB$|_DIR$|_CACHE$/.test(key))) {
        if (!window.confirm(fa ? 'تغییر مسیر داده نیازمند برنامهٔ مهاجرت جداگانه است. فقط draft ذخیره شود؟' : 'Data paths require a separate migration plan. Save the draft only?')) return;
      }
      const out = await AUTH.call(path, edits);
      if (msg) {
        msg.textContent = out.ok ? (path.endsWith('probe') ? inFa('install.ok') : inFa('install.restart'))
          : JSON.stringify((out.data && out.data.detail) || inFa('install.failed'));
        msg.className = `text-[10px] me-auto ${out.ok ? 'text-emerald-300' : 'text-red-300'}`;
      }
      if (out.ok && path.endsWith('settings')) {
        Object.assign(baseline, edits);
        // Reload on explicit Refresh to show persisted/overridden provenance.
      }
    };
    $('installProbe').onclick = () => run('/install/probe');
    $('installSave').onclick = () => run('/install/settings');
    const wizard = await AUTH.call('/install/wizard/status', null, 'GET');
    if (wizard.ok) {
      const steps = wizard.data.steps || [];
      body.insertAdjacentHTML('beforeend', `<div class="pt-3 border-t border-white/5 space-y-2">
        <div class="font-bold text-[11px]">${inFa('install.wizard')}</div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2">${steps.map((x) => `<div class="rounded-lg bg-white/[.03] p-2"><div>${x.step}</div><div class="num text-zinc-500">${x.state}</div></div>`).join('')}</div>
        <div class="flex flex-wrap gap-2"><button id="wizardPreflight" class="auth-btn !flex-none">${inFa('install.preflight')}</button>
        ${['install-service','install-proxy','restart-service','reload-proxy','rollback'].map((a) => `<button class="auth-btn !flex-none" data-wizard-action="${a}" ${(wizard.data.helper || {}).available ? '' : 'disabled'}>${a}</button>`).join('')}</div>
        <pre id="wizardOut" class="max-h-48 overflow-auto rounded-lg bg-black/40 p-2 text-[10px] whitespace-pre-wrap" dir="ltr"></pre></div>`);
      const show = (out) => { const el = $('wizardOut'); if (el) el.textContent = out.ok ? JSON.stringify(out.data, null, 2) : JSON.stringify((out.data || {}).detail || 'Failed'); };
      $('wizardPreflight').onclick = async () => { show(await AUTH.call('/install/wizard/preflight', {})); };
      body.querySelectorAll('[data-wizard-action]').forEach((button) => { button.onclick = async () => {
        const action = button.dataset.wizardAction;
        const confirmed = !['restart-service','reload-proxy','rollback'].includes(action) || window.confirm(`${action}?`);
        if (confirmed) show(await AUTH.call('/install/wizard/action', {action, confirm: confirmed}));
      }; });
    }
  }

  async function renderOperations() {
    const box = $('setOperations'), body = $('setOperationsBody');
    if (!box || !body) return;
    const st = await AUTH.me();
    if (!st || !st.is_owner) { box.hidden = true; return; }
    const res = await AUTH.call('/operations', null, 'GET');
    if (!res.ok) { box.hidden = true; return; }
    box.hidden = false;
    const liveStatus = await AUTH.call('/live/status', null, 'GET');
    const liveEvidence = await AUTH.call('/live/evidence', null, 'GET');
    const liveOn = !!(liveStatus.ok && liveStatus.data && liveStatus.data.placement_enabled);
    const evidenceReady = !!(liveEvidence.ok && liveEvidence.data && liveEvidence.data.ready);
    const archives = (res.data.archives || []);
    body.innerHTML = `<p class="text-[10px] text-amber-300">${esc(LANG === 'fa' ? 'بازیابی وب غیرفعال است؛ بازیابی فقط آفلاین پس از توقف سرویس و همهٔ نویسنده‌ها. پشتیبان حاوی دادهٔ حساس حساب است.' : 'Web restore is disabled. Restore offline only after stopping all writers. Backups contain sensitive account data.')}</p><div class="flex flex-wrap gap-2">
      <div class="text-[10px] text-zinc-500 num">${esc(res.data.backup_directory || '—')}</div>
      <button id="opsBackup" class="auth-btn !flex-none" type="button">${inFa('ops.backup')}</button>
      <select id="opsArchive" class="auth-input !w-auto">${archives.map((a) => `<option value="${a.name}">${a.name} · ${a.bytes} B</option>`).join('')}</select>
      <input id="opsConfirm" class="auth-input !w-auto" placeholder="RESTORE filename.zip">
      <button id="opsRestore" class="auth-btn !flex-none" type="button" ${res.data.restore_available && archives.length ? '' : 'disabled'}>${inFa('ops.restore')}</button>
    </div><div class="flex flex-wrap gap-2 pt-2 border-t border-white/5">
      <span class="text-[10px] text-zinc-500 self-center">${
        LANG === 'fa'
          ? 'پیکربندی پروکسی از تب «نصب سرور» انجام می‌شود؛ دامنه در فایل root-owned تنظیم می‌شود.'
          : 'Proxy configuration lives in the Server Install tab; the domain is set in a root-owned file.'}</span>
      <input id="opsRestartConfirm" class="auth-input !w-auto" placeholder="RESTART">
      <button id="opsRestart" class="auth-btn !flex-none" type="button" ${res.data.helper_available ? '' : 'disabled'}>${inFa('ops.restart')}</button>
      <span id="opsMsg" class="text-[10px] text-zinc-400"></span></div>
    <div class="pt-2 border-t border-rose-400/20 space-y-2">
      <div class="text-[11px] font-bold text-rose-300">${inFa('ops.live')}</div>
      <div class="text-[10px] text-zinc-500">${inFa('ops.liveGate')} · ${liveEvidence.ok ? `${liveEvidence.data.samples || 0}/30 · ${liveEvidence.data.days || 0}/5` : '—'}</div>
      <div class="flex flex-wrap gap-2">
        <input id="liveSymbol" class="auth-input !w-28" placeholder="BTCUSDT"><select id="liveSide" class="auth-input !w-auto"><option value="buy">buy</option><option value="sell">sell</option></select>
        <input id="liveEntry" class="auth-input !w-32" inputmode="decimal" placeholder="entry"><input id="liveStop" class="auth-input !w-32" inputmode="decimal" placeholder="stop">
        <input id="liveSize" class="auth-input !w-32" inputmode="decimal" placeholder="size quote"><input id="liveConfirm" class="auth-input !w-52" placeholder="PLACE-REAL-ORDER">
        <button id="livePlace" class="auth-btn !flex-none !bg-rose-500/20" type="button" ${(liveOn && evidenceReady) ? '' : 'disabled'}>${inFa('ops.livePlace')}</button>
      </div><span id="liveMsg" class="text-[10px] text-zinc-500"></span>
    </div>`;
    const run = async (path, payload) => {
      const out = await AUTH.call('/operations/' + path, payload);
      const msg = $('opsMsg');
      if (msg) { msg.textContent = out.ok ? 'OK' : ((out.data && out.data.detail) || 'Failed'); msg.className = `text-[10px] ${out.ok ? 'text-emerald-300' : 'text-red-300'}`; }
      if (out.ok && path === 'backup') renderOperations();
    };
    $('opsBackup').onclick = () => run('backup', {});
    $('opsRestore').onclick = () => run('restore', { archive: $('opsArchive').value, confirmation: $('opsConfirm').value });
    $('opsRestart').onclick = () => run('restart', { confirmation: $('opsRestartConfirm').value });
    $('livePlace').onclick = async () => {
      const key = `owner-${Date.now()}-${crypto.getRandomValues(new Uint32Array(1))[0].toString(16)}`;
      const out = await AUTH.call('/live/place', {
        symbol: $('liveSymbol').value.trim().toUpperCase(), side: $('liveSide').value,
        entry: Number($('liveEntry').value), stop: Number($('liveStop').value),
        size_quote: Number($('liveSize').value), tf: '15m', idempotency_key: key,
        confirmation: $('liveConfirm').value,
      });
      const msg = $('liveMsg');
      if (msg) { const detail = out.data && out.data.detail; msg.textContent = out.ok ? `${out.data.execution.state} · ${out.data.execution.client_order_id}` : (typeof detail === 'string' ? detail : JSON.stringify(detail || 'Failed')); msg.className = `text-[10px] ${out.ok ? 'text-emerald-300' : 'text-red-300'}`; }
    };
  }

  function bindLogout() {
    const out = $('logoutBtn');
    if (out) out.onclick = () => AUTH.logout();
    document.querySelectorAll('.navbtn[data-tab="account"]').forEach((b) => {
      b.addEventListener('click', () => setTimeout(renderAccount, 120));
    });
    const activityRefresh = $('accountActivityRefresh');
    if (activityRefresh) activityRefresh.onclick = renderMyActivity;
    const logoutAll = $('accountLogoutAll');
    if (logoutAll) logoutAll.onclick = async () => { const r = await AUTH.call('/auth/me/logout-all', {}); if (r.ok) location.reload(); };
    const passwordSave = $('accountPasswordSave');
    if (passwordSave) passwordSave.onclick = async () => {
      const r = await AUTH.call('/auth/password', { current: $('accountCurrentPassword').value, new: $('accountNewPassword').value });
      const msg = $('accountPasswordMsg'); if (msg) msg.textContent = r.ok ? 'OK' : ((r.data && r.data.detail) || 'Failed');
      if (r.ok) location.reload();
    };
    const settingsRefresh = $('setRefresh');
    if (settingsRefresh) settingsRefresh.addEventListener('click', () => { renderInstall(); renderOperations(); });
    const prefsSave = $('accountPrefsSave');
    if (prefsSave) prefsSave.onclick = async () => {
      const r = await AUTH.call('/auth/me/preferences', { language: $('accountLanguage').value, timezone: $('accountTimezone').value.trim(), theme: 'dark', default_page: $('accountDefaultPage').value }, 'PATCH');
      if (r.ok) renderAccount();
    };
    document.querySelectorAll('.navbtn[data-tab="users"]').forEach((b) => {
      b.addEventListener('click', () => setTimeout(() => { renderAccess(); renderUserActivity(); }, 120));
    });
    const refresh = $('usersRefresh');
    if (refresh) refresh.onclick = renderUserActivity;
    document.querySelectorAll('.navbtn[data-tab="desk"]').forEach((b) => {
      b.addEventListener('click', () => setTimeout(renderDeskLive, 120));
    });
  }

  async function loadCoinMetadata() {
    // Never block market data on a cosmetic provider. The server endpoint is
    // disk-only; credentials stay in the existing authenticated API wrapper.
    try {
      if (!NBX.core || !NBX.core.api || !window.setCoinMetadata) return;
      const data = await NBX.core.api.get(NBX.config.pythonApi + '/coins', 1);
      window.setCoinMetadata(data.coins, NBX.config.pythonApi);
    } catch (_) { /* bundled logos remain available, including offline */ }
  }

  async function start() {
    addLiveStylesOnce();
    injectMobileNavAndDownload();
    bindLogout();

    // Ask before booting. `null` means the question could not be asked (server
    // down, older build) — in that case carry on: the app has its own offline
    // handling, and blocking on a failed probe would turn a blip into a wall.
    const setupState = await fetch(NBX.config.pythonApi + '/setup/status', {
      credentials: 'same-origin', headers: { Accept: 'application/json' },
    }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (setupState && setupState.setup_required) {
      showSetupGate();
      return;
    }

    const authState = await AUTH.me();
    if (authState && authState.auth_required && !authState.user && !authState.token) {
      showAuthGate(authState);
      return;
    }
    paintUser(authState);
    if (authState && authState.user) {
      renderAccess();
      renderInstall();
      renderOperations();
    }

    // A session can end while the tab is open (expired, revoked, password
    // changed elsewhere). Re-ask every few minutes; reload only when the server
    // *says* nobody is signed in, never on a network error.
    setInterval(async () => {
      const now = await AUTH.me();
      if (now && now.auth_required && !now.user && !now.token) {
        document.documentElement.dataset.canMutate = '0';
        if (typeof window.invalidateAuditServer === 'function') window.invalidateAuditServer();
        location.reload();
      } else if (now && now.user) paintUser(now);
    }, 5 * 60 * 1000);

    try {
      await NBX.boot();
      loadCoinMetadata();
      setInterval(loadCoinMetadata, 60 * 60 * 1000);
      bindApiHealth();
      await scanNow(true);
      schedule();
      startWsPolling();
    } catch (e) {
      setLiveMode('MOBILE', null);
      renderLiveBadge();
    }
  }

  window.NBX_APP = {
    refreshDisplay() {
      if(paperSnapshot) paintPaperState(...paperSnapshot);
      if(typeof window.renderMarketOverview === 'function') window.renderMarketOverview();
      if(typeof window.renderAuditServer === 'function') window.renderAuditServer();
      renderSignalsLive(); renderFaPreview();
      if(typeof lastCandle!=='undefined' && lastCandle && $('lastPrice')) $('lastPrice').textContent = fmtPrice(lastCandle.close, live.activeSymbol);
    },
    refreshPaperBook: () => refreshPaperState(),
    setScanInterval(sec) {
      if (!Number.isFinite(Number(sec))) return null;
      schedule();
      return scanInterval();
    },
    refreshFundamentals: updateFaPreview,
    scanNow: () => scanNow(true),
    ensureSparks: () =>
      prefetchSparkCandles((live.ranked || []).slice(0, SIG_ROW_LIMIT).map((s) => s.symbol), live.timeframe),
    get intervalSec() { return scanInterval(); },
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
