# راهنمای جامع نصب و راهاندازی nobitrader روی ابرک آروانکلاد

**نسخهٔ مبنا:** `v1.10.2` (منتشرشده) · **سیستمعامل هدف:** Ubuntu 24.04 LTS
**مخاطب:** کسی که میخواهد یک نمونهٔ امن و قابل نگهداری روی ابرک آروان بالا بیاورد.
**زبان:** فارسی · **تاریخ:** ۱۴۰۵/۰۸/۰۴ (۲۰۲۶-۱۰-۲۶)

> این راهنما از روی کد واقعی مخزن نوشته شده است: `scripts/deploy_arvan.sh`،
> `deploy/*`، `server/security.py`، `server/install_wizard.py`،
> `scripts/check_deploy.py` و `scripts/manage_users.py`. سه موردی که با
> برچسب **«اندازهگیریشده»** مشخص شدهاند را روی nginx 1.26 / systemd واقعی
> اجرا کردهایم، نه اینکه از روی مستندات حدس زده باشیم. اسناد تکمیلی مخزن:
> [`deploy-arvan.md`](deploy-arvan.md) (راهنمای قدیمی/جزئیات پنل)،
> [`deploy-arvan-vpc.md`](deploy-arvan-vpc.md) (حالت دو ابرک)،
> [`SECURITY-NOTES.md`](SECURITY-NOTES.md).

---

## فهرست

| # | بخش |
|---|---|
| ۰ | [شروع سریع (برای کسانی که عجله دارند)](#۰-شروع-سریع) |
| ۱ | [تصمیمهای قبل از خرید ابرک](#۱-تصمیمهای-قبل-از-خرید-ابرک) |
| ۲ | [معماری نهایی — دقیقاً چه چیزی کجا اجرا میشود](#۲-معماری-نهایی--دقیقاً-چه-چیزی-کجا-اجرا-میشود) |
| ۳ | [ساخت ابرک در پنل آروان](#۳-ساخت-ابرک-در-پنل-آروان) |
| ۴ | [اتصال SSH و آمادهسازی اولیه](#۴-اتصال-ssh-و-آمادهسازی-اولیه) |
| ۵ | [DNS و فایروال آروان](#۵-dns-و-فایروال-آروان) |
| ۶ | [نصب — یک اجرا](#۶-نصب--یک-اجرا) |
| ۷ | [TLS و گواهی](#۷-tls-و-گواهی) |
| ۸ | [ساخت حساب مالک و اولین ورود](#۸-ساخت-حساب-مالک-و-اولین-ورود) |
| ۹ | [تأیید استقرار — این گام را رد نکنید](#۹-تأیید-استقرار--این-گام-را-رد-نکنید) |
| ۱۰ | [عملیات روزمره و نصبگر مرورگری](#۱۰-عملیات-روزمره-و-نصبگر-مرورگری) |
| ۱۱ | [پیکربندی و سختسازی بیشتر](#۱۱-پیکربندی-و-سختسازی-بیشتر) |
| ۱۲ | [بکاپ و بازیابی](#۱۲-بکاپ-و-بازیابی) |
| ۱۳ | [بهروزرسانی، بازگشت و جابهجایی](#۱۳-بهروزرسانی-بازگشت-و-جابهجایی) |
| ۱۴ | [عیبیابی — جدول خطاهای واقعی](#۱۴-عیبیابی--جدول-خطاهای-واقعی) |
| ۱۵ | [چکلیست نهایی](#۱۵-چکلیست-نهایی) |
| ۱۶ | [محدودیتها و هشدارهای قبل از معاملهٔ واقعی](#۱۶-محدودیتها-و-هشدارهای-قبل-از-معاملهٔ-واقعی) |

---

## ۰. شروع سریع

اگر قبلاً این کار را کردهاید، کل ماجرا این است. `DOMAIN` و `EMAIL` را عوض کنید:

```bash
# روی ابرک، با کاربر ubuntu/debian
sudo apt-get update && sudo apt-get -y upgrade
sudo git clone https://github.com/iman-taheri59/nobi /opt/nobi

DOMAIN=nobi.example.ir
EMAIL=you@example.ir

# یک اجرا: نصب + گواهی + کانفیگ nginx TLS
sudo bash /opt/nobi/scripts/deploy_arvan.sh --domain "$DOMAIN" --tls "$EMAIL"

# حساب مالک
sudo -u nobi NBX_AUTH_DB=/srv/nobi-data/auth.db \
  /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py create ADMIN --role owner

# تأیید
sudo /usr/local/libexec/nobi-privileged-helper --protocol 1 check
sudo -u nobi /opt/nobi/.venv/bin/python /opt/nobi/scripts/check_deploy.py \
  --public "https://$DOMAIN"
```

> `--tls` از نسخهٔ **۱٫۱۰٫۲** به بعد روی سرور تازه کار میکند: نصبگر نخست
> گواهی را از طریق یک سایت موقت `:80` میگیرد و **سپس** کانفیگ TLS را نصب
> مینماید (دلیلش در بخش ۷). اجرای دوبارهٔ اسکریپت امن است؛ اگر گواهی از قبل
> وجود داشته باشد دوباره درخواست نمیشود. اگر `--tls` را ندهید، فقط سایت `:80`
> بالا میآید و تا وقتی گواهی نگرفتهاید نباید وارد شوید.

---

## ۱. تصمیمهای قبل از خرید ابرک

| تصمیم | پیشنهاد | دلیل فنی |
|---|---|---|
| **ریجن** | داخل ایران (تهران، یا نزدیکترین به کاربران) | برنامه با `NBX_BASE_URL=https://apiv2.nobitex.ir` کار میکند. مستندات مخزن صریح میگوید صرافی از شبکههای خارج ایران پاسخ نمیدهد؛ سرور بالا میآید ولی همهٔ پنلها «سرور نوبیتکس در دسترس نیست» نشان میدهند — استقرار سالمِ بیفایده. پیش از خرید با `curl -sS -m 10 https://apiv2.nobitex.ir/market/stats` از همان ریجن تست کنید. |
| **سیستمعامل** | Ubuntu 24.04 LTS | پایتون ۳٫۱۲ دارد و همین حداقل است: پین `numpy==2.5.4` شرط `Requires-Python >=3.12` دارد، پس روی ۳٫۱۱ نصب وابستگی‌ها شکست می‌خورد. اسکریپت Debian 12/13 را هم قبول میکند. |
| **اندازه** | ۲ vCPU / ۴ GB RAM / ۴۰ GB دیسک (استاندارد) | پانداز و بکست حافظه میخواهند. یونیت systemd سقف `MemoryMax=2G` و `TasksMax=512` دارد — اگر بکستهای سنگین مینمایید، `systemctl edit nobi` و `MemoryMax=4G`. |
| **دیسک داده** | یک دیسک SSD جداگانه، mount روی `/srv/nobi-data` | بازسازی ابرک از ایمیج، دیسک ریشه را پاک میکند. ژورنال، `auth.db` و پوزیشنها روی ریشه = از دست رفتن قطعی. |
| **دسترسی** | SSH Key (نه رمز) | رمز فقط یکبار در پنل نشان داده میشود. |
| **دامنه** | دامنهای که کنترل DNSاش را دارید + رکورد `A` | Let's Encrypt و `server_name` به آن وابستهاند. |
| **TLS** | Let's Encrypt (certbot)، ۹۰ روزه با تمدید خودکار | توکن `bearer` است؛ روی HTTP عمومی افشا میشود. |
| **Node.js** | **روی سرور لازم نیست** | صفحات `index.html` و `nobitex-mobile.html` از قبل ساخته و کامیت شدهاند. فقط اگر خواستید داراییهای فرانتاند را روی سرور بازسازی کنید، **Node 24** لازم است (jsdom 30 به Node ≥ 22.22.2 نیاز دارد). |

> قیمت را از همان صفحهٔ ساخت ابرک ببینید؛ عدد ثابت در راهنما نمیگذارم چون
> تعرفه تغییر میکند و عدد کهنه بدتر از نبودن عدد است.

---

## ۲. معماری نهایی — دقیقاً چه چیزی کجا اجرا میشود

```
                       اینترنت
                          │  443/TCP (TLS) · 80/TCP (ACME + redirect)
                          ▼
              ┌───────────────────────┐
              │  nginx  (root)        │  /etc/nginx/sites-available/nobi
              │  TLS termination      │  root:root 0644
              │  X-Forwarded-For/Proto│
              └───────────┬───────────┘
                          │ 127.0.0.1:8000  (فقط لوپبک؛ پورت 8000 نباید در فایروال باز شود)
                          ▼
              ┌───────────────────────┐
              │ nobi.service          │  User=nobi · NoNewPrivileges=yes
              │ uvicorn main:app      │  ProtectSystem=strict
              │ --workers 1           │  ReadWritePaths=/srv/nobi-data
              │ EnvironmentFile=      │  /etc/nobi/nobi.env (root:nobi 0640)
              │   /etc/nobi/nobi.env  │
              └───────────┬───────────┘
                          │  فقط «یک action ID» از لیست سفید
                          ▼
        /run/nobi-installer.sock  (root:nobi 0660)
                          │
                          ▼
        ┌──────────────────────────────────┐
        │ nobi-installer.service (root)    │  socket-activated (LISTEN_FDS)
        │ ProtectSystem=full               │
        │ ReadWritePaths=/etc/systemd/system│
        │   /etc/nginx/sites-{available,enabled} │
        └──────────────┬───────────────────┘
                       ▼
        /usr/local/libexec/nobi-privileged-helper
        قالبها: /usr/local/share/nobi-installer/{nobi.service,nginx.conf}
                root:root 0444 — بیرون از checkout (قابلنوشتن توسط nobi نیست)
        دامنه:  /etc/nobi/domain  root:root 0444
```

**چرا سوکت و نه sudo؟** یونیت وب `NoNewPrivileges=yes` دارد (بیت setuid را بیکار
میکند) و `ProtectSystem=strict` (etc/ را فقطخواندنی میکند). پس یک helper روت که
از طریق `sudo` صدا زده شود، روی همان یونیتی که مخزن تحویل میدهد **اصلاً کار نمیکند**.
راهحل فعلی: سرویس root جداگانهای که با سوکت فعال میشود. هیچ قانون `sudoers` و هیچ
باینری setuid در مسیر نیست؛ `deploy/nobi-helper.sudoers` فقط یک یادداشت است و نصبگر
نمونهٔ قدیمی `/etc/sudoers.d/nobi-helper` را پاک میکند.

**پروتکل ۱، شش عمل مجاز:**
`install-service` · `install-proxy` · `restart-service` · `reload-proxy` · `rollback` · `check`.

**مسیرهای نوشتنی سرویس وب** (همه روی volume):
`journal.db` · `auth.db` · `paper_state.json` · `live_execution.db` ·
`managed.env` · `operations-audit.jsonl` · `setup.token` ·
`coin-cache/` · `candle-cache/` · `backtest-reports/` · `exports/`.

---

## ۳. ساخت ابرک در پنل آروان

مسیر: **پنل کاربری → سرور ابری → ابرک → ابرک جدید**

1. **ریجن و AZ** — داخل ایران. گزینهٔ «انتخاب AZ بر اساس بهترین عملکرد» فلیورهای
   موجود را نشان میدهد.
2. **نوع و اندازه** — استاندارد ۲vCPU/4GB.
3. **ایمیج** — Ubuntu 24.04 (تب ایمیجها). ایمیج تمیز بگیرید تا دستورات این
   راهنما خطبهخطب جواب بدهد.
4. **دیسک** — ریشه ۴۰ گیگ + **یک دیسک SSD اضافه** برای داده. فعلاً نیازی به
   mount در پنل نیست؛ در بخش ۶ انجام میشود.
5. **شبکه** — شبکهٔ عمومی، IPv4. اگر IPv6 گرفتید، قوانین فایروال IPv6 را هم
   بسازید (بخش ۵).
6. **امنیت** — نوع دسترسی **SSH Key**؛ کلید عمومی `~/.ssh/id_ed25519.pub` را
   بچسبانید. **مانیتورینگ خودکار** را روشن کنید. فایروال فعلاً Default بماند.
7. **گزینههای شرطی** — «بکاپ هفتگی» را روشن کنید (یک لایهٔ اضافی). «تخصیص
   خودکار دامنه» و «اسکریپت اولیه / cloud-init» را برای بار اول **توصیه
   نمیکنم**: اگر اسکریپت نصب خطا بدهد، سروری تحویل میگیرید که ظاهراً سالم
   است ولی نیمهنصب شده. دستی اجرا کنید تا خروجی را ببینید.
8. **نام** `nobitrader`، تعداد ۱، پرداخت ساعتی (مگر اینکه همیشه روشن باشد).
9. **ساخت ابرک** — همان لحظه نام کاربری و رمز نمایش داده میشود و ایمیل میشود؛
   ذخیره کنید (پس از بستن پنجره از پنل قابل بازیابی نیست). کاربر پیشفرض
   اوبونتو `ubuntu`، دبیان `debian` است.

---

## ۴. اتصال SSH و آمادهسازی اولیه

```bash
chmod 600 ~/nobitrader-key.pem
ssh -i ~/nobitrader-key.pem ubuntu@203.0.113.10
```

ویندوز (PowerShell): `ssh -i $env:USERPROFILE\Downloads\nobitrader-key.pem ubuntu@203.0.113.10`
اگر خطای `UNPROTECTED PRIVATE KEY FILE` گرفتید، از Properties → Security فقط
به کاربر خودتان دسترسی بدهید.

روی سرور:

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo reboot          # اگر کرنل آپدیت شد
# دوباره وصل شوید
```

### دیسک داده ( strongly recommended)

```bash
# نام دیسک دوم را پیدا کنید
lsblk
sudo mkfs.ext4 /dev/vdb                      # فقط بار اول؛ دادهها پاک میشوند!
sudo mkdir -p /srv/nobi-data
sudo mount /dev/vdb /srv/nobi-data
echo '/dev/vdb /srv/nobi-data ext4 defaults,noatime 0 2' | sudo tee -a /etc/fstab
```

> نصبگر اگر ببیند `/srv/nobi-data` روی دیسک ریشه است، هشدار میدهد ولی متوقف
> نمیشود. هشدار را جدی بگیرید: `findmnt -no TARGET /srv/nobi-data` باید
> `/srv/nobi-data` برگرداند.

### کد

```bash
sudo git clone https://github.com/iman-taheri59/nobi /opt/nobi
cd /opt/nobi && git log -1 --oneline     # باید نسخهٔ 1.10.2 یا جدیدتر
```

اگر مخزن خصوصی است، روی **سرور** یک deploy key بسازید
(`ssh-keygen -t ed25519 -C nobi-deploy`) و کلید عمومی را در
GitHub → Settings → Deploy keys با دسترسی **read-only** اضافه کنید.
هیچ PATی را روی سرور یا در تاریخچهٔ شل نگذارید.

---

## ۵. DNS و فایروال آروان

### DNS (پنل آروان → CDN / DNS)

| نوع | نام | مقدار | نکتهٔ حیاتی |
|---|---|---|---|
| `A` | `nobi` | IP عمومی ابرک | **ابر / پروکسی CDN خاموش** |

> اگر رکورد پشت CDN آروان باشد، IP واقعی دیده نمیشود، چالش HTTP-01 به سرور
> نمیرسد و certbot با `Timeout during connect` شکست میخورد. صفحهٔ داشبورد هم
> `Cache-Control: no-store` دارد، پس CDN سودی ندارد.

راستیآزمایی (قبل از certbot):

```bash
dig +short nobi.example.ir          # باید همان IP ابرک باشد
curl -sI http://nobi.example.ir | head -1
```

### فایروال (پنل → سرور ابری → فایروال)

نکات مستندات آروان: قوانین ورودی/خروجی **جدا** برای IPv4 و IPv6 تعریف میشوند؛
هر قانون = پروتکل (TCP) + پورت + مبدأ/مقصد؛ **پیشفرض دو قانون خروجی باز و
بقیه مسدود**؛ و مهمتر از همه **فایروال باید به ابرک متصل شود** وگرنه اعمال
نمیشود.

| # | جهت | پروتکل | پورت | مبدأ | توضیح |
|---|---|---|---|---|---|
| ۱ | ورودی | TCP | 22 | **فقط IP شما** | SSH؛ `all` نگذارید |
| ۲ | ورودی | TCP | 80 | all | redirect + چالش ACME |
| ۳ | ورودی | TCP | 443 | all | داشبورد |

**پورت 8000 را باز نکنید.** uvicorn روی `127.0.0.1` گوش میدهد؛ اگر روزی کسی
`--host 0.0.0.0` گذاشت، تنها چیزی که جلوی فاجعه را میگیرد همین نبودن قانون است.
قوانین خروجی را دستکاری نکنید: برنامه به `apiv2.nobitex.ir` و `pypi.org` نیاز دارد.

---

## ۶. نصب — یک اجرا

```bash
DOMAIN=nobi.example.ir
EMAIL=you@example.ir
sudo bash /opt/nobi/scripts/deploy_arvan.sh --domain "$DOMAIN" --tls "$EMAIL"
```

کاری که اسکریپت انجام میدهد (همه idempotent؛ اجرای دوباره امن است):

| گام | اقدام | نتیجهای که باید ببینید |
|---|---|---|
| ۰ | بررسی OS و پایتون ≥ ۳٫۱۲ | `python: Python 3.12.x` |
| ۱ | `apt install python3-venv python3-dev git nginx curl …` | `git nginx python3-venv ok` |
| ۲ | ساخت کاربر سیستمی `nobi` و کپی/نگهداری checkout در `/opt/nobi` | `user nobi created` |
| ۲ | ساخت venv و نصب `server/requirements.txt` | `deps ok` |
| ۳ | ساخت `/srv/nobi-data/{exports,coin-cache,candle-cache,backtest-reports}` | `chmod 750`، مالک `nobi` |
| ۴ | نوشتن `/etc/nobi/nobi.env` با `NBX_API_TOKEN` تصادفی ۳۲ بایت | `mode 640 owner root:nobi` |
| ۴b | نصب helper روت: قالبها در `/usr/local/share/nobi-installer` (0444)، چهار فایل اجرایی، دو یونیت systemd، `/etc/nobi/domain` | `systemctl enable --now nobi-installer.socket` |
| ۵ | نصب `nobi.service`، `daemon-reload`، `enable`، `restart` و انتظار سلامت | `nobi is up after Ns` |
| ۶a | **فقط با `--tls`**: نصب `certbot`، ساخت سایت موقت `:80` (در صورت نیاز)، `certbot certonly --webroot` | `certificate ready` |
| ۶b | نصب `/etc/nginx/sites-available/nobi`، حذف سایت موقت و `default`، `nginx -t`، `reload` | `nginx reloaded` |

ترتیب ۶a پیش از ۶b حیاتی است و تا پیش از ۱٫۱۰٫۲ برعکس بود (بخش ۷).

**نکات امنیتیای که نباید دور بزنید:**

- `/etc/nobi/nobi.env` مالک `root:nobi` و `0640` است. اسکریپت آن را **بازنویسی
  نمیکند**؛ توکنی که کار میکند با اجرای دوباره عوض نمیشود.
- `chown -R nobi:nobi /opt/nobi` انجام میشود، یعنی checkout توسط سرویس
  قابلنوشتن است. به همین دلیل قالبهای systemd/nginx **حتماً** بیرون از checkout
  و `root:root 0444` نصب میشوند — وگرنه یک پروسهٔ وب آلوده میتوانست یونیت را
  عوض کند و سپس همان `install-service` مجاز را صدا بزند (ارتقای امتیاز).
- دو متغیر حیاتی:
  ```ini
  NBX_API_TOKEN=<32 bytes random>
  NBX_TRUSTED_PROXIES=127.0.0.1
  ```
  پشت nginx همهٔ درخواستها از `127.0.0.1` دیده میشوند. اگر
  `NBX_TRUSTED_PROXIES` ست باشد و توکن خالی باشد، برنامه **بالا نمیآید**:
  `RuntimeError: NBX_TRUSTED_PROXIES is set but NBX_API_TOKEN is not...`.
  این یعنی محیط ناقص است، نه کد خراب — سرور ترجیح میدهد خاموش بماند تا اینکه
  در باز بماند.

### گزینهها

| فلگ | اثر |
|---|---|
| (بدون فلگ) | نصب کامل و سایت nginx — فقط وقتی گواهی از پیش در `/etc/letsencrypt/live/$DOMAIN/` موجود است |
| `--tls EMAIL` | گرفتن گواهی Let's Encrypt پیش از نصب سایت TLS (پیشنهادی) |
| `--no-nginx` | نصب همهچیز جز کانفیگ سایت nginx؛ برای وقتی که پروکسی جداگانهای دارید |
| `--tls` + `--no-nginx` | هشدار میدهد و `--tls` را نادیده میگیرد (گواهی به `:80` همان nginx نیاز دارد) |

### تأیید سریع بعد از نصب

```bash
systemctl is-active nobi nobi-installer.socket nginx
sudo ss -lntp | grep ':8000'                 # فقط 127.0.0.1
ls -l /run/nobi-installer.sock               # srw-rw---- root:nobi
sudo /usr/local/libexec/nobi-privileged-helper --protocol 1 check
```

خروجی `check` باید چیزی شبیه این باشد:

```json
{"ok": true, "protocol": 1,
 "actions": ["check","install-proxy","install-service","reload-proxy","restart-service","rollback"],
 "install-service": "ready", "install-proxy": "ready", "domain": "nobi.example.ir"}
```

---

## ۷. TLS و گواهی

### چرا ترتیب مهم است (اندازهگیریشده)

قالب `deploy/nginx.conf` شامل این دو خط است:

```nginx
ssl_certificate     /etc/letsencrypt/live/nobi.example.ir/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/nobi.example.ir/privkey.pem;
```

روی سرور تازهای که هنوز گواهی نگرفته، `nginx -t` چنین میدهد (خروجی واقعی،
nginx 1.26.3):

```
[emerg] cannot load certificate "/etc/letsencrypt/live/test.invalid/fullchain.pem":
BIO_new_file() failed (... No such file or directory ...)
nginx: configuration file ... test failed      → exit 1
```

تا نسخهٔ `v1.10.1`، `deploy_arvan.sh` نخست این کانفیگ را مینوشت و `nginx -t`
را اجرا میکرد و **بعد** `certbot --nginx` را صدا میزد — یعنی `--tls` روی اولین
نصب هرگز به مرحلهٔ گرفتن گواهی نمیرسید. همچنین اندازهگیری شد که با قرار دادن
یک گواهی قابلخواندن در همان مسیر، `syntax is ok / test is successful` و
`exit=0` برمیگردد: مشکل دقیقاً نبود فایل گواهی بود، نه نحوۀ کانفیگ.

**اصلاحشده در ۱٫۱۰٫۲:** نصبگر اکنون

1. `certbot` را نصب میکند و `/var/www/html` را میسازد؛
2. اگر `/etc/letsencrypt/live/$DOMAIN/fullchain.pem` از قبل موجود باشد، همان
   را نگه میدارد (بدون درخواست مجدد)؛
3. در غیر اینصورت، اگر هیچ سایتی روی `:80` نباشد، یک سایت موقت `nobi-acme`
   فقط برای `/.well-known/acme-challenge/` میسازد؛
4. `certbot certonly --webroot -w /var/www/html -d $DOMAIN` را اجرا میکند
   (چالش HTTP-01، بدون نیاز به سایت TLS)؛
5. سایت موقت را پاک میکند، سپس کانفیگ نهایی TLS را مینویسد، `nginx -t` و
   `reload` میکند.

ترتیب فوق با تست `server/tests/test_deploy_tls_order.py` قفل شده است تا
بازنگردد. از `certonly --webroot` (نه پلاگین `--nginx`) استفاده میشود چون
پلاگین `--nginx` خودش به یک سایت TLS موجود نیاز دارد — همان چیزی که هنوز
نمیتواند لود شود.

> **اگر از نسخهٔ قدیمیتر استفاده میکنید** (یا `--tls` را نزدهاید و سایت `:80`
> بالاست)، دستی گواهی بگیرید و سپس دوباره نصبگر را اجرا کنید:
> ```bash
> sudo apt-get install -y certbot
> sudo certbot certonly --webroot -w /var/www/html -d "$DOMAIN" \
>   --non-interactive --agree-tos -m you@example.ir
> sudo bash /opt/nobi/scripts/deploy_arvan.sh --domain "$DOMAIN"
> ```

### پیشنیازهای گرفتن گواهی

- رکورد `A` دامنه به IP ابرک رسیده باشد: `dig +short $DOMAIN`.
- **پروکسی/CDN آروان برای آن رکورد خاموش** باشد؛ وگرنه چالش HTTP-01 به سرور
  نمیرسد و certbot با `Timeout during connect` شکست میخورد.
- پورت `80` در فایروال آروان باز باشد (بخش ۵).
- فایروال **به ابرک متصل** باشد.

اگر certbot شکست خورد، نصبگر با پیام روشنی خارج میشود و کانفیگ نیمهکاره
نمیگذارد: DNS/CDN/فایروال را درست کنید و همان دستور را دوباره اجرا کنید
(اسکریپت idempotent است).

### بعد از نصب

```bash
curl -sI https://$DOMAIN | head -1                 # HTTP/2 200
sudo certbot renew --dry-run                        # تمدید خودکار را تست کنید
systemctl list-timers | grep certbot
```

> گواهی ۹۰ روزه است و certbot تایمر `renew` را خودش نصب میکند. اعتماد به
> تمدید خودکارِ آزمایشنشده همان داستان «باید درست باشد» است.
>
> روی nginx ≥ 1.26 ممکن است هشدار
> `the "listen ... http2" directive is deprecated` ببینید. **`nginx -t`
> همچنان exit 0 میدهد** و نصبگر آن را موفق حساب میکند (اندازهگیریشده). اوبونتو
> ۲۴٫۰۴ nginx 1.24 دارد که آنجا این هشدار وجود ندارد؛ به همین دلیل HTTP/2 روی
> خط `listen` گذاشته شده، نه دستور جداگانهٔ `http2 on;`.

---

## ۸. ساخت حساب مالک و اولین ورود

داشبورد **عضویت آزاد ندارد**؛ حساب اول فقط از روی سرور یا با توکن یکبارمصرف
ساخته میشود تا هر کسی که زودتر URL را پیدا کرد مالک نشود.

### روش الف — توکن یکبارمصرف (تجربهٔ مرورگری)

هنگام اولین اجرا، `server/setup_api.py` توکنی ۳۲ بایتهای میسازد، آن را در
`/srv/nobi-data/setup.token` با `0600` مینویسد و در ژورنال هشدار میدهد:

```bash
sudo journalctl -u nobi -n 200 --no-pager | grep 'FIRST-RUN SETUP TOKEN'
# یا:
sudo cat /srv/nobi-data/setup.token
```

صفحهٔ وب بهصورت خودکار ویزارد ساخت owner را باز میکند. نام کاربری، رمز
(دستکم ۱۲ کاراکتر، نه بخشی از نام کاربری، نه رمز رایج) و توکن را وارد کنید.
پس از موفقیت فایل توکن حذف میشود.

### روش ب — خط فرمان (مسیر recovery، همیشه در دسترس)

```bash
sudo -u nobi NBX_AUTH_DB=/srv/nobi-data/auth.db \
  /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py \
  create ADMIN --role owner
```

رمز را همانجا میپرسد، بدون نمایش و بدون رفتن به history. خروجی موفق:
`ساخته شد: ADMIN (owner)`.

### نقشها و دعوت

| نقش | خواندن | معامله/توقف/تنظیم | مدیریت کاربران |
|---|---|---|---|
| `owner` | ✓ | ✓ | ✓ |
| `operator` | ✓ | ✓ | ✗ |
| `viewer` | ✓ | ✗ | ✗ |

```bash
M="sudo -u nobi NBX_AUTH_DB=/srv/nobi-data/auth.db /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py"
$M list
$M invite viewer --note "برای سارا" --hours 72
$M role sara operator
$M passwd sara          # همهٔ نشستها بسته میشوند
$M disable sara
$M sessions sara
$M invites
```

کد دعوت **فقط یکبار** نمایش داده میشود (در دیتابیس فقط هش است). مسیر UI:
**تنظیمات → کاربران و دسترسی** (فقط owner).

### توکن API (برای اسکریپت، نه مرورگر)

```bash
curl -H "X-API-Token: $(sudo grep '^NBX_API_TOKEN=' /etc/nobi/nobi.env | cut -d= -f2)" \
     "https://$DOMAIN/api/paper/positions"
```

بعد از ساخته شدن حساب، معافیت لوکال برداشته میشود: `curl` بی توکن حتی از
خود سرور هم ۴۰۱ میگیرد. توکن قدامی `localStorage` دیگر لازم نیست.

---

## ۹. تأیید استقرار — این گام را رد نکنید

```bash
sudo -u nobi /opt/nobi/.venv/bin/python \
  /opt/nobi/scripts/check_deploy.py --public "https://$DOMAIN"
```

این ابزار ۱۳ سنجه دارد و اگر دری باز باشد **FAIL** میدهد. مهمترینش `readonly`
است: یک `POST /api/paper/halt` **بدون توکن** به آدرس عمومی میزند؛ جواب درست
`401`/`403` است و `200` یعنی «ANYONE CAN HALT TRADING».

### یک نکتهٔ اجرایی (اندازهگیریشده)

`check_deploy.py` برای سنجهٔ `config` وجود `server/.env` را با `mode 600` چک
میکند، درحالیکه نصبگر رازها را در `/etc/nobi/nobi.env` مینویسد. روی یک
استقرار استاندارد، سطر `config` قرمز میشود:

```
[fail] config  /opt/nobi/server/.env does not exist
```

سایر سنجهها (`token`, `proxy`, …) از `os.environ` میخوانند، پس با صادر کردن
محیط systemd درست کار میکنند. راهکار کمخطر (بدون کپی کردن راز):

```bash
# فایل کمینهای که فقط وجود داشته باشد؛ رازها از محیط میآیند
sudo -u nobi sh -c 'printf "NBX_TRUSTED_PROXIES=127.0.0.1\n" > /opt/nobi/server/.env'
sudo chmod 600 /opt/nobi/server/.env

( set -a; . /etc/nobi/nobi.env; set +a; exec /opt/nobi/.venv/bin/python /opt/nobi/scripts/check_deploy.py --public https://nobi.example.ir )
```

> **چرا بدون `sudo -u nobi`؟** خود فایل `0640 root:nobi` است، ولی پوشهٔ
> والد `/etc/nobi` با `0750 root:root` ساخته میشود؛ پس کاربر `nobi` به پوشه
> دسترسی عبور ندارد و `. /etc/nobi/nobi.env` با `Permission denied` شکست میخورد.
> چون `root` هستید، همان دستور را مستقیم (در یک subshell) اجرا کنید تا محیط
> systemd وارد شود و در شل اصلیتان باقی نماند. فایل `server/.env` بالا هیچ رازی
> ندارد و `EnvironmentFile` systemd بر آن ارجحیت دارد. اگر نخواستید فایل بسازید، همان یک سطر `config: fail` را
> بهعنوان محدودیت شناختهشدهٔ ابزار بپذیرید و روی بقیهٔ سنجهها تمرکز کنید.

خروجی مطلوب (اعداد روی سرور **شما** تولید میشوند):

```
Nobi deployment preflight · /opt/nobi
  [ok  ] config    /opt/nobi/server/.env, mode 600, 1 keys
  [ok  ] python    3.12.3 (/opt/nobi/.venv/bin/python)
  [ok  ] packages  fastapi, uvicorn, httpx, pandas import
  [ok  ] token     set, 43 characters
  [ok  ] proxy     127.0.0.1
  [ok  ] storage   journal on /srv/nobi-data, ...
  [ok  ] disk      .. GB free, ..% used
  [ok  ] exchange  USDTIRT book answered, .. bid level(s)
  [ok  ] bind      app on 127.0.0.1:8000 (loopback only)
  [ok  ] accounts  1 account(s), 1 owner(s)
  [ok  ] readonly  anonymous POST -> 401 (refused)
  [skip] reads     ok, auth mode=token; GET endpoints need no token ...
  [ok  ] tls       nobi.example.ir certificate valid until ...
```

چهار سطری که باید جدی بگیرید:

- **`readonly`** — تنها سنجهٔ رفتاری. حتماً `--public` بدهید؛ با
  `http://127.0.0.1:8000` عمداً `skip` میشود چون مسیر لوکال طبق طراحی باز است.
- **`bind`** — برنامه نباید روی `0.0.0.0` گوش دهد.
- **`storage` / `accounts`** — دیتابیسها روی volume و حداقل یک owner فعال.
- **`reads` عمداً `skip` است، نه `ok`**: توکن فقط اندپوینتهای تغییردهنده را
  میبندد؛ `GET`ها برای کسی که URL را بداند قابل خواندناند. اگر میخواهید
  این هم بسته شود، جلوی سایت **nginx basic auth** بگذارید (بخش ۱۱).

### چکهای دستی مکمل

```bash
# هدرها و ریدایرکت
curl -sI http://$DOMAIN  | head -3
curl -s "https://$DOMAIN/api/health" | python3 -m json.tool | head -20

# در بسته است؟
curl -s -o /dev/null -w '%{http_code}\n' -X POST "https://$DOMAIN/api/paper/halt"

# مرز امتیازات
sudo /usr/local/libexec/nobi-privileged-helper --protocol 1 check
sudo -u nobi /usr/local/bin/nobi-helper-client --protocol 1 check
sudo stat -c '%U:%G %a %n' /run/nobi-installer.sock \
  /usr/local/share/nobi-installer/nobi.service /etc/nobi/domain

# لاگها
sudo journalctl -u nobi -n 50 --no-pager
sudo journalctl -u nobi-installer.service -n 30 --no-pager
sudo tail -30 /var/log/nginx/nobi.error.log
```

---

## ۱۰. عملیات روزمره و نصبگر مرورگری

### سرویسها

```bash
sudo systemctl status nobi --no-pager
sudo systemctl status nobi-installer.socket --no-pager
sudo systemctl restart nobi
sudo systemctl reload nginx
sudo journalctl -u nobi -f
```

### نصبگر مرورگری (owner-only) — چه چیزی مجاز است

کارت «نصب / عملیات» داشبورد فقط **یک شناسهٔ عمل** را روی سوکت میفرستد.
هیچ مسیر، دامنه، آرگومان یا متن شلای از مرورگر پذیرفته نمیشود.

|عمل|کاری که helper روت انجام میدهد|
|---|---|
|`check`|فقطخواندنی: مالک/mode/محتوای قالبها و دامنه؛ پیش از نصب|
|`install-service`|کپی اتمیک `nobi.service` → `/etc/systemd/system`، ساخت بکاپ/ marker، `daemon-reload`، `enable --now`|
|`install-proxy`|جایگزینی `nobi.example.ir` با دامنهٔ `/etc/nobi/domain`، کپی به `sites-available/nobi`، ساخت symlink، `nginx -t`|
|`restart-service`|`systemctl restart nobi.service`|
|`reload-proxy`|`nginx -t` سپس `nginx -s reload`|
|`rollback`|بازگرداندن/حذف **هر دو** فایل managed از بکاپ (یا marker `.absent`)، پاک کردن symlink سرگردان، `daemon-reload` + `nginx -t`|

> `rollback` حتی بعد از **اولین** نصب هم کار میکند (marker `.absent` مینویسد)؛
> این مورد طی تست واقعی systemd کشف و رفع شد. اگر بکآپی نباشد، پیام
> `no rollback state exists` میدهد و نیمهای بازنمیگرداند.

معادل CLI برای عیبیابی (همان پروتکل ۱):

```bash
sudo -u nobi /usr/local/bin/nobi-helper-client --protocol 1 check
sudo -u nobi /usr/local/bin/nobi-helper-client --protocol 1 install-service
sudo -u nobi /usr/local/bin/nobi-helper-client --protocol 1 rollback
```

توجه: `NBX_INSTALL_HELPER` (برای `/api/operations/*`) و
`NBX_PRIVILEGED_HELPER_CLIENT` (برای ویزارد) هر دو پیشفرض
`/usr/local/bin/nobi-helper-client` دارند. اگر مسیر را عوض کردید،
**هر دو** را در `/etc/nobi/nobi.env` ست کنید.

---

## ۱۱. پیکربندی و سختسازی بیشتر

### مرجع متغیرها (`/etc/nobi/nobi.env`)

الگوی کامل و توضیحهرخط: `deploy/nobi.env.example`. مهمها:

| متغیر | مقدار پیشنهادی | توضیح |
|---|---|---|
| `NBX_API_TOKEN` | ۳۲ بایت تصادفی | بدون آن، پشت پروکسی سرویس بالا نمیآید |
| `NBX_TRUSTED_PROXIES` | `127.0.0.1` | فقط از این peer هدر `X-Forwarded-*` پذیرفته میشود |
| `NBX_JOURNAL_DB`, `NBX_AUTH_DB`, `NBX_PAPER_STATE` | `/srv/nobi-data/...` | حتماً روی volume |
| `NBX_CANDLE_CACHE`, `NBX_COIN_CACHE`, `NBX_BT_DIR`, `NBX_EXPORT_DIR` | `/srv/nobi-data/...` | قبلاً کنار سورس ساخته میشدند و با `ProtectSystem=strict` کرش میکردند |
| `NBX_MANAGED_ENV`, `NBX_OPERATIONS_AUDIT`, `NBX_SETUP_TOKEN_FILE`, `NBX_LIVE_DB` | `/srv/nobi-data/...` | تنظیمات ویزارد و توکن راهاندازی |
| `NBX_BASE_URL` | `https://apiv2.nobitex.ir` | نقطهٔ اتصال صرافی |
| `NBX_EQUITY_USD` | `100000` | سرمایهٔ مبنای محاسبات |
| `NBX_RATE_LIMIT` | `60` | تغییر در دقیقه بهازای کلاینت+مسیر |
| `NBX_LOGIN_RATE_LIMIT` | `20` | اگر چند نفر پشت یک NAT هستند، بالا ببرید |
| `NBX_CORS_ORIGINS` | خالی | همان origin؛ `null` فقط اگر فایل `file://` را هم باز میکنید |
| `NBX_LIVE_MODE` | `disabled` | `install_api.CHOICES` فقط `disabled` و `dry-run` را میپذیرد |

بعد از هر تغییر: `sudo systemctl restart nobi` و دوباره بخش ۹.

### محدودیت نرخ در nginx (اختیاری — هنوز در قالب مخزن نیست)

قالب shipped هیچ `limit_req`ای ندارد؛ محدودیت نرخ فعلاً سمت اپلیکیشن است.
اگر خواستید لایهٔ edge اضافه کنید، **محتاطانه** شروع کنید تا کاربران پشت NAT
قفل نشوند:

```nginx
# در http {} — معمولاً /etc/nginx/nginx.conf
limit_req_zone $binary_remote_addr zone=nobi_api:10m  rate=20r/s;
limit_req_zone $binary_remote_addr zone=nobi_auth:10m rate=1r/s;
limit_req_status 429;

# داخل server {} مربوط به 443
location /api/ {
    limit_req zone=nobi_api burst=40 nodelay;
    # ... همان proxy_pass/proxy_set_headerهای قالب اصلی
}
location /api/auth/login {
    limit_req zone=nobi_auth burst=5 nodelay;
    # ...
}
```

> مقادیر را با ترافیک واقعی خودتان تنظیم کنید؛ `1r/s` برای لاگین عمداً سختگیر
> است. `nginx -t` بزنید و بعد `reload`.

### بستن `GET`ها (اختیاری)

`check_deploy.py` سطر `reads` را `skip`گزارش میکند چون `GET`ها بدون توکناند.
اگر میخواهید کل سایت لایهٔ دوم داشته باشید:

```bash
sudo apt-get install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd admin     # رمز قوی
```

```nginx
# بالای location / در بلوک 443
auth_basic "nobitrader";
auth_basic_user_file /etc/nginx/.htpasswd;
```

با این کار `/api/health` هم `401` میدهد و `check_deploy.py` سطر `reads` را
`ok`گزارش میکند. **توجه:** اسکریپتهایی که با `X-API-Token` کار میکنند هم
بایدbasic auth بدهند (`curl -u admin:...`).

### هدرهای امنیتی موجود در قالب

`Strict-Transport-Security` · `X-Content-Type-Options: nosniff` ·
`Referrer-Policy: same-origin` · `client_max_body_size 2m` ·
`no-store` برای `/` · `proxy_read_timeout 75s`. لاگ فرمت `nobi` هدرها را
چاپ نمیکند؛ توکن را هرگز به URL منتقل نکنید.

---

## ۱۲. بکاپ و بازیابی

چیزهایی که ارزش بکاپ دارند:

| داده | مسیر | اگر برود |
|---|---|---|
| `journal.db` | `/srv/nobi-data/journal.db` | تاریخچهٔ سیگنالها |
| `auth.db` | `/srv/nobi-data/auth.db` | **هیچکس نمیتواند وارد شود** |
| `paper_state.json` | `/srv/nobi-data/paper_state.json` | پوزیشنها |
| `live_execution.db` | `/srv/nobi-data/live_execution.db` | دفتر اجرای واقعی |
| `nobi.env` | `/etc/nobi/nobi.env` | توکن اسکریپتها |
| `domain` + قالبها | `/etc/nobi/domain`, `/usr/local/share/nobi-installer/` | نصبگر مرورگری از کار میافتد |

### الف) ابزار همراه مخزن (توصیهشده)

```bash
sudo -u nobi /opt/nobi/.venv/bin/python /opt/nobi/scripts/state_backup.py \
  backup --output /srv/nobi-data/backup \
  --journal /srv/nobi-data/journal.db \
  --auth   /srv/nobi-data/auth.db \
  --live   /srv/nobi-data/live_execution.db
```

و بازیابی (حتماً قبلش سرویس و همهٔ نویسنده‌های DB را متوقف کنید؛ [پیش‌شرط‌های بازیابی آفلاین](operations-recovery.md)، از جمله checkpoint امن WAL، الزامی‌اند):

```bash
sudo systemctl stop nobi
sudo -u nobi /opt/nobi/.venv/bin/python /opt/nobi/scripts/state_backup.py \
  restore /srv/nobi-data/backup/<archive> --force --offline \
  --journal /srv/nobi-data/journal.db \
  --auth /srv/nobi-data/auth.db \
  --live /srv/nobi-data/live_execution.db
# پس از موفقیت و بررسی سازگاری، بازگرداندن سرویس مرحلهٔ جداگانه است.
```

### ب) SQLite سازگار (کپی خام از DB در حال نوشتن ممکن است خراب باشد)

```bash
sudo mkdir -p /srv/nobi-data/backup
sudo -u nobi sqlite3 /srv/nobi-data/journal.db \
  ".backup '/srv/nobi-data/backup/journal-$(date +%F).db'"
sudo -u nobi sqlite3 /srv/nobi-data/auth.db \
  ".backup '/srv/nobi-data/backup/auth-$(date +%F).db'"
sudo cp /srv/nobi-data/paper_state.json /srv/nobi-data/backup/paper-$(date +%F).json
sudo cp /etc/nobi/nobi.env /srv/nobi-data/backup/nobi-$(date +%F).env
```

تایمر روزانه (`sudo crontab -e`):

```cron
15 3 * * * /usr/bin/sqlite3 /srv/nobi-data/journal.db ".backup '/srv/nobi-data/backup/journal-$(date +\%F).db'" && /usr/bin/find /srv/nobi-data/backup -name 'journal-*.db' -mtime +14 -delete
```

### ج) بیرون از ابرک

بکاپی که روی همان دیسک است، بکاپ نیست. آروان **Object Storage سازگار با S3**
دارد؛ با `rclone` یا `aws s3 cp` منتقل کنید. سادهترین:

```bash
# از کامپیوتر خودتان
scp -i nobitrader-key.pem -r ubuntu@203.0.113.10:/srv/nobi-data/backup ./nobi-backup-$(date +%F)
```

لایههای پلتفرم: بکاپ هفتهای که هنگام ساخت ابرک روشن کردید + اسنپشات. طبق
مستندات آروان، پیش از گرفتن اسنپشات ابرک را **خاموش** کنید تا تصویر دیسک
سازگار باشد.

> **بکاپ آزمایشنشده بکاپ نیست.** حداقل یکبار روی یک ابرک آزمایشی restore کنید
> و بعد وارد شوید.

---

## ۱۳. بهروزرسانی، بازگشت و جابهجایی

### بهروزرسانی معمولی

```bash
sudo -u nobi git -C /opt/nobi pull --ff-only
sudo /opt/nobi/.venv/bin/pip install -q -r /opt/nobi/server/requirements.txt

# مهم: قالبهای root-owned بیرون از checkout هستند و با git pull بهروز نمیشوند
sudo bash /opt/nobi/scripts/deploy_arvan.sh --domain "$DOMAIN" --no-nginx

sudo systemctl restart nobi
sudo /usr/local/libexec/nobi-privileged-helper --protocol 1 check
# سپس بخش ۹ را دوباره اجرا کنید
```

> چرا `deploy_arvan.sh` را دوباره اجرا میکنیم: یونیتها، helperها، قالبها و
> `/etc/nobi/domain` در `/usr/local/...` و `/etc/systemd/system` نصب شدهاند،
> نه داخل `/opt/nobi`. فقط `git pull` کافی نیست. اجرای دوباره امن است و
> `nobi.env` را بازنویسی نمیکند.

اگر از fork/شاخه استفاده میکنید: `git fetch && git reset --hard origin/main`.

### بازگشت (rollback)

دو لایه دارید:

1. **فایلهای managed** (یونیت systemd و سایت nginx): از UI یا
   `sudo -u nobi /usr/local/bin/nobi-helper-client --protocol 1 rollback`.
   helper هر دو فایل را با هم برمیگرداند و symlink سرگردان را پاک میکند.
2. **کد برنامه**: `sudo -u nobi git -C /opt/nobi checkout <commit-قبلی>` سپس
   `pip install -r ...` و `restart`.

قبل از هر ارتقا، بکاپ بگیرید (بخش ۱۲) و `git -C /opt/nobi rev-parse HEAD`
را یادداشت کنید.

### جابهجایی به ابرک جدید

1. ابرک جدید را طبق بخش ۳ بسازید؛ همان دیسک داده را (با `rsync` یا restore
   بکاپ) منتقل کنید.
2. DNS را به IP جدید ببرید (پروکسی CDN خاموش).
3. همان `nobi.env` (با توکن یکسان) و `nobi.env` → `/etc/nobi/` منتقل شود.
4. `deploy_arvan.sh` اجرا شود، certbot دوباره گرفته شود، `check_deploy.py` اجرا شود.

---

## ۱۴. عیبیابی — جدول خطاهای واقعی

| پیام / نشانه | علت | درمان |
|---|---|---|
| `[emerg] cannot load certificate ".../fullchain.pem"` | روی نسخهٔ قدیمی نصبگر، `nginx -t` پیش از certbot اجرا میشده | به ۱٫۱۰٫۲ ارتقا دهید؛ یا گواهی را دستی بگیرید و نصبگر را دوباره اجرا کنید (بخش ۷) |
| `certbot could not obtain a certificate for ...` | DNS نرسیده، CDN روشن، یا پورت ۸۰ بسته | `dig +short $DOMAIN`، پروکسی رکورد خاموش، قانون ۸۰، سپس اجرای دوبارهٔ نصبگر |
| `the "listen ... http2" directive is deprecated` | nginx ≥ 1.26 | هشدار، **بیضرر**؛ `nginx -t` همچنان `exit 0` میدهد |
| `RuntimeError: NBX_TRUSTED_PROXIES is set but NBX_API_TOKEN is not` | `/etc/nobi/nobi.env` ناقص | توکن بسازید: `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`، در فایل بگذارید، `restart nobi` |
| `helper socket unavailable: [Errno 2] No such file or directory` | یونیت سوکت enable/show نشده | `sudo systemctl enable --now nobi-installer.socket` و `sudo systemctl daemon-reload` |
| `helper service must run as root` | سرویس helper با کاربر اشتباه اجرا میشود | `nobi-installer.service` باید `User=root` داشته باشد؛ فایل را از `deploy/` دوباره نصب کنید |
| `managed source is group/world writable` | قالبها `0444` نیستند | `sudo chmod 0444 /usr/local/share/nobi-installer/*` و `chown root:root` |
| `managed source is not root-owned` | مالک قالب عوض شده | `sudo chown root:root /usr/local/share/nobi-installer/*` |
| `managed source is missing or is a symlink` | قالب پاک شده یا symlink شده | اسکریپت نصب را دوباره اجرا کنید |
| `domain file does not contain a valid DNS name` | محتوای `/etc/nobi/domain` خراب/مخرب است | `echo 'nobi.example.ir' \| sudo tee /etc/nobi/domain && sudo chmod 0444 ...`؛ `check` دوباره |
| `failed to start nobi-installer.service` | تداخل `ProtectSystem` یا مسیر `ReadWritePaths` | `journalctl -u nobi-installer.service -n 50`؛ مطمئن شوید `/etc/systemd/system` و `sites-*` موجوداند |
| `502 Bad Gateway` | uvicorn بالا نیست | `systemctl status nobi` و `ss -lntp \| grep 8000` |
| `sqlite3.OperationalError: unable to open database file` | `/srv/nobi-data` mount نیست یا مالکش `nobi` نیست | `ls -ld /srv/nobi-data`؛ `chown -R nobi:nobi`; `journalctl -u nobi \| grep -i permission` |
| `PermissionError` هنگام import (candle cache / backtest) | مسیر داده داخل checkout و فقطخواندنی | متغیرهای `NBX_*_CACHE`, `NBX_BT_DIR` را به `/srv/nobi-data` ببرید |
| `certbot: Timeout during connect` | DNS نرسیده، رکورد پشت CDN، یا پورت ۸۰ بسته | `dig +short $DOMAIN`، پروکسی رکورد خاموش، قانون ۸۰ فایروال |
| `[fail] config /opt/nobi/server/.env does not exist` در `check_deploy.py` | محدودیت شناختهشدهٔ ابزار | راهکار بخش ۹ |
| `readonly anonymous POST -> 200: ANYONE CAN HALT TRADING` | پروکسی هدر `X-Forwarded-For` را نمیفرستد یا `NBX_TRUSTED_PROXIES` خالی است | `NBX_TRUSTED_PROXIES=127.0.0.1` و توکن؛ هدرهای nginx را چک کنید؛ `restart nobi` |
| همهچیز ۴۰۳ | `NBX_TRUSTED_PROXIES` خالی ولی nginx هدر میفرستد | همان بالا |
| صفحه قدیمی میآید | کش مرورگر یا سرویس قدیمی | hard reload + `systemctl restart nobi` (قالب `no-store` دارد) |
| سرویس بعد از ریبوت بالا نمیآید | `enable` نشده | `sudo systemctl enable nobi` و `sudo reboot` را امتحان کنید |
| پنلها «سرور نوبیتکس در دسترس نیست» | ریجن خارج ایران یا خروجی ۴۴۳ بسته | `curl -sS -m 10 https://apiv2.nobitex.ir/market/stats` روی سرور |

دستورات تشخیصی عمومی:

```bash
sudo journalctl -u nobi -f
sudo journalctl -u nobi-installer.socket -u nobi-installer.service -n 50 --no-pager
sudo nginx -t
sudo systemctl --failed
df -h /srv/nobi-data && findmnt -no TARGET /srv/nobi-data
```

### حالت Rescue آروان

پنل → عملیات ابرک → **حالت Rescue**. با دیسک نجات وارد میشوید، فایل سیستم
را بررسی/تعمیر میکنید — و **بعد حتماً از Rescue خارج شوید** وگرنه سرور همانجا
میماند. تغییر اندازه (resize) فقط وقتی ابرک **خاموش** است ممکن است؛ حالت
Stop هزینهٔ RAM/CPU دارد ولی سریع برمیگردد، Terminate فقط IP و دیسک را نگه
میدارد.

---

## ۱۵. چکلیست نهایی

- [ ] ابرک Ubuntu 24.04 داخل ایران ساخته شد، SSH Key و بکاپ هفتگی فعالاند
- [ ] دیسک داده جداگانه روی `/srv/nobi-data` mount شد و در `/etc/fstab` هست
- [ ] `dig +short $DOMAIN` همان IP ابرک است و **پروکسی CDN خاموش** است
- [ ] فایروال آروان **متصل** است: ۲۲ فقط از IP شما، ۸۰ و ۴۴۳ از همه، ۸۰۰۰ بسته
- [ ] نصب با `--tls` اجرا شد؛ `curl -sI https://$DOMAIN` مقدار `200` میدهد
- [ ] `sudo certbot renew --dry-run` سبز است
- [ ] `/etc/nobi/nobi.env` = `0640 root:nobi`، دارای `NBX_API_TOKEN` و `NBX_TRUSTED_PROXIES=127.0.0.1`
- [ ] `systemctl is-active nobi nobi-installer.socket nginx` هر سه `active`
- [ ] `ss -lntp` فقط `127.0.0.1:8000` را نشان میدهد
- [ ] `/run/nobi-installer.sock` = `srw-rw---- root:nobi`
- [ ] `--protocol 1 check` خروجی `ok:true` و `domain` درست میدهد
- [ ] قالبها `root:root 0444`اند و `/etc/nobi/domain` همان دامنهٔ شماست
- [ ] `/etc/sudoers.d/nobi-helper` وجود ندارد (مسیر قدیمی sudo حذف شده)
- [ ] حساب `owner` ساخته و ورود موفق انجام شد
- [ ] `check_deploy.py --public` خروجی `readonly -> 401` و `0 fail` میدهد
- [ ] بکاپ گرفته شد و **یکبار restore آزمایشی** انجام شد
- [ ] `NBX_LIVE_MODE=disabled` است (هیچ کلید صرافی/توکن تلگرامی روی سرور نیست)
- [ ] هیچ رازی در مخزن، URL، لاگ یا صفحهٔ داشبورد نیست

---

## ۱۶. محدودیتها و هشدارهای قبل از معاملهٔ واقعی

1. **حالت زنده خاموش بماند.** `install_api.CHOICES` برای `NBX_LIVE_MODE` فقط
   `disabled` و `dry-run` را میپذیرد — یعنی از طریق تنظیمات ویزارد نمیتوان
   `live` را فعال کرد. این یک قفل عمدی است. `docs/HANDOFF.md` صریح میگوید:
   تا انجام **soak واقعی پنجروزه / ۳۰ نمونه** و **قرارداد تست خصوصی با کلید
   READ/TRADE محدود به IP**، و **بازبینی امنیتی مستقل**، نباید سرمایهٔ واقعی
   وارد شود.
2. **دسترسی صرافی وابسته به مکان است.** بدون IP داخل ایران، بخش زیادی از
   دادهها خالی میماند. قبل از خرید ابرک، اتصال را از همان ریجن بسنجید.
3. **`GET`ها عمومیاند.** توکن فقط mutationها را میبندد. اگر داشبورد حاوی
   اطلاعات حساس است، basic auth یا محدودیت IP در nginx بگذارید.
4. **`limit_req` هنوز در قالب مخزن نیست.** اگر URL عمومی است، بخش ۱۱ را اعمال
   کنید و نرخ را با ترافیک واقعی تنظیم کنید.
5. **`check_deploy.py` و `layout_test.py` هنوز جزو CI نیستند.** دومی نیاز به
   Chromium/Playwright دارد. هر دو را بعد از استقرار دستی اجرا کنید.
6. **بازبینی امنیتی مستقل.** helper روت بالاترین امتیاز را در سیستم دارد؛
   تستهای منفی فعلی مالک/mode/symlink/دامنه را پوشش میدهند ولی TOCTOU،
   درخواست همزمان و rollback پس از کرش وسط نصب را نه. پیش از سرمایهٔ واقعی
   یک نفر دوم آن را بخواند.

---

### پیوست الف — فایلهای کلیدی مخزن

| فایل | نقش |
|---|---|
| `scripts/deploy_arvan.sh` | نصب idempotent روی Ubuntu 24.04 |
| `deploy/nobi.service` | یونیت وب سختگیری شده (`NoNewPrivileges`, `ProtectSystem=strict`) |
| `deploy/nginx.conf` | سایت TLS + هدرهای پروکسی |
| `deploy/nobi-installer.socket` / `.service` | سوکت `root:nobi 0660` و سرویس root سوکت-activated |
| `deploy/nobi-installer-helper.py` | daemon روت واسط (پروتکل ۱، لیست سفید) |
| `deploy/nobi-privileged-helper` | CLI روت؛ اعتبارسنجی قالبها، نصب اتمیک، rollback |
| `deploy/nobi-helper-client` | کلاینت بلامتیاز داخل پروسهٔ وب |
| `deploy/nobi.env.example` | الگوی کامل `/etc/nobi/nobi.env` |
| `scripts/check_deploy.py` | ۱۳ سنجهٔ استقرار (شامل `readonly` رفتاری) |
| `scripts/state_backup.py` | بکاپ/بازیابی سه دیتابیس |
| `scripts/manage_users.py` | کاربران، نقشها، دعوتها، نشستها |
| `docs/deploy-arvan-vpc.md` | حالت دو ابرک (nginx جدا از اپ) |

### پیوست ب — اگر nginx و اپ روی دو ابرک باشند (VPC)

در معماری دو ماشینی، `NBX_TRUSTED_PROXIES` دیگر `127.0.0.1` نیست بلکه
**IP خصوصی ابرک nginx** است. سه چیز باید با هم عوض شوند:

```diff
- NBX_TRUSTED_PROXIES=127.0.0.1
+ NBX_TRUSTED_PROXIES=10.20.1.10        # IP خصوصی ابرک nginx
# در unit:
- --host 127.0.0.1
+ --host 10.20.1.20                    # IP خصوصی ابرک اپ
- --forwarded-allow-ips=127.0.0.1
+ --forwarded-allow-ips=10.20.1.10
# در nginx (ابرک A):
- server 127.0.0.1:8000;
+ server 10.20.1.20:8000;
```

و فایروال ابرک B فقط TCP 8000 از `10.20.1.10` را بپذیرد. جزئیات کامل و
هزینه/محدودیتها در `docs/deploy-arvan-vpc.md`.

### پیوست ج — منابع رسمی آروان (نشانیها ممکن است جابهجا شوند)

- ابرک (ساخت، اتصال، اسنپشات، Rescue): `https://docs.arvancloud.ir/fa/cloud-server/instance/`
- فایروال (قوانین، پیشفرضها، اتصال به ابرک): `https://docs.arvancloud.ir/fa/cloud-server/firewall/`
- دیسک و پارتیشن: `https://docs.arvancloud.ir/fa/cloud-server/volume/`
- Object Storage (سازگار با S3): `https://docs.arvancloud.ir/fa/object-storage/`
- API / Terraform برای خودکارسازی ساخت: `https://docs.arvancloud.ir/fa/developer-tools/`

---

**پایان.** این راهنما همراه با اصلاح ترتیب TLS در `deploy_arvan.sh` وارد
نسخهٔ `1.10.2` شد؛ ترتیب درست با `server/tests/test_deploy_tls_order.py` قفل
شده است.
