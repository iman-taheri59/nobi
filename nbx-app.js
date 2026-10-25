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
      .live-badge{position:fixed;bottom:14px;right:14px;z-index:80;display:flex;gap:8px;align-items:center;
        padding:8px 12px;border-radius:14px;border:1px solid rgba(255,255,255,.12);
        background:rgba(9,9,11,.88);backdrop-filter:blur(12px);font-size:11px;box-shadow:0 6px 24px rgba(0,0,0,.45);}
      .live-badge .dot{width:.5rem;height:.5rem;border-radius:9999px;display:inline-block}
      html[dir="ltr"] .live-badge{right:auto;left:14px}
      @media (max-width:1023px){
        .live-badge{bottom:70px;right:10px;padding:6px 10px;font-size:10px;}
        html[dir="ltr"] .live-badge{right:auto;left:10px;}
        body{padding-bottom:68px !important;}
      }
      .nbx-mini-hbar{height:6px;border-radius:999px;background:rgba(255,255,255,.07);overflow:hidden}
      .nbx-mini-hbar > i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#34d399,#38bdf8)}
      #apiStatusPop{min-width:240px}
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
      document.body.appendChild(el);
    }
    const m = live.mode;
    const map = {
      ONLINE: ['#34d399', LANG === 'fa' ? 'داده زنده نوبیتکس' : 'Live Nobitex data'],
      MOBILE: ['#f87171', LANG === 'fa' ? 'سرور نوبیتکس در دسترس نیست' : 'Nobitex unreachable'],
      DEGRADED: ['#f87171', LANG === 'fa' ? 'سرور نوبیتکس در دسترس نیست' : 'Nobitex unreachable'],
      OFFLINE: ['#f87171', LANG === 'fa' ? 'آفلاین — بدون داده' : 'Offline — no data'],
    };
    const [color, text] = map[m] || map.MOBILE;
    // `st.books` is filled by REST polling too, so counting books and calling
    // it "WS" claimed a socket that was not there. The chip now reads the
    // transport field the server actually reports.
    const onWs = live.wsTransport === 'ws';
    const wsCh = Array.isArray(live.wsChannels) ? live.wsChannels.length : 0;
    el.innerHTML = `
      <span class="dot" style="background:${color};box-shadow:0 0 0 4px ${color}22"></span>
      <button id="liveBadgeBtn" style="font-family:inherit;color:inherit;background:none;border:none;cursor:pointer;display:flex;align-items:center;gap:8px">
        <span class="num">${text}</span>
        <span style="color:#a1a1aa">·</span>
        <span class="num">${live.latency !== null && live.latency !== undefined ? Math.round(live.latency) + 'ms' : '—'}</span>
        ${onWs && wsCh ? `<span style="color:#a1a1aa">·</span><span class="num" style="color:#818cf8">WS x${faNum(wsCh)}</span>` : ''}
      </button>
      <div id="apiStatusPop" class="hidden absolute bottom-full mb-2 right-0 glass p-3 text-xs space-y-1.5"></div>
    `;
    const btn = $('liveBadgeBtn');
    const pop = $('apiStatusPop');
    btn.onclick = () => {
      pop.classList.toggle('hidden');
      if (!pop.classList.contains('hidden')) {
        pop.innerHTML = [
          [LANG === 'fa' ? 'حالت اجرا' : 'Mode', `${m} · ${live.latency !== null && live.latency !== undefined ? Math.round(live.latency) + 'ms' : '—'}`],
          [LANG === 'fa' ? 'نمادهای فعال' : 'Active Symbols', live.ranked.length ? faNum(live.ranked.length) : '—'],
          [LANG === 'fa' ? 'تایم‌فریم' : 'Timeframe', live.timeframe],
          [LANG === 'fa' ? 'آخرین اسکن' : 'Last Scan', live.lastScanAt ? faNum(Math.round((Date.now() - live.lastScanAt) / 1000)) + (LANG === 'fa' ? ' ثانیه پیش' : 's ago') : '—'],
          // Where the prices actually come from: a push channel or a poll.
          [LANG === 'fa' ? 'مسیر قیمت' : 'Price path',
            onWs ? (LANG === 'fa' ? 'وب‌سوکت' : 'WebSocket')
                 : (LANG === 'fa' ? 'REST — دوره‌ای' : 'REST — polled')],
          [LANG === 'fa' ? 'کانال‌های وب‌سوکت' : 'WS channels', onWs ? faNum(wsCh) : '—'],
          [LANG === 'fa' ? 'پیام آمار بازار' : 'Market-stat frames', onWs ? faNum(live.wsStats || 0) : '—'],
          [LANG === 'fa' ? 'کندل وب‌سوکت' : 'WS candles', onWs ? faNum(live.wsCandle || 0) : '—'],
        ]
          .map(([k, v]) => `<div style="display:flex;justify-content:space-between;gap:12px"><span style="color:#a1a1aa">${k}:</span><span class="num">${v}</span></div>`)
          .join('');
      }
    };
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
      'sg.faCoverage': 'بنیادی زنده: {n} از {t}',
      'sg.faScored': 'بنیادی: {n} از {t}',
      'sg.faCoverageTitle': 'محاسبه‌شده: {s} · از پروفایل ثابت: {f} · ناموفق: {x}',
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
      'sg.faCoverage': 'Live FA: {n} of {t}',
      'sg.faScored': 'FA: {n} of {t}',
      'sg.faCoverageTitle': 'Scored: {s} · from static profile: {f} · failed: {x}',
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
          scanNow(true);
        })
    );
  }

  let paperSnapshot = null;
  async function refreshPaperState() {
    const api = NBX.core.api;
    let posRes = null;
    let metRes = null;
    let riskRes = null;
    try { posRes = await api.getPaperPositions(); } catch (e) {}
    try { metRes = await api.getPaperMetrics(); } catch (e) {}
    try { riskRes = await api.getBackendRiskState(); } catch (e) {}
    paperSnapshot = [posRes, metRes, riskRes];
    paintPaperState(...paperSnapshot);
  }

  function paintPaperState(posRes, metRes, riskRes) {
    const rows = (posRes && posRes.positions) || [];
    const list = rows
      .filter((p) => p.state !== 'CLOSED')
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

    const m = (metRes && metRes.metrics) || (posRes && posRes.metrics) || null;
    const rs = riskRes || window.__NBX_RISK_STATE || null;
    const eq = m && m.equity != null && m.equity !== '' && typeof m.equity !== 'boolean' && Number.isFinite(Number(m.equity)) ? Number(m.equity) : null;
    const startEq = m && m.start_equity != null && m.start_equity !== '' && typeof m.start_equity !== 'boolean' && Number.isFinite(Number(m.start_equity)) ? Number(m.start_equity) : null;
    const pct = eq !== null && startEq ? ((eq - startEq) / startEq) * 100 : null;

    if ($('kEquity')) $('kEquity').textContent = eq !== null ? window.NBXDisplay.money(eq, 'USD') : '\u2014';
    if ($('kToday')) {
      $('kToday').textContent = pct !== null ? window.NBXDisplay.percent(pct, 2) : '\u2014';
      $('kToday').className = 'num kpi-num ' + (pct !== null && pct < 0 ? 'text-red-400' : 'text-emerald-400');
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

    // The kill switch has to be visible AND reversible from the dashboard.
    const resumeBtn = $('resumeBtn');
    if (resumeBtn) {
      const isHalted = !!(rs && rs.halted);
      resumeBtn.classList.toggle('hidden', !isHalted);
      if (isHalted) resumeBtn.textContent = LANG === 'fa' ? 'رفع توقف اضطراری' : 'Reset kill switch';
    }
    if ($('kPf')) {
      const pfOk = !!(m && m.trades);
      const pfUp = !!(window.live && window.live.unavailable);
      // An em-dash on its own reads like a broken widget, and a bare "unavailable"
      // marker claims the exchange is down when it may simply be that no trade
      // has closed yet. Three states, three sentences.
      const pfCard = $('kPf') && $('kPf').closest('.glass');
      const pfSub = pfCard && pfCard.querySelector('[data-i18n="ov.kpi4sub"]');
      if (pfOk) {
        $('kPf').textContent = faNum(m.profit_factor, 2);
        if (pfSub) pfSub.textContent = inFa('ov.kpi4sub');
        if (pfCard) pfCard.removeAttribute('data-unavailable');
      } else if (pfUp) {
        $('kPf').textContent = '\u2014';
        if (pfSub) pfSub.textContent = LANG === 'fa'
          ? 'سرور نوبیتکس در دسترس نیست — عملکرد کاغذی خوانده نمی‌شود.'
          : 'Nobitex unreachable — the paper record cannot be read.';
        if (pfCard) pfCard.setAttribute('data-unavailable', '1');
      } else {
        $('kPf').textContent = LANG === 'fa' ? 'بدون داده' : 'no data';
        if (pfSub) pfSub.textContent = LANG === 'fa'
          ? 'هنوز معاملهٔ بسته‌شدهٔ کاغذی ثبت نشده — PF تعریف‌نشده است.'
          : 'no closed paper trade yet — PF is undefined.';
        if (pfCard) pfCard.removeAttribute('data-unavailable');
      }
    }
    const bar = document.querySelector('#page-overview .bar-fill[data-w]');
    if (bar && rs) {
      const cap = (rs.caps && rs.caps.maxHeatPct) || 6;
      bar.style.width = Math.min(100, Math.max(0, ((rs.openHeat || 0) / cap) * 100)) + '%';
    }

    // Keep the sizing preview (Desk page) on the same equity as the backend.
    if (rs && Number.isFinite(Number(rs.equity))) {
      try { EQUITY = Number(rs.equity); } catch (e) {}
    }

    // The ticket's preview compares against these numbers; it must be the same
    // object the server gates on, not a second literal set.
    if (rs && rs.caps && typeof RISK_LIMITS !== 'undefined') {
      RISK_LIMITS = {
        riskPct: Number(rs.caps.riskPct),
        maxSingleAssetPct: Number(rs.caps.maxSingleAssetPct),
        minRR: Number(rs.caps.minRR),
      };
    }

    // Risk caps / kill switches come from /api/risk/state, not demo arrays.
    if (rs && typeof caps !== 'undefined' && Array.isArray(caps)) {
      const c = rs.caps || {};
      const openCount = list.length;
      const cashPct = m && Number(m.equity) ? (Number(m.cash) / Number(m.equity)) * 100 : null;
      const rows = [
        {
          name: { fa: 'پوزیشن‌های همزمان', en: 'Concurrent positions' },
          val: { fa: `${faNum(openCount)} / ${faNum(c.maxConcurrent || 0)}`, en: `${faNum(openCount)} / ${faNum(c.maxConcurrent || 0)}` },
          pct: c.maxConcurrent ? (openCount / c.maxConcurrent) * 100 : 0,
          tone: 'sky',
        },
        {
          name: { fa: 'دمای کل سبد', en: 'Portfolio heat' },
          val: { fa: `${faNum(rs.openHeat || 0, 2)}٪ / ${faNum(c.maxHeatPct || 0)}٪`, en: `${goodPct(rs.openHeat || 0,2)} / ${goodPct(c.maxHeatPct || 0)}` },
          pct: c.maxHeatPct ? ((rs.openHeat || 0) / c.maxHeatPct) * 100 : 0,
          tone: 'amber',
        },
        {
          name: { fa: 'سقف تک‌دارایی', en: 'Single-asset cap' },
          val: { fa: `${faNum(c.maxSingleAssetPct || 0)}٪`, en: `${goodPct(c.maxSingleAssetPct || 0)}` },
          pct: Math.min(100, c.maxSingleAssetPct || 0),
          tone: 'indigo',
        },
        {
          name: { fa: 'بافر نقد', en: 'Cash buffer' },
          val: {
            fa: cashPct !== null ? `${faNum(cashPct, 1)}٪ (حداقل ${faNum(c.minCashPct || 0)}٪)` : '—',
            en: cashPct !== null ? `${goodPct(cashPct,1)} (min ${goodPct(c.minCashPct || 0)})` : '—',
          },
          pct: cashPct !== null ? Math.min(100, Math.max(0, cashPct)) : 0,
          tone: 'emerald',
        },
      ];
      caps.length = 0;
      caps.push(...rows);
      if (typeof renderCaps === 'function') renderCaps();
    }

    if (rs && typeof kills !== 'undefined' && Array.isArray(kills)) {
      const up = live.upstream || {};
      const rows = [
        {
          n: { fa: 'کلید اضطراری', en: 'Kill switch' },
          v: { fa: rs.halted ? 'مسلح' : 'غیرفعال', en: rs.halted ? 'Armed' : 'Off' },
          ok: !rs.halted,
        },
        {
          n: { fa: 'زیان روزانه', en: 'Daily P&L' },
          v: { fa: `${faNum(rs.dailyPnl || 0, 2)}٪ / −۳٪`, en: `${goodPct(rs.dailyPnl || 0,2)} / ${goodPct(-3)}` },
          ok: (rs.dailyPnl || 0) > -3,
        },
        {
          n: { fa: 'سلامت API صرافی', en: 'Exchange API health' },
          v: {
            fa: up.ok === true ? 'سالم' : up.ok === false ? 'در دسترس نیست' : 'نامشخص',
            en: up.ok === true ? 'healthy' : up.ok === false ? 'unreachable' : 'unknown',
          },
          ok: up.ok !== false,
        },
      ];
      kills.length = 0;
      kills.push(...rows);
      if (typeof renderKills === 'function') renderKills();
    }

    live.risk = rs;
    // Store the response *envelope*, not m: drawEquity() reads `.curve` from
    // here, and assigning the inner metrics object left that key undefined —
    // so the equity chart painted "Nobitex unreachable" forever, live or not.
    live.paperMetrics = metRes ? Object.assign({}, metRes, { metrics: m })
                               : { metrics: m, curve: [] };
    if (metRes && Array.isArray(metRes.curve) && metRes.curve.length) {
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
    return eng.candles[sym + ':' + tf] || eng.candles[sym] || null;
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

  /* The strip above the signal list is rewritten by applyStaticI18n() on every
     tab switch, which used to restore "Scanning…" and a fixed "last cycle: 23s
     ago" even with the exchange down. This function is the single writer. */
  function syncSignalsHeader() {
    const sh = $('page-signals');
    if (!sh) return;
    const noData = !!live.unavailable || !live.ranked.length;
    const set = (sel, txt, cls) => {
      const el = sh.querySelector(sel);
      if (el) { el.textContent = txt; if (cls) el.className = cls; }
    };
    if (noData) {
      set('[data-i18n="sg.scanned"]', LANG === 'fa' ? 'بدون داده' : 'no data',
          'chip num text-red-300 border-red-300/40');
      set('[data-i18n="sg.cycle"]', LANG === 'fa' ? 'چرخه: —' : 'cycle: —', 'chip');
      set('[data-i18n="sg.last"]', LANG === 'fa' ? 'سرور پاسخ نداد' : 'no reply from the server',
          'text-[10px] sm:text-[11px] text-red-200/70 self-center');
      set('#sgFaCoverage', LANG === 'fa' ? 'بنیادی زنده: —' : 'live FA: —',
          'chip num text-violet-300 border-violet-300/30');
      return;
    }
    set('[data-i18n="sg.scanned"]', inFa('sg.scanned').replace('{ranked}', faNum(live.ranked.length)),
        'chip num');
    set('[data-i18n="sg.cycle"]', inFa('sg.cycle').replace('{tf}', window.NBXDisplay.timeframe(live.timeframe)).replace('{m}', faNum(2)), 'chip');
    set('[data-i18n="sg.last"]',
        inFa('sg.last').replace('{s}', live.lastScanAt ? faNum(Math.round((Date.now() - live.lastScanAt) / 1000)) : '—'),
        'text-[10px] sm:text-[11px] text-zinc-500 self-center');
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
            LANG === 'fa' ? 'سرور نوبیتکس در دسترس نیست' : 'The Nobitex server is unreachable'}</div>
          <div class="text-[10px] sm:text-[11px] text-red-200/80 leading-5 mt-1">${
            LANG === 'fa'
              ? 'دسترسی به سرور نوبیتکس امکان‌پذیر نیست؛ دادهٔ زنده دریافت نشد. اتصال شبکه یا فیلتر را بررسی کن و دوباره تلاش کن.'
              : 'The Nobitex server is unreachable; no live data was received. Check the network or filtering and try again.'}</div>
          ${live.unavailableDetail ? `<div class="text-[9px] text-red-200/60 mt-1.5 num" dir="ltr">${String(live.unavailableDetail).slice(0,120)}</div>` : ''}
          <button type="button" onclick="scanNow(true)" class="mt-3 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-red-400/15 text-red-200 border border-red-300/40">${
            LANG === 'fa' ? 'تلاش دوباره' : 'Retry'}</button>
        </div>`;
        syncSignalsHeader();
        if (typeof refreshIcons === 'function') refreshIcons();
      } else if (!list.children.length) {
        list.innerHTML = `<div class="glass p-6 text-center text-[11px] text-zinc-500">${
          LANG === 'fa' ? 'در حال اسکن نمادها…' : 'scanning symbols…'}</div>`;
      }
      return;
    }
    const shdr = $('page-signals');
    if (shdr) {
      const scannedEl = shdr.querySelector('[data-i18n="sg.scanned"]');
      if (scannedEl) scannedEl.textContent = inFa('sg.scanned').replace('{ranked}', faNum(ranked.length));
      const cycleEl = shdr.querySelector('[data-i18n="sg.cycle"]');
      if (cycleEl) cycleEl.textContent = inFa('sg.cycle').replace('{tf}', window.NBXDisplay.timeframe(live.timeframe)).replace('{m}', faNum(2));
      const lastEl = shdr.querySelector('[data-i18n="sg.last"]');
      if (lastEl) lastEl.textContent = inFa('sg.last').replace('{s}', live.lastScanAt ? faNum(Math.round((Date.now() - live.lastScanAt) / 1000)) : '—');
      const faEl = shdr.querySelector('#sgFaCoverage') || shdr.querySelector('[data-i18n="sg.faCoverage"]');
      if (faEl) {
        const c = live.faCoverage;
        if (!c) {
          faEl.textContent = '—';
        } else {
          // `live` counts scores backed by a live provider call; the rest come
          // from the static profile table and are labelled as such.
          // An older backend reports only `scored`; do not relabel that as "live".
          const hasLive = c.live !== undefined;
          const liveN = hasLive ? c.live : c.scored;
          faEl.textContent = inFa(hasLive ? 'sg.faCoverage' : 'sg.faScored')
            .replace('{n}', faNum(liveN))
            .replace('{t}', faNum(c.total));
          faEl.className = 'chip num ' + (!hasLive
            ? 'text-zinc-300 border-zinc-300/30'
            : liveN === c.total ? 'text-emerald-300 border-emerald-300/30'
            : liveN === 0 ? 'text-red-300 border-red-300/30' : 'text-amber-300 border-amber-300/30');
          faEl.title = inFa('sg.faCoverageTitle')
            .replace('{s}', faNum(c.scored))
            .replace('{f}', faNum(c.fallback || 0))
            .replace('{x}', faNum(c.failed || 0));
        }
      }
    }

    const hidden = Math.max(0, ranked.length - SIG_ROW_LIMIT);
    const shown = live.showAll ? ranked : ranked.slice(0, SIG_ROW_LIMIT);
    list.innerHTML = shown
      .map((s, i) => {
        const col = s.composite >= 60 ? 'emerald' : s.composite >= 45 ? 'amber' : 'red';
        const comp = s.components || {};
        const dir = s.direction === 'LONG' ? 'text-emerald-300' : s.direction === 'EXIT' ? 'text-red-300' : 'text-zinc-300';
        const sparkU = s.composite >= 50;
        const symLabel = LANG === 'fa' ? s.symbol.replace('IRT', '/تومان') : s.symbol;
        return `
      <div data-sig-sym="${s.symbol}" data-sig-idx="${i}" tabindex="0" role="button"
           class="sig-row glass p-3.5 sm:p-4 flex flex-col md:flex-row md:items-center gap-3 sm:gap-4 cursor-pointer">
        <div class="flex items-center gap-3 md:w-44 shrink-0">
          <div class="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center font-extrabold text-sm num ${
            i < 3 ? 'text-emerald-300' : 'text-zinc-500'
          }">${faNum(i + 1)}</div>
          <div class="min-w-0"><div class="font-bold text-[13px] sm:text-sm">${window.coinIcon(s.symbol)}${symLabel}</div>
            <span class="chip ${dir}"${
          s.direction_raw && s.direction_raw !== s.direction
            ? ` title="${LANG === 'fa'
                ? `${s.direction_raw} بود؛ وتوی فاندامنتال (${(s.fa && s.fa.vetoes || []).join('، ')}) سقف ${faNum(+s.veto_cap)} گذاشت`
                : `was ${s.direction_raw}; a fundamental veto (${(s.fa && s.fa.vetoes || []).join(', ')}) capped it at ${+s.veto_cap}`}"`
            : ''
        }><span class="dot ${
          s.direction === 'LONG' ? 'bg-emerald-400' : s.direction === 'EXIT' ? 'bg-red-400' : 'bg-zinc-400'
        }"></span> ${window.NBXDisplay.label(s.direction)}</span>${
          s.direction_raw && s.direction_raw !== s.direction
            ? `<span class="chip text-[9px] text-amber-300 border-amber-300/30 ms-1">${LANG === 'fa' ? 'وتو' : 'veto'}</span>`
            : ''
        }
          </div>
          <span class="text-[10px] text-zinc-500 ms-auto md:hidden">${window.NBXDisplay.label(s.regime || '')}</span>
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 sm:gap-3 mb-1.5">
            <span class="text-[10px] text-zinc-500 hidden sm:block w-24">${inFa('sg.score')}</span>
            <div class="nbx-mini-hbar flex-1"><i style="width:${clamp(s.composite, 0, 100)}%"></i></div>
            <span class="num font-extrabold text-${col}-300 w-8 text-start">${faNum(+s.composite.toFixed(0))}</span>
            ${s.composite_basis && s.composite_basis !== 'ta+fa+liq'
              ? `<span class="chip text-[9px] text-amber-300 border-amber-300/30" title="${inFa('sg.basisTitle')}">${inFa('sg.basisNoFa')}</span>`
              : ''}
          </div>
          <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
            <span class="chip">${LANG==='fa'?'تکنیکال':'TA'} <b class="num">${faNum(+(s.score || 0).toFixed(0))}</b></span>
            <span class="chip${s.fa_score == null ? ' text-zinc-500' : ''}" title="${s.composite_basis || ''}">${inFa('sg.faN')} <b class="num">${s.fa_score == null ? '—' : faNum(Math.round(s.fa_score))}</b></span>
            <span class="chip">${inFa('sg.liq')} <b class="num">${faNum(s.liquidity_score)}</b></span>
            <span class="chip">${window.NBXDisplay.label('trend')} <b class="num">${faNum(+(comp.trend || 0))}</b></span>
            <span class="chip">${window.NBXDisplay.label('momentum')} <b class="num">${faNum(+(comp.momentum || 0))}</b></span>
            <span class="chip ${s.regime === 'TREND' ? 'text-emerald-300' : 'text-zinc-500'}">${window.NBXDisplay.label(s.regime || '')}</span>
            ${s.risk && s.risk.stopPct ? `<span class="chip">${window.NBXDisplay.label('stop')} <b class="num">${window.NBXDisplay.percent(s.risk.stopPct, 2, false)}</b></span>` : ''}
          </div>
        </div>
        <div class="w-full md:w-48 lg:w-60 xl:w-72 2xl:w-80 shrink-0">
          ${typeof spark === 'function' ? spark(sparkU, s.symbol, {big: true}) : ''}
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

  /* ---------- FA page data-driven products ---------- */
  async function fetchBackendFA(symbols) {
    const api = NBX.core.api;
    const results = [];
    for (const sym of symbols.slice(0, 6)) {
      try {
        const r = await api.getBackendFA(sym);
        if (r) results.push(r);
      } catch (e) {}
    }
    return results;
  }

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

  function faUnavailableHTML() {
    return `<div class="text-center py-4">
      <i data-lucide="plug-zap" class="w-4.5 h-4.5 text-red-300 mx-auto mb-1.5"></i>
      <div class="text-[11px] font-bold text-red-200">${LANG === 'fa'
        ? 'دادهٔ فاندامنتال در دسترس نیست' : 'Fundamentals are unavailable'}</div>
      <div class="text-[10px] text-red-200/70 leading-5 mt-0.5">${LANG === 'fa'
        ? 'دسترسی به سرور نوبیتکس امکان‌پذیر نیست؛ دادهٔ زنده دریافت نشد.'
        : 'The Nobitex server is unreachable; no live data was received.'}</div>
    </div>`;
  }

  async function updateFaPreview() {
    if (!live.ranked.length) {
      if ($('faList')) $('faList').innerHTML = faUnavailableHTML();
      // The three KPI tiles above the list must not keep a previous value (or a
      // static caption) while nothing was computed.
      if ($('faAvg')) $('faAvg').textContent = '—';
      if ($('faVetoes')) $('faVetoes').textContent = '—';
      if ($('faPrem')) $('faPrem').textContent = '—';
      const bar = document.querySelector('#page-fa .bar-fill');
      if (bar) bar.style.width = '0%';
      // Three tiles reading "—" with no reason is the bug the live probe
      // reports; mark the cards so the sentence beneath them is the answer.
      ['faAvg', 'faVetoes', 'faPrem'].forEach((id) => {
        const card = $(id) && $(id).closest('.glass');
        if (card) card.setAttribute('data-unavailable', '1');
      });
      return;
    }
    const fa = await fetchBackendFA(live.ranked.map((x) => x.symbol));
    // A provider that answers with `unavailable` must not be averaged into a
    // number: the page would show a "fundamental score" nobody computed.
    const usable = fa.filter((x) => x && typeof x.score === 'number');
    if (!usable.length && $('faList')) {
      $('faList').innerHTML = faUnavailableHTML();
      if ($('faAvg')) $('faAvg').textContent = '—';
      if ($('faVetoes')) $('faVetoes').textContent = '—';
      return;
    }
    const avgFA = usable.length ? usable.reduce((a, x) => a + x.score, 0) / usable.length : null;
    if ($('faAvg')) $('faAvg').textContent = avgFA !== null ? faNum(Math.round(avgFA)) : '—';
    // A score assembled from a hard-coded profile is not a live reading. It is
    // still the best number available, so it is shown — labelled, not hidden
    // and not passed off as live (ADR-0014). Only `fallback` counts: a cached
    // live read is stale-by-minutes, not invented.
    const isStatic = (x) => x.source === 'fallback' || (x.profile || (x.evidence || {}).profile) === 'fallback';
    const fb = usable.filter(isStatic);
    const sub = $('faAvgSub');
    if (sub) {
      if (!usable.length) {
        sub.textContent = '';
      } else if (fb.length) {
        sub.textContent = LANG === 'fa'
          ? `${faNum(fb.length)} از ${faNum(usable.length)} نماد از پروفایل ثابت (بدون تماس زندهٔ ارائه‌دهنده)`
          : `${faNum(fb.length)} of ${faNum(usable.length)} symbols from a static profile (no live provider call)`;
        sub.className = 'text-[10px] text-amber-300/90 leading-5 mt-0.5';
      } else {
        sub.textContent = LANG === 'fa' ? 'همه از ارائه‌دهندهٔ زنده' : 'all from a live provider';
        sub.className = 'text-[10px] text-zinc-500 leading-5 mt-0.5';
      }
    }
    const vetoCt = fa.filter((x) => x.vetoes && x.vetoes.length).length;
    if ($('faVetoes')) $('faVetoes').textContent = fa.length ? faNum(vetoCt) : '—';
    if ($('faPrem')) {
      const pp = NBX.core.engine.premium_pct;
      const known = typeof pp === 'number' && Number.isFinite(pp);
      $('faPrem').textContent = known
        ? window.NBXDisplay.percent(pp, 2)
        : LANG === 'fa' ? 'محاسبه‌نشدنی' : 'not available';
      // A dash here reads as "zero" or "loading". The premium needs all three
      // of BTCIRT, BTCUSDT and USDTIRT to be quoted; say that instead.
      $('faPrem').title = known
        ? ''
        : LANG === 'fa'
          ? 'پرمیوم به هر سه جفت BTCIRT، BTCUSDT و USDTIRT نیاز دارد؛ دست‌کم یکی در فهرست بازار نیست.'
          : 'The premium needs BTCIRT, BTCUSDT and USDTIRT; at least one is missing from the market list.';
      $('faPrem').classList.toggle('text-base', !known);
    }
    if ($('faList')) {
      const faScoreBySym = {};
      fa.forEach((x) => (faScoreBySym[x.symbol] = x));
      /* Same defect as the signals list had (1.1.2): a hard-coded `.slice(0, 6)`
       * drew six rows of a ranked list of thirty-nine, and nothing said the rest
       * existed. It shares the named constant and the same show-all switch. */
      const faHidden = Math.max(0, live.ranked.length - SIG_ROW_LIMIT);
      const faRows = live.ranked.slice(0, live.showAll ? live.ranked.length : SIG_ROW_LIMIT);
      $('faList').innerHTML = faRows
        .map((x) => {
          const f = faScoreBySym[x.symbol] || NBX.FA.scoreSymbol(x.symbol);
          const sc = f && typeof f.score === 'number' ? f.score : null;
          // ADR-0025: "no provider is mapped for this coin" is a different fact
          // from "the provider did not answer", and it used to accuse Nobitex —
          // which is not where fundamentals come from at all.
          const noProvider = !!(f && (f.source === 'no-provider' || f.profile === 'none'
            || (f.evidence || {}).profile === 'none'));
          if (sc === null || noProvider) {
            return `<div class="flex items-center gap-2.5 sm:gap-3">
              <span class="font-bold text-[13px] sm:text-sm shrink-0">${window.coinIcon(x.symbol, 20)}${x.symbol.replace(/IRT|USDT/g, '')}</span>
              <span class="flex-1 text-[10px] text-zinc-400">${LANG === 'fa'
                ? (noProvider ? 'منبع بنیادی برای این نماد نگاشت نشده (امتیاز خنثی)'
                              : 'منبع بنیادی پاسخ نداد')
                : (noProvider ? 'no fundamental provider is mapped for this coin (neutral score)'
                              : 'the fundamental provider did not answer')}</span>
            </div>`;
          }
          const isFallback = f && (f.source === 'fallback' ||
            f.profile === 'fallback' || (f.evidence || {}).profile === 'fallback');
          const extra =
            f && f.evidence && f.evidence.tvl
              ? (LANG==='fa'?'ارزش قفل‌شده ':'TVL ') + window.NBXDisplay.money(f.evidence.tvl, 'USD', 0)
              : window.NBXDisplay.label(x.direction) + (LANG==='fa'?' · قدرت روند ':' · ADX ') + faNum(+((x.diagnostics?.adx || 24).toFixed(0)));
          const fallbackChip = isFallback
            ? `<span class="chip text-[9px] text-amber-300 border-amber-300/30 ms-1">${LANG === 'fa' ? 'پروفایل ثابت' : 'static profile'}</span>`
            : '';
          return `
        <div class="flex items-center gap-2.5 sm:gap-3">
          <span class="font-bold text-[13px] sm:text-sm shrink-0">${window.coinIcon(x.symbol, 20)}${x.symbol.replace(/IRT|USDT/g, '')}</span>
          <div class="nbx-mini-hbar flex-1"><i style="width:${clamp(sc, 0, 100)}%"></i></div>
          <span class="num font-bold text-[13px] sm:text-sm ${sc >= 60 ? 'text-sky-300' : sc >= 45 ? 'text-amber-300' : 'text-red-300'}">${faNum(
            +sc.toFixed(0)
          )}${fallbackChip}</span>
          <span class="text-[10px] text-zinc-500 w-32 lg:w-48 hidden md:block truncate">${extra}</span>
        </div>`;
        })
        .join('')
        + (faHidden
          ? `<button type="button" class="w-full mt-1 text-[10px] font-bold text-zinc-400 hover:text-white"
                     onclick="window.sigShowAll(${live.showAll ? 'false' : 'true'})">${
              live.showAll ? inFa('sg.showLess')
                           : inFa('sg.showAll').replace('{n}', faNum(live.ranked.length))}</button>`
          : '');
    }

    try {
      const rs = await NBX.core.api.getBackendRiskState();
      window.__NBX_RISK_STATE = rs;
    } catch (e) {}
  }

  /* ---------- Scan scheduler (Backend first → Mobile Standalone Engine) ---------- */
  async function scanNow(force) {
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
        const sig = await api.getBackendSignals(live.timeframe);
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

  function paintUser(state) {
    const chip = $('userChip'), name = $('userName'), role = $('userRole'), out = $('logoutBtn');
    if (!state || !chip) return;
    if (state.user) {
      name.textContent = state.user.username;
      role.textContent = '· ' + roleLabel(state.user.role);
      chip.classList.remove('hidden');
      chip.classList.add('inline-flex');
      chip.title = state.user.username + ' — ' + roleLabel(state.user.role);
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
  async function renderAccess() {
    const box = $('setAccess');
    const body = $('setAccessBody');
    const who = $('setAccessWho');
    if (!box || !body) return;
    const st = await AUTH.me();
    if (!st || !st.user) { box.hidden = true; return; }
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

  async function renderInstall() {
    const box = $('setInstall'), body = $('setInstallBody');
    if (!box || !body) return;
    const st = await AUTH.me();
    if (!st || !st.is_owner) { box.hidden = true; return; }
    const res = await AUTH.call('/install/settings', null, 'GET');
    if (!res.ok) { box.hidden = true; return; }
    box.hidden = false;
    const values = (res.data && res.data.values) || {};
    const fields = [
      ['NBX_AUTH_DB', '/var/lib/nobitrader/auth.db'],
      ['NBX_JOURNAL_DB', '/var/lib/nobitrader/journal.db'],
      ['NBX_LIVE_DB', '/var/lib/nobitrader/live_execution.db'],
      ['NBX_CANDLE_CACHE', '/var/lib/nobitrader/candle_cache'],
      ['NBX_BT_DIR', '/var/lib/nobitrader/backtests'],
      ['NBX_EXPORT_DIR', '/var/lib/nobitrader/exports'],
      ['NBX_SCAN_LOOP_S', '900'], ['NBX_SCAN_LOOP_TF', '15m'],
      ['NBX_GATE_PREFETCH', '20'], ['NBX_BOOK_TTL_S', '120'],
      ['NBX_BOOK_GATE_TTL_S', '300'], ['NBX_BOOK_KEEP_S', '3600'],
      ['NBX_LIVE_MODE', 'disabled'],
    ];
    body.innerHTML = `<div class="grid grid-cols-1 lg:grid-cols-2 gap-2">${fields.map(([key, fallback]) => `
      <label class="auth-field !mb-0"><span class="num">${key}</span>
        <input data-install="${key}" value="${String(values[key] ?? fallback).replaceAll('&','&amp;').replaceAll('"','&quot;')}">
      </label>`).join('')}</div>
      <div class="flex flex-wrap justify-end gap-2 pt-2 border-t border-white/5">
        <span id="installMsg" class="text-[10px] text-zinc-400 me-auto"></span>
        <button id="installProbe" class="auth-btn !flex-none" type="button">${inFa('install.probe')}</button>
        <button id="installSave" class="auth-btn !flex-none" type="button">${inFa('install.save')}</button>
      </div>`;
    const payload = () => Object.fromEntries([...body.querySelectorAll('[data-install]')]
      .map((el) => [el.dataset.install, el.value.trim()]));
    const run = async (path) => {
      const msg = $('installMsg');
      const out = await AUTH.call(path, payload());
      if (msg) {
        msg.textContent = out.ok ? (path.endsWith('probe') ? inFa('install.ok') : inFa('install.restart'))
          : ((out.data && out.data.detail) || inFa('install.failed'));
        msg.className = `text-[10px] me-auto ${out.ok ? 'text-emerald-300' : 'text-red-300'}`;
      }
    };
    $('installProbe').onclick = () => run('/install/probe');
    $('installSave').onclick = () => run('/install/settings');
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
    body.innerHTML = `<div class="flex flex-wrap gap-2">
      <button id="opsBackup" class="auth-btn !flex-none" type="button">${inFa('ops.backup')}</button>
      <select id="opsArchive" class="auth-input !w-auto">${archives.map((a) => `<option value="${a.name}">${a.name} · ${a.bytes} B</option>`).join('')}</select>
      <input id="opsConfirm" class="auth-input !w-auto" placeholder="RESTORE filename.zip">
      <button id="opsRestore" class="auth-btn !flex-none" type="button">${inFa('ops.restore')}</button>
    </div><div class="flex flex-wrap gap-2 pt-2 border-t border-white/5">
      <input id="opsDomain" class="auth-input !w-auto" placeholder="${inFa('ops.domain')}">
      <button id="opsApply" class="auth-btn !flex-none" type="button" ${res.data.helper_available ? '' : 'disabled'}>${inFa('ops.apply')}</button>
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
    $('opsApply').onclick = () => run('apply-config', { domain: $('opsDomain').value.trim() });
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
    document.querySelectorAll('.navbtn[data-tab="settings"]').forEach((b) => {
      b.addEventListener('click', () => setTimeout(renderAccess, 120));
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
      if (now && now.auth_required && !now.user && !now.token) location.reload();
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
      if(live.ranked.length) { renderSignalsLive(); updateFaPreview(); }
      if(typeof lastCandle!=='undefined' && lastCandle && $('lastPrice')) $('lastPrice').textContent = fmtPrice(lastCandle.close, live.activeSymbol);
    },
    setScanInterval(sec) {
      if (!Number.isFinite(Number(sec))) return null;
      schedule();
      return scanInterval();
    },
    scanNow: () => scanNow(true),
    ensureSparks: () =>
      prefetchSparkCandles((live.ranked || []).slice(0, SIG_ROW_LIMIT).map((s) => s.symbol), live.timeframe),
    get intervalSec() { return scanInterval(); },
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
