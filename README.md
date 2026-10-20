# ربات معامله‌گر نوبیتکس — اتاق فرمان

داشبورد دوزبانه (فارسی/انگلیسی) با موتور تحلیل تکنیکال، موتور فاندامنتال،
مدیریت ریسک، معاملات کاغذی، بک‌تست Walk-Forward، ژورنال سیگنال و بک‌اند
FastAPI.

---

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
python3 -m venv ../.venv
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
python3 scripts/build_standalone.py   # nobitex-mobile.html
```

باندل تک‌فایلی از `index.html` ساخته می‌شود؛ اگر بعد از تغییر استایل، `npm run
css` را نزنی، `build_standalone.py` هشدار می‌دهد که CSS از markup عقب است.

---

## تست‌ها — هر کدام یک چیز مشخص را اثبات می‌کنند

| دستور | چه چیزی را |
|---|---|
| `python3 -m pytest server/tests -q` | منطق بک‌اند + قرارداد «هر پنل می‌تواند خودش را توضیح دهد» |
| `python3 scripts/probe_live.py --selftest` | خودِ طبقه‌بند پنل‌ها (۸ حالت) |
| `python3 scripts/probe_live.py --url http://127.0.0.1:8000` | حالت «داده نیست»: ۴۵ پنل باید خودشان را توضیح بدهند |
| `python3 scripts/mock_upstream.py --port 8010` + پروب روی ۸۰۱۰ | حالت «داده هست»: ۴۵ پنل باید عدد واقعی نشان بدهند |
| `python3 scripts/layout_test.py` | ۳ ویوپورت × ۱۰ تب: سرریز افقی، نمودارهای خالی |
| `python3 scripts/ui_smoke_prep.py && node scripts/ui_smoke_test.js` | DOM و MSI بدون خطای JS |

---

## تأیید مسیر داده زنده

از بیشتر ماشین‌های بیرون ایران، `apiv2.nobitex.ir` در دسترس نیست. `probe_live.py`
برای همین دو حالت را جدا می‌سنجد و کد خروج می‌دهد:

```bash
python3 scripts/probe_live.py --json probe.json --md probe.md
```

| کد خروج | معنا |
|---|---|
| ۰ | مسیر زنده تأیید شد |
| ۱ | حداقل یک پنل باگ دارد (گزارش، تب و پنل را می‌گوید) |
| ۲ | نوبیتکس در دسترس نبود؛ فقط نیمهٔ «خطا» سنجیده شد |

برای آزمایش نیمهٔ زنده روی ماشینی که به نوبیتکس نمی‌رسد، `mock_upstream.py` یک
فیکسچر هم‌قرارداد است (`source: "mock"`، هیچ عددی از آن در باندل نیست):

```bash
python3 scripts/mock_upstream.py --port 8010
python3 scripts/probe_live.py --url http://127.0.0.1:8010
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

## مستندات

- [`docs/README.md`](docs/README.md) — نقشهٔ مستندات (از کجا شروع کنم)
- [`docs/adr/`](docs/adr) — ۱۴ ADR دوزبانه (قرارداد داده در ADR-0014)
- [`docs/adr/README.md`](docs/adr/README.md) — کدام ADR پیاده شده و شاهدش کجاست
- [`docs/reviews/2026-10-20-live-path-verification-fa.md`](docs/reviews/2026-10-20-live-path-verification-fa.md)
  — تأیید مسیر زنده: ۲۰ باگ، فیکسچر، و آنچه هنوز اثبات نشده
