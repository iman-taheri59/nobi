# بازبینی انطباق با API نوبیتکس — ۱۴۰۵/۰۷/۲۸ (2026-10-20)

> **بایگانی — وضعیت در تاریخ ۱۴۰۵/۰۷/۲۸ (2026-10-20).** این سند گزارش همان روز است و برخی گزارهها (مثلاً نبود `/api/orderbook` یا `/api/paper/orders`) اکنون نادرستاند. برای وضعیت جاری به `README.md`، `docs/HANDOFF.md` و کد `server/main.py` مراجعه کنید.

**دامنه:** همهٔ مستندات رسمی (`github.com/nobitex/docs-api`) در برابر هر جایی از این
مخزن که با صرافی حرف می‌زند: `server/nobitex_client.py`، `server/main.py`،
`server/paper_trading.py`، `server/ws_execution.py`، `scripts/run_with_mock_exchange.py`،
`scripts/mock_upstream.py`، `nbx-core.js`، `index.html`.

**روش:** هر بند مستند با کد مقایسه شد؛ هر مغایرت یا اصلاح شد و تست گرفت، یا به‌صراحت
«باقی‌مانده» علامت خورد. هیچ بندی با حدس بسته نشد. عدد ساختگی هم اضافه نشد: هر
عددی که در گزارش می‌آید از payload واقعی یا از خودِ مستند است.

**نتیجه:** **۱۱ مغایرت واقعی** پیدا شد؛ ۹ تای آن اصلاح و تست شد، ۲ تا مستند و
باز گذاشته شد (چون منبع رسمی‌شان وجود ندارد). تست‌ها از **۱۹۶** به **۲۲۲** رسید
(۲۶ تست تازه در `server/tests/test_api_conformance.py`).

---

## ۱. جدول مغایرت‌ها

| # | مغایرت | کد ما (قبل) | مستند | اثر واقعی | حکم |
|---|---|---|---|---|---|
| ۱ | کلید بازار مستند `btc-rls` بود، نه نماد | `main._load_universe_locked` فقط `norm()` می‌زد ⇒ `BTC-RLS` | `/market/stats` کلید را `btc-rls` می‌دهد؛ اردربوک/چارت `BTCIRT` می‌خواهند | هر نماد «بازار ناموجود» بود؛ premium و تبدیل ارز هم روی همین رشته کار می‌کرد | **رفع شد** (`units.market_symbol`) |
| ۲ | قیمت ریالی «تومان» خوانده می‌شد | هیچ تبدیلی نبود؛ UI برچسب «تومان» می‌زد | «واحد قیمت در بازارهای ریالی به ریال (و نه تومان)» | همهٔ اعداد بازار ۱۰ برابر بزرگ‌تر از برچسبشان | **رفع شد** (`scale_stats/orderbook/udf` + `NBX_PRICE_UNIT`) |
| ۳ | `status:"failed"` با HTTP 200 | `_request` آن را دادهٔ سالم برمی‌گرداند | خطای کاری = HTTP 200 + `status:"failed"` + `code` | «موجودی کافی نیست» به «بازار خالی» تبدیل می‌شد | **رفع شد** (`ClientError(code)`) |
| ۴ | `backOff` پاسخ ۴۲۹ | `sleep(1.2 × attempt)` | پاسخ ۴۲۹ شامل `backOff` (ثانیه) و `limit`؛ تکرار بی‌امان ⇒ مسدودی ۲ دقیقه‌ای توکن | retry کوتاه‌تر از خواستهٔ صرافی، احتمال بلاک شدن | **رفع شد** |
| ۵ | حداقل ارزش سفارش | بروکر هیچ حداقلی نداشت | `SmallOrder` = ۳٬۰۰۰٬۰۰۰ ریال / ۱۱ USDT | بروکر سفارشی را پر می‌کرد که صرافی رد می‌کند | **رفع شد** (دروازهٔ `min-notional` در `preflight`) |
| ۶ | بازار بسته | نادیده | `isClosed` در `/market/stats` | پر شدن سفارش در بازار تعطیل | **رفع شد** (دروازهٔ `market-closed`) |
| ۷ | دقت مقدار و قیمت | هر مقدار دلخواه پذیرفته می‌شد | مقدار truncate، قیمت round؛ `amountPrecisions`/`pricePrecisions` در `/v2/options` | مقدار ۰٫۰۰۰۲۳۴۵۶۷۸۹ و قیمت خارج از تیک ثبت می‌شد | **رفع شد** (`truncate_amount`/`round_price` + `apply_options`) |
| ۸ | درخواست کندل با `from` حدسی | `from`/`to` با محاسبهٔ خودمان | `countback` بر `from` اولویت دارد | پنجرهٔ خالی وقتی سقف بازهٔ سرور جای دیگری بود | **رفع شد** (ارسال `countback`) |
| ۹ | `s:"no_data"` | پیام «circuit breaker is open» | UDF: `s:"no_data"` یعنی «کندلی نیست»، نه قطعی | خواننده دنبال شبکه می‌رفت | **رفع شد** (پیام `no_data` + `errmsg`) |
| ۱۰ | کانال و payload وب‌سوکت | `market:BTCIRT` و ساختار `{bestBid}` | `public:orderbook-BTCIRT`؛ `pub.data` رشتهٔ JSON با اسنپ‌شات `[[price, amount]]` | با فعال‌کردن WS هیچ‌چیز نمی‌رسید | **رفع شد** |
| ۱۱ | `clientOrderId` | سفارش هیچ شناسه‌ای نداشت، ولی UI ادعا می‌کرد دارد | `clientOrderId` ≤ ۳۲ کاراکتر برای idempotency | ادعای بلیت دروغ بود | **رفع شد** (۲۷ کاراکتر، در `Position` و `OrderEvent`) |

### مغایرت‌هایی که حین همین کار بیرون افتادند (نه در مستندات، در خودمان)

| # | مسئله | اثر | حکم |
|---|---|---|---|
| ۱۲ | سرمایهٔ حساب کاغذی بعد از restart با نرخ ارز ضرب می‌شد | `_mark_equity_minute` وقتی `usdt_irt=0` بود تقسیم بر ۱٫۰ می‌کرد ⇒ تومان به‌جای دلار | **رفع شد**: نرخ ورود روی پوزیشن ذخیره می‌شود (`quote_usd`) |
| ۱۳ | فیکسچر `status:"ok"` نداشت | `/api/health` می‌گفت «صرافی در دسترس نیست» ولی داشبورد قیمت می‌کشید | **رفع شد** |
| ۱۴ | `preflight` سقف ۲۵٪ را با خطای شناور رد می‌کرد | سفارش دقیقاً روی سقف: «۲۵٪ در برابر ۲۵٪ مسدود» | **رفع شد** (تلورانس ۱e-9 + متن دروازه فقط در شکست) |
| ۱۵ | `nbx-core.js` پلن ریسک محلی با `size_quote: 18500` می‌ساخت | پیش‌نمایشی از هیچ | **رفع شد** (پیام «پلن در دسترس نیست») |
| ۱۶ | `postPaperOrder` در خطای شبکه به موتور JS می‌افتاد | سفارش ساختگی فقط در مرورگر، با ۲ دروازه به‌جای ۱۰ | **رفع شد** (خطای شبکه صریح) |

---

## ۲. جزئیات هر اصلاح

### ۲.۱ نماد بازار: `btc-rls` ← `BTCIRT`

```python
def market_symbol(key):        # server/units.py
    if '-' in s:
        coin, _, dst = s.partition('-')
        return f"{coin.upper()}IRT" if dst in ("rls", "irt") else f"{coin.upper()}{dst.upper()}"
```

`norm()` قبلی `btc-rls` را به `BTC-RLS` تبدیل می‌کرد. این رشته به `/v3/orderbook/`
و `/market/udf/history` می‌رفت؛ صرافی «بازار ناموجود» می‌داد و UI آن را «بازار خالی»
نشان می‌داد. تست: `test_universe_loads_the_documented_stats_keys`
(شامل اینکه بلوک منسوخ `global` کنار گذاشته می‌شود).

> **نکتهٔ ریز:** کلید منسوخ `global` (آمار بایننس) عمداً حذف می‌شود؛ مستند می‌گوید
> در آینده حذف خواهد شد و «قیمت جهانی» چیزی نیست که این داشبورد معامله کند.

### ۲.۲ ریال در برابر تومان

سه تابع تبدیل در `units.py` و **فقط** در `NobitexClient` صدا زده می‌شوند:

| داده | تبدیل |
|---|---|
| `latest`, `bestBid/Ask`, `dayOpen/High/Low`, `mark`, `volumeDst` | ÷ ۱۰ |
| `dayChange` (درصد) و `volumeSrc` (ارز پایه) | بدون تغییر |
| سطوح اردربوک `[[price, amount]]` | قیمت ÷ ۱۰، مقدار دست‌نخورده |
| OHLC در UDF | ÷ ۱۰، حجم دست‌نخورده |

`NBX_PRICE_UNIT=toman` تنها سوئیچ است اگر روزی منبعی تومانی شد.
تست: `test_stats_quoted_in_rial_are_converted_to_toman`،
`test_orderbook_and_candles_of_a_rial_market_are_converted_too`.

**شاهد زنده:** `USDTIRT = 100,805` (تومان)، `BTCIRT ≈ 9.0e9` (تومان) — نسبت
≈ ۸۹٬۰۰۰ دلار، یعنی عدد با برچسبش می‌خواند.

### ۲.۳ پوشش خطای کاری

```python
if isinstance(body, dict) and body.get("status") == "failed":
    raise ClientError(str(body.get("code") or "FAILED"), str(body.get("message") or "…"))
```

و در ۴۲۹:

```python
raise RateLimitError("RATE", "rate limited by Nobitex",
                     {"status": 429, "backoff_s": float(body.get("backOff") or 0),
                      "limit": body.get("limit")})
```

`sleep(min(60, backoff_s or 1.2*attempt))` — دیگر «حدس خودمان» جای «خواستهٔ صرافی»
را نمی‌گیرد. تست: `test_a_200_with_status_failed_is_an_error_not_a_result`،
`test_429_exposes_the_backoff_the_exchange_asked_for`.

### ۲.۴ دروازه‌های صرافی در `preflight`

```python
Gate("market-closed", not market_closed, …)
Gate("min-notional", notional_usd >= min_notional_usd - 1e-9, …)
```

چون `preflight` تنها منبع `/api/risk/whatif` و `PaperBroker.place_limit` است،
پیش‌نمایش و اجرا با هم عوض شدند. حداقل با نرخ همان لحظه به دلار تبدیل می‌شود
(`min_notional_usd(symbol, rate)`): ۳٬۰۰۰٬۰۰۰ ریال = ۳۰۰٬۰۰۰ تومان.

**شاهد زنده** (سفارش ۱۰۰٬۰۰۰ تومانی):

```
min-notional: $0.97 is below the exchange minimum ($2.95)
```

و در UI: «مسدود می‌شود: حداقل ارزش سفارش صرافی» — تصویر
`docs/reviews/conformance-whatif.png`.

### ۲.۵ دقت مقدار و قیمت

- مقدار **truncate** می‌شود (`floor`)، قیمت **round half-up**؛ گرد کردن بانکی
  آن‌چیزی نیست که صرافی مستند کرده.
- جدول دقت پیش‌فرض در `units.py` است و در startup با `/v2/options`
  (`amountPrecisions`، `pricePrecisions`، `minOrders`) بازنویسی می‌شود.
  لاگ: `options: 5 precision/minimum override(s) applied`.

### ۲.۶ وب‌سوکت

پیش از این: کانال `market:BTCIRT` (هرگز منتشر نمی‌شود) و انتظار
`{"bestBid": ...}`. پس از این: `public:orderbook-BTCIRT`، پارس `pub.data` به‌عنوان
رشتهٔ JSON، گرفتن بهترین bid/ask از آرایه‌ها، و تبدیل ریال→تومان.
تست: `test_ws_parses_the_published_orderbook_frame`.

### ۲.۷ سرمایه و نرخ ارز

بدترین باگ این دور، و پنهان‌ترین:

```python
# قبل
p.filled_qty * (p.last_price or p.entry_price) / (self.usdt_irt if … else 1.0)
```

اگر `usdt_irt` صفر بود (بعد از restart، تا اولین سفارش IRT)، تومان بر ۱ تقسیم
می‌شد: سرمایهٔ ۱۰۰٬۰۰۰ دلاری به **۲٬۰۸۳٬۱۴۷** می‌پرید. اکنون نرخ ورود روی پوزیشن
ذخیره می‌شود و بستن/TP1 هم از همان استفاده می‌کند؛ برای stateهای قدیمی از
`size_quote/size_usd` بازسازی می‌شود.
تست: `test_equity_marks_a_persisted_irt_position_without_a_live_fx_rate`.

---

## ۳. بندهایی که *بررسی شد و مغایرت نبود*

| بند مستند | وضعیت ما |
|---|---|
| `Authorization: Token <hex>` و `X-TOTP` | ما هیچ هدر احراز نمی‌فرستیم؛ `_auth_headers` عمداً `AuthRequired` می‌دهد (ADR-0003/0009) ⇒ **منطبق با تصمیم، نه با API** |
| `POST /market/orders/add` (سفارش واقعی) | هرگز صدا زده نمی‌شود؛ تنها بروکر کاغذی |
| صفحه‌بندی `page`/`pageSize` | روت‌های خصوصی استفاده نمی‌شوند |
| تعداد درخواست: `market/stats` ۲۰/دقیقه | TTL جهان ۱۲۰ ثانیه ⇒ ۰٫۵/دقیقه |
| `v3/orderbook` ۳۰۰/دقیقه | poll هر ۵ ثانیه برای هر نماد ⇒ ۱۲/دقیقه/نماد |
| محدودیت ۳۰۰ سفارش/۱۰ دقیقه | هیچ سفارشی به صرافی نمی‌رود |
| OHLC: `s:"ok"/"no_data"/"error"` | هر سه حالت حالا متمایزند |
| مقادیر پولی به‌شکل رشته | `units` همه را به float تبدیل می‌کند؛ رشته‌بودن ورودی مشکلی نمی‌سازد |
| `srcCurrency`/`dstCurrency` در پاسخ سفارش **نام نمایشی** است («ریال»، «Litecoin») | در مسیر کاغذی خوانده نمی‌شود؛ اگر روزی لایو شد، تله است ⇒ ثبت شد |

---

## ۴. باقی‌مانده‌ها (صادقانه، بدون گرد کردن)

| # | موضوع | چرا بسته نشد |
|---|---|---|
| ۱ | **آدرس پایهٔ تازهٔ API** | `changelog` می‌گوید «خرداد ۱۴۰۴: تغییر آدرس پایه API‌ها» ولی آدرس جدید را نمی‌دهد. همهٔ نمونه‌های مستند هنوز `https://apiv2.nobitex.ir` هستند. تغییری ندادیم؛ `NBX_BASE_URL` برای همین هست. |
| ۲ | **`/v2/options` در محیط واقعی** | روی این ماشین به صرافی نمی‌رسیم؛ فقط با فیکسچر آزموده شد. مسیر خطا (ماندن روی مقدار مستندشده) تست دارد. |
| ۳ | **جریان سفارش واقعی** | `clientOrderId` تولید می‌شود ولی به صرافی نمی‌رود (اجرای زنده خاموش). تله‌های ثبت‌شده: نام نمایشی ارزها، `status: Active/Done/Inactive/Canceled`، `backOff`. |
| ۴ | **صفحهٔ سفارش‌ها در UI** | آرایهٔ `orders` در `index.html` هیچ‌وقت از سرور پر نمی‌شود و روت `/api/paper/orders` وجود ندارد. جدول همیشه «چیزی برای نمایش نیست» می‌گوید — عدد نیست، ولی «چرخه‌حیات زندهٔ سفارش‌ها» هم نیست. |
| ۵ | **کرون `candle-{market}-{resolution}`** | کانال کندل و `market-stats-all` در مستند هست؛ ما هیچ‌کدام را مصرف نمی‌کنیم (poll می‌کنیم). |
| ۶ | **بروکر JS در باندل مستقل** | در `nobitex-mobile.html` (بدون بک‌اند) موتور محلی تنها منبع است و همان ۲ دروازه را دارد؛ قواعد صرافی در آن پیاده نشده. |
| ۷ | **نمادهای جدید** | فهرست دستی `SYMBOLS` حذف شد (هیچ‌جا خوانده نمی‌شد و با مستند نمی‌خواند: `MATICIRT` نداشت). منبع، `market/stats` است. |

---

## ۵. شاهد اجرایی

```
pytest -q server/tests                     ⇒ 222 passed
python scripts/probe_live.py --url :8022  ⇒ PASS — 45 panels, 3 cross-checked
python scripts/layout_test.py --url :8022 ⇒ PASS — 3 viewports, 10 tabs
/api/risk/whatif (BTCIRT)                  ⇒ 10 gates, blocked_by=null, min-notional $2.95
/api/paper/order size_quote=100000         ⇒ ok:false, "min-notional: $0.97 is below the exchange minimum ($2.95)"
/api/paper/order size_quote=2000000        ⇒ ok:true, qty=0.000221 (۶ رقم اعشار، truncate)
/api/paper/metrics بعد از restart          ⇒ equity=100000 (قبلاً 2,083,147)
```

> عددها از فیکسچر هم‌قرارداد (`scripts/mock_upstream.py`) می‌آیند که خودش اکنون
> payload مستندشده تولید می‌کند (`btc-rls`، ریال، `status:"ok"`) تا مسیر تبدیل
> واقعاً آزموده شود.

---

## ۶. فایل‌های تغییریافته

| فایل | نقش |
|---|---|
| `server/units.py` | **جدید** — تنها نقطهٔ تبدیل واحد/نماد/دقت/حداقل |
| `server/nobitex_client.py` | پوشش `status:"failed"`، `backOff`، `countback`، `no_data`، فراخوانی `units`، حذف `SYMBOLS` |
| `server/main.py` | نماد از `btc-rls`، `volumeDst`، `closed_markets`، `_load_options`، دو دروازه در what-if |
| `server/paper_trading.py` | دقت مقدار/قیمت، `quote_usd`، `clientOrderId`، نرخ ورود در بستن/TP1 |
| `server/risk_engine.py` | دروازه‌های `min-notional` و `market-closed`، تلورانس شناور |
| `server/ws_execution.py` | کانال عمومی، پارس اسنپ‌شات، تبدیل ریال |
| `nbx-core.js` | حذف پلن جعلی `18500`، حذف پرکردن محلی سفارش |
| `index.html` | برچسب فارسی/انگلیسی دروازه‌ها |
| `scripts/run_with_mock_exchange.py` | payload مستندشده، `/v2/options`، `status:"ok"` |
| `server/tests/test_api_conformance.py` | **جدید** — ۲۶ تست |
| `server/tests/test_core.py` | اصلاح دو تست که با واحدهای ناهمگون نوشته شده بودند |
