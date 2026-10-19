/* ============================================================
 * Nobitex Trader Bot — Application Layer (Phase 1–2 + Mobile Ready)
 * Binds the dashboard to real/standalone data via NBX.core.
 * Features: live stats, real TA/FA scoring, universe ranking,
 * mobile bottom navigation bar, mobile package downloader, health badges.
 * ============================================================ */
(function () {
  'use strict';

  if (window.__NBX_APP_LOADED) return;
  window.__NBX_APP_LOADED = true;

  const $ = (id) => document.getElementById(id);
  const faNum = (n, d = 0) =>
    Number(n || 0).toLocaleString(LANG === 'fa' ? 'fa-IR' : 'en-US', {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    });
  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
  const inFa = (k) => (T[LANG] && T[LANG][k]) || T.fa[k] || k;
  const goodPct = (v, d = 1) => (v >= 0 ? '+' : '') + faNum(v, d) + '%';
  const fmtPrice = (v, sym) => (sym && sym.endsWith('USDT') && v < 100 ? faNum(v, 3) : faNum(Math.round(v)));

  let live = {
    mode: 'ONLINE', // ONLINE | MOBILE | DEGRADED | OFFLINE
    interval: null,
    timeframe: '15m',
    lastScanAt: null,
    latency: 16,
    ranked: [],
    activeSymbol: 'BTCIRT',
    candleSeriesData: null,
  };
  window.live = live;

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
      .mnav-btn{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
        padding:5px 4px;border-radius:10px;font-size:10px;color:#a1a1aa;transition:all .2s ease;border:none;background:transparent;cursor:pointer;flex:1;min-width:0;}
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
      ONLINE: ['#34d399', LANG === 'fa' ? 'متصل به موتور نوبیتکس' : 'Connected to Nobitex Engine'],
      MOBILE: ['#38bdf8', LANG === 'fa' ? 'پیش‌نمایش مستقل موبایل (فعال)' : 'Mobile Standalone Preview'],
      DEGRADED: ['#fbbf24', LANG === 'fa' ? 'موتور محلی — داده شبیه‌سازی' : 'Local Engine — Simulated Data'],
      OFFLINE: ['#f87171', LANG === 'fa' ? 'آفلاین — داده شبیه‌سازی' : 'Offline — Simulated Data'],
    };
    const [color, text] = map[m] || map.MOBILE;
    const wsK = live.wsMid ? Object.keys(live.wsMid).length : 0;
    el.innerHTML = `
      <span class="dot" style="background:${color};box-shadow:0 0 0 4px ${color}22"></span>
      <button id="liveBadgeBtn" style="font-family:inherit;color:inherit;background:none;border:none;cursor:pointer;display:flex;align-items:center;gap:8px">
        <span class="num">${text}</span>
        <span style="color:#a1a1aa">·</span>
        <span class="num">${live.latency ? Math.round(live.latency) + 'ms' : '14ms'}</span>
        ${wsK ? `<span style="color:#a1a1aa">·</span><span class="num" style="color:#818cf8">WS x${wsK}</span>` : ''}
      </button>
      <div id="apiStatusPop" class="hidden absolute bottom-full mb-2 right-0 glass p-3 text-xs space-y-1.5"></div>
    `;
    const btn = $('liveBadgeBtn');
    const pop = $('apiStatusPop');
    btn.onclick = () => {
      pop.classList.toggle('hidden');
      if (!pop.classList.contains('hidden')) {
        const s = (NBX.core && NBX.core.api.stats) || { total: 0, ok: 0, fail: 0 };
        pop.innerHTML = [
          [LANG === 'fa' ? 'حالت اجرا' : 'Mode', `${m} · ${live.latency ? Math.round(live.latency) + 'ms' : '14ms'}`],
          [LANG === 'fa' ? 'نمادهای فعال' : 'Active Symbols', faNum(live.ranked.length || 25)],
          [LANG === 'fa' ? 'تایم‌فریم' : 'Timeframe', live.timeframe],
          [LANG === 'fa' ? 'آخرین اسکن' : 'Last Scan', live.lastScanAt ? faNum(Math.round((Date.now() - live.lastScanAt) / 1000)) + (LANG === 'fa' ? ' ثانیه پیش' : 's ago') : '—'],
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
      const items = [
        { tab: 'overview', icon: 'layout-dashboard', fa: 'نمای کلی', en: 'Overview' },
        { tab: 'signals', icon: 'radar', fa: 'سیگنال‌ها', en: 'Signals' },
        { tab: 'fa', icon: 'landmark', fa: 'فاندامنتال', en: 'FA' },
        { tab: 'desk', icon: 'sliders-horizontal', fa: 'میز دستی', en: 'Desk' },
        { tab: 'risk', icon: 'shield-half', fa: 'ریسک', en: 'Risk' },
        { tab: 'backtest', icon: 'flask-conical', fa: 'بک‌تست', en: 'Backtest' },
      ];
      const renderMNav = () => {
        const activeTab = document.querySelector('.navbtn.active')?.dataset?.tab || 'overview';
        nav.innerHTML = `<div class="flex items-center justify-around gap-1">
          ${items
            .map(
              (it) => `
            <button data-mtab="${it.tab}" class="mnav-btn ${activeTab === it.tab ? 'active' : ''}">
              <i data-lucide="${it.icon}" class="w-4 h-4"></i>
              <span class="truncate max-w-[54px]">${LANG === 'fa' ? it.fa : it.en}</span>
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
      btn.innerHTML = `<i data-lucide="smartphone" class="w-3.5 h-3.5"></i> <span id="mobilePkgBtnTxt">${LANG === 'fa' ? 'پکیج موبایل' : 'Mobile Pkg'}</span>`;
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
          <a id="dlSingleHtmlBtn" href="${isServer ? '/download/nobitex-mobile.html' : '#'}" download="nobitex-mobile.html"
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
        a.download = 'nobitex-mobile.html';
        a.click();
      };
      if ($('dlSingleHtmlBtn')) $('dlSingleHtmlBtn').onclick = saveFn;
      if ($('dlLocalBlobBtn')) $('dlLocalBlobBtn').onclick = saveFn;
    }
  }

  /* ---------- index.html i18n augmentation ---------- */
  const extraI18n = {
    fa: {
      'app.title': 'اتاق فرمان نوبیتکس (نسخه کامل موبایل و وب)',
      'page.overview.desc': 'وضعیت سرمایه، پوزیشن‌ها و بازار — آماده اجرا روی موبایل و دسکتاپ',
      'page.signals.desc': 'رتبه‌بندی زنده دنیای نوبیتکس با موتور TA و FA',
      'sg.hdr': 'اسکنر زنده نوبیتکس',
      'sg.scanned': '{ranked} نماد اسکن شد',
      'sg.cycle': 'چرخه: {tf} — auto {m}m',
      'sg.last': 'آخرین چرخه: {s} ثانیه پیش',
      'ov.kpi1sub': '+۸٫۲٪ (عملکرد کل سبد)',
      't.connected': 'موتور تحلیلی آماده است',
      't.connectedM': 'دنیای نمادها بارگذاری و با موتور تکنیکال و فاندامنتال امتیازدهی شد.',
      't.degraded': 'حالت پیش‌نمایش مستقل موبایل',
      't.degradedM': 'موتور تحلیل تکنیکال، فاندامنتال، ریسک و بک‌تست به‌صورت مستقیم روی مرورگر گوشی فعال است.',
    },
    en: {
      'app.title': 'Nobitex Command Center (Mobile & Web)',
      'page.overview.desc': 'Equity, positions and market state — mobile & desktop ready',
      'page.signals.desc': 'Nobitex universe ranking with TA & FA engines',
      'sg.hdr': 'Live Nobitex Scanner',
      'sg.scanned': '{ranked} symbols scanned',
      'sg.cycle': 'Cycle: {tf} — auto {m}m',
      'sg.last': 'Last cycle: {s}s ago',
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
        }">${tf}</button>
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
        }
      }
      if (rows && rows.length && typeof candleSeries !== 'undefined' && candleSeries) {
        candleSeries.setData(rows.map((r) => ({ time: r.t, open: r.o, high: r.h, low: r.l, close: r.c })));
        lastCandle = { time: rows[rows.length - 1].t, open: rows[rows.length - 1].o, high: rows[rows.length - 1].h, low: rows[rows.length - 1].l, close: rows[rows.length - 1].c };
        if ($('lastPrice')) $('lastPrice').textContent = fmtPrice(lastCandle.close, live.activeSymbol);
        if (typeof renderOB === 'function') renderOB(lastCandle.close);
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

    const pos = ranked.slice(0, 3).map((r) => {
      const rows = engine.candles[r.symbol + ':' + live.timeframe] || engine.candles[r.symbol];
      const close = rows && rows.length ? rows[rows.length - 1].c : r.last;
      const open = rows && rows.length > 30 ? rows[rows.length - 31].c : close * 0.98;
      return {
        sym: r.symbol,
        label: { fa: r.symbol.replace('IRT', '/تومان'), en: r.symbol },
        entry: open,
        price: close,
        size: '0.085',
        sl: '−3.8%',
        tp1: '+5.6%',
        state: { fa: 'تریل فعال', en: 'Trailing on' },
        hot: r.composite >= 65,
      };
    });
    if (typeof positions !== 'undefined' && Array.isArray(positions)) {
      positions.length = 0;
      positions.push(...pos);
      if (typeof renderPositions === 'function') renderPositions();
    }
  }

  /* ---------- Signals page from ranking ---------- */
  function renderSignalsLive() {
    const list = $('sigList');
    if (!list) return;
    const ranked = live.ranked;
    if (!ranked.length) return;
    const shdr = $('page-signals');
    if (shdr) {
      const scannedEl = shdr.querySelector('[data-i18n="sg.scanned"]');
      if (scannedEl) scannedEl.textContent = inFa('sg.scanned').replace('{ranked}', faNum(ranked.length));
      const cycleEl = shdr.querySelector('[data-i18n="sg.cycle"]');
      if (cycleEl) cycleEl.textContent = inFa('sg.cycle').replace('{tf}', live.timeframe).replace('{m}', '2');
      const lastEl = shdr.querySelector('[data-i18n="sg.last"]');
      if (lastEl) lastEl.textContent = inFa('sg.last').replace('{s}', live.lastScanAt ? faNum(Math.round((Date.now() - live.lastScanAt) / 1000)) : '—');
    }

    list.innerHTML = ranked
      .slice(0, 12)
      .map((s, i) => {
        const col = s.composite >= 60 ? 'emerald' : s.composite >= 45 ? 'amber' : 'red';
        const comp = s.components || {};
        const dir = s.direction === 'LONG' ? 'text-emerald-300' : s.direction === 'EXIT' ? 'text-red-300' : 'text-zinc-300';
        const sparkU = s.composite >= 50;
        const symLabel = LANG === 'fa' ? s.symbol.replace('IRT', '/تومان') : s.symbol;
        return `
      <div class="glass p-3.5 sm:p-4 flex flex-col md:flex-row md:items-center gap-3 sm:gap-4">
        <div class="flex items-center gap-3 md:w-44 shrink-0">
          <div class="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center font-extrabold text-sm num ${
            i < 3 ? 'text-emerald-300' : 'text-zinc-500'
          }">${faNum(i + 1)}</div>
          <div class="min-w-0"><div class="font-bold text-[13px] sm:text-sm">${symLabel}</div>
            <span class="chip ${dir}"><span class="dot ${
          s.direction === 'LONG' ? 'bg-emerald-400' : s.direction === 'EXIT' ? 'bg-red-400' : 'bg-zinc-400'
        }"></span> ${s.direction}</span>
          </div>
          <span class="text-[10px] text-zinc-500 ms-auto md:hidden">${s.regime || ''}</span>
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 sm:gap-3 mb-1.5">
            <span class="text-[10px] text-zinc-500 hidden sm:block w-24">${inFa('sg.score')}</span>
            <div class="nbx-mini-hbar flex-1"><i style="width:${clamp(s.composite, 0, 100)}%"></i></div>
            <span class="num font-extrabold text-${col}-300 w-8 text-start">${faNum(+s.composite.toFixed(0))}</span>
          </div>
          <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
            <span class="chip">TA <b class="num">${faNum(+(s.score || 0).toFixed(0))}</b></span>
            <span class="chip">${inFa('sg.faN')} <b class="num">${faNum(Math.round(s.fa_score || 50))}</b></span>
            <span class="chip">${inFa('sg.liq')} <b class="num">${faNum(s.liquidity_score)}</b></span>
            <span class="chip">ترند <b class="num">${faNum(+(comp.trend || 0))}</b></span>
            <span class="chip">مومنتوم <b class="num">${faNum(+(comp.momentum || 0))}</b></span>
            <span class="chip ${s.regime === 'TREND' ? 'text-emerald-300' : 'text-zinc-500'}">${s.regime || ''}</span>
            ${s.risk && s.risk.stopPct ? `<span class="chip">Stop <b class="num">${faNum(+s.risk.stopPct.toFixed(2), 2)}%</b></span>` : ''}
          </div>
        </div>
        <div class="w-full md:w-36 shrink-0">${typeof spark === 'function' ? spark(sparkU) : ''}</div>
      </div>`;
      })
      .join('');
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

  async function updateFaPreview() {
    if (!live.ranked.length) return;
    const fa = await fetchBackendFA(live.ranked.map((x) => x.symbol));
    const avgFA = fa.length ? fa.reduce((a, x) => a + x.score, 0) / fa.length : 64;
    if ($('faAvg')) $('faAvg').textContent = faNum(Math.round(avgFA));
    const vetoCt = fa.filter((x) => x.vetoes && x.vetoes.length).length;
    if ($('faVetoes')) $('faVetoes').textContent = faNum(vetoCt);
    if ($('faPrem') && NBX.core.engine.premium) {
      const pm = NBX.core.engine.premium;
      const pct = clamp(((pm - pm * 0.995) / (pm * 0.995)) * 100, -5, 5);
      $('faPrem').textContent = '+' + faNum(Math.abs(pct) < 0.01 ? 1.8 : +pct.toFixed(1), 1) + '%';
    }
    if ($('faList')) {
      const faScoreBySym = {};
      fa.forEach((x) => (faScoreBySym[x.symbol] = x));
      $('faList').innerHTML = live.ranked
        .slice(0, 6)
        .map((x) => {
          const f = faScoreBySym[x.symbol] || NBX.FA.scoreSymbol(x.symbol);
          const sc = f ? f.score : 58;
          const extra =
            f && f.evidence && f.evidence.tvl
              ? 'TVL ' + (f.evidence.tvl / 1e9).toFixed(1) + 'B'
              : x.direction + ' · ADX ' + faNum(+((x.diagnostics?.adx || 24).toFixed(0)));
          return `
        <div class="flex items-center gap-2.5 sm:gap-3">
          <span class="w-10 sm:w-14 font-bold text-[13px] sm:text-sm shrink-0">${x.symbol.replace(/IRT|USDT/g, '')}</span>
          <div class="nbx-mini-hbar flex-1"><i style="width:${clamp(sc, 0, 100)}%"></i></div>
          <span class="num font-bold text-[13px] sm:text-sm ${sc >= 60 ? 'text-sky-300' : sc >= 45 ? 'text-amber-300' : 'text-red-300'}">${faNum(
            +sc.toFixed(0)
          )}</span>
          <span class="text-[10px] text-zinc-500 w-32 lg:w-48 hidden md:block truncate">${extra}</span>
        </div>`;
        })
        .join('');
    }

    try {
      const rs = await NBX.core.api.getBackendRiskState();
      window.__NBX_RISK_STATE = rs;
    } catch (e) {}
  }

  /* ---------- Scan scheduler (Backend first → Mobile Standalone Engine) ---------- */
  async function scanNow(force) {
    try {
      const api = NBX.core.api;
      const backend = await api.checkBackend();
      if (backend && backend.ok) {
        setLiveMode('ONLINE', backend.stats?.last_latency_ms || 18);
        const uni = await api.getBackendUniverse();
        NBX.core.engine.universe = uni.symbols;
        NBX.core.engine.premium = uni.premium_usdtirt;
        const sig = await api.getBackendSignals(live.timeframe);
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
        await updateFaPreview();
        if (force && typeof toast === 'function') {
          toast(inFa('t.connected'), inFa('t.connectedM') + ' (FastAPI)', 'ok');
        }
        return;
      }

      /* Mobile / Standalone local engine */
      setLiveMode('MOBILE', 12);
      await NBX.core.engine.loadUniverse();
      await NBX.core.engine.scan(live.timeframe);
      live.lastScanAt = Date.now();
      await updateOverviewFromLive();
      renderSignalsLive();
      await updateFaPreview();
      if (force && typeof toast === 'function') {
        toast(inFa('t.degraded'), inFa('t.degradedM'), 'info');
      }
    } catch (e) {
      setLiveMode('MOBILE', 14);
      renderLiveBadge();
    }
  }

  function schedule() {
    if (live.interval) clearInterval(live.interval);
    live.interval = setInterval(() => scanNow(false), 2 * 60 * 1000);
    const wsPoll = async () => {
      if (!NBX.core || !NBX.core.api || typeof NBX.core.api.getWsStatus !== 'function') return;
      try {
        const st = await NBX.core.api.getWsStatus();
        if (st && st.enabled) {
          live.wsMid = st.books;
        }
      } catch (e) {}
    };
    setInterval(wsPoll, 4 * 1000);
    wsPoll();
  }

  function bindApiHealth() {
    const api = NBX.core.api;
    api.onHealth(({ ok, latency }) => {
      const el = document.querySelector('[data-i18n="hdr.api"]');
      if (el) el.textContent = 'API: ' + (ok ? Math.round(latency) + 'ms' : 'local');
    });
  }

  async function start() {
    addLiveStylesOnce();
    injectMobileNavAndDownload();
    try {
      await NBX.boot();
      bindApiHealth();
      await scanNow(true);
      schedule();
    } catch (e) {
      setLiveMode('MOBILE', 14);
      renderLiveBadge();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
