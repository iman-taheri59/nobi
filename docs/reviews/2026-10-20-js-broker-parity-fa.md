# بروکر مرورگر، هم‌قاعده با بروکر سرور — ۱۴۰۵/۰۷/۲۸ (2026-10-20)

**مسئله:** `nobitex-mobile.html` (باندل تک‌فایلی که روی گوشی باز می‌شود) یک بروکر
کاغذی جدا دارد. آن بروکر **سه** بررسی داشت — کلید توقف، سقف ۲۵٪ تک‌دارایی، موجودی —
در حالی که سرور **ده** دروازه اجرا می‌کند. علاوه بر آن، مبلغ را به ارز مظنه با
موجودی دلاری مقایسه می‌کرد: سفارش ۰٫۰۱ BTCIRT (~۹ میلیون تومان) در برابر سقف
۲۵٬۰۰۰ تومانی، یعنی عملاً هیچ سفارش IRT ممکن نبود، و سفارش‌های USDT هم بدون
حداقل صرافی و بدون دقت مقدار پر می‌شدند.

این همان الگوی ADR-0014 است، یک لایه پایین‌تر: دو پیاده‌سازی از یک قاعده که
هیچ‌کدام نمی‌داند دیگری چه می‌گوید.

---

## ۱. چه چیزی عوض شد

| فایل | تغییر |
|---|---|
| `nbx-core.js` | `NBX.Units` (نماد، دقت، حداقل صرافی)، `NBX.Risk.preflight` (پورت دقیق `risk_engine.preflight`)، بازنویسی `NBX.Paper.placeOrder` روی همان دروازه‌ها، و تبدیل `btc-rls`→`BTCIRT` + ریال→تومان در `loadUniverse` |
| `scripts/check_preflight_parity.py` | ۲۸ سناریو × ۱۰ دروازه: Python و JS باید **دروازه‌به‌دروازه** یکی باشند |
| `scripts/preflight_parity.js` | پل node برای همان مقایسه |
| `scripts/js_broker_test.js` | ۹ تست رفتار: نرخ ارز، حداقل صرافی، بازار بسته، دقت، سرمایه |
| `server/tests/test_js_parity.py` | هر دو اسکریپت داخل `pytest` اجرا می‌شوند |
| `index.html` | حذف دروازهٔ ریسک محلی در فرم سفارش دستی |

---

## ۲. پورت `preflight`

تابع JS همان امضای Python را دارد و همان محاسبات را انجام می‌دهد:

```
halted → market-closed → concurrent-cap → cash-present → single-asset-cap →
min-notional → risk-cap → heat-cap → min-cash → funds
```

همان ترتیب، همان آستانه‌ها، همان `1e-9` برای خطای شناور، همان `risk_pct_cap × 1.05`.
نام دروازه‌ها یکسان است تا پیام خطا در هر دو طرف یکی باشد:

```
سرور:   min-notional: $0.99 is below the exchange minimum ($2.98)
مرورگر: min-notional: $0.99 is below the exchange minimum ($2.98)
```

## ۳. هم‌ارزی چطور اثبات می‌شود

`check_preflight_parity.py` یک شبکهٔ قطعی می‌سازد که هر دروازه را از **دو طرف**
لمس می‌کند (سفارش دقیقاً روی سقف ۲۵٪ و یک سنت بالاتر؛ ریسک دقیقاً ۰٫۷۵٪، ۰٫۷۸۸٪،
۰٫۸۰٪؛ حرارت ۵٫۹۹ و ۶٫۰۰؛ نقدینگی روی کف ۱۰٪ و زیر آن؛ …)، هر دو پیاده‌سازی را
صدا می‌زند و اولین اختلاف را با شناسهٔ سناریو و شناسهٔ دروازه گزارش می‌کند.

همچنین **جدول‌ها و پیش‌فرض‌ها** مقایسه می‌شوند: ۱۷ مقدار دقت، ۲ تیک قیمت، ۲ حداقل
صرافی و ۵ پیش‌فرض ریسک. اگر فردا کسی یک نماد به `units.py` اضافه کند و به
`nbx-core.js` نه، این اسکریپت می‌شکند.

```
PASS — 28 cases × 10 gates: python and js agree, tables and defaults identical
```

## ۴. رفتار بروکر مرورگر (۹ تست)

```
ok   IRT order without an FX rate is refused — no-fx-rate
ok   IRT notional is converted to USD before it touches cash — size_usd=4.9452
ok   order below the exchange minimum is refused (11 USDT)
ok   order below the exchange minimum is refused (3,000,000 Rial)
ok   closed market is refused — market-closed
ok   amount truncates and price rounds before the fill — qty=0.000234 price=73040
ok   equity stays sane when the live FX rate disappears — equity=99999.99
ok   preflight exposes ten gates, including the exchange rules
ok   stats keys become symbols and Rial becomes Toman — btc-rls→BTCIRT, global→null
```

دو مورد از این‌ها جنس باگ‌های قبلی را دارند:

- **`no-fx-rate`:** قبلاً اگر `USDTIRT` در جهان نبود، مبلغ IRT بر ۱ تقسیم و
  تومان به‌جای دلار مصرف می‌شد. حالا سفارش رد می‌شود، مثل سرور.
- **سرمایه بعد از گم‌شدن نرخ:** `quote_usd` روی پوزیشن ذخیره می‌شود؛ علامت‌گذاری
  سرمایه به نرخ زندهٔ لحظه وابسته نیست.

## ۵. یک دروازهٔ تکراری که حذف شد

فرم سفارش دستی در `index.html` پیش از ارسال، خودش `risk > 0.75%` را با یک toast
عمومی رد می‌کرد — قاعده‌ای که سرور با اعداد واقعی حساب می‌کند. اگر این دو یک روز
اختلاف پیدا می‌کردند، نسخهٔ مرورگر برنده می‌شد (چون اول جواب می‌دهد) و دلیلش هم
دیده نمی‌شد. حالا تصمیم فقط در سرور گرفته می‌شود و پیامش نام دروازه را می‌گوید.

## ۶. آنچه در این دور بسته **نشد**

| # | موضوع | چرا |
|---|---|---|
| ۱ | **صف تأیید (Desk) بی‌منبع است** | `let queue = []` در `index.html` هیچ‌جا پر نمی‌شود و سرور هیچ روت صف ندارد؛ دکمه‌های تأیید/رد عملاً دست‌نیافتنی‌اند. همان جنس «سفارش‌ها» است و در گام بعد با آن یکی می‌شود. |
| ۲ | **تب سفارش‌ها** | `orders` هم خالی است؛ روت `/api/paper/orders` وجود ندارد. گام بعد. |
| ۳ | **آزمون باندل روی `file://`** | playwright در این محیط پروتکل `file:` را مسدود می‌کند؛ کد باندل با `node` آزموده شد و حضور `preflightOrder` در فایل ساخته‌شده با grep تأیید شد، ولی «باز شدنِ» فایل روی گوشی این‌جا سنجیده نشد. |

## ۷. شاهد

```
python3 -m pytest server/tests -q          ⇒ 226 passed
python3 scripts/check_preflight_parity.py  ⇒ PASS — 28 cases × 10 gates
node scripts/js_broker_test.js             ⇒ PASS — 9/9
python3 scripts/probe_live.py --url :8022  ⇒ PASS — 45 panels
python3 scripts/layout_test.py --url :8022 ⇒ PASS — 3 viewports, 10 tabs
```
