# ADR-0050 — گیت رژیم بازار روی LONG و stop همسو با افق، هر دو پشت پرچم

**فارسی** · [English](../en/ADR-0050-market-regime-gate-and-horizon-aligned-stop.md)

**وضعیت:** پذیرفته — کد روی `main`؛ در production با deploy بعدی (پس از پایان soak ۲۰۲۶-۱۱-۱۰) فعال می‌شود و تا آن زمان رفتار هیچ سرویسی را تغییر نمی‌دهد
**تاریخ:** ۲۰۲۶-۱۱-۰۶
**مرتبط:** ADR-0008 (ریسک)، ADR-0022 (گیت‌های سایه)، ADR-0044/0046 (Signal Intelligence و Drift)، `docs/analysis/journal-diagnostic-2026-11-06.md`

## زمینه

تشخیص ژورنال production (پنجرهٔ تمیز پس از ۲۸ اکتبر، ~۵٬۹۰۰ سیگنال، ۹۴۳ LONG) دو واقعیت را نشان داد:

1. **هیچ لبهٔ جهت‌داری در IC ترکیبی نیست و LONG بدترین دسته است** (بازده ۱h: LONG −۰٫۳۵٪، NEUTRAL −۰٫۲۳٪، EXIT −۰٫۱۳٪)؛ در همان پنجره کل universe ۳٫۵ تا ۴٪ ریخت. امتیاز TA با بازده ۱h *ضدهمبسته* بود (IC −۰٫۰۷) و FA و نقدینگی مثبت (+۰٫۱۱). به بیان ساده: در بازاری که همه با هم پایین می‌روند، «قوی‌ترین نمودار» سیگنال خرید نیست.
2. **stop با افق هم‌خوان نیست.** stop = ۲×ATR(15m) ≈ ۱٫۲٪ است؛ در افق ۲۴h، ۹۳٪ LONGها به stop می‌خورند و R̄ = −۰٫۶۴. بازشبیه‌سازی روی همان داده: نرخ stop در H1 با k=۱→۵۴٪، k=۲→۲۳٪، k=۳→۵٪، k=۴→۱٪. ضریب ۲٫۰ در چهار جا به‌صورت literal تکرار شده بود (`journal.py`, `ta_engine.py`, `risk_engine.py`, `nbx-core.js`).

سیاست پروژه (ADR-0008، ADR-0022) روشن است: هیچ تغییر رفتار معاملاتی بدون دورهٔ سایه و شواهد ژورنال؛ و هیچ پارامتر پنهان.

## تصمیم

### ۱) گیت رژیم بازار (`server/regime.py`)

- **ورودی:** همان `market_overview()` که داشبورد نشان می‌دهد — `change.median_24h` و `breadth.pct_up` روی universe فعلی. هیچ منبع دادهٔ جدیدی اضافه نمی‌شود.
- **حالت‌ها:** `risk-off` اگر median ≤ `NBX_REGIME_MIN_MEDIAN_24H` (پیش‌فرض −۲٫۰٪) **یا** pct_up ≤ `NBX_REGIME_MIN_BREADTH_PCT` (پیش‌فرض ۳۵٪)؛ `risk-on` اگر median ≥ +۲ و pct_up ≥ ۶۵؛ در غیر این صورت `neutral`. اگر overview `ok=False` باشد یا فیلد نداشته باشد → `unknown`، و **`unknown` هرگز مسدود نمی‌کند** (شکست دادهٔ بازار نباید سیگنال‌ها را خاموش کند).
- **سه مود با `NBX_REGIME_GATE`:** `off` (هیچ ثبتی)، **`shadow` (پیش‌فرض)**: روی هر snapshot `market_regime` و `regime_gate{mode, would_block, applied:false}` ثبت می‌شود و جهت دست نمی‌خورد؛ `enforce`: LONG در `risk-off` به NEUTRAL تبدیل می‌شود و composite به سقف ۶۷ (همان `VETO_CAP`) محدود می‌گردد؛ `direction_raw`/`uncapped_composite`/`regime_cap` نگه داشته می‌شوند — دقیقاً الگوی veto موجود.
- **ثبت:** فیلدهای `market_regime`, `regime_gate`, `regime_cap` به `features` ژورنال اضافه شدند؛ رژیم لحظهٔ اسکن در `/api/signals`, `/api/health` (`market_regime`) و `/api/market/overview` (`regime`) دیده می‌شود. `scripts/journal_diagnostic.py` بازده LONG را به تفکیک `features.market_regime` می‌شکند (بخش 3b).
- **ارتقا به `enforce` فقط با شواهد:** حداقل دو هفته shadow، و در گزارش تشخیصی، میانگین بازده LONGهای `risk-off` به‌طور معنادار (CI ۹۵٪ جدا) از LONGهای `neutral`/`risk-on` بدتر باشد. تغییر مود = تغییر env + ADR amendment، نه ویرایش کد.

### ۲) stop همسو با افق — ضریب به یک تنظیم تبدیل شد

- `NBX_RISK_K` (پیش‌فرض **۲٫۰، بدون تغییر رفتار**) در `risk_engine.parse_risk_k()` با کران ۱–۵ parse می‌شود؛ مقدار بد مثل `NBX_RR_LEVELS` با هشدار به پیش‌فرض برمی‌گردد. `journal.RISK_K`, `ta_engine.RISK_K` (بلیت دستی و پلن paper) و `RiskConfig.risk_k_multiplier` همگی از همین منبع می‌خوانند؛ تست `test_journal_and_ta_engine_read_the_same_multiplier` این را قفل می‌کند.
- **هیچ ضریب تازه‌ای الان اعمال نمی‌شود.** k=۳ کاندید است، اما فقط به‌عنوان یک بازوی A/B سایهٔ 2.6.0 (با `config_hash` جدا) ارزیابی می‌شود، نه با تغییر production. دلیل: با افزایش k، حجم پوزیشن (ADR-0008، ریسک ثابت ۰٫۷۵٪) کوچک‌تر می‌شود و تغییر هم‌زمان k در ژورنال و live بدون دورهٔ سایه، مقایسهٔ قبل/بعد را از بین می‌برد.
- `nbx-core.js:333` (موتور مرورگر، فقط نمایش) هنوز `×2` دارد؛ وقتی مقدار env غیر از ۲ شود باید از `/api/health` یا `/api/config` بخواند — یادداشت باز در CHANGELOG.

## پیامدها

- با deploy بعدی، ژورنال از اولین اسکن رژیم را ثبت می‌کند؛ بدون این ثبت، هیچ بحثی دربارهٔ «گیت کمک می‌کند یا نه» قابل اثبات نیست.
- در مود `enforce`، سیگنال‌های LONG در بازار ریزشی ناپدید نمی‌شوند؛ به NEUTRAL با سقف ۶۷ تبدیل می‌شوند و `direction_raw` در snapshot می‌ماند — UI و Telegram همان مسیر veto را می‌روند.
- `unknown` عمداً «باز» است: اگر `/market/stats` از دسترس خارج شود، گیت خاموش است نه بسته.
- آستانه‌ها (−۲٪ / ۳۵٪) از همان پنجرهٔ تشخیصی انتخاب شدند و روی آن in-sample‌اند؛ ارزیابی shadow روی دادهٔ بعد از deploy، holdout واقعی است.
- این ADR وزن‌دهی ترکیبی، فیلتر نقدینگی، کارمزد در R و A/B سایه را **تغییر نمی‌دهد** — آن‌ها در ADR جداگانهٔ 2.6.0 می‌آیند.
