# راه‌اندازی کامل روی ویندوز با Laragon

> مثال‌ها برای نصب جدید در پوشهٔ `nobitrader` هستند. اگر نصب موجود شما پوشهٔ دیگری دارد، همان مسیر واقعی را استفاده کنید؛ تغییر نام پوشه الزامی نیست.

این سند فرض می‌کند Laragon نصب است و می‌خواهی پروژه را از مخزن بگیری و کل
سیستم را روی همان ماشین بالا بیاوری.

---

## ۰) لاراگون دقیقاً کجای این پروژه است؟

نوبی یک بک‌اند **Python/FastAPI** است که خودش صفحه را سرو می‌کند. لاراگون
(Apache/Nginx + PHP + MySQL) در این پروژه **هیچ نقشی در اجرا ندارد** — نه PHP
لازم است، نه MySQL. چیزی که از لاراگون استفاده می‌کنیم:

| استفاده | لازم است؟ |
|---|---|
| ترمینال و پوشهٔ `C:\laragon\www` | راحت است، اختیاری |
| دامنهٔ محلی مثل `http://nobitrader.test` (reverse proxy) | اختیاری |
| Apache/Nginx برای سرو فایل‌ها | ❌ لازم نیست |
| PHP / MySQL | ❌ لازم نیست |

> ⚠️ **اشتباه رایج:** در لاراگون «Create pretty URL» بزنی تا
> `C:\laragon\www\nobitrader` ریشهٔ سایت شود. آن‌وقت `index.html` از دیسک سرو می‌شود
> ولی `/api/health` و بقیهٔ مسیرها ۴۰۴ می‌دهند، چون API روی پورت ۸۰۰۰ است.
> راه درست، بخش ۶ است.

---

## ۱) پیش‌نیازها

| ابزار | نسخه | چرا |
|---|---|---|
| Python | **3.12** یا بالاتر (۳٫۱۱ کافی نیست: `numpy==2.5.4` شرط `Requires-Python >=3.12` دارد) | بک‌اند |
| Git for Windows | هر نسخهٔ تازه | گرفتن مخزن |
| Node.js | ۱۸ یا بالاتر | فقط برای بازسازی CSS و تست‌های JS |
| Laragon | نصب‌شده | ترمینال + دامنهٔ محلی |

پایتون از python.org نصب می‌شود و در نصب **تیک «Add python.exe to PATH»** را
بزن. بعد در ترمینال لاراگون:

```bat
python --version
node -v
git --version
```

اگر `python` را نشناخت، لاراگون خودش پایتون ندارد؛ همان نصب بالا انجام نشده یا
PATH باز نشده (ترمینال را ببند و دوباره باز کن).

---

## ۲) گرفتن مخزن

```bat
cd C:\laragon\www
git clone https://github.com/iman-taheri59/nobi.git nobitrader
cd nobitrader
```

بدون git هم می‌شود: از گیت‌هاب `Code → Download ZIP` و اکسترکت در همان مسیر.

---

## ۳) محیط پایتون

```bat
cd C:\laragon\www\nobitrader\server
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

آنچه نصب می‌شود: fastapi، uvicorn، httpx، pandas، numpy، pyarrow، pydantic،
websockets. همه wheel ویندوز دارند؛ کامپایلر لازم نیست.

نشانهٔ موفقیت:

```bat
python -c "import fastapi, pandas, pyarrow; print('ok')"
```

> اگر PyPI از شبکهٔ تو کند/مسدود بود، از یک آینهٔ داخلی استفاده کن:
> `pip install -r requirements.txt -i <آدرس آینه>` — آدرس آینه را خودت تأیید کن؛
> این سند هیچ آینه‌ای را توصیه نمی‌کند.

**هر بار** که می‌خواهی سرور را اجرا کنی، اول باید venv فعال باشد
(`.venv\Scripts\activate`). این فعال‌سازی در هر پنجرهٔ تازه لازم است.

---

## ۴) تنظیمات

```bat
cd C:\laragon\www\nobitrader\server
copy .env.example .env
notepad .env
```

`server/_env.py` این فایل را **قبل از** هر ماژول دیگری می‌خواند، پس هر مقداری که
در آن بگذاری واقعاً اثر دارد. متغیرهای محیطی ویندوز بر فایل اولویت دارند.

برای اجرای لوکال، فایل را می‌توانی دست‌نخورده بگذاری. تنها کلیدهایی که ممکن است
بخواهی:

| کلید | پیش‌فرض | توضیح |
|---|---|---|
| `NBX_EQUITY_USD` | `100000` | سرمایهٔ حساب کاغذی (دلار) |
| `NBX_API_TOKEN` | خالی | **توکن نوبیتکس نیست.** کلید خودِ این برنامه برای نوشتن از راه دور. روی لوکال خالی بگذار |
| `NBX_CORS_ORIGINS` | خالی | برای `file://` (فایل موبایل) مقدار `null` بگذار |
| `NBX_WS_URL` | خالی | اگر خواستی سوکت Centrifugo روشن شود: `wss://ws.nobitex.ir/connection/websocket` |
| `NBX_TELEGRAM_TOKEN` / `_CHAT_ID` | خالی | هر دو با هم؛ وگرنه چنل خاموش می‌ماند و UI هم همین را می‌گوید |
| `NBX_JOURNAL_DB` | `server/journal.db` | مسیر ژورنال پیش‌بینی‌ها |

**هیچ توکن نوبیتکسی لازم نیست.** کلاینت فقط `GET` عمومی می‌زند
(`/market/stats`، `/v3/orderbook`، `/market/udf/history`) و هیچ‌جا هدر
`Authorization` نمی‌سازد.

---

## ۵) اجرا

```bat
cd C:\laragon\www\nobitrader\server
.venv\Scripts\activate
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

برای توسعه `--reload` اضافه کن (فقط فایل‌های `.py` را می‌پاید؛ نوشتن
`journal.db` و `paper_state.json` در همان پوشه باعث ری‌استارت نمی‌شود).

مرورگر: **http://127.0.0.1:8000**

در کنسول باید این ترتیب را ببینی:

```
INFO:nbx-app:security: auth mode=loopback-only, cors=same-origin
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:nbx-app:universe: 17 rows loaded (premium 0.0%)
INFO:nbx-app:rescan 15m: 17 symbols, FA 17/17
```

اولین اجرا دو فایل می‌سازد: `server/journal.db` و `server/paper_state.json`.
۱۰ تا ۲۰ ثانیه صبر کن تا اسکن اول تمام شود؛ تا آن موقع پنل‌ها «—» نشان می‌دهند
که درست است، نه یک عدد ساختگی.

---

## ۶) دامنهٔ محلی `nobitrader.test` (اختیاری، مخصوص لاراگون)

هدف: `http://nobitrader.test` برود به همان اپ روی پورت ۸۰۰۰. این یک **reverse proxy**
است، نه سرو فایل از دیسک.

### اگر لاراگون روی Apache است

فایل `C:\laragon\etc\apache2\sites-enabled\nobitrader.test.conf` را بساز:

```apache
<VirtualHost *:80>
    ServerName nobitrader.test
    ProxyPreserveHost On
    ProxyPass        / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/
</VirtualHost>
```

در `httpd.conf` این دو خط باید بدون `#` باشند:

```apache
LoadModule proxy_module        modules/mod_proxy.so
LoadModule proxy_http_module   modules/mod_proxy_http.so
```

### اگر لاراگون روی Nginx است

فایل `C:\laragon\etc\nginx\sites-enabled\nobitrader.test.conf`:

```nginx
server {
    listen 80;
    server_name nobitrader.test;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### در هر دو حالت

1. لاراگون → **Stop** بعد **Start** (ری‌استارت سرویس وب).
2. فایل `C:\Windows\System32\drivers\etc\hosts` را با Notepad (Run as
   administrator) باز کن و یک خط اضافه کن:
   ```
   127.0.0.1  nobitrader.test
   ```
3. `http://nobitrader.test` را باز کن.

چون صفحه و API هر دو از یک origin می‌آیند، **هیچ تنظیم CORS لازم نیست** و
`request.client.host` هم `127.0.0.1` می‌ماند، پس نوشتن‌ها (سفارش کاغذی، توقف
اضطراری) از loopback مجاز می‌مانند.

اگر بدون پروکسی `index.html` را از Apache سرو کنی، API روی `:8000` یک origin
دیگر است و باید در `.env` بگذاری:
`NBX_CORS_ORIGINS=http://nobitrader.test`.

---

## ۷) تأیید اینکه همه‌چیز سر جایش است

```bat
curl http://127.0.0.1:8000/api/health
```

در پاسخ دنبال این‌ها بگرد:

```json
"ok": true,
"auth":     { "mode": "loopback-only", "token_required": false },
"upstream": { "ok": true }
```

`upstream.ok=false` یعنی نوبیتکس از این شبکه در دسترس نیست — صفحه کار می‌کند و
همین را می‌گوید، عدد جای‌گزین نمی‌گذارد.

سنجش ۴۴ پنل (به Stdlib نیاز دارد، نه مرورگر):

```bat
cd C:\laragon\www\nobitrader
python scripts\probe_live.py --url http://127.0.0.1:8000
```

خروجی مطلوب: `PASS — 44 panels verified …`.

تست‌های بک‌اند و دروازه‌های ایستا:

```bat
python -m pytest -q server\tests
ruff check server scripts
mypy
npm run lint
```

---

## ۸) شروع خودکار با ویندوز (بدون لاراگون)

لاراگون پردازهٔ پایتون را مدیریت نمی‌کند. یک فایل `C:\laragon\www\nobitrader\start.bat`:

```bat
@echo off
cd /d C:\laragon\www\nobitrader\server
call .venv\Scripts\activate
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

و یک shortcut از آن در `shell:startup` (Run → `shell:startup` → Paste shortcut).

---

## ۹) به‌روزرسانی

```bat
cd C:\laragon\www\nobitrader
git pull
cd server
.venv\Scripts\activate
pip install -r requirements.txt
```

اگر `requirements.txt` یا فایل‌های UI عوض شده باشند:

```bat
cd C:\laragon\www\nobitrader
npm ci
npm run css
python scripts\build_standalone.py
```

سپس سرور را دوباره بالا بیاور.

---

## ۱۰) فایل موبایل

`nobitex-mobile.html` تک‌فایلی است و از `file://` هم باز می‌شود، ولی دادهٔ بازار
را از همین بک‌اند می‌گیرد. برای اینکه از `file://` بتواند به API وصل شود:

```env
NBX_CORS_ORIGINS=null
```

بدون بک‌اند، پنل‌ها جملهٔ «سرور نوبیتکس در دسترس نیست» را نشان می‌دهند — نه
عدد ساختگی.

---

## ۱۱) وقتی چیزی کار نکرد

| نشانه | علت | راه |
|---|---|---|
| `Could not import module "main"` | از پوشهٔ اشتباه اجرا شد | `cd ...\server` |
| `No module named 'fastapi'` | venv فعال نیست | `.venv\Scripts\activate` |
| `[Errno 10048] bind failed` | پورت ۸۰۰۰ مشغول | `netstat -ano \| findstr :8000` و `taskkill /PID <pid> /F`، یا `--port 8001` |
| پنجرهٔ Firewall هنگام اجرا | `--host 0.0.0.0` زده‌ای | همان `127.0.0.1` را بزن |
| صفحه باز می‌شود، همه «—» | اسکن اول تمام نشده، یا upstream قطع است | ۲۰ ثانیه صبر؛ `curl .../api/health` |
| `nobitrader.test` → 502 | پروکسی به :8000 نمی‌رسد | اول بدون پروکسی `127.0.0.1:8000` را چک کن؛ بعد `mod_proxy` |
| سفارش کاغذی از گوشی/لن رد می‌شود | `loopback-only` است | از همان ماشین باز کن، یا `NBX_API_TOKEN` بگذار و هدر بفرست |
| در تب لاگ، کارت «تست‌های پروژه» غیرفعال است | سرور از پوشهٔ کد منبع اجرا نشده، یا `NBX_DEV_TOOLS=0`، یا ابزارها نصب نیستند | دلیلش زیر همان کارت نوشته شده؛ `python -m pip install -r server\requirements-dev.txt` |
| در تب لاگ، بررسی‌ها همه «نامعلوم» | پردازه هنوز با صرافی حرف نزده یا سوکت وصل نیست | «با صرافی» را بزن؛ اگر باز هم نامعلوم ماند، `/api/health` را چک کن |
| `database is locked` | دو نمونهٔ سرور روی یک `journal.db` | یکی را ببند، یا `NBX_JOURNAL_DB` جدا بده |
| `playwright install chromium` دانلود نمی‌شود | CDN مسدود است یا آنتی‌ویروس فایل را می‌بلعد | از مرورگر نصب‌شده استفاده کن: `python scripts\layout_test.py --channel msedge` (بخش ۳٫۳ `docs/testing.md`) |
| `playwright is not installed for this interpreter` | `python` خالی، venv پروژه نیست؛ پکیج در مفسر دیگری نصب شده | با همان مسیری که در پیام چاپ شده نصب کن: `"...\python.exe" -m pip install playwright`، یا اسکریپت را با `.venv\Scripts\python.exe` اجرا کن |

---

## ۱۲) خلاصهٔ چهار خطی

```bat
cd C:\laragon\www\nobitrader\server
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

بعد `http://127.0.0.1:8000`. نه PHP، نه MySQL، نه توکن نوبیتکس.
