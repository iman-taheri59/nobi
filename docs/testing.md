# تست‌ها روی ماشینی که نوبیتکس در دسترس است

CI و بیشتر تست‌های این مخزن جایی اجرا می‌شوند که `apiv2.nobitex.ir` در دسترس
نیست، پس فقط نیمی از قرارداد را می‌سنجند: «وقتی داده نیست، بگو نیست». نیم
دیگر — «وقتی داده هست، عدد واقعی است» — فقط روی ماشینی سنجیده می‌شود که به
صرافی می‌رسد. این سند همان مسیر است، به ترتیبِ ارزان به گران.

---

## ۰) یک‌بار

```bash
cd /path/to/nobitrader

# پایتون (سرور + ابزار تست)
pip install -r server/requirements-dev.txt

# جاوااسکریپت (eslint + jsdom)، سپس CSS و باندل
npm ci
npm run css

# مرورگر برای سه اسکریپت UI (probe_live / layout_test / fa_label_test)
# اگر این دانلود نشد، بخش ۳٫۳ سه راه دیگر دارد.
python -m playwright install chromium
```

---

## ۱) دروازه‌های ایستا — بدون شبکه، چند ثانیه

هر چهار دستور باید **صفر** بدهند. عدد غیرصفر یعنی build باید قرمز باشد.

```bash
ruff check server scripts      # All checks passed!
mypy                           # Success: no issues found in N source files
npm run lint                   # بی‌خروجی
npm run lint:dashboard         # 0 error(s), 0 warning(s) in 1 inline block
```

---

## ۲) تست‌های قرارداد — بدون سرور، ~۱۰ ثانیه

```bash
python -m pytest -q server/tests
```

انتظار: `321 passed` (این عدد با هر تست تازه بالا می‌رود؛ مهم صفر بودنِ failed است).

سه تستی که به مرورگر یا سرور نیاز ندارند و مستقل ارزش دارند:

```bash
python scripts/probe_live.py --selftest        # طبقه‌بند پنل‌ها، ۸ حالت
node scripts/js_broker_test.js                 # بروکر مرورگر == سرور
python scripts/check_preflight_parity.py       # ۲۸ حالت × ۱۰ دروازه
```

---

## ۳) روی سرور واقعی — نیمه‌ای که فقط تو می‌توانی بسنجی

اول سرور را بالا بیاور (پنجرهٔ جدا، بگذار باز بماند):

```bash
cd server
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

۱۵ تا ۳۰ ثانیه صبر کن تا اسکن اول تمام شود. سه خطی که باید در کنسول ببینی:

```
INFO:nbx-app:universe: 60 rows loaded (premium 0.xxx%)
INFO:nbx-app:rescan 15m: NN symbols, FA NN/NN
INFO:nbx-app:journal: NN new prediction(s) on 15m
```

اگر `NN symbols` خیلی کمتر از تعداد universe بود (مثلاً ۵ از ۴۰)، نیمهٔ اسکن
را از دست داده‌ای — بخش ۵ را ببین.

### ۳٫۱ پروب ۴۵ پنل — مهم‌ترین دستور این سند

```bash
python scripts/probe_live.py --url http://127.0.0.1:8000 \
       --json probe-live.json --md probe-live.md
```

برای هر پنل در ده تب یک سؤال: عدد واقعی از صرافی آمده، صادقانه می‌گوید
«نمی‌توانم»، یا خالی است و توضیحی هم ندارد؟ سومی همیشه باگ است.

| خروجی | معنا |
|---|---|
| `0` | PASS — همهٔ ۴۵ پنل خودشان را توضیح می‌دهند |
| `1` | حداقل یک پنل باگ دارد؛ گزارش نام تب و پنل را می‌گوید |
| `2` | نوبیتکس در دسترس نبود؛ فقط نیمهٔ «خطا» سنجیده شد |

`probe-live.md` را باز کن: جدولی از هر پنل با ستون‌های `DATA / EMPTY /
EXPLAINED / BLANK`. دنبال کلمهٔ `BLANK` بگرد.

### ۳٫۲ برچسب‌های فاندامنتال

```bash
python scripts/fa_label_test.py --url http://127.0.0.1:8000 --case all
```

سه حالت را می‌سنجد: `live` (از ارائه‌دهنده)، `cached` (کش)، `fallback`
(پروفایل ثابت). مهم‌ترینش این است که در حالت fallback صفحه **بگوید** «پروفایل
ثابت، بدون تماس زنده» — نه اینکه عدد را مثل دادهٔ زنده نشان دهد.

### ۳٫۳ چیدمان در مرورگر واقعی

```bash
python scripts/layout_test.py --url http://127.0.0.1:8000
```

سه عرض (۳۹۰ / ۷۶۸ / ۱۴۴۰) × ده تب: اسکرول افقی، تبی که باز نمی‌شود، ردیفی که
نمودارش خالی است. jsdom این‌ها را نمی‌بیند؛ فقط این اسکریپت می‌بیند.

#### اگر مرورگر بالا نیامد

دو خطای متفاوت‌اند و راه‌حلشان هم متفاوت است. اول تشخیص بده کدام است:

| پیام | یعنی | راه |
|---|---|---|
| `the playwright package is not installed for this interpreter` | **پکیج** پایتون نصب نیست — نه این‌که مرورگر کم است | با همان مسیری که پیام چاپ می‌کند: `"...\python.exe" -m pip install playwright` |
| `could not start a browser: …` | پکیج هست، مرورگر نیست | یکی از سه راه پایین |

⚠️ رایج‌ترین تله در ویندوز: `python` خالی، venv پروژه نیست. اگر پکیج را در venv
نصب کرده باشی و اسکریپت را با `python` اجرا کنی، پیام «نصب نیست» می‌گیری. از
این به بعد خود اسکریپت مسیر مفسرِ در حال اجرا را در همان پیام می‌نویسد؛ همان را
با `-m pip install` استفاده کن. **`--channel` جای نصب پکیج را نمی‌گیرد** — فقط
جای دانلود مرورگر را.

اگر پکیج هست و فقط Chromium دانلود نمی‌شود: Playwright یک **نسخهٔ مشخص** را از
CDN خودش می‌گیرد؛ شبکه‌ای که آن دامنه را مسدود کند (یا پروکسی/آنتی‌ویروس شرکتی
که فایل اجرایی را می‌بلعد) این مرحله را متوقف می‌کند. لازم نیست با آن بجنگید —
**هر سه اسکریپت مرورگری** دو راه دیگر دارند:

```bat
:: ۱) مرورگری که از قبل نصب است. روی ویندوز، Edge هست.
python scripts\layout_test.py   --url http://127.0.0.1:8000 --channel msedge
python scripts\probe_live.py    --url http://127.0.0.1:8000 --channel msedge --json probe-live.json --md probe-live.md
python scripts\fa_label_test.py --url http://127.0.0.1:8000 --case all --channel msedge

:: ۲) هر باینری Chromium که خودت دانلود کرده‌ای
python scripts\layout_test.py --url http://127.0.0.1:8000 --executable C:\tools\chrome-win64\chrome.exe

:: یا یک‌بار در همان پنجره (برای هر سه اسکریپت):
set NBX_CHROME=C:\tools\chrome-win64\chrome.exe
python scripts\layout_test.py --url http://127.0.0.1:8000
```

**دربارهٔ zip‌ای که خودت دانلود کرده‌ای:** Playwright ساختار پوشه‌ای خاص خودش
(`chromium-<build>/chrome-win/chrome.exe`) را نمی‌خواهد؛ فقط مسیر یک فایل
اجرایی را می‌خواهد. پس:

1. zip را باز کن (هر جایی — لازم نیست در پوشهٔ Playwright باشد).
2. `chrome.exe` را داخلش پیدا کن (معمولاً یک سطح زیر ریشه).
3. همان مسیر را به `--executable` بده.

اگر نسخهٔ آن بیلد خیلی قدیم یا خیلی تازه باشد، Playwright ممکن است در
پروتکل CDP به مشکل بخورد؛ در آن حالت `--channel msedge` انتخاب مطمئن‌تری
است. پیام خطا در این حالت صریح است و هر سه راه را نشان می‌دهد:

```
FAIL
 - could not start a browser: BrowserType.launch: ...
one of these fixes it:
  python -m playwright install chromium     # Playwright's own build
  --channel msedge                          # Edge, already on Windows
  --executable C:\path\to\chrome.exe       # a build you unpacked
```

### ۳٫۴ DOM و کارت MSI

```bash
python scripts/ui_smoke_prep.py
node scripts/ui_smoke_test.js
```

server باید بالا باشد (صفحه داده را از آن می‌گیرد). انتظار: `PASS`.
مسیر فایل موقت با `NBX_SMOKE_PAGE` قابل تغییر است.

---

### ۳٫۵ همان تست‌ها، از داخل خود داشبورد

تب **لاگ خطاها** حالا دو کارت دارد:

| کارت | چه می‌سنجد | چه وقت |
|---|---|---|
| بررسی سلامت سیستم | **این پردازهٔ در حال اجرا** — لاگ، ژورنال، حساب کاغذی، سقف‌های ریسک، تازگی نرخ USDT/IRT، فهرست بازار، کلیدها، وب‌سوکت، و با دکمهٔ «با صرافی»: `/market/stats`، کندل BTCIRT و دفتر سفارش | هر وقت |
| تست‌های پروژه | **این کد** — همان `pytest` / `ruff` / `mypy` که CI اجرا می‌کند | فقط وقتی سرور از پوشهٔ کد منبع اجرا شده |

چهار چیزی که این کارت‌ها می‌گویند و باید جدی گرفته شوند:

- **سالم / خراب / نامعلوم / خطای خود بررسی** چهار وضعیت جدا هستند.
  «نامعلوم» یعنی سنجیده نشد — نه اینکه سالم است. (مثلاً پردازه هنوز با صرافی
  حرف نزده، پس نرخی برای قضاوت نیست.)
- **تیتر، بدترین وضعیت است، نه اکثریت.** ده سبز و یک قرمز = قرمز.
- **هر ردیف می‌گوید چه دید**: `wrote #812, read it back` نتیجه است، «سالم» نه.
- **اجرای تست، حافظهٔ زنده را دست نمی‌زند.** زیر-process با `NBX_JOURNAL_DB`
  و `NBX_PAPER_STATE` جدا اجرا می‌شود؛ وگرنه یک تست که عمداً خطا می‌سازد،
  ردیف ساختگی در همین لاگ می‌نویسد.

از خط فرمان، همان دو کار بدون مرورگر:

```bash
curl -s -X POST http://127.0.0.1:8000/api/diagnostics/run \
     -H 'Content-Type: application/json' -d '{"kinds":["core","storage"]}' | python -m json.tool
curl -s http://127.0.0.1:8000/api/devsuite          # available? چرا نه؟
curl -s -X POST http://127.0.0.1:8000/api/devsuite/run -d '{}' -H 'Content-Type: application/json'
curl -s http://127.0.0.1:8000/api/devsuite/<run_id>  # تا state از running در بیاید
```

`NBX_DEV_TOOLS=0` اجرای تست از داشبورد را خاموش می‌کند؛ در نصب مستقر
(بدون `server/tests/`) خودبه‌خود خاموش است و دلیلش نوشته می‌شود.

---

## ۴) چیزی که فقط با چشم خودت دیده می‌شود

این‌ها assertion ندارند؛ چهار عددی است که اگر خراب باشند یعنی جایی دروغ گفته.

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/universe
curl -s http://127.0.0.1:8000/api/risk/state
curl -s "http://127.0.0.1:8000/api/signals?tf=15m"
```

| چه چیزی را ببین | سالم | خراب |
|---|---|---|
| `health.upstream.ok` | `true` | `false` ⇒ پنل‌های بازار باید جمله بدهند نه عدد |
| `health.stats.success_rate` | بالای ۹۵ | پایین ⇒ بخشی از درخواست‌ها رد می‌شوند |
| `universe.count` | ۶۰ish | صفر ⇒ اسکن بدون بازار |
| `risk.state.fx.age_s` | زیر ۳۰۰ | بالای ۳۰۰ ⇒ نرخ کهنه نباید `equity_irt` بدهد |
| `risk.state.caps.riskPct` | ۰٫۷۵ | عددی دیگر ⇒ نسخهٔ سوم قواعد ریسک برگشته |
| `signals.count` | نزدیک به universe منهای ۱ (USDTIRT) | خیلی کمتر ⇒ بنگر بخش ۵ |
| `signals.scored[].data_source` | `live` | `cache` برای همه ⇒ شبکه یا ۴۲۹ |

---

## ۵) پنج رگرسیونی که این پروژه قبلاً داشته — و چه چیزی نشانشان می‌دهد

| نشانه در لاگ/صفحه | باگی که برگشته |
|---|---|
| `circuit breaker is open` برای نمادهای بی‌ربط، یا `rescan: 5 symbols` بعد از یک ۴۰۳ | یک خطای per-symbol دارد breaker سراسری را باز می‌کند (`is_outage`) |
| `rate from usdt-irt is NNNNs old` به‌صورت ۵۰۰ | نرخ کهنه دوباره به‌عنوان نرخ برگشته |
| سفارش IRT در حالت قطعی صرافی **ثبت می‌شود** | `no-fx-rate` دور زده شده |
| هشدار «از سقف ۰٫۲۵٪٪» (دو درصد) یا هشدار با عددی غیر از `risk.state.caps` | قاعدهٔ ریسک دوباره دو نسخه شده |
| پنلی با «—» تنها، بدون جمله | پنل خودش را توضیح نمی‌دهد (`probe_live` می‌گیرد) |

---

## ۶) اجرای طولانی (نیم‌ساعته) — چیزی که CI هرگز نمی‌بیند

سرور را نیم ساعت روی فید واقعی بگذار و بعد:

```bash
curl -s http://127.0.0.1:8000/api/health        # stats.fail، success_rate
curl -s http://127.0.0.1:8000/api/risk/state    # fx.age_s
curl -s http://127.0.0.1:8000/api/journal/summary | python -m json.tool | head -40
```

سالم: `success_rate` بالای ۹۵، `fx.age_s` همیشه زیر ۳۰۰، ژورنال با `source:
live` پر می‌شود. اگر `fail` بالا رفت، پیام‌های `nbx-client` در کنسول می‌گوید
کدام مسیر.

---

## ۷) اگر خواستی همان چیزی را که CI می‌بیند بازتولید کنی

```bash
# فیکسچر هم‌قرارداد (به نوبیتکس نیاز ندارد)
python scripts/mock_upstream.py --port 8010 &
python scripts/run_with_mock_exchange.py --port 8000 --exchange http://127.0.0.1:8010 &
python scripts/probe_live.py --url http://127.0.0.1:8000
```

با `NBX_RATE_TTL_S` و `NBX_UNIVERSE_TTL_S` می‌شود حالت «فید مرده» را عمداً
ساخت و مسیر `no-fx-rate` را دید (بخش ۵).
