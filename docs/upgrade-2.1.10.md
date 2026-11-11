# ارتقا به 2.1.10 — چک‌لیست استقرار کنترل‌شده

**فارسی** · [English](upgrade-2.1.10.en.md)

> **یادداشت تاریخی (ADR-0058):** این checklist فقط درخت نسخهٔ 2.1.10 را توصیف می‌کند؛ ارجاع‌های فایل/هش به `nobitex-mobile.html` متعلق به همان payload قدیمی‌اند. خروجی HTML مستقل در شاخهٔ کاری 2.5.7 وجود ندارد؛ از این سند برای انتشار یا استقرار جاری استفاده نکنید.

این سند checkpoint محلی `2.1.10` را برای یک استقرار بعدی و صریحاً مجاز آماده می‌کند. این سند **مجوز** استقرار، push، tag، restart سرویس production، فعال‌کردن Live/Auto یا ثبت سفارش نیست.

نمونهٔ هدف: `https://capoy.ir` روی ابرک Ubuntu آروان با nginx + systemd. پیش از اجرا، مسیرها/دامنه را با نصب واقعی تطبیق دهید.

## 1. این نسخه چه چیزی را تغییر می‌دهد

- `2.1.9`: کوکی‌های Secure نشست/CSRF در HTTPS عمومی، هدرهای امنیتی مرورگر، `private, no-store` برای API/docs، اصلاح ارث‌بری هدرهای proxy در nginx، رفع `drift/cohorts` global، و پیش‌نمایش/گیت اجرایی صف تأیید.
- `2.1.10`: کامنت‌ها/docstringهای انگلیسی، گزارش‌های انگلیسی تحلیل/بررسی، کاتالوگ‌های دوزبانه تحلیل، تست‌های تطبیق ترجمه، و همین چک‌لیست استقرار.
- هیچ مرحله‌ای از انتشار Live یا Auto را فعال نمی‌کند، کلید صرافی اضافه نمی‌کند و سفارش واقعی نمی‌فرستد.

## 2. پیش‌نیازها

- [ ] اپراتور دسترسی shell و پنجرهٔ نگهداری دارد.
- [ ] نسخه/health عمومی فعلی ثبت شده است.
- [ ] commit/آرشیو deployed فعلی ثبت شده است.
- [ ] Live غیرفعال، placement غیرفعال و Auto خاموش است.
- [ ] مسیر واقعی env، سایت nginx، یونیت systemd و پوشهٔ داده مشخص است.
- [ ] مقصد backup فضای کافی دارد و داخل مسیر writable سرویس نیست.
- [ ] مسئول rollback و معیارهای تصمیم توافق شده است.

نمونه پیش‌بررسی:

```bash
curl -fsS 'https://capoy.ir/api/health?predeploy=2.1.10'
sudo systemctl status nobi --no-pager
sudo nginx -t
sudo -u nobi git -C /opt/nobi rev-parse HEAD
```

## 3. اقلام استقرار

از کل درخت سورس/commit `2.1.10` استفاده کنید، نه کپی پراکندهٔ چند فایل. فایل‌های مهم برای تطبیق پس از انتقال:

```text
VERSION
package.json
package-lock.json
server/main.py
server/identity.py
server/paper_trading.py
server/signal_drift.py
index.html
nobitex-mobile.html
deploy/nginx.conf
deploy/nobi.env.example
deploy/nobi.service
```

`deploy/nobi.env.example` مرجع است، **جایگزین** `/etc/nobi/nobi.env` نیست. فایل env زنده حاوی secret است و باید در-place ویرایش شود.

یک manifest محلی checksum برای فایل‌های مهم در `deploy/upgrade-2.1.10.files` قرار دارد. پس از انتقال درخت سورس، آن را از ریشهٔ مخزن تطبیق دهید:

```bash
sha256sum -c deploy/upgrade-2.1.10.files
```

قالب nginx دامنه/مسیر گواهی نمونه دارد. پیش از نصب آن را با میزبان واقعی تطبیق دهید:

- `server_name`
- مسیر certificate/key
- آدرس/پورت upstream
- مسیر ACME/webroot
- هر location سفارشی که سایت زنده لازم دارد

## 4. Backup پیش از دست‌زدن به سرویس

یک downtime کوتاه برنامه‌ریزی کنید. اول اپ را متوقف کنید تا فایل‌های SQLite سازگار کپی شوند.

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/root/nobi-backup-$STAMP"
sudo mkdir -p "$BACKUP"

sudo systemctl stop nobi

sudo tar -C /srv -czf "$BACKUP/nobi-data.tar.gz" nobi-data
sudo install -m 0600 /etc/nobi/nobi.env "$BACKUP/nobi.env"
sudo cp -a /etc/nginx/sites-available/nobi "$BACKUP/nginx-site"
sudo cp -a /etc/nginx/sites-enabled/nobi "$BACKUP/nginx-site-enabled" || true
sudo cp -a /etc/systemd/system/nobi.service "$BACKUP/nobi.service"
sudo -u nobi git -C /opt/nobi rev-parse HEAD | sudo tee "$BACKUP/old-commit.txt"

sudo sha256sum "$BACKUP"/* | sudo tee "$BACKUP/SHA256SUMS"
```

اگر نصب مسیرهای دیگری دارد، صریحاً جایگزین کنید. اگر backup شکست خورد، ادامه ندهید.

## 5. نصب سورس جدید

اگر commit از مسیر عادی انتشار منتشر شده باشد، updater مستند قابل استفاده است:

```bash
sudo bash /opt/nobi/scripts/update_arvan.sh --check
sudo bash /opt/nobi/scripts/update_arvan.sh --backup --verify --public https://capoy.ir
```

اگر checkpoint هنوز local-only است، **انتظار نداشته باشید `origin/main` آن را داشته باشد**. سورس checkout یا آرشیو را به سرور منتقل کنید و سپس وابستگی‌ها را با کاربر سرویس نصب کنید:

```bash
sudo -u nobi /opt/nobi/.venv/bin/python -m pip install -r /opt/nobi/server/requirements.txt
sudo -u nobi /opt/nobi/.venv/bin/python -c 'import fastapi, uvicorn, pandas; print("deps ok")'
cat /opt/nobi/VERSION
```

## 6. تغییر لازم env

فایل env واقعی، معمولاً `/etc/nobi/nobi.env`، را ویرایش کنید:

```bash
sudoedit /etc/nobi/nobi.env
```

مطمئن شوید:

```text
NBX_COOKIE_SECURE=always
NBX_TRUSTED_PROXIES=127.0.0.1
NBX_API_TOKEN=<secret موجود، چاپ یا ارسال نشود>
```

اگر nginx روی loopback نیست، IP/CIDR واقعی proxy را بگذارید. Live/Auto را فعال نکنید. فایل را با example جایگزین نکنید.

## 7. نصب قالب nginx

پس از تطبیق قالب با میزبان واقعی:

```bash
sudo install -m 0644 /opt/nobi/deploy/nginx.conf /etc/nginx/sites-available/nobi
sudo nginx -t
sudo systemctl reload nginx
```

اگر سایت زنده چیدمان nginx دیگری دارد، به‌جای overwrite کورکورانه، قواعد لازم را merge کنید:

- مجموعهٔ کامل `X-Forwarded-*` در هر location پروکسی
- هدرهای امنیتی با `always`
- hide کردن هدرهای امنیتی upstream برای جلوگیری از تکرار
- `Cache-Control` صریح برای `/` و `/assets/`

## 8. Restart و تأیید

```bash
sudo systemctl daemon-reload
sudo systemctl restart nobi
sudo systemctl status nobi --no-pager
curl -fsS 'https://capoy.ir/api/health?postdeploy=2.1.10'
```

نسخهٔ مورد انتظار health: `2.1.10`.

checker استقرار را اجرا کنید. اگر env سرویس فقط برای root خواندنی است، secret را در command line چاپ نکنید؛ از مسیر تأیید خود updater یا یک shell که متغیرهای `NBX_*` را به‌صورت امن در محیط دارد استفاده کنید:

```bash
sudo -u nobi /opt/nobi/.venv/bin/python /opt/nobi/scripts/check_deploy.py --public https://capoy.ir
```

سپس owner probe را از ماشین مورد اعتماد اپراتور اجرا کنید. این probe به‌جز login/logout فقط‌خواندنی است:

```bash
python3 capoy_owner_probe.py --role owner --output capoy-owner-probe-post-2.1.10.json
```

نتایج مورد انتظار probe:

- `session cookie Secure on HTTPS` → pass
- `edge header strict-transport-security` → pass
- `edge header x-content-type-options` → pass
- `edge header referrer-policy` → pass
- `frame protection` → pass
- `authenticated API no-store` → pass
- `GET /api/v1/signal-intelligence/drift/cohorts` → 200، نه 422
- logout نشست را پاک کند

بررسی دستی UI، بدون ثبت سفارش:

- صف تأیید `R:TP2` اجرایی نشان دهد
- ردیف‌های `executable:false` قابل تأیید نباشند
- پیش‌نمایش اجرایی کهنه دوباره رنگ‌آمیزی/قفل شود
- رد قطعی broker کندل را بازنشسته کند، نه دعوت به retry فوری

## 9. Rollback

اگر health شکست خورد، login شکست خورد، کوکی‌ها هنوز Secure نیستند، هدرها غایب/تکراری‌اند، یا صف/API پس‌رفت کرد، rollback کنید.

```bash
sudo systemctl stop nobi
# درخت سورس/آرشیو قبلی را که پیش از استقرار ثبت شده بازگردانید.
sudo install -m 0600 "$BACKUP/nobi.env" /etc/nobi/nobi.env
sudo install -m 0644 "$BACKUP/nginx-site" /etc/nginx/sites-available/nobi
sudo install -m 0644 "$BACKUP/nobi.service" /etc/systemd/system/nobi.service
sudo -u nobi /opt/nobi/.venv/bin/python -m pip install -r /opt/nobi/server/requirements.txt
sudo systemctl daemon-reload
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl start nobi
```

آرشیو داده را فقط در صورت تأیید خرابی داده بازگردانید. لاگ‌ها و خروجی probe استقرار ناموفق را برای تشخیص نگه دارید.

## 10. پس از استقرار

- Live/Auto را تا تصمیم جداگانه و مبتنی بر شواهد خاموش نگه دارید.
- دعوت‌نامه‌های باز و نشست‌های اضافی owner/operator را بررسی کنید.
- وجود backup و زمان‌بندی تست restore را تأیید کنید.
- اگر probe JSON حاوی متریک عملیاتی/مالی است، آن را عمومی منتشر نکنید.
