# بررسی دوم دو تحلیل nobi و نسخهٔ عمومی capoy.ir

> **یادداشت بایگانی (۲۰۲۶-۱۱-۰۵):** این سند عکس لحظه‌ای بازبینی روی commit `2b5ea13` (2.2.2) است و بازنویسی نمی‌شود. وضعیت فعلی هر یافته در [`README.md`](README.md) همین پوشه است.


> **به‌روزرسانی remediation — 2026-11-04:** بخش‌های اصلی این سند شرح baseline تاریخی‌اند. وضعیت کد اصلاح‌شده، نتایج نهایی تست و محدودیت‌های باقی‌مانده در بخش ۹ ثبت شده است. هیچ deployment یا سفارش واقعی انجام نشده است.

**تاریخ:** 2026-11-04  
**مبنای کد:** commit `2b5ea130f53959d357c5c8a664bfb1a52bd781e0` (`2.2.2`)  
**اسناد بررسی‌شده:** `nobi-audit-report.en.md` (890 خط) و `nobi-analysis-en.md` (488 خط)  
**نسخهٔ گزارش‌شده توسط سایت:** `2.2.1` در snapshot عمومی health  
**ماهیت کار:** بررسی فقط‌خواندنی کد، probeهای مستقل محلی، تطبیق با مستندات رسمی و درخواست‌های عمومی GET. هیچ credential صرافی یا حساب، سفارش یا تغییر تنظیمی استفاده/انجام نشد.

> **محدودیت مهم:** فایل‌های evidence bundle و raw probe outputs همراه دو سند ارائه نشده‌اند؛ فقط خود دو فایل Markdown در دسترس بود. بنابراین همهٔ اعداد دقیق آن‌ها به‌عنوان حقیقت مستقل پذیرفته نشده‌اند. مواد audit شده نیز محتوای server و `nbx-core.js` را نسبت به HEAD تغییر نداده‌اند. شش تغییر mode-only در فایل‌های deployment/script دیده شد و دست نخورده باقی ماند.

---

## ۱. جمع‌بندی اجرایی

هر دو سند مفید و عمدتاً با کد سازگارند، اما جای یکدیگر را نمی‌گیرند:

- **`nobi-audit-report.en.md` برای تصمیم go-live اولویت دارد.** یافته‌های مهم آن دربارهٔ سقف ۵۰۰ کندل، نبود pagination در کلاینت اصلی، کارمزد واقعی نوبیتکس، استفادهٔ preflight سفارش live از حساب Paper، و شمارش dry-run در promotion evidence، مستقل تأیید شد.
- **`nobi-analysis-en.md` مکمل خوبی برای research parity است.** تفاوت backtest JS/Python، مقدار ثابت Profit Factor در JS، collapse شدن H1/H2 در تایم‌فریم روزانه، و انتخاب outer grid بر اساس OOS از افزوده‌های مفید آن‌اند.
- **یک یافتهٔ گزارش دوم باید اصلاح شود:** نادیده‌گرفتن high/low کندلِ ورود، در مدل «ورود روی close همان کندل»، باگ نیست؛ آن محدوده پیش از ایجاد موقعیت رخ داده است. رفتار بازتولید شد، اما تعبیر آن به‌عنوان «entry-candle risk» درست نیست.
- **هیچ‌کدام از دو سند یا بررسی عمومی سایت، اثبات سودآوری یا زیان‌دهی زنده نیست.** خروجی سلامت، تعداد اسکن، یا یک بک‌تست، جای نمونهٔ مستقل و قابل‌اتکای نتیجهٔ واقعی را نمی‌گیرد.
- **قبل از فعال‌سازی سفارش واقعی، دو مسیر live باید اصلاح شوند:** account context واقعی برای preflight و تفکیک سفارش‌های واقعی از dry-run در promotion gate. این جمع‌بندی به معنای آن نیست که سایت هم‌اکنون در حالت live است؛ وضعیت خصوصی live بدون خروجی احراز هویت مشخص نیست.

### رأی نهایی دربارهٔ اسناد

| سؤال | پاسخ |
|---|---|
| آیا یافته‌های مشترک معتبرند؟ | در مواد اصلی، بله: cross-quote liquidity، TA parity، FA fallback/cache، TA-only backtest، محدودیت evidence و config hash با بررسی قبلی و کد سازگارند. |
| اگر فقط یک سند مبنای remediation شود کدام؟ | سند اول، به‌شرط افزودن موارد معتبر سند دوم و اصلاح جزئیات. |
| آیا همهٔ severityها پذیرفته می‌شوند؟ | خیر؛ entry-candle یک false positive است و چند رقم/عبارت نیاز به qualification دارد. |
| آیا نسخهٔ آنلاین همان کد audit شده است؟ | ثابت نیست؛ health نسخهٔ 2.2.1 می‌دهد، درحالی‌که checkout روی 2.2.2 است. commit deployment هم در health افشا نشده است. |
| آیا بررسی حساب واقعی کامل شده؟ | خیر؛ endpointهای خصوصی login می‌خواهند و اتصال مستقیم محیط به سایت timeout شد. خروجی GET-only حساب Viewer لازم است. |

## ۲. یافته‌های جدید که مستقل تأیید شدند

### ۲.۱ سقف ۵۰۰ کندل و نبود pagination — تأییدشده، اولویت بالا

مستندات رسمی نوبیتکس صراحتاً می‌گویند:

- هر پاسخ UDF حداکثر **۵۰۰ کندل** دارد؛
- `countback > 500` به ۵۰۰ محدود می‌شود؛
- دریافت دسته‌های قدیمی‌تر به پارامتر `page` نیاز دارد؛
- کندل‌های ۱/۵/۱۵/۳۰ دقیقه فقط برای **۷ روز اخیر** در دسترس‌اند.

منبع رسمی: [Nobitex UDF OHLC documentation](https://apidocs.nobitex.ir/market_data/%D8%AF%D8%B1%DB%8C%D8%A7%D9%81%D8%AA-%D8%AF%D8%A7%D8%AF%D9%87-%D9%87%D8%A7%DB%8C-ohlc)

در کد:

- `server/nobitex_client.py:401-418` فقط `countback` می‌فرستد و `page` ندارد.
- `server/main.py:225-249` برای resolver مقدار ۱۲۰۰ کندل درخواست می‌کند.
- `server/journal.py:97-100` برای H3 روی 15m مقدار `604800/900 = 672` کندل می‌سازد.
- cache بر اساس range دقیق است و چند پاسخ ۵۰۰تایی را به یک تاریخچهٔ طولانی‌تر تجمیع نمی‌کند.
- `scripts/fetch_history.py` pagination دارد، اما این اسکریپت جداگانه در مسیر اصلی client/resolver استفاده نمی‌شود.

**نتیجهٔ عملی:** H3 روی 15m با آخرین ۵۰۰ کندل قابل resolve نیست. زمانی که ۶۷۲ کندل آینده برای تکمیل H3 موجود باشد، کندل ورود از پنجرهٔ ۵۰۰کندلی بیرون افتاده است؛ و پیش از آن نیز resolver به‌خاطر شرط کافی‌بودن آینده، outcome را ثبت نمی‌کند. H1=96 و H2=288 به این مشکل شدت مشابه ندارند.

**Qualification:** رفع pagination به‌تنهایی retention هفت‌روزه را حذف نمی‌کند. resolver باید به‌موقع اجرا شود و ردیف‌هایی که دیگر قابل بازیابی نیستند صریحاً `unresolvable/expired` شوند، نه اینکه بی‌نهایت pending بمانند.

### ۲.۲ دو تبدیل متفاوت برای آستانهٔ adaptive — تأییدشده، اولویت بالا برای evidence

- `server/adaptive.py` فاصله از anchor=40 را مقیاس می‌کند:  
  `recommended = 40 + (base - 40) * factor`
- `server/adaptive_evidence.py` خود base را مقیاس می‌کند:  
  `tested = base * factor`

برای factor=1.2:

| مقدار | تبدیل کد recommendation | تبدیل evidence |
|---|---:|---:|
| LONG base=68 | 73.6 | 81.6 |
| NEUTRAL base=55 | 58.0 | 66.0 |

پس evidence، آستانه‌ای را آزمایش می‌کند که recommendation واقعاً پیشنهاد نکرده است. خوشبختانه هر دو مسیر `applied=False` دارند؛ بنابراین این یافته **ناعتبارسازی آزمون آماری** است، نه ادعای تغییر خودکار آستانهٔ live.

### ۲.۳ preflight سفارش live از context حساب Paper استفاده می‌کند — تأییدشده، بحرانی پیش از live

Call graph تأییدشده:

1. `live_api.place` و `live_api.dry_run` به `_checked_intent` می‌روند؛
2. `_checked_intent` تابع injected `_PREFLIGHT` را صدا می‌زند؛
3. `main._live_preflight` مستقیماً `risk_whatif` را برمی‌گرداند؛
4. `risk_whatif` از `get_broker(...)` و سپس `br.equity`، `br.cash`، موقعیت‌های Paper و `risk.open_heat` استفاده می‌کند.

در این مسیر، snapshot تراز/موقعیت‌های واقعی صرافی خوانده نمی‌شود. `authorize_live_order` نیز policy مجوز کاربر است، نه جایگزین account reconciliation.

**Mitigation موجود:** live به‌صورت پیش‌فرض disabled است و confirmation، نقش operator، CSRF و policy دارد. این‌ها خطر تصادفی را کم می‌کنند، اما پیش از enable واقعی، مشکل account context را حل نمی‌کنند.

### ۲.۴ dry-run می‌تواند promotion evidence را کامل کند — تأییدشده، بحرانی پیش از live

`ExecutionLedger.promotion_evidence()` تمام ردیف‌های `live_execution` را می‌شمارد و stateهای مجاز را به سفارش واقعی محدود نمی‌کند. dry-run و live از همان ledger قابل‌استفاده‌اند.

در یک پایگاه‌دادهٔ موقت و کاملاً محلی، ۳۰ dry-run با timestamp توزیع‌شده روی پنج روز ساخته شد؛ نتیجه:

```json
{
  "ready": true,
  "samples": 30,
  "days": 5,
  "ambiguous": 0,
  "states": {"dry-run": 30},
  "reasons": []
}
```

این probe هیچ سفارش واقعی یا شبکه‌ای نداشت و فقط منطق query/gate را آزمود. بنابراین ۳۰ dry-run در پنج روز، بدون نمونهٔ سفارش واقعی، می‌تواند شرط فعلی `ready` را بسازد.

### ۲.۵ gap از stop عبور می‌کند، اما خروج روی stop ثبت می‌شود — تأییدشده

در ژورنال، `simulate_long` تنها high/low را بررسی می‌کند و open کندل را برای gap handling مصرف نمی‌کند. backtest نیز fill را روی stop با slippage ثابت محاسبه می‌کند.

Fixture مستقل: entry=100، ATR=1، stop=98؛ کندل بعدی open=80، high=81، low=79، close=80.

| مسیر | خروج ثبت‌شده | R ثبت‌شده |
|---|---:|---:|
| Journal | 98.0 | -1.0 |
| Python backtest | 97.9412 | -1.029 |

قیمت واقعی تضمین‌شده‌ای ۸۰ نیست؛ به liquidity و execution بستگی دارد. اما ثبت ۹۸ به‌عنوان fill، با gap نزولی به ۸۰ خوش‌بینانه است. لازم است gap policy صریح شود؛ برای long stop معمولاً open بدتر از stop باید مبنای اجرای محافظه‌کارانه قرار گیرد، همراه slippage/liquidity واقع‌گرایانه.

### ۲.۶ backtest جاوااسکریپت با Python هم‌معنا نیست — تأییدشده

- JS در `nbx-core.js` کل موقعیت را روی TP1 می‌بندد؛ TP2 در exit loop استفاده نمی‌شود.
- Python روی TP1 نیمی را می‌بندد، stop را به break-even می‌برد و باقی را برای TP2/exit بعدی نگه می‌دارد.
- JS وقتی حداقل یک برد و هیچ زیانی وجود دارد، PF را **2.4** قرار می‌دهد؛ Python در وضعیت مشابه cap=99.99 دارد.

روی fixture یک‌معاملهٔ برنده و بدون زیان:

```text
JS:     trades=1, wins=1, losses=0, profit_factor=2.4,  exit reason=tp1
Python: trades=1, wins=1, losses=0, profit_factor=99.99, exit reason=tp1
```

PF با صفر زیان از نظر ریاضی تعریف نامتناهی/نامشخص دارد و cap یک قرارداد گزارش است. مشکل اصلی، قرارداد متفاوت و عدد دلخواه 2.4 است که ranking و gate را می‌تواند تغییر دهد.

### ۲.۷ H1 و H2 در تایم‌فریم روزانه collapse می‌شوند — تأییدشده

```text
15m: H1=96 bars, H2=288 bars, H3=672 bars
1h:  H1=24 bars, H2=72 bars,  H3=168 bars
4h:  H1=6 bars,  H2=18 bars,  H3=42 bars
D:   H1=1 bar,   H2=1 bar,    H3=7 bars
```

علت `max(1, round(seconds / step))` است. بنابراین روی D، H1 و H2 دو افق مستقل نیستند و نباید در گزارش یا bootstrap به‌عنوان دو سنجهٔ متفاوت تفسیر شوند.

### ۲.۸ وقفهٔ زمانی در کندل‌ها کنترل نمی‌شود — تأییدشده

- `aligned_closes` فقط timestamp مشترک را intersection می‌کند و فاصلهٔ زمانی مجاورها را بررسی نمی‌کند.
- `log_returns` هر دو close متوالی را «یک بازه» می‌گیرد، حتی اگر بین آن‌ها چند کندل غایب باشد.
- journal resolver نیز تعداد ردیف را با horizon می‌سنجد، اما continuity کامل timestampها را شرط resolve نمی‌کند.

این موضوع correlation، annualisation، MFE/MAE و زمان واقعی outcome را می‌تواند مخدوش کند. راه‌حل، «درست نکردن کندل غایب» است؛ باید gap صریح ثبت و کیفیت نمونه محدود/رد شود.

### ۲.۹ کارمزد مدل با پایهٔ تعرفهٔ فعلی یکسان نیست — تأییدشده، ولی وابسته به حساب

مدل 0.2% per leg است. جدول رسمی نوبیتکس در زمان بررسی برای tier پایه نشان می‌دهد:

- بازار تومانی: maker=0.25% و taker=0.25%؛
- بازار تتری: maker=0.10% و taker=0.13%.

منبع رسمی: [Nobitex pricing](https://nobitex.ir/pricing/)

بنابراین 0.2% برای حساب پایهٔ بازار IRT کم‌برآورد و برای USDT بیش‌برآورد است. tier حجم ۳۰روزه و maker/taker واقعی باید در سناریوها اعمال شود. limit بودن سفارش به‌تنهایی maker بودن fill را تضمین نمی‌کند.

## ۳. نتیجهٔ بازتولید TA parity

یک fixture مستقل با seed ثابت `20261104`، ۱۰۰ سری تصادفی ۸۰کندلی و OHLC/volume معتبر ساخته شد. Python و JS روی دادهٔ یکسان اجرا شدند:

| سنجه | نتیجهٔ مستقل |
|---|---:|
| میانگین اختلاف مطلق score | 2.63 |
| میانهٔ اختلاف مطلق score | 1.25 |
| بیشینهٔ اختلاف score | 25.8 |
| اختلاف direction | 19 از 100 |
| بیشینهٔ اختلاف component | trend=35، momentum=25، volume=40، structure=20 |

این نمونه، وجود اختلاف تصمیم‌ساز را تأیید می‌کند. عدد «max=46.4» یا سایر ارقام دقیق سند دوم، بدون fixture اصلی آن، مستقل تأیید نمی‌شود و لازم هم نیست؛ نتیجهٔ کیفی در نمونهٔ ما نیز واضح است.

## ۴. مواردی که باید اصلاح یا محدود شوند

1. **Entry-candle risk باگ نیست.** ورود روی close است و high/low پیش از close نباید به موقعیتی که هنوز وجود ندارد آسیب بزند. رفتار probe شد، اما severity رد می‌شود. Gap کندل‌های بعد از ورود مشکل واقعی است.
2. **Outer grid و inner walk-forward را یکی نکنید.** انتخاب پارامتر داخل هر window روی train است؛ انتخاب بهترین grid نهایی از aggregate OOS انجام می‌شود. مشکل دومی این است که OOS پس از انتخاب، holdout دست‌نخورده برای همان انتخاب نیست.
3. **Structure breakout روی close است، نه high.** کد close جاری را با بیشینهٔ ۱۹ close قبلی می‌سنجد. عبارت «previous 19-bar high» اگر high قیمت تعبیر شود دقیق نیست.
4. **Volume baseline عملاً ۲۹ کندل قبلی است.** `iloc[-30:-1]` نه ۳۰ کندل کامل.
5. **Timestamp سلامت را با outage کلی اشتباه نگیرید.** `upstream.age_s` سن snapshot probe ذخیره‌شده در health است، نه سن همهٔ market data یا زمان آخرین request موفق client.
6. **Health نسخه را ثابت می‌کند، نه commit یا config کامل را.** نباید همهٔ یافته‌های 2.2.2 را بدون بررسی به deployment 2.2.1 نسبت داد.
7. **هیچ profitability claim از این probeها خارج نمی‌شود.** fixtureهای gap/PF/adaptive برای آزمون قرارداد نرم‌افزارند، نمونهٔ عملکرد بازار نیستند.

## ۵. مشاهدهٔ نسخهٔ عمومی capoy.ir

Snapshot عمومی `https://capoy.ir/api/health` با timestamp `2026-11-04T03:56:46.930862+00:00`:

| فیلد | مقدار | تفسیر مجاز |
|---|---|---|
| `ok` | true | process پاسخ می‌دهد؛ به معنای سلامت همهٔ زیرسیستم‌ها نیست. |
| `backend` | python-fastapi-nobi | نوع backend. |
| `version` | 2.2.1 | با checkout 2.2.2 متفاوت است. |
| `upstream.ok` | true | آخرین snapshot ثبت‌شدهٔ probe سالم بوده است. |
| `upstream.age_s` | 19857.9s ≈ 5.516h | snapshot probe قدیمی بود؛ اثبات outage ۵٫۵ساعتهٔ همهٔ upstream نیست. |
| `upstream.degraded` | false | با `provenance.source=live` سازگار است، اما freshness لحظه‌ای را تضمین نمی‌کند. |
| scan loop | enabled، interval=300s | اسکن سرور فعال بوده است. |
| scan runs | 249 | خلاف وضعیت `runs=0` در سند قدیمی؛ اثبات کیفیت سیگنال نیست. |
| last scan | `last_ok=true`، 38 نماد | 38/38 در freshness tolerance جاری بودند. |
| last bar | 03:30Z، age=1.57 bars | freshness مجاز در آن snapshot؛ نه قیمت لحظهٔ مطالعهٔ بعدی. |
| stats | total=26656، ok=26644، fail=12 | نرخ واقعی ≈99.95498% است؛ `success_rate=100.0` گرد شده است. |
| auth | accounts=true، token_required=true | داده‌های خصوصی پشت login هستند. |

در source فعلی، `_upstream_snapshot()` وقتی snapshot قدیمی است probe تازه را asynchronous آغاز می‌کند، اما همان پاسخ، snapshot قبلی را برمی‌گرداند. ابزار عمومی نیز همان پاسخ cacheشده را تکرار کرد؛ بنابراین نتیجهٔ probe تازه مشاهده نشد. نتیجهٔ درست این است که **health باید freshness خود probe را واضح‌تر نمایش دهد**، نه اینکه قطعی ۵٫۵ساعته اثبات شده باشد.

### دسترسی عمومی سایر مسیرها

- `/api/signals?tf=15m`، `/api/backtest/reports?limit=5` و `/api/risk/state` پاسخ login لازم دادند.
- `/api/universe` در یک تلاش عمومی HTTP 500 داد؛ body و علت در دسترس نبود. این یک observation است، نه اثبات outage مداوم.
- صفحهٔ عمومی shell داشبورد و هشدارهای no-data/upstream را نشان داد؛ هیچ دادهٔ حساب یا اثبات اجرای واقعی مشاهده نشد.
- هیچ POST، سفارش، تغییر تنظیم یا ورود با credential انجام نشد.

### آنچه از نسخهٔ آنلاین هنوز معلوم نیست

- mode واقعی execution و محتوای promotion evidence؛
- معتبر/تکمیل‌بودن journal outcomes و تعداد pending/unresolvable؛
- paper equity/positions و نتایج با provenance کامل؛
- reportهای backtest واقعی و config hash آن‌ها؛
- اینکه `/api/universe` پس از login سالم است یا خطای deployment دارد.

## ۶. اولویت اقدام

### P0 — پیش از هر سفارش واقعی

1. **LiveAccountSnapshot جدا از Paper:** تراز آزاد، موقعیت‌ها و سفارش‌های باز واقعی، با timestamp و fail-closed freshness.
2. **Preflight واقعی:** هیچ fallback به Paper برای equity/cash/open positions در مسیر live نباید وجود داشته باشد.
3. **Promotion evidence واقعی:** `real_samples`، `real_days` و outcomeهای reconciled را از dry-run جدا کنید؛ dry-run فقط readiness فنی را نشان دهد.
4. **اتصال evidence به استراتژی:** config/version/dataset و کیفیت execution لازم باشد؛ count و day coverage به‌تنهایی کافی نیست.
5. **وضعیت deployment را read-only بررسی کنید:** تا اثبات disabled/امن‌بودن، live را enable نکنید.

### P1 — پیش از اعتماد به نتایج research

1. pagination کامل UDF + dedup + gap report + مدیریت retention هفت‌روزه؛
2. یک `threshold_transform` مشترک برای adaptive recommendation و evidence با parity test روی factor grid؛
3. gap-aware execution، continuity-aware outcomes و ثبت/حل محافظه‌کارانهٔ ابهام post-TP1 در همان کندل؛
4. یک backtest canonical یا shared engine؛ حذف PF=2.4 و یکسان‌سازی TP1/TP2/fees/slippage؛
5. جلوگیری از interpretation مستقل H1/H2 در D و افزودن holdout نهایی برای grid selection؛
6. سناریوهای fee بر اساس quote، tier و maker/taker.

### P2 — Observability و deployment

- تفکیک `process_ok`، `last_probe_ok`، `probe_age`، `market_data_freshness` و `scan_last_ok`؛
- نمایش commit/build identifier در health یا diagnostics امن؛
- بررسی دادهٔ universe پس از login؛ خطای 500 عمومی قبلی در تلاش دوم تکرار نشد و فقط در صورت بازتولید باید به‌عنوان incident پیگیری شود؛
- گزارش صریح pending، expired و excluded در journal به‌جای pending نامتناهی؛
- خالص‌سازی GETهای Paper از side effect و واگذاری ارزیابی دستی به مسیر محافظت‌شده، با حفظ loop پس‌زمینه.

## ۷. دادهٔ لازم برای تکمیل بررسی حساب

حساب Viewer انتخاب شد، اما اتصال مستقیم sandbox به `capoy.ir` روی IPv4 نیز timeout شد و ابزار fetch عمومی توان ارسال cookie/header ورود ندارد. بنابراین **رمز نباید ارسال شود**.

فایل `/home/user/capoy-readonly-export.js` آماده شده است. پس از login با Viewer، اجرای آن در console همان origin فقط GET same-origin انجام می‌دهد و یک JSON دانلود می‌کند. این فایل:

- cookie، localStorage یا رمز را نمی‌خواند و در خروجی نمی‌گذارد؛
- POST، logout، order یا settings call ندارد؛
- `/api/paper/positions` و `/api/paper/metrics` را عمداً حذف کرده، چون handler آن‌ها `evaluate_positions()` را اجرا و وضعیت Paper را ممکن است به‌روز کند؛
- پاسخ 403 مسیرهای operator-only را ثبت می‌کند و برایشان دسترسی بالاتر لازم نیست.

پیش از اشتراک‌گذاری JSON، اطلاعات شخصی/مالی حساس را بررسی و حذف کنید. برای `/api/live/status` و `/api/live/evidence`، اگر Viewer پاسخ 403 گرفت، یک خروجی متنی sanitized از کاربر مجاز کافی است؛ Owner یا کلید API نوبیتکس لازم نیست.

---

## ۸. یافته‌های تکمیلی ادامهٔ بررسی

### ۸.۱ GETهای Paper می‌توانند state را تغییر دهند — تأییدشده، محدود به Paper

`GET /api/paper/positions` و `GET /api/paper/metrics` هر دو `broker.evaluate_positions()` را اجرا می‌کنند. این تابع فقط mark-to-market نیست: می‌تواند TP1 را به‌صورت partial اجرا کند، trailing stop را جابه‌جا کند، time-stop/SL را با `_close()` اجرا کند، order event بسازد و `save()` را روی state انجام دهد.

این GETها dependency محافظ mutation با `require_client` ندارند و middleware عمومی نیز فقط session معتبر می‌خواهد؛ بنابراین حساب Viewer که از نظر نقش `can_mutate=false` است، می‌تواند این مسیر را فراخوانی کند. loop پس‌زمینه نیز همین ارزیابی را انجام می‌دهد، اما این موضوع side effect ناشی از GET و ناسازگاری با read-only role را حذف نمی‌کند.

probe محلی و isolated، بدون اتصال به سایت:

```json
{
  "before": {"state":"OPEN","orders":0,"state_file_exists":false},
  "after": {"state":"CLOSED","close_reason":"sl-hit","orders":1,"state_file_exists":true}
}
```

**دامنه:** این یافته Paper است و به‌تنهایی سفارش واقعی صرافی ایجاد نمی‌کند. شدت آن P2 در API/authorization semantics است، نه P0 برای live. بهتر است GET فقط state ذخیره‌شده را برگرداند و ارزیابی به loop پس‌زمینه یا یک POST محافظت‌شده واگذار شود. exporter ارائه‌شده از ابتدا این دو مسیر را حذف کرده بود.

### ۸.۲ ابهام post-TP1 با فرض خوش‌بینانه حل می‌شود — تأییدشده، اولویت research بالا

در Python و ژورنال، پس از TP1 موقعیت ناقص می‌شود و stop جدید ساخته می‌شود؛ اما بررسی stop جدید در همان کندل انجام نمی‌شود. اگر high به TP1 رسیده و low نیز زیر stop جدید باشد، ترتیب intrabar نامعلوم است. پیاده‌سازی فعلی عملاً فرض می‌کند low پیش از TP1 رخ داده و باقی موقعیت به کندل بعد می‌رسد.

Fixture مستقل:

- entry=100، stop اولیه=98؛
- کندل بعدی high=104، low=99.5، close=103.5؛
- TP1 در این کندل دیده می‌شود؛
- stop جدید ژورنال=100.2 و stop جدید تقریبی Python=100.06 است، هر دو بالای low=99.5.

| خروجی | نتیجهٔ فعلی |
|---|---|
| Python backtest | `reason=eod`، `R=1.36`، `ambiguous=false` |
| Journal | `hit_sl=false`، `timeout=true`، `R=1.475` |

این اعداد performance واقعی بازار نیستند؛ نشان می‌دهند مسیر خوش‌بینانه می‌تواند نتیجه را بالا ببرد و ambiguity نیز ثبت نشود. برای سازگاری با stop-first فعلی، برخورد TP1 و post-TP1 stop در یک کندل باید به‌عنوان حالت ambiguous مشخص و با سیاست محافظه‌کارانه یا دادهٔ تایم‌فریم پایین‌تر resolve شود.

### ۸.۳ دامنهٔ اختلاف JS/Python باید دقیق بماند

`runWalkForward` مرورگر خود را `browser-experimental` معرفی می‌کند، `source=synthetic` و `params_optimised=false` می‌دهد و روی train بهینه‌سازی Python را انجام نمی‌دهد. بنابراین اختلاف JS/Python واقعی است، اما به‌تنهایی ثابت نمی‌کند reportهای canonical سمت server خراب‌اند؛ نتیجهٔ درست این است که fallback مرورگر نباید با evidence سرور یکی تلقی شود.

دو تفاوت تکمیلی نیز دیده شد: JS در `runWindow` همان cap دارایی ۲۵٪ Python را اعمال نمی‌کند، و fallback بدون window در یک مسیر `params_used` را با TP1=1.5 و TP2=2.5 ثابت گزارش می‌کند حتی اگر config دیگری داده شده باشد. مورد دوم metadata error با شدت پایین در مسیر experimental است.

### ۸.۴ snapshot دوم سایت و اصلاح مشاهدهٔ universe

در snapshot تازهٔ `2026-11-04T04:52:36.955469Z`:

- scan runs از **249 به 260** رسید؛ ۱۱ اجرا در حدود ۵۶ دقیقه با interval پنج‌دقیقه‌ای سازگار است.
- `last_ok=true` و 38/38 نماد current باقی ماند؛ آخرین scan در `04:49:05Z` کامل شده بود.
- stats به total=27845، ok=27833 و fail=12 رسید؛ شمارش خطا افزایش نیافت.
- `upstream.age_s=3350` تقریباً برابر فاصله از snapshot قبلی بود و `latency_ms=5.0` گزارش شد. این رفتار با تفسیر کد سازگار است: درخواست health قبلی probe تازه را asynchronous آغاز کرده و پاسخ بعدی نتیجهٔ آن را دیده است.

درخواست دوم `/api/universe` این‌بار پاسخ استاندارد **401/login** داد، نه HTTP 500. بنابراین خطای 500 قبلی تکرارنشدنی است و نباید به‌عنوان نقص پایدار deployment گزارش شود. دادهٔ universe پس از login هنوز بررسی نشده است.

---

## ۹. وضعیت remediation در working tree — 2026-11-04

این بخش وضعیت `/home/user/nobi` **پس از اصلاح** است؛ جدول‌ها و probeهای بخش‌های قبلی، شرح baseline پیش از remediation هستند و نباید به‌عنوان رفتار فعلی کد خوانده شوند. تغییرات هنوز commit یا deploy نشده‌اند.

### ۹.۱ اصلاحات اعمال‌شده

| حوزه | وضعیت فعلی |
|---|---|
| OHLC و horizon | history بالای 500 کندل صفحه‌بندی، dedup/sort و trim می‌شود. H1 روی D دیگر به H2 تبدیل نمی‌شود. ورود تکراری روی یک candle و fill غیرواقعی gap نیز regression دارند. |
| Intrabar و TP2 | gap از stop با open قیمت‌گذاری می‌شود؛ TP1+stop در یک candle محافظه‌کارانه و `ambiguous` است. Paper هر دو side و Journal نیمهٔ دوم را روی TP2 می‌بندند، حتی وقتی TP1/TP2 در یک tick/candle باشند. PnL خروج TP1 سمت sell نیز اصلاح شد. |
| Journal | `r_multiple` و `return_pct` proceeds هر دو leg را شامل می‌شوند؛ `hit_tp2`، `exit_reason`، `ambiguous` و dataset hash/range در schema و Signal Evidence هستند. migration روی DB قدیمی idempotent است. |
| TA | RMA/RSI/TR/Bollinger/DI/ADX و warmup در Python/JS هم‌قرارداد شدند. parity عددی روی barهای معتبر، componentها، direction، regime، risk و diagnostics تست می‌شود؛ fixtureهای تزریقی تنها منبع سیگنال parity هستند. |
| FA | cache key به depth/premium حساس است؛ provider، age، staleness، cache و profile در evidence ثبت می‌شوند. دادهٔ ناموجود neutral evidence ساختگی نیست و score روی componentهای موجود بازتوزین می‌شود. TVL فقط با mapping صریح و asset-class مناسب مصرف می‌شود. |
| Liquidity | ranking و scan بر مبنای USD turnover هستند؛ IRT بدون FX معتبر یا quote ناشناس مقدار دلاری ساختگی نمی‌گیرد. |
| Risk | daily/weekly realized-PnL و consecutive-stop triggers در Paper، preflight، `/api/risk/state`، Python backtest و JS وصل شدند. halt فقط ورود جدید را متوقف می‌کند و مدیریت موقعیت باز ادامه دارد؛ regression سومین stop ورود چهارم را مسدود می‌کند. |
| Live safety | real-order route فقط `_LIVE_PREFLIGHT` با account snapshot واقعی/timestamp/freshness را قبول می‌کند و بدون provider با 503 fail-closed است. dry-run و ambiguous execution دیگر promotion evidence کامل نمی‌کنند. |
| Adaptive/health/Paper GET | تبدیل anchor/factor در recommendation و evidence یکسان شد. health آخرین probe را با age/stale/refresh گزارش می‌کند. GETهای Viewer دیگر Paper evaluation یا state mutation انجام نمی‌دهند. |
| Fees و grid search | `fee_pct` در backtest قابل تنظیم و در config hash/report ثبت است؛ fee صفر حتی با slippage مثبت gate را pass نمی‌کند. انتخاب «بهترین» grid فقط از IS انجام می‌شود، نه OOS flattering. default اهداف 1.2R/2R با Live/Paper/Journal یکسان است. |
| Portfolio | نوسان هر asset از سری خودش و correlation هر جفت فقط از timestampهای مشترک همان جفت است؛ latest-n استفاده می‌شود و gap یک asset دیگر جفت‌ها را حذف نمی‌کند. benchmark عمداً intersection قبلی را حفظ می‌کند. |
| Replay metadata | Backtest dataset hash/range، engine/source hash و execution assumptions دارد. Journal engine/config identity منابع تصمیم و env مؤثر را پوشش می‌دهد و dataset identity را جدا نگه می‌دارد. |
| Standalone | `nobitex-mobile.html` پس از آخرین تغییر JS بازسازی شد؛ contract تولید/byte-check در full Python suite پاس شد. |

### ۹.۲ نتایج نهایی آزمون

- **Python full suite: `1776 passed`** با `PYTHONPATH=/home/user/nobi:/home/user/nobi/server`.
- **mypy: بدون خطا در 165 فایل**؛ **Ruff: بدون خطا**.
- **ESLint و dashboard lint: بدون خطا**؛ JS broker، intrabar parity و lockfile check پاس شدند.
- هر **۲۰ هدف تست DOM/Node** شامل display/icons/adaptive/escaping/detail/queue و review phaseهای 1 تا 13 با **Node `v22.23.4`** پاس شدند.
- `git diff --check` پاک است و exec bitهای فایل‌های executable مطابق Git index بازیابی شده‌اند.

### ۹.۳ آنچه هنوز اثبات یا تکمیل نشده است

1. **سودآوری یا alpha اثبات نشده است.** تست سبز، fixture معامله و gate نرم‌افزاری هیچ‌کدام evidence بازده واقعی نیستند؛ frozen holdout و effective-sample/bootstrap evidence هنوز برای promotion لازم است.
2. **Backtest کامل composite انجام نشده است.** موتور canonical همچنان TA-only است و `matches_live=false` می‌گوید؛ FA/liquidity/order-book تاریخی point-in-time باید archive شوند.
3. **Execution هنوز مدل OHLC است.** entry روی signal-close، latency صفر، بدون depth/partial fill و با slippage scenario است. next-open/latency/depth stress به‌عنوان کار research باقی است و اکنون صریحاً disclose شده است.
4. **Tier واقعی کارمزد حساب مشخص نیست.** 0.2% فقط `default-assumption-unverified-account-tier` است؛ maker/taker واقعی و tier حجم ۳۰روزه باید از حساب گرفته شود. limit order به‌تنهایی maker fill نیست.
5. **Pagination دادهٔ منقضی را برنمی‌گرداند.** retention صرافی می‌تواند H3 روی timeframeهای کوتاه را unresolved بگذارد؛ resolver نباید candle یا outcome ساختگی بسازد.
6. **Hash به‌تنهایی replay کامل نیست.** `dataset_hash` و source identity، وجود payload اصلی را جایگزین نمی‌کنند؛ `raw_payload_archived=false` و `complete_replay=false` صریح‌اند.
7. **نمونه‌های مستقل و inference آماری هنوز محدودند.** overlap، censoring، calibration و confidence intervals مبتنی بر effective sample هنوز نیازمند ارزیابی research جدا هستند.
8. **Deployment عمومی پس از این تغییرات بررسی نشده است.** هیچ push، deploy، login اختصاصی، سفارش واقعی یا عملیات نوشتنی روی capoy.ir انجام نشد. وضعیت live پس از deployment آینده باید دوباره preflight شود.

### ۹.۴ addendum سه اصلاح failing-first — 2026-11-04

سه یافتهٔ باقی‌ماندهٔ `nobi-review.md` در commit محلی **`66e75757deb4080e2feeffe44f39188cbb12bc11`** (`fix: close auto freshness and execution accounting findings`) اصلاح شدند. این commit روی `v2.4.0` قرار دارد؛ tag و version bump جدیدی ندارد و push/deploy نشده است.

| یافته | اصلاح و شاهد regression |
|---|---|
| Auto روی cache عمومی یا timeframe دیگر حساب می‌کرد | `_signal_lookup` با timeframe صریح دیگر به `_cached["signals"]` fallback نمی‌کند. auto فقط pool همان timeframe را می‌بیند و پیش از sizing، هم age اسکن و هم freshness آخرین candle نماد را بررسی می‌کند. نبود timeframe، `stale-signals` و `stale-candles:<reason>` با audit و بدون order رد می‌شوند. |
| `status:"ok"` سطح پاسخ، order ردشده را accepted/reconciled می‌کرد | place و reconcile هر دو `order.status` را بررسی می‌کنند. `Rejected` با `error_code="order-rejected"` ذخیره و از qualified promotion evidence کنار گذاشته می‌شود. کنترل `Active + matchedAmount=0` همچنان accepted است و idempotency یک ارسال را حفظ می‌کند. |
| Cash/equity پوزیشن sell/short علامت نادرست داشت | ورود short proceeds را واریز و liability را با علامت منفی mark می‌کند. close و TP1 هزینهٔ buyback و کارمزد خروج را برداشت می‌کنند؛ تست‌ها entry، price-rise loss، full close و TP1+TP2 را با cash/equity و کارمزدها بررسی می‌کنند. رفتار buy تغییر نکرده است. |

**اثبات failing-first:** تست‌های تازه پیش از اصلاح روی کد قبلی **`10 failed, 4 passed`** دادند. همان selection پس از اصلاح **`14 passed`** شد. تعداد شکست‌ها شامل دو پارامتر sell در تست accounting موجود است؛ بنابراین تست‌ها صرفاً رفتار قبلی را تأیید نمی‌کردند.

**Gate نهایی این commit:**

- Python full suite: **`1808 passed, 1 warning`**؛ هشت تست جدید نسبت به gate نسخهٔ 2.4.0.
- Ruff: پاک؛ mypy: **بدون خطا در 168 فایل**.
- تمام scriptهای `test:*` و lintهای JS/DOM با Node **`v22.22.2`** پاس شدند؛ Node محیط 20 به‌خاطر engine رسمی jsdom 30 برای این suite مناسب نبود.
- preflight parity: **28 حالت × 10 gate** پاس.
- pip-audit روی requirements اصلی: بدون advisory شناخته‌شده. یافته‌های moderate زنجیرهٔ توسعهٔ npm در یافتهٔ جداگانهٔ قبلی باقی‌اند.
- بازتولید مستقل پس از اصلاح: درخواست `1h` دیگر سیگنال `15m` برنمی‌گرداند؛ `Rejected` به `rejected` تبدیل می‌شود؛ افزایش قیمت short از حدود 100 به 110 در fixture ده‌واحدی، equity را **100-** تغییر می‌دهد.

هیچ‌کدام از هشت محدودیت بخش ۹.۳ با این commit ادعای بسته‌شدن ندارند. سودآوری، canonical composite backtest، مدل کامل execution، fee tier واقعی، retention/replay/inference و verification روی deployment همچنان بازند. هیچ سفارش واقعی، Live activation یا state change روی production انجام نشده است.

---

## منابع و بازتولیدپذیری

- checkout بررسی‌شده: `/home/user/nobi`، commit `2b5ea130f53959d357c5c8a664bfb1a52bd781e0`.
- بررسی جامع قبلی: `/home/user/nobi-comprehensive-audit-fa-en.md`.
- اسناد الصاقی: `/home/user/uploads/nobi-audit-report.en.md` و `/home/user/uploads/nobi-analysis-en.md`.
- probe مستقل JS در `/home/user/review-inputs/independent-probes.js` باقی مانده است؛ نتایج عددی مستقل به‌دلیل ماندگارنبودن فایل موقت JSON، مستقیماً در متن گزارش ثبت شده‌اند.
- exporter حساب Viewer: `/home/user/capoy-readonly-export.js`؛ syntax آن با Node بررسی شده است.
- snapshot عمومی deployment: [capoy.ir/api/health](https://capoy.ir/api/health).
- قرارداد رسمی دادهٔ بازار: [مستندات Nobitex UDF OHLC](https://apidocs.nobitex.ir/market_data/%D8%AF%D8%B1%DB%8C%D8%A7%D9%81%D8%AA-%D8%AF%D8%A7%D8%AF%D9%87-%D9%87%D8%A7%DB%8C-ohlc).
- تعرفهٔ رسمی کارمزد: [Nobitex pricing](https://nobitex.ir/pricing/).
