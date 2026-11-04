/* Conditional price/premium alert management (ADR-0049).
 * No polling and no boot-time request: an eligible user must open or refresh
 * the dialog. Viewer/anonymous sessions never call the forbidden endpoints.
 * This module has no Paper/Live/order path and never invents unread state.
 */
(function () {
  'use strict';

  const TEXT = {
    fa: {
      title: 'هشدارهای قیمت و پرمیوم',
      subtitle: 'قواعد مشترک حساب؛ فقط Owner و Operator، بدون مسیر سفارش یا تغییر ریسک',
      close: 'بستن پنل هشدارها', refresh: 'به‌روزرسانی', refreshing: 'در حال خواندن قواعد و رویدادها…',
      scope: 'ارزیابی روی دادهٔ تازهٔ بازار انجام می‌شود؛ اولین مشاهده فقط baseline است و هشدارها هیچ سفارش Paper/Live ایجاد نمی‌کنند.',
      accessAnonymous: 'برای مدیریت هشدارها ابتدا با حساب Owner یا Operator وارد شوید. هیچ درخواستی به مسیرهای هشدار ارسال نشد.',
      accessViewer: 'نقش Viewer فقط‌خواندنی است و دسترسی به قواعد و رویدادهای هشدار ندارد. هیچ درخواستی به مسیرهای هشدار ارسال نشد.',
      newRule: 'قاعدهٔ تازه', metric: 'معیار', metricLast: 'آخرین قیمت بازار', metricPremium: 'پرمیوم USDTIRT (٪)',
      symbol: 'نماد', operator: 'شرط', above: 'عبور به بالای', below: 'عبور به پایین', threshold: 'آستانه (واحد همان بازار)',
      create: 'ساخت قاعده', creating: 'در حال ساخت…',
      formNote: 'برای پرمیوم، نماد USDTIRT و آستانه بین ۱۰۰- تا ۱۰۰ است. آستانهٔ قیمت باید مثبت باشد.',
      rules: 'قواعد', events: 'رویدادهای trigger', notLoaded: 'برای خواندن وضعیت تازه، پنل را باز یا به‌روزرسانی کنید.',
      noRules: 'هنوز قاعده‌ای ثبت نشده است.', noEvents: 'رویداد trigger ثبت‌شده‌ای وجود ندارد؛ این به معنای اثبات نبود نوسان نیست.',
      enabledBadge: '{n} قاعدهٔ فعال؛ شمارش رویداد خوانده‌نشده نیست',
      enabled: 'فعال', disabled: 'غیرفعال', enable: 'فعال‌کن', disable: 'غیرفعال‌کن',
      delete: 'حذف', confirmDelete: 'تأیید حذف', cancel: 'انصراف', deleteQuestion: 'قاعده حذف شود؟ تاریخچهٔ رویدادها باقی می‌ماند.',
      lastValue: 'آخرین مقدار', observedAt: 'زمان مشاهده', thresholdLabel: 'آستانه',
      status: 'وضعیت', arming: 'در انتظار baseline', armed: 'مسلح؛ شرط هنوز برقرار نیست',
      'condition-met': 'شرط برقرار؛ منتظر بازگشت و عبور تازه', triggered: 'آخرین عبور ثبت شد',
      'no-data': 'داده‌ای در دسترس نیست؛ disarm', stale: 'دادهٔ کهنه؛ disarm',
      'invalid-data': 'دادهٔ نامعتبر؛ disarm', unknown: 'نامشخص',
      previousValue: 'قبلی', value: 'جدید', source: 'منبع', historyRetained: 'قاعده حذف‌شده؛ snapshot تاریخی',
      deliverySent: 'تحویل Telegram تأیید شد', deliveryFailed: 'تحویل تأیید نشد', deliveryPending: 'نتیجهٔ تحویل ثبت نشده (pending)',
      deliveryNote: 'شکست تحویل retry خودکار ندارد. ثبت trigger و نتیجهٔ تحویل دو واقعیت جدا هستند و هیچ‌کدام سفارش ایجاد نمی‌کنند.',
      created: 'قاعده ساخته شد؛ اولین دادهٔ تازه فقط baseline می‌سازد.',
      enabledNow: 'قاعده فعال شد و برای baseline تازه disarm/arm شد.',
      disabledNow: 'قاعده غیرفعال شد و scheduler دیگر آن را ارزیابی نمی‌کند.',
      deleted: 'قاعده حذف شد؛ رویدادهای تاریخی حفظ شدند.',
      invalidInput: 'نماد، معیار یا آستانه معتبر نیست. ورودی را بررسی کنید.',
      loadError: 'خواندن هشدارها انجام نشد؛ دادهٔ قدیمی به‌عنوان وضعیت تازه نمایش داده نمی‌شود.',
      mutationError: 'عملیات انجام نشد. دسترسی، CSRF و قرارداد ورودی را بررسی کنید.',
      staleReply: 'پاسخ قدیمی پس از بستن پنل یا تغییر نقش نادیده گرفته شد.',
      network: 'خطای ارتباط', emptyList: '—'
    },
    en: {
      title: 'Price and premium alerts',
      subtitle: 'Account-shared rules; Owner/Operator only, with no order or risk-change path',
      close: 'Close alerts', refresh: 'Refresh', refreshing: 'Reading rules and events…',
      scope: 'Evaluation uses fresh market evidence. The first observation is only a baseline; alerts never place Paper/Live orders.',
      accessAnonymous: 'Sign in as Owner or Operator to manage alerts. No alert endpoint was requested.',
      accessViewer: 'Viewer is read-only and cannot read alert rules or events. No alert endpoint was requested.',
      newRule: 'New rule', metric: 'Metric', metricLast: 'Market last price', metricPremium: 'USDTIRT premium (%)',
      symbol: 'Symbol', operator: 'Condition', above: 'Cross above', below: 'Cross below', threshold: 'Threshold (native market units)',
      create: 'Create rule', creating: 'Creating…',
      formNote: 'Premium rules require USDTIRT and a -100…100 threshold. A last-price threshold must be positive.',
      rules: 'Rules', events: 'Trigger events', notLoaded: 'Open or refresh the dialog to read current state.',
      noRules: 'No rules have been created yet.', noEvents: 'No trigger event is stored; this does not prove there was no market movement.',
      enabledBadge: '{n} enabled rules; not an unread-event count',
      enabled: 'Enabled', disabled: 'Disabled', enable: 'Enable', disable: 'Disable',
      delete: 'Delete', confirmDelete: 'Confirm delete', cancel: 'Cancel', deleteQuestion: 'Delete this rule? Historical events are retained.',
      lastValue: 'Last value', observedAt: 'Observed', thresholdLabel: 'Threshold',
      status: 'Status', arming: 'Waiting for baseline', armed: 'Armed; condition is false',
      'condition-met': 'Condition met; waiting for reset and a fresh crossing', triggered: 'Latest crossing recorded',
      'no-data': 'No data; disarmed', stale: 'Stale data; disarmed',
      'invalid-data': 'Invalid data; disarmed', unknown: 'Unknown',
      previousValue: 'Previous', value: 'New', source: 'Source', historyRetained: 'Deleted rule; retained history',
      deliverySent: 'Telegram delivery confirmed', deliveryFailed: 'Delivery not confirmed', deliveryPending: 'Delivery outcome not recorded (pending)',
      deliveryNote: 'Failed delivery is not retried automatically. Trigger persistence and delivery evidence are separate facts; neither places an order.',
      created: 'Rule created; the first fresh observation only establishes a baseline.',
      enabledNow: 'Rule enabled and reset for a fresh baseline.',
      disabledNow: 'Rule disabled; the scheduler will not evaluate it.',
      deleted: 'Rule deleted; historical events were retained.',
      invalidInput: 'The symbol, metric, or threshold is invalid. Review the form.',
      loadError: 'Alerts could not be read; old data is not presented as current.',
      mutationError: 'The operation failed. Check access, CSRF, and the input contract.',
      staleReply: 'A late response was ignored after the dialog closed or the role changed.',
      network: 'Network error', emptyList: '—'
    }
  };

  const state = {
    user: null, identity: 'anonymous', epoch: 0, open: false,
    rules: [], events: [], loaded: false, loading: false, mutation: false,
    error: '', notice: '', confirmDelete: null,
  };
  let initialized = false;
  let inflight = null;
  let previousOverflow = '';
  let observer = null;

  const $ = id => document.getElementById(id);
  const lang = () => String(document.documentElement.lang || 'fa').toLowerCase().startsWith('en') ? 'en' : 'fa';
  function t(key, vars) {
    let value = (TEXT[lang()] && TEXT[lang()][key]) || TEXT.fa[key] || key;
    Object.entries(vars || {}).forEach(([name, replacement]) => {
      value = value.replace(new RegExp('\\{' + name + '\\}', 'g'), String(replacement));
    });
    return value;
  }
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
  }
  function role() {
    const auth = state.user || {};
    return String((auth.user && auth.user.role) || '').toLowerCase();
  }
  function canAccess() { return role() === 'owner' || role() === 'operator'; }
  function fmt(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString(lang() === 'fa' ? 'fa-IR' : 'en-US', { maximumFractionDigits: 8 });
  }
  function fmtTime(seconds) {
    const number = Number(seconds);
    if (!Number.isFinite(number) || number <= 0) return '—';
    return new Date(number * 1000).toLocaleString(lang() === 'fa' ? 'fa-IR' : 'en-US');
  }
  function metricValue(metric, value) {
    const rendered = fmt(value);
    if (rendered === '—') return rendered;
    return metric === 'premium_pct' ? rendered + (lang() === 'fa' ? '٪' : '%') : rendered;
  }
  function cookie(name) {
    try {
      const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
      return match ? decodeURIComponent(match[1]) : '';
    } catch (_) { return ''; }
  }
  function apiRoot() {
    return window.NBX && window.NBX.config && typeof window.NBX.config.pythonApi === 'string'
      ? window.NBX.config.pythonApi : '';
  }
  function boundedDetail(data) {
    if (!data || typeof data.detail !== 'string') return '';
    return data.detail.slice(0, 240);
  }

  async function call(path, method, body) {
    if (!canAccess()) return { ok: false, status: 403, skipped: true, data: {} };
    const verb = method || 'GET';
    const headers = { Accept: 'application/json' };
    if (verb !== 'GET') {
      if (verb !== 'DELETE') headers['Content-Type'] = 'application/json';
      const csrf = cookie('nbx_csrf');
      if (csrf) headers['X-CSRF-Token'] = csrf;
    }
    try {
      const response = await fetch(apiRoot() + path, {
        method: verb, headers, credentials: 'same-origin', cache: 'no-store',
        ...((verb === 'GET' || verb === 'DELETE') ? {} : { body: JSON.stringify(body || {}) }),
      });
      let data = {};
      try { data = await response.json(); } catch (_) { data = {}; }
      return { ok: response.ok, status: response.status, data: data || {} };
    } catch (_) {
      return { ok: false, status: 0, data: {} };
    }
  }

  function statusLabel(status) {
    const key = String(status || 'unknown');
    const known = ['arming', 'armed', 'condition-met', 'triggered', 'no-data', 'stale', 'invalid-data', 'disabled'];
    return known.includes(key) ? t(key) : (key || t('unknown'));
  }
  function emptyMessage(key) {
    return `<div class="rounded-lg border border-white/5 bg-white/[.02] px-3 py-3 text-[11px] text-zinc-500">${esc(t(key))}</div>`;
  }
  function renderRules() {
    const host = $('alertsRules');
    if (!host) return;
    if (!state.loaded) { host.innerHTML = ''; return; }
    if (!state.rules.length) { host.innerHTML = emptyMessage('noRules'); return; }
    host.innerHTML = state.rules.map(rule => {
      const id = Number(rule.id);
      if (!Number.isInteger(id) || id <= 0) return '';
      const enabled = rule.enabled === true;
      const busy = state.mutation ? 'disabled aria-disabled="true"' : '';
      const action = state.confirmDelete === id
        ? `<span class="text-[10px] text-red-200">${esc(t('deleteQuestion'))}</span><button type="button" data-alert-action="confirm-delete" data-id="${id}" class="px-2 py-1 rounded-lg text-[10px] border border-red-300/40 text-red-300" ${busy}>${esc(t('confirmDelete'))}</button><button type="button" data-alert-action="cancel-delete" data-id="${id}" class="px-2 py-1 rounded-lg text-[10px] border border-white/10 text-zinc-400" ${busy}>${esc(t('cancel'))}</button>`
        : `<button type="button" data-alert-action="toggle" data-id="${id}" class="px-2 py-1 rounded-lg text-[10px] border border-white/10 text-zinc-300" ${busy}>${esc(t(enabled ? 'disable' : 'enable'))}</button><button type="button" data-alert-action="delete" data-id="${id}" class="px-2 py-1 rounded-lg text-[10px] border border-red-300/30 text-red-300" ${busy}>${esc(t('delete'))}</button>`;
      return `<article class="rounded-xl border border-white/10 bg-white/[.025] p-3" data-rule-id="${id}">
        <div class="flex flex-wrap items-center gap-2">
          <b class="num text-[12px] text-zinc-100" dir="ltr">${esc(rule.symbol)}</b>
          <span class="text-[10px] text-zinc-400">${esc(t(rule.metric === 'premium_pct' ? 'metricPremium' : 'metricLast'))}</span>
          <span class="chip text-[9px] ${enabled ? 'text-emerald-300 border-emerald-300/30' : 'text-zinc-400 border-white/10'}">${esc(t(enabled ? 'enabled' : 'disabled'))}</span>
          <span class="chip text-[9px] text-sky-300 border-sky-400/20">${esc(statusLabel(rule.last_status))}</span>
        </div>
        <div class="num text-[11px] text-zinc-300 mt-2" dir="ltr">${esc(t(rule.operator === 'below' ? 'below' : 'above'))} ${esc(metricValue(rule.metric, rule.threshold))}</div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-1.5 mt-2 text-[10px] text-zinc-500">
          <span>${esc(t('lastValue'))}: <b class="num text-zinc-300" dir="ltr">${esc(metricValue(rule.metric, rule.last_value))}</b></span>
          <span>${esc(t('observedAt'))}: <b class="num text-zinc-300">${esc(fmtTime(rule.last_observed_at))}</b></span>
        </div>
        <div class="flex flex-wrap items-center gap-2 mt-3">${action}</div>
      </article>`;
    }).join('');
  }
  function renderEvents() {
    const host = $('alertsEvents');
    if (!host) return;
    if (!state.loaded) { host.innerHTML = ''; return; }
    if (!state.events.length) { host.innerHTML = emptyMessage('noEvents'); return; }
    host.innerHTML = state.events.map(event => {
      const sent = event.delivery_sent;
      const deliveryClass = sent === true ? 'text-emerald-300 border-emerald-300/30' : sent === false ? 'text-red-300 border-red-300/30' : 'text-amber-300 border-amber-300/30';
      const deliveryText = sent === true ? t('deliverySent') : sent === false ? t('deliveryFailed') : t('deliveryPending');
      const reason = sent === false && event.delivery_reason ? ` · ${esc(event.delivery_reason)}` : '';
      return `<article class="rounded-xl border border-white/10 bg-white/[.025] p-3" data-event-id="${Number(event.id) || 0}">
        <div class="flex flex-wrap items-center gap-2">
          <b class="num text-[12px]" dir="ltr">${esc(event.symbol)}</b>
          <span class="text-[10px] text-zinc-400">${esc(t(event.metric === 'premium_pct' ? 'metricPremium' : 'metricLast'))}</span>
          <span class="chip text-[9px] ${deliveryClass}">${esc(deliveryText)}${reason}</span>
          ${event.rule_id == null ? `<span class="chip text-[9px] text-zinc-400 border-white/10">${esc(t('historyRetained'))}</span>` : ''}
        </div>
        <div class="num text-[11px] text-zinc-300 mt-2" dir="ltr">${esc(metricValue(event.metric, event.previous_value))} → <b>${esc(metricValue(event.metric, event.value))}</b> · ${esc(t(event.operator === 'below' ? 'below' : 'above'))} ${esc(metricValue(event.metric, event.threshold))}</div>
        <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[10px] text-zinc-500"><span>${esc(t('observedAt'))}: <b class="num">${esc(fmtTime(event.observed_at))}</b></span><span>${esc(t('source'))}: <b class="num">${esc(event.source || '—')}</b></span></div>
      </article>`;
    }).join('');
  }

  function render() {
    const modal = $('alertsModal');
    if (!modal) return;
    modal.classList.toggle('hidden', !state.open);
    modal.setAttribute('aria-hidden', state.open ? 'false' : 'true');
    modal.querySelectorAll('[data-alert-i18n]').forEach(el => { el.textContent = t(el.dataset.alertI18n); });
    modal.querySelectorAll('[data-alert-i18n-aria]').forEach(el => el.setAttribute('aria-label', t(el.dataset.alertI18nAria)));
    const bell = $('alertsBtn');
    if (bell) bell.setAttribute('aria-label', t('title'));
    const allowed = canAccess();
    const access = $('alertsAccess');
    if (access) access.classList.toggle('hidden', allowed);
    const accessText = $('alertsAccessText');
    if (accessText && !allowed) accessText.textContent = t(state.user && state.user.user ? 'accessViewer' : 'accessAnonymous');
    const content = $('alertsContent');
    if (content) content.classList.toggle('hidden', !allowed);
    const refresh = $('alertsRefresh');
    if (refresh) { refresh.classList.toggle('hidden', !allowed); refresh.disabled = state.loading || state.mutation; }
    const submit = $('alertSubmit');
    if (submit) { submit.disabled = state.loading || state.mutation; submit.textContent = t('create'); }

    const message = state.error || state.notice || (state.loading ? t('refreshing') : (allowed && !state.loaded ? t('notLoaded') : ''));
    const status = $('alertsStatus');
    if (status) {
      status.classList.toggle('hidden', !message);
      status.className = `${message ? '' : 'hidden '}text-[11px] leading-5 rounded-lg border px-3 py-2 ${state.error ? 'border-red-300/30 bg-red-400/5 text-red-200' : 'border-sky-400/20 bg-sky-400/5 text-sky-100/80'}`;
      status.textContent = message;
    }
    const rulesCount = $('alertsRulesCount');
    if (rulesCount) rulesCount.textContent = state.loaded && allowed ? fmt(state.rules.length) : '—';
    const eventsCount = $('alertsEventsCount');
    if (eventsCount) eventsCount.textContent = state.loaded && allowed ? fmt(state.events.length) : '—';
    const badge = $('alertsBadge');
    if (badge) {
      const enabled = allowed && state.loaded ? state.rules.filter(rule => rule.enabled === true).length : 0;
      badge.classList.toggle('hidden', enabled === 0);
      badge.textContent = enabled ? fmt(enabled) : '';
      badge.setAttribute('aria-hidden', enabled ? 'false' : 'true');
      badge.title = enabled ? t('enabledBadge', { n: fmt(enabled) }) : '';
    }
    renderRules(); renderEvents();
    if (window.refreshIcons) { try { window.refreshIcons(); } catch (_) {} }
  }

  function invalidate() {
    state.epoch += 1;
    state.rules = []; state.events = []; state.loaded = false; state.loading = false;
    state.mutation = false;
    state.error = ''; state.notice = ''; state.confirmDelete = null;
    inflight = null;
  }
  function setUser(auth) {
    const user = auth && auth.user;
    const identity = user
      ? JSON.stringify([user.id || '', user.username || '', user.role || ''])
      : (auth && auth.token ? 'token' : 'anonymous');
    if (identity !== state.identity) invalidate();
    state.identity = identity;
    state.user = auth || null;
    render();
  }
  async function refresh() {
    if (!canAccess()) {
      state.error = ''; state.notice = ''; render();
      return { ok: false, skipped: true, status: 403 };
    }
    if (inflight) return inflight;
    const epoch = state.epoch;
    state.loading = true; state.loaded = false;
    state.rules = []; state.events = [];
    state.error = ''; state.notice = ''; render();
    const run = (async () => {
      const [rules, events] = await Promise.all([
        call('/alerts/rules?limit=200', 'GET'),
        call('/alerts/events?limit=50', 'GET'),
      ]);
      if (epoch !== state.epoch) return { ok: false, stale: true };
      if (!rules.ok || !events.ok || !Array.isArray(rules.data.rules) || !Array.isArray(events.data.events)) {
        state.rules = []; state.events = []; state.loaded = false;
        const detail = boundedDetail(rules.data) || boundedDetail(events.data);
        state.error = t('loadError') + (detail ? ` ${detail}` : '');
        return { ok: false, status: !rules.ok ? rules.status : events.status };
      }
      state.rules = rules.data.rules;
      state.events = events.data.events;
      state.loaded = true;
      return { ok: true };
    })();
    inflight = run;
    try { return await run; }
    finally {
      if (inflight === run) inflight = null;
      if (epoch === state.epoch) { state.loading = false; render(); }
    }
  }
  async function mutate(path, method, body) {
    if (!canAccess()) return { ok: false, skipped: true };
    const epoch = state.epoch;
    state.mutation = true; state.error = ''; state.notice = ''; render();
    const result = await call(path, method, body);
    if (epoch !== state.epoch) return { ok: false, stale: true };
    state.mutation = false;
    if (!result.ok) {
      const detail = boundedDetail(result.data);
      state.error = t('mutationError') + (detail ? ` ${detail}` : '');
      render();
    }
    return result;
  }
  async function afterMutation(result, noticeKey) {
    if (!result.ok) return result;
    const refreshed = await refresh();
    if (refreshed.ok) { state.notice = t(noticeKey); render(); }
    return refreshed;
  }
  function open() {
    if (!state.open) previousOverflow = document.body.style.overflow;
    state.open = true;
    document.body.style.overflow = 'hidden';
    render();
    const close = $('alertsClose'); if (close) close.focus();
    return canAccess() ? refresh() : Promise.resolve({ ok: false, skipped: true, status: 403 });
  }
  function close() {
    if (!state.open) return;
    state.open = false;
    // Reject late replies without discarding a successfully read enabled-rule
    // count. The next open/refresh clears the cache before presenting new data.
    state.epoch += 1;
    state.loading = false; state.mutation = false;
    state.error = ''; state.notice = ''; state.confirmDelete = null;
    inflight = null;
    document.body.style.overflow = previousOverflow;
    render();
    const bell = $('alertsBtn'); if (bell) bell.focus();
  }
  function syncMetric() {
    const metric = $('alertMetric'), symbol = $('alertSymbol');
    if (!metric || !symbol) return;
    const premium = metric.value === 'premium_pct';
    symbol.readOnly = premium;
    if (premium) symbol.value = 'USDTIRT';
    symbol.setAttribute('aria-readonly', premium ? 'true' : 'false');
  }
  async function createRule(event) {
    event.preventDefault();
    if (!canAccess() || state.mutation) return;
    const symbol = String(($('alertSymbol') || {}).value || '').trim().toUpperCase();
    const metric = String(($('alertMetric') || {}).value || 'last');
    const operator = String(($('alertOperator') || {}).value || 'above');
    const threshold = Number(($('alertThreshold') || {}).value);
    const valid = /^[A-Z0-9]{2,24}$/.test(symbol)
      && (metric === 'last' || metric === 'premium_pct')
      && (operator === 'above' || operator === 'below')
      && Number.isFinite(threshold)
      && (metric === 'last' ? threshold > 0 : Math.abs(threshold) <= 100)
      && (metric !== 'premium_pct' || symbol === 'USDTIRT');
    if (!valid) { state.error = t('invalidInput'); state.notice = ''; render(); return; }
    const result = await mutate('/alerts/rules', 'POST', { symbol, metric, operator, threshold });
    if (result.ok) { $('alertThreshold').value = ''; await afterMutation(result, 'created'); }
  }
  async function ruleAction(action, id) {
    const rule = state.rules.find(item => Number(item.id) === id);
    if (action === 'delete') { state.confirmDelete = id; render(); return; }
    if (action === 'cancel-delete') { state.confirmDelete = null; render(); return; }
    if (state.mutation) return;
    if (action === 'toggle' && rule) {
      const enabled = !rule.enabled;
      const result = await mutate(`/alerts/rules/${id}`, 'PATCH', { enabled });
      await afterMutation(result, enabled ? 'enabledNow' : 'disabledNow');
    } else if (action === 'confirm-delete' && rule) {
      const result = await mutate(`/alerts/rules/${id}`, 'DELETE', null);
      state.confirmDelete = null;
      await afterMutation(result, 'deleted');
    }
  }
  function trapFocus(event) {
    if (!state.open || event.key !== 'Tab') return;
    const dialog = $('alertsDialog'); if (!dialog) return;
    const items = Array.from(dialog.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'))
      .filter(el => !el.closest('.hidden'));
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  function init() {
    if (initialized || !$('alertsModal')) return;
    initialized = true;
    const bell = $('alertsBtn'); if (bell) bell.addEventListener('click', open);
    const closeButton = $('alertsClose'); if (closeButton) closeButton.addEventListener('click', close);
    const backdrop = $('alertsBackdrop'); if (backdrop) backdrop.addEventListener('click', close);
    const refreshButton = $('alertsRefresh'); if (refreshButton) refreshButton.addEventListener('click', refresh);
    const form = $('alertRuleForm'); if (form) form.addEventListener('submit', createRule);
    const metric = $('alertMetric'); if (metric) metric.addEventListener('change', syncMetric);
    const rules = $('alertsRules');
    if (rules) rules.addEventListener('click', event => {
      const button = event.target.closest('button[data-alert-action]');
      if (!button) return;
      ruleAction(button.dataset.alertAction, Number(button.dataset.id));
    });
    document.addEventListener('keydown', event => {
      if (state.open && event.key === 'Escape') { event.preventDefault(); close(); }
      else trapFocus(event);
    });
    if (window.MutationObserver) {
      observer = new MutationObserver(render);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
    }
    syncMetric(); render();
  }

  window.NBXAlerts = { init, setUser, open, close, refresh, render };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
