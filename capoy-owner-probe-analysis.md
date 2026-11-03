# تحلیل خروجی Owner Probe روی capoy.ir

[English](capoy-owner-probe-analysis.en.md)

فایل بررسی‌شده:

```text
/home/user/nobi-review/live-owner-probe/capoy-owner-probe.json
```

مشخصات اجرا:

```text
script_version: 2.0.0
target_app_version: 2.1.8
base_origin: https://capoy.ir
expected_role: owner
generated_at: 2026-11-02T06:30:09Z
request_count: 75
duration_s: 164
aborted: false
```

نتیجه کلی:

```text
checks:           pass=10, warn=6, fail=1, info=2
endpoint_outcomes: ok=73, unexpected=1
```

probe فقط `POST /api/auth/login` و `POST /api/auth/logout` را به‌عنوان mutation مجاز انجام داد؛ بقیه درخواست‌ها GET بودند. هیچ سفارش، تغییر تنظیمات، تغییر کاربر، backup/restore/restart یا اجرای diagnostics/devsuite انجام نشد. خروجی نیز فاقد رمز، مقدار کوکی، لاگ خام، نام کاربری، IP و user-agent است؛ اما معیارهای مالی/عملیاتی آن همچنان می‌تواند حساس باشد.

---

## ۱) تنها fail ثبت‌شده: کوکی نشست روی HTTPS، Secure نیست

در پاسخ login:

```text
nbx_session:
  secure=false
  httponly=true
  samesite=lax
  path=/
  host-only=true
  max-age present

nbx_csrf:
  secure=false
  httponly=false
  samesite=lax
  path=/
  host-only=true
  max-age present
```

بنابراین check زیر fail شد:

```text
session cookie Secure on HTTPS → fail
```

### چرا مهم است؟

سایت با HTTPS بررسی شده، اما کوکی نشست `Secure` ندارد. اگر کاربر روزی وارد `http://capoy.ir` شود، یا مسیر HTTP فعال باشد، مرورگر می‌تواند کوکی نشست را روی درخواست HTTP ارسال کند. این برای یک داشبورد مالی با حساب owner ریسک جدی است.

`HttpOnly=true`، `SameSite=Lax` و host-only بودن نکات مثبت هستند، ولی جای `Secure` را نمی‌گیرند. `nbx_csrf` هم عمداً `HttpOnly=false` است تا JavaScript بتواند آن را در هدر CSRF برگرداند؛ این به‌خودی‌خود ایراد نیست، اما بهتر است آن هم `Secure` باشد.

### علت محتمل

در کد، `server/identity.py` تابع `is_secure()` وقتی کوکی را Secure می‌کند که درخواست را HTTPS ببیند. در استقرار پشت nginx/reverse proxy، اگر اپلیکیشن داخلی HTTP باشد و هدر `X-Forwarded-Proto: https` به آن نرسد، کوکی غیر Secure ساخته می‌شود.

در فایل مرجع `deploy/nginx.conf` نیز یک نکته مهم وجود دارد: هدرهای proxy در سطح server تعریف شده‌اند، اما بعضی locationها خودشان `proxy_set_header` دارند. در nginx، اگر یک location حتی یک `proxy_set_header` داشته باشد، ممکن است هدرهای proxy سطح server به آن location ارث نرسند. برای مثال:

```nginx
location / {
    proxy_pass http://nobi_app;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
}
```

این می‌تواند باعث شود `X-Forwarded-Proto` سطح server به درخواست‌های این location نرسد. همچنین `location = /` و `/assets/` هدرهای مخصوص خود را دارند.

### اصلاح پیشنهادی

یک فایل مشترک برای هدرهای proxy بسازید و در همه locationهایی که به اپلیکیشن proxy می‌کنند include کنید:

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
```

مثلاً:

```nginx
location / {
    proxy_pass http://nobi_app;
    proxy_http_version 1.1;
    include /etc/nginx/snippets/nobi-proxy-headers.conf;
    proxy_set_header Connection "";
}

location = / {
    proxy_pass http://nobi_app;
    include /etc/nginx/snippets/nobi-proxy-headers.conf;
    add_header Cache-Control "no-store" always;
}
```

اگر TLS در لایه دیگری خاتمه می‌یابد، `$scheme` ممکن است طرح واقعی مرورگر را نشان ندهد. در آن حالت باید هدر پروتکل واقعی از لبه TLS به‌درستی منتقل شود و فقط به پروکسی‌های مورد اعتماد اتکا شود.

همچنین اگر `NBX_TRUSTED_PROXIES` تنظیم شده است، باید IP/CIDR پروکسی واقعی که به اپلیکیشن وصل می‌شود را پوشش دهد؛ برای nginx روی loopback معمولاً `127.0.0.1` و در صورت نیاز `::1`.

نکته دقیق: در کد فعلی، اگر `X-Forwarded-Proto: https` برسد، حتی از peer غیرمورد اعتماد نیز کوکی سخت‌گیرانه‌تر می‌شود؛ پس علت اصلی این fail به‌احتمال زیاد نرسیدن/اشتباه بودن `X-Forwarded-Proto` است، نه صرفاً نبود `NBX_TRUSTED_PROXIES`.

### تأیید پس از اصلاح

دوباره probe اجرا شود. انتظار:

```text
session cookie Secure on HTTPS → pass
```

و در پاسخ login هر دو کوکی `Secure` داشته باشند. logout در این اجرا موفق بود و check زیر pass شد:

```text
session cleared after logout → pass
```

شدت: **بالا**.

---

## ۲) شش warn: هدرهای امنیتی و cache کنترل

checkهای warn:

```text
edge header strict-transport-security
edge header x-content-type-options
edge header content-security-policy
edge header referrer-policy
frame protection
authenticated API no-store
```

### نکته مهم: HSTS/nosniff/Referrer-Policy کاملاً غایب نیستند

probe این checkها را روی پاسخ `/` انجام داد. در `/` این هدرها دیده نشدند:

```text
Strict-Transport-Security
X-Content-Type-Options
Referrer-Policy
```

اما در بسیاری از پاسخ‌های API و همچنین `/manifest.json` و `/version-check.html` این‌ها وجود داشتند:

```text
Strict-Transport-Security: max-age=31536000
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
```

مسیرهایی که در خروجی probe این سه هدر را نداشتند:

```text
/
/nbx-core.js
/nbx-app.js
/nobitex-mobile.html
```

این الگو با مشکل ارث‌بری `add_header` در nginx سازگار است: اگر یک location خودش `add_header` داشته باشد، ممکن است `add_header`های سطح server به آن ارث نرسند. در فایل مرجع، `location = /` و `/assets/` چنین وضعیتی دارند. البته پیکربندی واقعی سرور ممکن است متفاوت باشد؛ باید همان کانفیگ فعال بررسی شود.

### CSP و frame protection

در مسیرهای بررسی‌شده، این‌ها دیده نشدند:

```text
Content-Security-Policy
X-Frame-Options
Permissions-Policy
```

نبود `X-Frame-Options` یا `Content-Security-Policy: frame-ancestors` یعنی محافظت صریح در برابر clickjacking وجود ندارد.

حداقل پیشنهادی:

```text
X-Frame-Options: DENY
```

یا بهتر:

```text
Content-Security-Policy: frame-ancestors 'none'
```

برای CSP کامل، چون صفحه احتمالاً inline script/style دارد، ابتدا با `Content-Security-Policy-Report-Only` شروع کنید و پس از رفع خطاها آن را enforce کنید.

### Cache-Control برای API احراز هویت‌شده

check زیر warn شد:

```text
authenticated API no-store
```

مثلاً `/api/auth/me` احراز هویت‌شده `Cache-Control` نداشت. در مقابل، بعضی مسیرها مانند `/api/health`، `/api/risk/plan` و `/api/v1/signal-intelligence/evidence` هدر no-store داشتند. ریشه سایت نیز `no-store, must-revalidate` داشت.

پیشنهاد: برای پاسخ‌های احراز هویت‌شده API، به‌ویژه account/settings/audit/paper/live/journal، این هدر به‌صورت سراسری اعمال شود:

```text
Cache-Control: private, no-store, max-age=0
```

شدت: **متوسط**.

---

## ۳) یک endpoint واقعاً خطا داد: drift/cohorts

تنها endpoint با outcome غیرمنتظره:

```text
GET /api/v1/signal-intelligence/drift/cohorts → 422
```

اثر انگشت پیام خطا:

```text
41a76e7a6ab612c9
```

این اثر انگشت دقیقاً برابر است با:

```text
zip() argument 2 is longer than argument 1
```

### ریشه در کد

مسیر پیش‌فرض این endpoint در `server/main.py` حالت `mode=global` است. در `server/signal_drift.py`، تابع `cohort_inventory()` برای global این‌ها را دارد:

```python
dimensions = ()
cohort_key = "global"
```

ولی بعداً اجرا می‌شود:

```python
item["dimensions"] = dict(zip(dimensions, str(item["cohort_key"]).split("|"), strict=True))
```

از آنجا که:

```python
"global".split("|") == ["global"]
```

یک عضو در سمت راست وجود دارد، ولی `dimensions` خالی است؛ بنابراین `zip(..., strict=True)` خطا می‌دهد و endpoint به‌جای 200، پاسخ 422 می‌دهد.

### اصلاح پیشنهادی

```python
parts = str(item["cohort_key"]).split("|") if dimensions else []
item["dimensions"] = dict(zip(dimensions, parts, strict=True))
```

و یک آزمون رگرسیون برای `mode=global` اضافه شود.

شدت: **متوسط**؛ بخش drift cohorts را خراب می‌کند، ولی مستقیماً معامله یا امنیت را نمی‌شکند.

---

## ۴) مشکل صف تأیید در داده زنده هم دیده شد

در `/api/paper/orders?limit=20` این ردیف‌ها دیده شدند:

```text
PUMPIRT  REJECTED  QUEUE  2026-11-02 03:36 UTC
ZECIRT   REJECTED  QUEUE  2026-11-02 03:34 UTC
HOTIRT   REJECTED  QUEUE  2026-11-01 19:45 UTC
HOTIRT   REJECTED  QUEUE  2026-11-01 19:18 UTC
ADAIRT   CLOSED    QUEUE
```

اثر انگشت دلیل رد برای چهار ردیف اول:

```text
ce1e216f0847d795
```

این دقیقاً برابر است با:

```text
execution-rr-too-low
```

پس مشکل فقط HOTIRT نیست. چندین سیگنال صف تأیید بعداً توسط broker کاغذی رد شده‌اند.

این با تحلیل قبلی سازگار است:

- صف تأیید، executable بودن روی دفتر سفارش فعلی را پیش از ارسال نمی‌سنجد.
- broker روی قیمت اجرا، TP2 R:R را بررسی می‌کند.
- اگر قیمت اجرا نسبت به پلن بدتر شده باشد، سفارش رد می‌شود.
- در کد رابط، پس از شکست قطعی، ردیف صف به‌درستی حذف/قفل نمی‌شود و می‌تواند دوباره قابل اقدام بماند.

از نظر ایمنی معامله، رفتار broker fail-closed است: سفارش رد می‌شود و نباید پوزیشن ساخته شود. اما از نظر قرارداد محصول و تجربه کاربر، صف نباید سیگنالی را قابل تأیید نشان دهد که در شرایط فعلی executable نیست.

اقدام پیشنهادی:

1. پیش‌نمایش اجرایی سمت سرور برای صف: `executable`, `blocked_by`, `plan_entry`, `execution_entry`, `execution_rr`, `min_rr`, `depth_ok`, `as_of`.
2. غیرفعال کردن دکمه تأیید وقتی `executable=false` است.
3. نمایش `R:TP2` اجرایی، نه فقط `R:TP1`.
4. حذف/قفل کردن ردیف پس از شکست قطعی.
5. انتقال کامل `tp1/tp2/rr/min_rr` در rejection.
6. حذف نکردن guard اجرایی broker.

شدت: **بالا از نظر اعتماد محصول**، ولی نه به‌معنای اجرای سفارش خطرناک.

---

## ۵) وضعیت عملیاتی و امنیتی

### موارد مثبت

```text
setup_required=false
/api/docs anonymous → 401
/openapi.json anonymous → 401
login/logout موفق
session cleared after logout → pass
live mode=disabled
configured=false
placement_enabled=false
executor=paper
default exec mode=semi
auto.enabled=false
sends_to_exchange=false
scan_loop enabled and last_ok=true
38/38 symbols current
upstream ok, age_s≈6.3
success_rate≈99.9%
```

`GET /api/live/orders?status=all` پاسخ 409 داد، اما چون live غیرفعال است این پاسخ مورد انتظار بود و probe آن را ok ثبت کرد.

### دعوت‌نامه باز و نشست‌ها

```text
users: total=4, active=3
roles: owner=2, operator=2
sessions: 7
invites: total=4, used=3, open=1
invite roles: owner=1, viewer=1, operator=2
```

یک دعوت‌نامه باز باقی مانده است. خروجی probe نشان نمی‌دهد کدام دعوت باز است؛ فقط توزیع نقش‌ها را می‌دهد. بنابراین نمی‌توان قطعی گفت دعوت باز owner است. باید در بخش کاربران/دعوت‌نامه‌ها بررسی شود. اگر دعوت باز owner یا غیرضروری است، revoke شود.

همچنین ۷ نشست فعال دیده شد. نشست‌های اضافی/قدیمی، مخصوصاً برای ownerها، بهتر است بسته شوند. نشست خود probe پس از logout پاک شد.

### بکاپ

```text
operations.archive_count=0
archive_bytes_total=0
restore_available=false
helper_available=true
```

در مسیر بکاپ گزارش‌شده هیچ آرشیوی دیده نشد. اگر بکاپ جای دیگری است، باید مستند شود؛ در غیر این صورت برای یک اپ مالی، بکاپ منظم و تست restore ضروری است.

### Wizard نصب

```text
bootstrap-owner → passed
host-preflight, storage, runtime, reverse-proxy,
service-install, validation, complete → pending
```

این لزوماً به‌معنای خرابی نصب نیست؛ ممکن است نصب دستی انجام شده باشد. اما وضعیت wizard با استقرار واقعی هم‌خوان نیست و مرحله validation/reverse-proxy بهتر است بررسی و مستند شود.

### خطاها

نمونه ۲۰ خطای آخر در ۱۶۸ ساعت گذشته:

```text
level: warning=20
sources: nbx-app=18, coin_metadata=2
```

به‌دلیل پاک‌سازی، متن خام خطاها در خروجی نیست. این نمونه فقط warning نشان می‌دهد، ولی چون limit=20 است، نمی‌توان گفت خطای error/critical وجود ندارد.

### Telegram

```text
notify.configured=false
transport=disabled
```

اگر انتظار هشدار تلگرام دارید، تنظیم نشده است.

### WebSocket

```text
enabled=true
transport=rest-poll
ws_url=null
ws_connected=false
ticks=0
```

این یعنی عملاً REST polling فعال است، نه WebSocket واقعی. اگر عمدی است، مشکل نیست؛ ولی UI نباید آن را WebSocket متصل نشان دهد.

### سطح عمومی

موارد قابل سخت‌سازی که قبلاً هم دیده شده بود:

```text
/api/health عمومی است و version/auth/scan-loop را نشان می‌دهد
/version-check.html عمومی است
/robots.txt → 404
```

این‌ها به‌تنهایی fail نیستند، ولی برای یک اپ مالی بهتر است سطح عمومی حداقلی شود.

---

## ۶) شواهد آماری استراتژی فعلاً مثبت نیست

این بخش توصیه مالی نیست؛ فقط وضعیت شواهد ثبت‌شده است.

Signal Intelligence در ۳۰ روز، افق H1:

```text
signals=16715
resolved=2231
win_rate_pct=40.92
expectancy_r=-0.0837
avg_return_pct=-0.1847
expectancy_ci95_r=[-0.1112, -0.0563]
```

Journal summary:

```text
H1: n=2231, hit≈40.9%, avgr≈-0.0837
H2: n=1527, hit≈28.9%, avgr≈-0.486
MSI: degraded, ratio=0.533
stage: train, read_only=true
```

Backtest promotion gate برای نمونه ZECIRT/1h:

```text
state=INSUFFICIENT_DATA
windows=2, required>=3
oos_trades=27, required>=30
profit_factor=0.94, required>=1.3
expectancy_r=-0.013
```

Paper metrics:

```text
trades=1
equity=99921.95 USD
expectancy_r=-1.184
```

Live evidence:

```text
ready=false
samples=0
days=0
reasons: insufficient-samples, insufficient-days
```

نتیجه: داده‌های فعلی از ارتقا به live پشتیبانی نمی‌کنند. خاموش بودن live و auto در این مرحله درست است. نمونه paper هم بسیار کوچک است و نباید مبنای تصمیم live قرار گیرد.

---

## اولویت اقدامات

### فوری

1. اصلاح `Secure` بودن کوکی نشست و CSRF روی HTTPS.
2. بررسی دعوت‌نامه باز؛ اگر owner یا غیرضروری است، revoke شود.
3. بستن نشست‌های اضافی owner/operator.
4. اصلاح باگ `drift/cohorts` در حالت global.

### کوتاه‌مدت

5. یکسان‌سازی هدرهای امنیتی روی HTML/JS/API.
6. افزودن `Cache-Control: private, no-store, max-age=0` برای APIهای احراز هویت‌شده.
7. اصلاح قرارداد صف تأیید و پیش‌نمایش اجرایی.
8. راه‌اندازی/مستندسازی بکاپ و تست restore.

### بعدی

9. بهبود شواهد استراتژی، کاهش نرخ رد اجرایی، و بررسی کیفیت سیگنال‌ها پیش از هرگونه live.
10. اصلاح نمایش وضعیت WS/REST polling و تکمیل wizard/validation در صورت نیاز.
11. حداقلی‌سازی سطح عمومی health/version-check.

---

## محدودیت‌های این تحلیل

- این یک probe HTTP/API است، نه آزمون مرورگر رندرشده.
- فقط یک نماد/تایم‌فریم نمونه‌برداری شد؛ همه بازارها و همه endpointها آزموده نشدند.
- چند GET سنگین به‌صورت پیش‌فرض skip شدند: heavy compute و gate ledger evidence.
- بعضی GETها ممکن است cache یا read-model را تازه کنند؛ هیچ endpoint کنترلی/تغییردهنده فراخوانی نشد.
- نقش owner مرز ایمنی probe را تغییر نداد؛ هیچ سفارش یا تغییر تنظیمات/کاربران انجام نشد.
