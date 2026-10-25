# nobitrader

**فارسی** · [English](README.en.md)

نسخهٔ جاری: **1.2.5** · تگ انتشار: **v1.2.5**

راهنمای [نسخه‌بندی و GitHub Release](docs/releases.md) شامل قواعد افزایش نسخه، تگ و انتشار فایل موبایل است. [تاریخچهٔ تغییرات](CHANGELOG.md) را نیز ببینید ([English](CHANGELOG.en.md)).

داشبورد دوزبانه (فارسی/انگلیسی) با موتور تحلیل تکنیکال، موتور فاندامنتال،
مدیریت ریسک، معاملات کاغذی، بک‌تست Walk-Forward، ژورنال سیگنال و بک‌اند
FastAPI.

---

## ورود به داشبورد

داشبورد حساب کاربری دارد: چند نفر، یک حساب معاملاتی مشترک، سه نقش
(`owner` / `operator` / `viewer`). عضویت فقط با **کد دعوت** است و حساب اول
از روی سرور ساخته می‌شود:

```bash
python scripts/manage_users.py create YOURNAME --role owner
```

پس از آن، `/api/*` برای ناشناس بسته است و صفحه به‌جای داده، فرم ورود نشان می‌دهد.
`NBX_API_TOKEN` می‌ماند — برای اسکریپت‌ها، نه برای مرورگر. جزئیات:
[`docs/adr/fa/ADR-0018`](docs/adr/fa/ADR-0018-حساب-کاربری-و-ورود.md) و
[راهنمای استقرار §۷](docs/deploy-arvan.md).

## اصلی که همه‌چیز را می‌سازد: هیچ عددی ساختگی نیست

هر پنل در هر لحظه یکی از این سه حالت است، و هر سه در تست بررسی می‌شوند:

| حالت | رفتار درست |
|---|---|
| داده هست | عدد واقعی، از منبعی که می‌شود نامش را برد |
| داده نیست (صرافی قطع) | یک جملهٔ صریح که می‌گوید چرا |
| داده از سرور خودمان است | چه صرافی باشد چه نباشد، نمایش داده می‌شود |

«—» تنها، هیچ‌کدام از اینها نیست؛ همان چیزی است که هفته‌ها باگ را پنهان کرد.

---

## شروع سریع

### ۱) داشبورد با بک‌اند (حالت کامل)

```bash
cd server
python -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
../.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

مرورگر: `http://localhost:8000`

تنظیمات اختیاری: `cp server/.env.example server/.env`. هیچ کلیدی لازم نیست تا
وقتی فقط می‌خوانید؛ نوشتن روی وضعیت (سفارش کاغذی، توقف اضطراری، بک‌تست) از
loopback مجاز است و از راه دور به `NBX_API_TOKEN` نیاز دارد. هیچ رازی از
مرورگر نوشته نمی‌شود.

### ۲) فایل تک‌صفحه‌ای روی گوشی

`nobitex-mobile.html` همهٔ استایل، آیکون و موتورهای محاسباتی را در خود دارد و
از `file://` هم باز می‌شود. **اما** دادهٔ بازار را از بک‌اند می‌گیرد: بدون
دسترسی به سرور، پنل‌های بازار جملهٔ «سرور نوبیتکس در دسترس نیست» را نشان
می‌دهند — نه عدد جای‌گزین. (ادعای قبلی README که «۱۰۰٪ قابلیت‌ها آفلاین
اجرا می‌شود» درست نبود و حذف شد.)

---

## ساخت

```bash
npm install                 # tailwind + jsdom
npm run css                 # assets/css/tailwind.css را می‌سازد
python scripts/build_standalone.py   # nobitex-mobile.html
```

باندل تک‌فایلی از `index.html` ساخته می‌شود؛ اگر بعد از تغییر استایل، `npm run
css` را نزنی، `build_standalone.py` هشدار می‌دهد که CSS از markup عقب است.

---

## تست‌ها — هر کدام یک چیز مشخص را اثبات می‌کنند

| دستور | چه چیزی را |
|---|---|
| `python -m pytest server/tests -q` | منطق بک‌اند + قرارداد «هر پنل می‌تواند خودش را توضیح دهد» |
| `python scripts/probe_live.py --selftest` | خودِ طبقه‌بند پنل‌ها (۸ حالت) |
| `python scripts/probe_live.py --url http://127.0.0.1:8000` | حالت «داده نیست»: ۴۵ پنل باید خودشان را توضیح بدهند |
| `python scripts/mock_upstream.py --port 8010` + پروب روی ۸۰۱۰ | حالت «داده هست»: ۴۵ پنل باید عدد واقعی نشان بدهند |
| `python scripts/layout_test.py` | ۳ ویوپورت × ۱۰ تب: سرریز افقی، نمودارهای خالی |
| `python scripts/ui_smoke_prep.py && node scripts/ui_smoke_test.js` | DOM و MSI بدون خطای JS |
| `python scripts/check_preflight_parity.py` | ده دروازهٔ ریسک در Python و JS یکی باشند |
| `node scripts/js_broker_test.js` | بروکر مرورگر: نرخ ارز، حداقل صرافی، بازار بسته، دقت |

### دروازه‌های ایستا (ADR-0016)

این‌ها همان چیزی هستند که CI اجرا می‌کند؛ محلی هم دقیقاً همین‌طور اجرا می‌شوند:

```bash
ruff check server scripts        # → All checks passed
mypy                             # → Success: no issues found
npm run lint                     # bundles + scripts
npm run lint:dashboard           # بلوک درون‌خطی index.html
```

شمارهٔ فایل‌های `mypy` عمداً در این سند نیست: تا ۱.۰.۲۲ عبارت «۵۸ source files» اینجا
مانده بود در حالی که شمار واقعی ۷۶ بود، و عددی که با هر فایل تازه کهنه می‌شود،
به‌جای مستند، تله است. معیار، همان خط «Success: no issues found» است.

پیکربندی در `pyproject.toml` و `eslint.config.mjs` است، نه در خط فرمان. آنچه
عمداً فعال **نشده** و دلیلش، در ADR-0016 آمده است (طول خط، `except Exception`).
`check_untyped_defs` فعال است؛ بدنهٔ تست‌ها با یک directive در خط اول خودشان از
آن معاف‌اند.

CI سه job دارد: `python` (ruff + mypy + pytest)، `js` (eslint + برابری بروکر و
preflight + تازگی `nobitex-mobile.html`)، و `ui` (۴۵ پنل روی فیکسچر صرافی +
اسموک jsdom با ۷ سلول KPI، صداقت کارت MSI و صفر خطای JS).

---

## تأیید مسیر داده زنده

از بیشتر ماشین‌های بیرون ایران، `apiv2.nobitex.ir` در دسترس نیست. `probe_live.py`
برای همین دو حالت را جدا می‌سنجد و کد خروج می‌دهد:

```bash
python scripts/probe_live.py --json probe.json --md probe.md
```

| کد خروج | معنا |
|---|---|
| ۰ | مسیر زنده تأیید شد |
| ۱ | حداقل یک پنل باگ دارد (گزارش، تب و پنل را می‌گوید) |
| ۲ | نوبیتکس در دسترس نبود؛ فقط نیمهٔ «خطا» سنجیده شد |

برای آزمایش نیمهٔ زنده روی ماشینی که به نوبیتکس نمی‌رسد، `mock_upstream.py` یک
فیکسچر هم‌قرارداد است (`source: "mock"`، هیچ عددی از آن در باندل نیست):

```bash
python scripts/mock_upstream.py --port 8010
python scripts/probe_live.py --url http://127.0.0.1:8010
```

---

## ساختار

```
index.html              داشبورد (تک‌صفحه، دوزبانه)
nbx-core.js             موتور TA/FA، API client، offline fallback
nbx-app.js              اسکن، سیگنال، عملکرد، میز
nobitex-mobile.html     باندل تک‌فایلی (ساخته می‌شود، دستی ویرایش نکن)
assets/css/             tailwind (src + built) و استایل‌های پایه
server/                 FastAPI: main, ta/fa/risk/portfolio/benchmark,
                        market_overview, paper, backtest, journal, errorlog,
                        notify, security, settings_view
server/tests/           pytest
scripts/                probe_live, mock_upstream, layout_test, ui_smoke, build
docs/                   ADR، تحلیل، ops، بازبینی‌ها
```

---

## مرز واحد و نماد (ADR-0015)

صرافی بازارها را `btc-rls` نام می‌دهد، نماد معامله `BTCIRT` است و قیمت بازارهای
ریالی **به ریال** است؛ داشبورد همه‌جا «تومان» می‌نویسد. هر سه تبدیل در یک فایل
است و فقط کلاینت API صدایش می‌زند:

| فایل | کار |
|---|---|
| `server/units.py` | `btc-rls → BTCIRT`، ریال→تومان، دقت مقدار/قیمت، حداقل سفارش |
| `NobitexClient` | تنها جایی که این تبدیل‌ها اجرا می‌شود |
| `risk_engine.preflight` | دروازه‌های `min-notional` و `market-closed` صرافی |
| `nbx-core.js` (`NBX.Units`، `NBX.Risk`) | همان قواعد برای باندل بدون بک‌اند؛ با تست هم‌ارزی |

نتیجه: عدد داشبورد = عدد صرافی، و سفارشی که زیر حداقل صرافی است در پیش‌نمایش هم
رد می‌شود. جزئیات: [`docs/reviews/2026-10-20-nobitex-api-conformance-fa.md`](docs/reviews/2026-10-20-nobitex-api-conformance-fa.md).

## دو روت «قبل از معامله»

| روت | چه می‌گوید | تغییر state |
|---|---|---|
| `POST /api/risk/whatif` | اگر این سفارش را بگذارم، حرارت/نقد/دروازه‌ها چه می‌شود | هرگز |
| `POST /api/portfolio/allocate` | بودجهٔ حرارت بین کاندیداهای LONG چطور تقسیم شود (با همبستگی) | هرگز |
| `POST /api/benchmark/relative` | بتا/آلفا/خطای ردیابی هر نماد نسبت به یک معیار | هرگز |
| `GET /api/market/overview` | گستردگی، پراکندگی و تمرکز گردش بازار | هرگز |

هر دو از `risk_engine.preflight` می‌خوانند — همان تابعی که `PaperBroker.place_limit`
اجرا می‌کند — تا پیش‌نمایش نتواند چیزی را وعده بدهد که بروکر رد می‌کند.
جزئیات: `docs/reviews/2026-10-20-whatif-preflight-fa.md`،
`docs/reviews/2026-10-20-a3-portfolio-allocator-fa.md`،
`docs/reviews/2026-10-20-a7-benchmark-a6-market-fa.md`.

## عملیات و بازیابی

پشتیبان سازگار از دیتابیس‌های در حال استفاده (بدون `.env` و کلیدهای API):

```bash
python scripts/state_backup.py backup --output backups/
python scripts/state_backup.py restore backups/nobitrader-state-....zip --force
```

دریافت تاریخچهٔ صفحه‌بندی‌شده با checkpoint، حذف تکرار و گزارش شکاف:

```bash
python scripts/fetch_history.py --symbol BTCIRT --resolution 15 \
  --from 2026-01-01 --to 2026-10-25 --output data/BTCIRT-15.json \
  --checkpoint data/BTCIRT-15.checkpoint.json
```

## مستندات

- [`docs/README.md`](docs/README.md) — نقشهٔ مستندات (از کجا شروع کنم) · [English](docs/README.en.md)
- [`docs/adr/`](docs/adr) — ۲۰ ADR دوزبانه؛ ADR-0019 توضیح می‌دهد چرا `nobitex-mobile.html` نسخهٔ دوم نیست و یک صفحهٔ ریسپانسیو بیشتر نداریم (قرارداد داده ADR-0014، مرز واحد ADR-0015)
- [`docs/adr/README.md`](docs/adr/README.md) — کدام ADR پیاده شده و شاهدش کجاست
- [`docs/releases.en.md`](docs/releases.en.md) — نسخه‌بندی و انتشار (انگلیسی) · [`docs/releases.md`](docs/releases.md) (فارسی)
- [`docs/reviews/2026-10-20-live-path-verification-fa.md`](docs/reviews/2026-10-20-live-path-verification-fa.md)
  — تأیید مسیر زنده: ۲۰ باگ، فیکسچر، و آنچه هنوز اثبات نشده
- [`docs/reviews/2026-10-20-nobitex-api-conformance-fa.md`](docs/reviews/2026-10-20-nobitex-api-conformance-fa.md)
  — انطباق با مستندات رسمی: ۱۶ مغایرت، چه چیزی اصلاح شد و چه چیزی باز ماند

### لوگوی نمادها

نام، نماد، شبکه‌ها و مشخصات معاملاتی از `/v2/options` نوبیتکس دریافت و کش می‌شوند. CoinGecko فقط لوگو و نامِ جایگزین را تأمین می‌کند؛ مرورگر تصاویر را از خود سرور می‌گیرد. منبع و زمان دریافت هر بخش در API مشخص است. نگاشت لوگوی ۵۰ ارز صریح است و نمادهای نامطابق خودکار جایگزین نمی‌شوند. [راهنما و تنظیمات](docs/coin-metadata.md).

۴۰ لوگوی ارز نیز به‌صورت محلی و بدون درخواست به CDN همراه پروژه است؛ نسخهٔ تک‌فایلی موبایل هم تصاویر را داخل خودش دارد. برای نمادهای بدون لوگو، حروف کوتاه نام ارز نمایش داده می‌شود. منبع و مجوز تصاویر در `assets/coins/README.md` ثبت شده است.

- ساخت مجدد: `npm run build`
- آزمون لوگوها (نسخهٔ وب و موبایل): `npm run test:icons`
