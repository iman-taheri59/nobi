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
  const numLocale = () => (typeof window.numLocale === 'function'
    ? window.numLocale()
    : (LANG === 'fa' ? 'fa-IR' : 'en-US'));
  const faNum = (n, d = 0) =>
    Number(n || 0).toLocaleString(numLocale(), {
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
    latency: null,   // no invented latency: a number appears only from a real call
    ranked: [],
    activeSymbol: 'BTCIRT',
    candleSeriesData: null,
    source: null,
    upstream: null,
    risk: null,
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
    const wsK = live.wsMid ? Object.keys(live.wsMid).length : 0;
    el.innerHTML = `
      <span class="dot" style="background:${color};box-shadow:0 0 0 4px ${color}22"></span>
      <button id="liveBadgeBtn" style="font-family:inherit;color:inherit;background:none;border:none;cursor:pointer;display:flex;align-items:center;gap:8px">
        <span class="num">${text}</span>
        <span style="color:#a1a1aa">·</span>
        <span class="num">${live.latency !== null && live.latency !== undefined ? Math.round(live.latency) + 'ms' : '—'}</span>
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
          [LANG === 'fa' ? 'حالت اجرا' : 'Mode', `${m} · ${live.latency !== null && live.latency !== undefined ? Math.round(live.latency) + 'ms' : '—'}`],
          [LANG === 'fa' ? 'نمادهای فعال' : 'Active Symbols', live.ranked.length ? faNum(live.ranked.length) : '—'],
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
      'sg.faCoverage': 'بنیادی زنده: {n} از {t}',
      'sg.faScored': 'بنیادی: {n} از {t}',
      'sg.faCoverageTitle': 'محاسبه‌شده: {s} · از پروفایل ثابت: {f} · ناموفق: {x}',
      'sg.basisNoFa': 'بدون بنیادی',
      'sg.basisTitle': 'امتیاز ترکیبی فقط از TA و نقدینگی محاسبه شده است',
      'sg.cycle': 'چرخه: {tf} — auto {m}m',
      'sg.last': 'آخرین چرخه: {s} ثانیه پیش',
      'ov.kpi1sub': 'نسبت به سرمایه اولیه',
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
      'sg.faCoverage': 'Live FA: {n} of {t}',
      'sg.faScored': 'FA: {n} of {t}',
      'sg.faCoverageTitle': 'Scored: {s} · from static profile: {f} · failed: {x}',
      'sg.basisNoFa': 'no FA',
      'sg.basisTitle': 'Composite computed from TA and liquidity only',
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
    // The overview header used to claim "live" on every load, including when
    // the exchange was down. Keep the two statements from disagreeing.
    // The latency chip in the header used to print a hard-coded "API: 124ms".
    const api = document.getElementById('apiChip');
    if (api) {
      const ok = m === 'ONLINE' && live.latency !== null && live.latency !== undefined;
      api.className = 'hidden 2xl:inline-flex chip num shrink-0 ' +
        (ok ? 'text-emerald-300 border-emerald-300/30' : 'text-red-300 border-red-300/40');
      api.innerHTML = `<i data-lucide="${ok ? 'wifi' : 'wifi-off'}" class="w-3.5 h-3.5"></i> `
        + `<span>${ok ? 'API: ' + Math.round(live.latency) + 'ms' : (LANG === 'fa' ? 'API: بدون پاسخ' : 'API: no reply')}</span>`;
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

  async function refreshPaperState() {
    const api = NBX.core.api;
    let posRes = null;
    let metRes = null;
    let riskRes = null;
    try { posRes = await api.getPaperPositions(); } catch (e) {}
    try { metRes = await api.getPaperMetrics(); } catch (e) {}
    try { riskRes = await api.getBackendRiskState(); } catch (e) {}

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
          size: String(+(p.filled_qty || p.qty || 0).toFixed(4)),
          sl: '\u2212' + faNum(p.stop_pct || 0, 1) + '%',
          tp1: '+' + faNum(p.tp1 && p.entry_price ? ((p.tp1 - p.entry_price) / p.entry_price) * 100 : 0, 1) + '%',
          state: {
            fa: p.state === 'PARTIAL' ? 'TP1 زده شد' : 'باز',
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
    const eq = m && Number.isFinite(Number(m.equity)) ? Number(m.equity) : null;
    const startEq = m && Number.isFinite(Number(m.start_equity)) ? Number(m.start_equity) : null;
    const pct = eq !== null && startEq ? ((eq - startEq) / startEq) * 100 : null;

    if ($('kEquity')) $('kEquity').textContent = eq !== null ? faNum(eq, 0) : '\u2014';
    if ($('kToday')) {
      $('kToday').textContent = pct !== null ? (pct >= 0 ? '+' : '') + faNum(pct, 2) + '%' : '\u2014';
      $('kToday').className = 'num kpi-num ' + (pct !== null && pct < 0 ? 'text-red-400' : 'text-emerald-400');
    }
    if ($('kTodayV')) {
      $('kTodayV').textContent = eq !== null && startEq !== null ? faNum(eq - startEq, 0) + ' USD' : '';
    }
    if ($('kRisk')) $('kRisk').textContent = rs ? faNum(rs.openHeat || 0, 2) + '%' : '\u2014';

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

    // Risk caps / kill switches come from /api/risk/state, not demo arrays.
    if (rs && typeof caps !== 'undefined' && Array.isArray(caps)) {
      const c = rs.caps || {};
      const openCount = list.length;
      const cashPct = m && Number(m.equity) ? (Number(m.cash) / Number(m.equity)) * 100 : null;
      const rows = [
        {
          name: { fa: 'پوزیشن‌های همزمان', en: 'Concurrent positions' },
          val: { fa: `${faNum(openCount)} / ${faNum(c.maxConcurrent || 0)}`, en: `${openCount} / ${c.maxConcurrent || 0}` },
          pct: c.maxConcurrent ? (openCount / c.maxConcurrent) * 100 : 0,
          tone: 'sky',
        },
        {
          name: { fa: 'دمای کل سبد', en: 'Portfolio heat' },
          val: { fa: `${faNum(rs.openHeat || 0, 2)}٪ / ${faNum(c.maxHeatPct || 0)}٪`, en: `${rs.openHeat || 0}% / ${c.maxHeatPct || 0}%` },
          pct: c.maxHeatPct ? ((rs.openHeat || 0) / c.maxHeatPct) * 100 : 0,
          tone: 'amber',
        },
        {
          name: { fa: 'سقف تک‌دارایی', en: 'Single-asset cap' },
          val: { fa: `${faNum(c.maxSingleAssetPct || 0)}٪`, en: `${c.maxSingleAssetPct || 0}%` },
          pct: Math.min(100, c.maxSingleAssetPct || 0),
          tone: 'indigo',
        },
        {
          name: { fa: 'بافر نقد', en: 'Cash buffer' },
          val: {
            fa: cashPct !== null ? `${faNum(cashPct, 1)}٪ (حداقل ${faNum(c.minCashPct || 0)}٪)` : '—',
            en: cashPct !== null ? `${cashPct.toFixed(1)}% (min ${c.minCashPct || 0}%)` : '—',
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
          v: { fa: `${faNum(rs.dailyPnl || 0, 2)}٪ / −۳٪`, en: `${rs.dailyPnl || 0}% / -3%` },
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
    set('[data-i18n="sg.cycle"]', inFa('sg.cycle').replace('{tf}', live.timeframe).replace('{m}', '2'), 'chip');
    set('[data-i18n="sg.last"]',
        inFa('sg.last').replace('{s}', live.lastScanAt ? faNum(Math.round((Date.now() - live.lastScanAt) / 1000)) : '—'),
        'text-[10px] sm:text-[11px] text-zinc-500 self-center');
  }
  window.syncSignalsHeader = syncSignalsHeader;

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
      if (cycleEl) cycleEl.textContent = inFa('sg.cycle').replace('{tf}', live.timeframe).replace('{m}', '2');
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

    list.innerHTML = ranked
      .slice(0, 12)
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
            ${s.composite_basis && s.composite_basis !== 'ta+fa+liq'
              ? `<span class="chip text-[9px] text-amber-300 border-amber-300/30" title="${inFa('sg.basisTitle')}">${inFa('sg.basisNoFa')}</span>`
              : ''}
          </div>
          <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
            <span class="chip">TA <b class="num">${faNum(+(s.score || 0).toFixed(0))}</b></span>
            <span class="chip${s.fa_score == null ? ' text-zinc-500' : ''}" title="${s.composite_basis || ''}">${inFa('sg.faN')} <b class="num">${s.fa_score == null ? '—' : faNum(Math.round(s.fa_score))}</b></span>
            <span class="chip">${inFa('sg.liq')} <b class="num">${faNum(s.liquidity_score)}</b></span>
            <span class="chip">ترند <b class="num">${faNum(+(comp.trend || 0))}</b></span>
            <span class="chip">مومنتوم <b class="num">${faNum(+(comp.momentum || 0))}</b></span>
            <span class="chip ${s.regime === 'TREND' ? 'text-emerald-300' : 'text-zinc-500'}">${s.regime || ''}</span>
            ${s.risk && s.risk.stopPct ? `<span class="chip">Stop <b class="num">${faNum(+s.risk.stopPct.toFixed(2), 2)}%</b></span>` : ''}
          </div>
        </div>
        <div class="w-full md:w-48 lg:w-60 xl:w-72 2xl:w-80 shrink-0">
          ${typeof spark === 'function' ? spark(sparkU, s.symbol, {big: true}) : ''}
          <div class="text-[9px] text-zinc-600 text-center mt-0.5">${t('sg.expand')}</div>
        </div>
      </div>`;
      })
      .join('');
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
    const vetoCt = fa.filter((x) => x.vetoes && x.vetoes.length).length;
    if ($('faVetoes')) $('faVetoes').textContent = fa.length ? faNum(vetoCt) : '—';
    if ($('faPrem')) {
      const pp = NBX.core.engine.premium_pct;
      $('faPrem').textContent =
        typeof pp === 'number' && Number.isFinite(pp)
          ? (pp >= 0 ? '+' : '') + faNum(pp, 2) + '%'
          : '—';
    }
    if ($('faList')) {
      const faScoreBySym = {};
      fa.forEach((x) => (faScoreBySym[x.symbol] = x));
      $('faList').innerHTML = live.ranked
        .slice(0, 6)
        .map((x) => {
          const f = faScoreBySym[x.symbol] || NBX.FA.scoreSymbol(x.symbol);
          const sc = f && typeof f.score === 'number' ? f.score : null;
          if (sc === null) {
            return `<div class="flex items-center gap-2.5 sm:gap-3">
              <span class="w-10 sm:w-14 font-bold text-[13px] sm:text-sm shrink-0">${x.symbol.replace(/IRT|USDT/g, '')}</span>
              <span class="flex-1 text-[10px] text-red-200/80">${LANG === 'fa'
                ? 'دادهٔ فاندامنتال در دسترس نیست — سرور تحلیل پاسخ نداد'
                : 'fundamentals unavailable — the analysis server did not answer'}</span>
            </div>`;
          }
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
    // A slow server must not leave the panel blank.
    try { renderSignalsLive(); } catch (e) {}
    try {
      const api = NBX.core.api;
      const backend = await api.checkBackend();
      if (backend && backend.backend) {
        const upstreamOk = !!(backend.upstream && backend.upstream.ok);
        setLiveMode(upstreamOk ? 'ONLINE' : 'DEGRADED', backend.upstream?.latency_ms ?? backend.stats?.last_latency_ms ?? null);
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
        prefetchSparkCandles((sig.scored || []).slice(0, 12).map((s) => s.symbol), live.timeframe);
        await updateFaPreview();
        if (force && typeof toast === 'function') {
          if (upstreamOk) {
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
      setLiveMode('MOBILE', null);
      renderLiveBadge();
    }
  }

  window.NBX_APP = {
    setScanInterval(sec) {
      if (!Number.isFinite(Number(sec))) return null;
      schedule();
      return scanInterval();
    },
    scanNow: () => scanNow(true),
    ensureSparks: () =>
      prefetchSparkCandles((live.ranked || []).slice(0, 12).map((s) => s.symbol), live.timeframe),
    get intervalSec() { return scanInterval(); },
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
