# بازبینی‌ها — فهرست و وضعیت یافته‌ها

[English](README.en.md) · **فارسی**

**آخرین به‌روزرسانی:** ۲۰۲۶-۱۱-۰۵ — کد: `main` پس از `bc11fda`؛ production: `capoy.ir` = 2.5.0 (`dcac952`)، در soak پایداری.

قاعدهٔ این پوشه: هر بازبینی **عکس لحظه‌ای** است و بازنویسی نمی‌شود. تنها سند
زنده همین فایل است: می‌گوید هر یافته امروز کجاست و با کدام commit/تگ بسته شد.
اگر چیزی اینجا با متن یک بازبینی قدیمی نمی‌خواند، این فایل درست است و آن
بازبینی تاریخ دارد.

## فهرست به ترتیب زمان

| تاریخ | فایل | چیست | مبنای کد |
|---|---|---|---|
| ۲۰۲۶-۱۰-۱۹ | [`2026-10-19-project-review-fa.md`](2026-10-19-project-review-fa.md) | بازبینی کامل پروژه (فاز ۱) | ۱٫x |
| ۲۰۲۶-۱۰-۲۰ | `2026-10-20-*.md` (۱۰ فایل) + `probe-*.md` + `*.png` | بازبینی‌های تک‌موضوعی: مسیر زنده، انطباق API، بروکر JS، صف، what-if، WS، exec-mode، A3/A6/A7 | ۱٫x |
| ۲۰۲۶-۱۰-۲۱ | [`2026-10-21-shared-constants-fa.md`](2026-10-21-shared-constants-fa.md) | ثابت‌های مشترک کارمزد/سرمایه | ۱٫x |
| ۲۰۲۶-۱۱-۰۲ | [`2026-11-02-external-audit-report.en.md`](2026-11-02-external-audit-report.en.md) | **ورودی بیرونی ۱** — audit فنی/کمی (۸۹۰ خط) | 2.2.1/2.2.2 |
| ۲۰۲۶-۱۱-۰۲ | [`2026-11-02-external-analysis.en.md`](2026-11-02-external-analysis.en.md) | **ورودی بیرونی ۲** — تحلیل research parity (۴۸۸ خط) | 2.2.2 |
| ۲۰۲۶-۱۱-۰۳ | [`2026-11-03-comprehensive-audit.fa.md`](2026-11-03-comprehensive-audit.fa.md) · [en](2026-11-03-comprehensive-audit.en.md) | بازبینی جامع مستقل: فرمول‌های واقعی TA/FA، ریسک، IC، اولویت‌بندی + الحاقیهٔ remediation | `2b5ea13` |
| ۲۰۲۶-۱۱-۰۴ | [`2026-11-04-review-of-reviews-and-capoy.fa.md`](2026-11-04-review-of-reviews-and-capoy.fa.md) · [en](2026-11-04-review-of-reviews-and-capoy.en.md) | بازبینیِ دو ورودی بیرونی + مشاهدهٔ production + وضعیت remediation (بخش ۹) | `2b5ea13` → `66e7575` |
| ۲۰۲۶-۱۱-۰۴ | [`probes/independent-probes-2026-11-04.js`](probes/independent-probes-2026-11-04.js) | probe مستقل JS که اعداد بازبینی بالا را تولید کرد | — |
| ۲۰۲۶-۱۱-۰۵ | [`../ops/fa/soak-2026-11.md`](../ops/fa/soak-2026-11.md) | دفتر وقایع soak پایداری production (پیش‌شرط‌ها، baseline، چک روزانه) | 2.5.0 |

## ماتریس یافته‌ها → وضعیت

شماره‌ها به بخش‌های `2026-11-04-review-of-reviews-and-capoy` ارجاع می‌دهند
(که خود، یافته‌های دو ورودی بیرونی و بازبینی جامع را جمع کرده است).

### P0 — پیش از هر سفارش واقعی

| یافته | وضعیت | شاهد |
|---|---|---|
| ۲.۳ preflight سفارش live از حساب Paper می‌خواند | ✅ بسته | `live_api._LIVE_PREFLIGHT` جدا؛ بدون snapshot واقعی 503 fail-closed؛ v2.3.x |
| ۲.۴ dry-run می‌تواند promotion evidence را کامل کند | ✅ بسته | `live_execution.promotion_evidence` فقط `accepted/reconciled/canceled`؛ روی production `samples=0` |
| اتصال evidence به config/dataset | ✅ بسته | `dataset_hash`, engine identity در journal/backtest |
| بررسی read-only deployment پیش از enable | ✅ بسته | ۲۰۲۶-۱۱-۰۵: `mode=disabled, placement_enabled=false` |

### P1 — پیش از اعتماد به نتایج research

| یافته | وضعیت | شاهد |
|---|---|---|
| ۲.۱ سقف ۵۰۰ کندل، بدون pagination | ✅ بسته | `nobitex_client` با `page`؛ dedup/sort/trim |
| ۲.۱ retention ۷روزه → pending بی‌نهایت | ✅ بسته (صریح) | journal: `outside-retention` / `not-yet-open` / `gap`؛ H3 روی 15m عمداً خالی می‌ماند |
| ۲.۲ دو تبدیل آستانهٔ adaptive | ✅ بسته | `adaptive.scale_threshold` مشترک با `adaptive_evidence` |
| ۲.۵ gap از stop با قیمت stop پر می‌شد | ✅ بسته | gap با open قیمت‌گذاری می‌شود؛ TP1+stop هم‌کندل `ambiguous` |
| ۲.۶ PF=2.4 ثابت در JS؛ TP2 در JS نبود | ✅ بسته | `nbx-core.js` cap 99.99؛ TP1/TP2 هم‌قرارداد |
| ۲.۷ H1/H2 روی D یکی می‌شدند | ✅ بسته | `journal.horizon_supported` — افق پشتیبانی‌نشده غایب، نه تکراری |
| ۲.۸ وقفهٔ کندل کنترل نمی‌شد | ✅ بسته | `portfolio.py` جفت‌به‌جفت روی timestamp مشترک؛ gap صریح |
| ۲.۹ کارمزد ۰٫۲٪ ≠ تعرفهٔ پایه | ◐ **نیمه** | `fee_pct` قابل تنظیم و در hash؛ `maker_taker_split=False`, `account_tier_verified=False` — نیازمند GET حساب واقعی |
| ۸.۱ GETهای Paper با side effect | ✅ بسته | GETهای Viewer خالص |
| ۸.۲ ابهام post-TP1 خوش‌بینانه | ✅ بسته | حل محافظه‌کارانه + پرچم `ambiguous` |
| ۴.۲ انتخاب grid از OOS (flattering) | ◐ **نیمه** | انتخاب از IS؛ **holdout منجمد جدا** هنوز نیست |
| ۹.۴ auto روی cache عمومی/TF دیگر | ✅ بسته | `66e7575` |
| ۹.۴ `status:"ok"` سفارش ردشده را accepted می‌کرد | ✅ بسته | `66e7575`؛ `order-rejected` خارج از evidence |
| ۹.۴ علامت cash/equity در short | ✅ بسته | `66e7575` |

### P2 — Observability و deployment

| یافته | وضعیت | شاهد |
|---|---|---|
| تفکیک process / probe / freshness / scan | ✅ بسته | `health.upstream{age_s,stale,refresh_scheduled}` + `scan_loop.last_freshness` |
| commit/build id در health | ✅ بسته در کد، **منتظر deploy** | `build_info.py`, `bc11fda`؛ روی production پس از soak |
| universe پس از login / 500 گذرا | ✅ بسته | تکرار نشد؛ ۲۰۲۶-۱۱-۰۵ با session Operator 200 |
| pending/expired صریح در journal | ✅ بسته | همان ۲.۱ |

### محدودیت‌های ۹.۳ (اثبات‌نشده‌ها — با کد بسته نمی‌شوند)

| # | محدودیت | چه چیزی آن را می‌بندد | وضعیت ۲۰۲۶-۱۱-۰۵ |
|---|---|---|---|
| ۱ | سودآوری/alpha | نمونهٔ مستقل آینده‌نگر؛ سپس ≥۳۰ سفارش واقعی | باز؛ ژورنال فعلی: IC −۰٫۰۷، exp −۰٫۳۹R |
| ۲ | composite backtest (TA-only) | archive point-in-time از FA/عمق | **شروع شد:** `market_archive` از ۰۹:۳۳Z روی production |
| ۳ | execution مدل OHLC | دادهٔ عمق ضبط‌شده + stress | وابسته به ۲ |
| ۴ | fee tier واقعی | GET حساب از مالک | باز (ورودی لازم) |
| ۵ | pagination ≠ دادهٔ منقضی | archive محلی از امروز | با ۲ پوشش می‌یابد |
| ۶ | hash ≠ replay کامل | archive payload خام | **شروع شد** (۲) |
| ۷ | inference آماری | ≥۳۰ نمونهٔ واقعی | باز |
| ۸ | deployment پس از تغییرات بررسی نشده | preflight روی سرور | ✅ بسته ۲۰۲۶-۱۱-۰۵ (۸ پیش‌شرط سبز) |

### رد‌شده‌ها (false positive)

| ادعا | چرا رد شد |
|---|---|
| «entry-candle risk» (ورودی بیرونی ۲) | ورود روی close است؛ high/low پیش از وجود موقعیت رخ داده |

## وقایع پس از remediation (برای ردیابی)

| تاریخ | رویداد |
|---|---|
| ۲۰۲۶-۱۱-۰۴ | انتشارهای 2.3.0–2.3.4، 2.4.0 (ADR-0049 هشدار قیمت)، CI سبز |
| ۲۰۲۶-۱۱-۰۵ | انتشار 2.5.0 (`dcac952`)؛ deploy production ۰۷:۱۹Z؛ شروع soak |
| ۲۰۲۶-۱۱-۰۵ | ابزار ops: `update_arvan --backup` با `state_backup` (`45c4f32`)، probe صرافی همسان با سرویس (`904d70a`)، snapshot و Secure cookie (`255ddc7`)، `market_archive` (`c6dc001`)، `build` در health (`bc11fda`) |
