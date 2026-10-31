# نصب و راه‌اندازی nobitrader روی ابر آروان

> **نسخهٔ جدیدتر:** راهنمای جامع و بهروزشده را در
> [`deploy-arvan-cloud.md`](deploy-arvan-cloud.md) ببینید (نسخهٔ ۱٫۱۰٫۲ به بعد، شامل
> معماری سوکتهلپر، ترتیب درست TLS، بکاپ و جدول عیبیابی). این صفحه همچنان جزئیات
> پنل آروان و نمونهٔ خروجی `check_deploy.py` را نگه میدارد.

> نام محصول nobitrader است؛ نام سرویس و مسیرهای قدیمی استقرار، شناسه‌های فنی واقعی‌اند و تغییر نکرده‌اند. [توضیح نام‌های فنی و نسخه‌بندی](releases.md).

این راهنما برای نصب داشبورد روی یک **ابرک اوبونتو ۲۴.۰۴** در آروان‌کلاد نوشته شده است.
هر دستوری که در ادامه می‌بینید روی همان سرور قابل اجراست؛ هیچ‌جا فرض نشده که روی کامپیوتر
خودتان چیزی نصب باشد جز یک ترمینال.

> **یک هشدار که ارزش خواندن دارد.** این نرم‌افزار می‌تواند سفارش مقاله بگذارد، معامله را
> متوقف کند و در حالت توسعه، تست‌ها را اجرا کند. یک استقرار اشتباه «خراب» به نظر نمی‌رسد؛
> مثل داشبوردی کار می‌کند که همه‌چیز درست است. برای همین در گام ۷ یک ابزار سنجش هست که
> به‌جای «باید امن باشد»، **در را هل می‌دهد و جواب را نشان می‌دهد**.

---

## فهرست

1. [خلاصه در یک نگاه](#۱-خلاصه-در-یک-نگاه)
2. [ساخت ابرک در پنل آروان](#۲-ساخت-ابرک-در-پنل-آروان)
3. [اتصال SSH](#۳-اتصال-ssh)
4. [نصب خودکار](#۴-نصب-خودکار)
5. [DNS و گواهی TLS](#۵-dns-و-گواهی-tls)
6. [فایروال آروان](#۶-فایروال-آروان)
7. [توکن در مرورگر و اولین ورود](#۷-توکن-در-مرورگر-و-اولین-ورود)
8. [تأیید استقرار — این گام را رد نکنید](#۸-تأیید-استقرار--این-گام-را-رد-نکنید)
9. [بکاپ](#۹-بکاپ)
10. [به‌روزرسانی](#۱۰-به‌روزرسانی)
11. [عیب‌یابی](#۱۱-عیب-یابی)
12. [چک‌لیست](#۱۲-چک-لیست)

---

## ۱. خلاصه در یک نگاه

| تصمیم | انتخاب | چرا |
|---|---|---|
| منطقه | نزدیک‌ترین ریجن به کاربران (تهران برای دسترسی از ایران) | تأخیر شبکه؛ مقررات هم مهم است |
| سیستم‌عامل | Ubuntu 24.04 LTS | `python.12` دارد و کد به ۳.۱۱+ نیاز دارد (`datetime.UTC`) |
| اندازه | ۲ vCPU / ۴ گیگ رم / ۴۰ گیگ دیسک، نوع «استاندارد» | pandas و backtest حافظه می‌خواهند؛ ۱ گیگ با swap جواب می‌دهد ولی کند است |
| نوع پرداخت | ساعتی، مگر اینکه همیشه روشن باشد | — |
| دیسک داده | یک دیسک جدا (SSD) که به ابرک متصل می‌شود | بازسازی ابرک از ایمیج، دیسک ریشه را پاک می‌کند و journal می‌رود |
| دسترسی | SSH key، نه رمز | رمز یک‌بار در ایمیل می‌آید و از پنل دیگر دیده نمی‌شود |
| TLS | Let's Encrypt با certbot | توکن یک bearer credential است؛ روی HTTP عمومی است |
| توکن API | اجباری، ۳۲ بایت تصادفی | بدون آن پشت پروکسی، همه دنیا «لوکال» حساب می‌شوند |

نکته‌ای که مستندات آروان صریح می‌گوید و در استقرار مهم است: **فایروال پیش‌فرض روی ابرک
جدید فعال است** و باید به ابرک **متصل** شود؛ حذف فایروال یعنی حذف قوانین، نه «همه‌چیز باز».

قیمت را از پنل (مرحلهٔ انتخاب اندازه و روش پرداخت) ببینید — عدد ثابتی در این راهنما نمی‌نویسم
چون تعرفه تغییر می‌کند و عدد کهنه بدتر از نبودن عدد است.

---

## ۲. ساخت ابرک در پنل آروان

مسیر: **پنل کاربری → سرور ابری → ابرک → ابرک جدید**

مراحل پنل (بر اساس مستندات رسمی) این‌ها هستند:

1. **ریجن و AZ** — ریجن را انتخاب کنید. گزینه‌ی «انتخاب AZ بر اساس بهترین عملکرد» فلیورهای
   موجود را نشان می‌دهد. ریجن را نزدیک کاربرانتان بردارید (برای کاربران جنوب، دیتاسنتر قیصر در
   اهواز) چون تأخیر شبکه‌ی کاربر به سرور همان چیزی است که داشبورد را «کند» یا «سریع» نشان می‌دهد.
   **ریجن بیرون ایران (آمستردام / `eu-west1`) برای این اپ کار نمی‌کند:** `apiv2.nobitex.ir` از
   شبکه‌های بیرون ایران پاسخ نمی‌دهد، سرور بالا می‌آید و همه‌ی پنل‌ها طبق قرارداد ADR-0014 جمله‌ی
   «سرور نوبیتکس در دسترس نیست» را نشان می‌دهند — استقرار سالمِ بی‌فایده. با
   `scripts/probe_live.py` این را قبل از خرید سرور بسنجید.
2. **نوع و اندازه** — پایه / استاندارد / پرمیوم. برای این کار **استاندارد ۲vCPU/4GB** کافی است.
3. **سیستم‌عامل** — تب ایمیج‌ها → **Ubuntu 24.04**. ایمیج شخصی یا اسنپ‌شات هم مجاز است ولی
   برای نصب اول، ایمیج تمیز انتخاب کنید تا این راهنما خط‌به‌خط جواب بدهد.
4. **دیسک** — دیسک ریشه (Root) مثلاً ۴۰ گیگ. اگر می‌خواهید داده جدا بماند (توصیه می‌شود)،
   همان‌جا یک **دیسک SSD اضافه** بسازید و بعداً در گام ۹ به آن mount کنید.
5. **شبکه** — شبکه‌ی عمومی، IPv4. (IPv6 هم گزینه است، ولی لازم نیست.)
6. **امنیت**:
   - **نوع دسترسی: SSH Key.** کلید عمومی‌تان را بچسبانید (`~/.ssh/id_ed25519.pub`).
   - **مانیتورینگ خودکار** را روشن کنید؛ هنگام خرابی سرور فیزیکی، ابرک را جابه‌جا و ری‌استارت می‌کند.
   - **فایروال**: فعلاً Default بماند؛ در گام ۶ قانون درست را می‌سازیم.
7. **تنظیمات دیگر شرطی** — این‌جا دو گزینه هست که به کار شما می‌آید:
   - **بکاپ هفتگی**: روشن کنید. حتی اگر بکاپ خودتان را دارید، یک لایهٔ دیگر است.
   - **تخصیص خودکار دامنه**: می‌توانید روشن کنید و دامنهٔ رایگان بگیرید؛ ولی برای TLS و
     کنترل، دامنهٔ خودتان روی همین IP بهتر است.
   - **اسکریپت اولیه (cloud-init)**: می‌توانید همین اسکریپت نصب را در آن بگذارید تا سرور
     آماده بالا بیاید؛ برای بار اول توصیه نمی‌کنم چون اگر خطا بدهد سرور خاموش-به-نظر-سالم
     تحویل می‌گیرید. دستی در گام ۴ اجرا کنید تا خروجی را ببینید.
8. **نام و تعداد** — نام `nobitrader`، تعداد ۱، روش پرداخت ساعتی.
9. **ساخت ابرک**. صفحهٔ بعد نام کاربری و رمز را نشان می‌دهد و به ایمیل هم می‌فرستد.
   **همان لحظه ذخیره کنید**؛ مستندات می‌گوید با بستن پنجره دیگر از پنل قابل بازیابی نیست.
   (با SSH Key، رمز فقط برای کنسول اضطراری است.)

نام کاربر پیش‌فرض تصاویر لینوکس: اوبونتو = `ubuntu`، دبیان = `debian`.

---

## ۳. اتصال SSH

IP عمومی ابرک را از پنل بردارید.

**لینوکس / مک:**

```bash
chmod 600 ~/nobitrader-key.pem
ssh -i ~/nobitrader-key.pem ubuntu@203.0.113.10
```

**ویندوز (PowerShell):** کلیدی که پنل می‌دهد `.pem` است؛ OpenSSH ویندوز آن را قبول می‌کند.

```powershell
ssh -i $env:USERPROFILE\Downloads\nobitrader-key.pem ubuntu@203.0.113.10
```

اگر از PuTTY استفاده می‌کنید فایل `.ppk` را از پنل بگیرید و در Connection → SSH → Auth بارگذاری کنید.

> فایل کلید در ویندوز نباید در مسیری باشد که OneDrive همگام می‌کند. و اگر ویندوز خطای
> `UNPROTECTED PRIVATE KEY FILE` داد، با راست‌کلیک → Properties → Security فقط به کاربر
> خودتان دسترسی بدهید.

بعد از ورود، اولین کار:

```bash
sudo apt-get update && sudo apt-get -y upgrade
```

---

## ۴. نصب خودکار

اسکریپت `scripts/deploy_arvan.sh` در مخزن، پکیج‌ها، کاربر سرویس، venv، دایرکتوری داده،
فایل راز‌ها، واحد systemd و سایت nginx را می‌سازد. سه کار را **عمداً** انجام نمی‌دهد:
رمز/توکن را در فایل مخزن نمی‌نویسد، فایروال را باز نمی‌کند، و ادعا نمی‌کند امن است
(آن کار گام ۸ است).

روی سرور:

```bash
# ۱) کد را بیاورید
sudo git clone https://github.com/iman-taheri59/nobi /opt/nobi

# ۲) نصب
sudo bash /opt/nobi/scripts/deploy_arvan.sh --domain nobitrader.example.ir

# یا همراه با گرفتن گواهی در همان اجرا:
sudo bash /opt/nobi/scripts/deploy_arvan.sh --domain nobitrader.example.ir --tls you@example.com
```

اگر مخزن خصوصی است و کلون با خطای احراز هویت می‌خورد، روی **سرور** یک کلید deploy بسازید
(`ssh-keygen -t ed25519 -C nobi-deploy`) و کلید عمومی را در GitHub → Settings → Deploy keys
با دسترسی read-only اضافه کنید. هیچ توکن شخصی را در سرور یا در تاریخچه‌ی شل نگذارید.

اجرای دوبارهٔ اسکریپت بی‌خطر است: هر مرحله اول بررسی می‌کند، و **`.env` موجود را بازنویسی
نمی‌کند** — توکنی که کار می‌کند با یک اجرای دوباره عوض نمی‌شود.

خروجی موفق چیزی شبیه این است:

```
==> installing python dependencies
    deps ok
==> preparing /srv/nobi-data
==> writing /etc/nobi/nobi.env
    created (32 random bytes)
    mode 640 owner root:nobi
==> installing the systemd unit
    nobi is up after 3s
```

### اگر می‌خواهید دستی انجام دهید

<details>
<summary>معادل دستی گام‌های اسکریپت</summary>

```bash
sudo apt-get install -y python-venv python-dev git nginx
sudo adduser --system --group --home /opt/nobi --shell /usr/sbin/nologin nobi
sudo git clone https://github.com/iman-taheri59/nobi /opt/nobi
sudo python -m venv /opt/nobi/.venv
sudo /opt/nobi/.venv/bin/pip install -r /opt/nobi/server/requirements.txt

sudo mkdir -p /srv/nobi-data/exports /etc/nobi
sudo chown -R nobi:nobi /srv/nobi-data

# توکن را تولید و مستقیم در فایل بنویسید (در ترمینال چاپ نشود)
sudo sh -c 'umask 077; printf "NBX_API_TOKEN=%s\nNBX_TRUSTED_PROXIES=127.0.0.1\nNBX_JOURNAL_DB=/srv/nobi-data/journal.db\nNBX_PAPER_STATE=/srv/nobi-data/paper_state.json\nNBX_EXPORT_DIR=/srv/nobi-data/exports\n" \
  "$(python -c "import secrets;print(secrets.token_urlsafe(32))")" > /etc/nobi/nobi.env'
sudo chown root:nobi /etc/nobi/nobi.env && sudo chmod 640 /etc/nobi/nobi.env

sudo cp /opt/nobi/deploy/nobi.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now nobi
sudo systemctl status nobi --no-pager
```
</details>

> **یک نکتهٔ کوچک دربارهٔ صفحهٔ داشبورد:** سرویس با `ProtectSystem=strict` اجرا می‌شود، پس
> `/opt/nobi` برای نوشتن بسته است. صفحهٔ آماده (`nobitex-mobile.html`) در مخزن هست و همان
> سرو می‌شود؛ اگر زمانی لازم شد صفحه از منابع بازسازی شود، سرویس نمی‌تواند بنویسد، نسخهٔ
> موجود را سرو می‌کند و در لاگ هشدار می‌دهد. برای بازسازی عمدی، روی سرور
> `sudo -u nobi /opt/nobi/.venv/bin/python /opt/nobi/scripts/build_standalone.py` را با
> `ProtectSystem` خاموش (مثلاً `systemctl edit nobi` و `ProtectSystem=`) اجرا کنید — یا
> بسازید و از سیستم خودتان آپلود کنید. این عمدی است: یک سرویس در معرض اینترنت نباید در
> مسیر یک درخواست HTTP کد بسازد و اجرا کند.

### چرا این دو متغیر حیاتی‌اند

```ini
NBX_TRUSTED_PROXIES=127.0.0.1
NBX_API_TOKEN=<32 bytes>
```

پشت nginx، هر درخواست از دید برنامه **از 127.0.0.1** می‌آید. اگر برنامه به این واقعیت
توجه نکند و قانون «فقط لوکال اجازه دارد» را اجرا کند، نتیجه این است که **همه‌ی اینترنت
لوکال حساب می‌شود**. این باگ در همین کد وجود داشت و اندازه‌گیری شد (جدول زیر از اجرای واقعی
روی نسخهٔ قبل از رفع است):

| درخواست | نتیجه |
|---|---|
| `POST /api/paper/halt` از 127.0.0.1 بدون توکن، با هدر `X-Forwarded-For: 203.0.113.9` | **200 — توقف معاملات انجام شد** |

حالا دو چیز با هم جلوی آن را می‌گیرند:

1. با `NBX_TRUSTED_PROXIES` برنامه می‌فهمد پشت پروکسی است و آدرس **واقعی** کلاینت را از
   راست‌ترین هپ غیرپروکسی زنجیره می‌خواند (هپ چپ را خود کلاینت می‌نویسد و قابل جعل است).
2. اگر `NBX_TRUSTED_PROXIES` ست باشد و `NBX_API_TOKEN` خالی، برنامه **بالا نمی‌آید**:

```
RuntimeError: NBX_TRUSTED_PROXIES is set but NBX_API_TOKEN is not. Behind a proxy every
request looks like it came from loopback, so the loopback fallback would let the whole
internet call the mutating endpoints.
ERROR:    Application startup failed. Exiting.
```

این خطا ترسناک است ولی خبر خوبی است: سرور ترجیح می‌دهد خاموش بماند تا اینکه مثل سالم
کار کند و در باز باشد. اگر در `journalctl -u nobi` این را دیدید، یعنی `.env` ناقص است، نه
اینکه کد خراب است.

---

## ۵. DNS و گواهی TLS

در پنل آروان → **CDN / DNS** → دامنه‌تان:

| نوع | نام | مقدار | نکته |
|---|---|---|---|
| `A` | `nobi` | IP ابرک | **ابر (پروکسی/CDN) خاموش** |

چرا CDN خاموش: اگر رکورد پشت CDN آروان باشد، IP واقعی دیده نمی‌شود، certbot برای
HTTP-01 به سرور نمی‌رسد، و در حالت‌های خاص حلقه/۴۰۳ می‌گیرید. برای داشبورد، CDN سودی هم
ندارد چون صفحه `no-store` است.

بعد:

```bash
sudo apt-get install -y certbot python-certbot-nginx
sudo certbot --nginx -d nobitrader.example.ir --redirect
sudo systemctl reload nginx
```

چک کنید:

```bash
curl -sI https://nobitrader.example.ir | head -1     # باید HTTP/2 200 باشد
```

اگر `--tls` را به اسکریپت داده بودید، این گام انجام شده است.

> گواهی ۹۰ روزه است و certbot تایمر `renew` را خودش نصب می‌کند. یک‌بار
> `sudo certbot renew --dry-run` بزنید تا مطمئن شوید تمدید کار می‌کند — اعتماد به تمدید
> خودکارِ آزمایش‌نشده، همان داستان «باید درست باشد» است.

---

## ۶. فایروال آروان

پنل → سرور ابری → **فایروال**. سه چیز را مستندات صریح می‌گوید:

- قوانین ورودی و خروجی **جدا** برای IPv4 و IPv6 تعریف می‌شوند؛
- هر قانون = پروتکل (TCP یا UDP؛ برای پورت دلخواه باید TCP انتخاب شود) + پورت (یک پورت،
  بازه، یا `all`) + مبدأ/مقصد (IP، رنج، یا `all`)؛
- **پیش‌فرض: دو قانون خروجی باز، بقیه مسدود.** و فایروال باید به ابرک **متصل** باشد.

قوانینی که لازم دارید (سمت ورودی، IPv4):

| # | جهت | پروتکل | پورت | مبدأ | توضیح |
|---|---|---|---|---|---|
| ۱ | ورودی | TCP | 22 | **IP خودتان** | SSH؛ `all` نگذارید |
| ۲ | ورودی | TCP | 80 | all | فقط برای redirect و ACME |
| ۳ | ورودی | TCP | 443 | all | داشبورد |

**پورت 8000 را باز نکنید.** برنامه به `127.0.0.1` گوش می‌دهد، پس حتی اگر بازش کنید کاری
نمی‌کند؛ ولی اگر روزی `--host 0.0.0.0` شد، تنها چیزی که جلوی فاجعه را می‌گیرد همین
نبودن قانون است. توکن روی HTTP ساده رمزنگاری نمی‌شود.

اگر IPv6 روی ابرک فعال است، همان سه قانون را برای IPv6 هم بسازید؛ وگرنه ممکن است از یک
مسیر باز بماند. (در پنل مشخص است که ابرک IPv6 دارد یا نه.)

> **اگر VPC دارید یا به آن فکر می‌کنید:** ضمیمهٔ [`deploy-arvan-vpc.md`](deploy-arvan-vpc.md)
> همان تصمیم را با عدد بررسی می‌کند — چه چیزی VPC اضافه می‌کند، چه هزینه‌ای دارد، و چرا برای
> یک ابرک تک‌نفره معمولاً لازم نیست. خلاصه: اگر nginx و برنامه روی **دو ماشین** باشند،
> `NBX_TRUSTED_PROXIES` دیگر `127.0.0.1` نیست بلکه IP خصوصی ابرک nginx است.

قانون خروجی لازم نیست اضافه کنید: پیش‌فرض آروان خروجی را باز می‌گذارد و برنامه برای گرفتن
قیمت از `apiv2.nobitex.ir` و برای `pip`/`apt` به آن نیاز دارد. اگر روزی خروجی را بستید،
این دو را باز کنید: HTTPS (443) به `apiv2.nobitex.ir` و `pypi.org`.

---

## ۷. ساخت حساب و اولین ورود

داشبورد از این نسخه **حساب کاربری** دارد. سه چیز را باید بدانید:

- **عضویت با دعوت است.** هیچ‌کس نمی‌تواند خودش حساب بسازد.
- **حساب اول فقط از روی سرور ساخته می‌شود.** اگر فرم عمومی می‌بود، هر کسی که اول
  آدرس را پیدا می‌کرد مالک می‌شد.
- **توکن قدیمی کار می‌کند،** ولی برای اسکریپت — نه برای مرورگر.

### ۷.۱ حساب مالک

```bash
sudo -u nobi NBX_AUTH_DB=/srv/nobi-data/auth.db \
  /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py \
  create YOURNAME --role owner
```

رمز را در همان لحظه می‌پرسد (بدون نمایش). قواعدش: دست‌کم ۱۲ کاراکتر، نه بخشی از نام
کاربری، و نه در فهرست رمزهای رایج.

خروجی موفق:

```
ساخته شد: YOURNAME (owner) — id=1
این نخستین حساب است؛ از این به بعد API بدون ورود بسته است.
```

از این لحظه:

| چه کسی | نتیجه |
|---|---|
| مرورگر ناشناس | صفحهٔ ورود؛ `/api/*` همه ۴۰۱ |
| کاربر واردشده | به‌اندازهٔ نقشش |
| `curl` با `X-API-Token` | کار می‌کند (برای اسکریپت) |
| `curl` بی‌توکن از همان سرور | ۴۰۱ — مسیر لوکال دیگر «هویت» نیست |

> **همین جملهٔ آخر مهم‌ترین تغییر است.** تا وقتی هیچ حسابی نبود، درخواست از
> `127.0.0.1` مجاز بود (راحتی توسعه). با وجود حساب، این معافیت برداشته می‌شود؛ وگرنه
> هر پردازه‌ای روی همان ماشین می‌توانست بی‌اجازه معامله کند.

### ۷.۲ دعوت بقیه

از داشبورد: **تنظیمات → کاربران و دسترسی** (فقط برای مالک) → «دعوت جدید» → نقش را
انتخاب کنید → کد را کپی کنید. کد **فقط یک‌بار** نمایش داده می‌شود؛ در دیتابیس فقط
هشِ آن هست.

یا از روی سرور:

```bash
sudo -u nobi NBX_AUTH_DB=/srv/nobi-data/auth.db \
  /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py invite viewer --note "برای سارا"
```

کاربر در صفحهٔ ورود روی «کد دعوت دارم» می‌زند و کد + نام کاربری + رمز را وارد می‌کند.
حساب با همان نقشی ساخته می‌شود که در دعوت تعیین شده بود — نه نقشی که در درخواست
می‌نویسد.

### ۷.۳ نقش‌ها

| نقش | می‌خواند | معامله/توقف/تنظیم | مدیریت کاربران |
|---|---|---|---|
| `owner` | ✓ | ✓ | ✓ |
| `operator` | ✓ | ✓ | ✗ |
| `viewer` | ✓ | ✗ | ✗ |

viewer همان داده را می‌بیند ولی هر تغییری ۴۰۳ می‌گیرد و در بالای صفحه نوشته می‌شود
«نقش شما فقط‌خواندنی است».

### ۷.۴ توکن، برای اسکریپت‌ها

```bash
curl -H "X-API-Token: $(sudo grep NBX_API_TOKEN /etc/nobi/nobi.env | cut -d= -f2)" \
     https://nobitrader.example.ir/api/paper/positions
```

دیگر لازم نیست کسی در Console مرورگر بنویسد `localStorage.setItem(...)`. اگر می‌خواهید
توکن را کامل بردارید، در `/etc/nobi/nobi.env` خالی کنید — ولی توجه: `NBX_TRUSTED_PROXIES`
هم باید ست بماند و `NBX_API_TOKEN` خالی + پروکسی = سرویس بالا نمی‌آید (بخش ۴).

### ۷.۵ مدیریت روزمره

```bash
M="sudo -u nobi NBX_AUTH_DB=/srv/nobi-data/auth.db /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py"
$M list                 # کاربران + شمارش نشست‌ها
$M role sara operator   # تغییر نقش
$M disable sara         # غیرفعال + بستن همهٔ نشست‌هایش
$M passwd sara          # رمز جدید (همه دستگاه‌ها خارج می‌شوند)
$M sessions sara        # فقط بستن نشست‌ها
$M invites              # دعوت‌ها و وضعیتشان
```

### ۷.۶ اگر رمزتان را فراموش کردید

```bash
sudo -u nobi NBX_AUTH_DB=/srv/nobi-data/auth.db \
  /opt/nobi/.venv/bin/python /opt/nobi/scripts/manage_users.py passwd YOURNAME
```

دسترسی به شل سرور = دسترسی به حساب. این طراحی است، نه نقص: بازیابی رمز از راه ایمیل
یعنی یک کانال جدید برای حمله، برای نرم‌افزاری که یک نفر اداره می‌کند.

## ۸. تأیید استقرار — این گام را رد نکنید

```bash
sudo -u nobi /opt/nobi/.venv/bin/python /opt/nobi/scripts/check_deploy.py \
    --public https://nobitrader.example.ir
```

این اسکریپت ۱۲ چیز را می‌سنجد و **اگر قفل باز باشد FAIL می‌دهد**. چیزی که باید ببینید
این شکل است (اعداد داخل آن — مسیر، نسخهٔ پایتون، فضای دیسک، تاریخ گواهی — روی سرور **شما**
تولید می‌شوند؛ من نمی‌توانم از این‌جا عدد سرور شما را بدانم و عدد جعلی نمی‌نویسم):

```
Nobi deployment preflight · /opt/nobi
  [ok  ] config    /opt/nobi/server/.env, mode 600, 8 keys
  [ok  ] python    3.12.3 (/opt/nobi/.venv/bin/python)
  [ok  ] packages  fastapi, uvicorn, httpx, pandas import
  [ok  ] token     set, 43 characters
  [ok  ] proxy     127.0.0.1
  [ok  ] storage   journal on /srv/nobi-data
  [ok  ] disk      38.1 GB free at /srv/nobi-data, 21% used
  [ok  ] exchange  USDTIRT book answered, 25 bid level(s)
  [ok  ] bind      app on 127.0.0.1:8000 (loopback only)
  [ok  ] accounts  3 account(s), 1 owner(s)
  [ok  ] readonly  anonymous POST -> 401 (refused)
  [skip] reads     ok, auth mode=token; GET endpoints need no token, so positions
                   and equity are readable by anyone with the URL
  [ok  ] tls       nobitrader.example.ir certificate valid until Mar 12 23:59:59 2026 GMT

11 ok · 1 skip · 0 error · 0 fail   →  OK
```

چیزی که **اندازه‌گیری** شده و می‌توانم نشان دهم، رفتار واقعی همین درخواست روی همین کد است.
چهار سناریو، از اجرای واقعی با `curl` روی یک نمونهٔ محلی:

| سناریو | درخواست | پاسخ |
|---|---|---|
| برنامه لوکال، بدون پروکسی، درخواست از 127.0.0.1 | `POST /api/paper/halt` | `200` (درست: کاربر لوکال است) |
| همان برنامه، با هدر جعلی `X-Forwarded-For: 203.0.113.9` | `POST /api/paper/halt` | **`403`** (قفل بسته شد) |
| پشت پروکسی بدون توکن، با `NBX_TRUSTED_PROXIES=127.0.0.1` | `POST /api/paper/halt` | `401` |
| پروکسی که هدر `X-Forwarded-For` را پاک می‌کند و برنامه بی‌توکن | `POST /api/paper/halt` | **`200` — همان FAIL که `readonly` گزارش می‌دهد** |

سطر آخر مهم است: عمداً یک پروکسی معیوب ساختم که هدر را حذف می‌کند، و `check_deploy.py` دقیقاً
همان را گرفت. یعنی ابزار روی پیکربندی معیوب ساکت نمی‌ماند.

چهار سطر را در خروجی خودتان جدی بگیرید:

- **`readonly`** — یک `POST /api/paper/halt` **بدون توکن** به آدرس عمومی می‌زند. جواب درست
  `401`/`403` است. `200` یعنی هر کسی می‌تواند معامله‌های شما را متوقف کند و پیامش هم
  «ANYONE CAN HALT TRADING» است. این تنها سنجه‌ای است که به‌جای پیکربندی، **رفتار** را
  می‌سنجد.
- **`bind`** — مطمئن می‌شود خودِ برنامه روی آدرس عمومی گوش نمی‌دهد.
- **`storage`** — مطمئن می‌شود journal روی دیسک ریشه نیست (وگرنه با بازسازی ابرک می‌رود).
- **`accounts`** — اگر حساب ساخته باشید، تعدادشان را نشان می‌دهد؛ اگر `auth.db` روی
  دیسک داده نباشد یا مالک فعال نداشته باشد، `FAIL`.
- **`reads`** — این یکی عمداً `skip` است، نه `ok`: توکن فقط اندپوینت‌های تغییردهنده را
  می‌بندد و `GET`ها بی‌احراز هویت‌اند. ابزاری که این را «سبز» نشان دهد، همان چیزی است
  که این پروژه از آن فرار می‌کند. اگر سایت را پشت basic auth بگذارید، این سطر `ok` می‌شود.

اگر `--public` را به `http://127.0.0.1:8000` بدهید، سنجهٔ `readonly` را `skip` می‌کند و
دلیلش هم نوشته می‌شود: مسیر لوکال **طبق طراحی** باز است، پس آزمایش از لوکال هیچ‌چیز را
ثابت نمی‌کند.

---

## ۹. بکاپ

چهار چیز ارزش بکاپ دارد: `journal.db` (تاریخچه/دفتر)، `paper_state.json` (پوزیشن‌های
مقاله)، **`auth.db`** (کاربران و نشست‌ها — اگر برود هیچ‌کس نمی‌تواند وارد شود)، و
`/etc/nobi/nobi.env` (توکن اسکریپت‌ها).

**الف) بکاپ سازگار با SQLite** (کپی خام از یک دیتابیس در حال نوشتن، ممکن است خراب باشد):

```bash
sudo mkdir -p /srv/nobi-data/backup
sudo -u nobi sqlite3 /srv/nobi-data/journal.db \
  ".backup '/srv/nobi-data/backup/journal-$(date +%F).db'"
sudo cp /srv/nobi-data/paper_state.json /srv/nobi-data/backup/paper-$(date +%F).json
sudo -u nobi sqlite3 /srv/nobi-data/auth.db \
  ".backup '/srv/nobi-data/backup/auth-$(date +%F).db'"
```

این را در یک تایمر روزانه بگذارید (`sudo crontab -e`):

```cron
15 3 * * * /usr/bin/sqlite3 /srv/nobi-data/journal.db ".backup '/srv/nobi-data/backup/journal-$(date +\%F).db'" && /usr/bin/find /srv/nobi-data/backup -name 'journal-*.db' -mtime +14 -delete
```

**ب) بیرون از ابرک.** بکاپی که روی همان دیسک است، بکاپ نیست:

```bash
# از کامپیوتر خودتان
scp -i nobitrader-key.pem -r ubuntu@203.0.113.10:/srv/nobi-data/backup ./nobitrader-backup-$(date +%F)
```

اگر مقصد S3 دارید، آروان **Object Storage** سازگار با S3 دارد و `rclone` یا `aws s3 cp` کار می‌کند.
(CLI آروان فقط برای کانتینر ابری است، برای ابرک از ابزارهای استاندارد استفاده کنید.)

**ج) لایه‌های پلتفرم.**
- **بکاپ هفتگی** که در ساخت ابرک روشن کردید.
- **اسنپ‌شات**: از منوی عملیات ابرک → «ایجاد اسنپ‌شات». مستندات توصیه می‌کند پیش از گرفتن
  اسنپ‌شات ابرک را **خاموش** کنید تا تصویر دیسک سازگار باشد.

`NBX_API_TOKEN` را در بکاپ نگه دارید ولی آن را جایی عمومی نگذارید. اگر توکن لو رفت، فقط
کافی است در `/etc/nobi/nobi.env` عوضش کنید و `systemctl restart nobi` بزنید و در مرورگر
هم `localStorage` را به‌روز کنید — چرخش توکن دو خط است.

---

## ۱۰. به‌روزرسانی

```bash
sudo -u nobi git -C /opt/nobi pull --ff-only
sudo /opt/nobi/.venv/bin/pip install -q -r /opt/nobi/server/requirements.txt
sudo systemctl restart nobi
```

بعد دوباره گام ۸ را اجرا کنید. اگر استقرارتان از یک fork/برنچ است، `pull` را با `fetch` و
`reset --hard origin/main` جایگزین کنید — همان دستوری که در `docs/run-windows.md` هم هست.

> اگر بعد از به‌روزرسانی سرویس بالا نیامد، اول `journalctl -u nobi -n 50 --no-pager` را
> بخوانید. شایع‌ترین علت، همان گاردِ `NBX_TRUSTED_PROXIES`/`NBX_API_TOKEN` است؛ یعنی نسخهٔ
> جدید محیط را ناقص دیده، نه اینکه خراب باشد.

---

## ۱۱. عیب‌یابی

| نشانه | علت معمول | کار |
|---|---|---|
| `502 Bad Gateway` | سرویس بالا نیست یا روی پورت دیگری است | `systemctl status nobi --no-pager` و `sudo ss -lntp \| grep 8000` |
| `Application startup failed` + `RuntimeError: NBX_TRUSTED_PROXIES is set but NBX_API_TOKEN is not` | `.env` ناقص | توکن را در `/etc/nobi/nobi.env` بگذارید، `systemctl restart nobi` |
| همه‌چیز ۴۰۳ | `NBX_TRUSTED_PROXIES` خالی است ولی nginx هدر `X-Forwarded-For` می‌فرستد | `NBX_TRUSTED_PROXIES=127.0.0.1` را ست کنید (این دقیقاً همان `readonly` در چک‌لیست است) |
| دکمه‌ها ۴۰۱، صفحه لود می‌شود | توکن در `localStorage` نیست/غلط است | گام ۷ |
| certbot: `Timeout during connect` | رکورد DNS هنوز به IP نرسیده، یا رکورد پشت CDN است، یا پورت ۸۰ در فایروال بسته است | `dig +short nobitrader.example.ir`، پروکسی رکورد را خاموش کنید، قانون ۸۰ را اضافه کنید |
| صفحه قدیمی می‌آید | کش مرورگر یا سرویس قدیمی | `Cache-Control: no-store` هست؛ hard reload و `systemctl restart nobi` |
| `pandas` نصب نمی‌شود / `No matching distribution` | نسخهٔ پایتون خیلی قدیمی یا خیلی جدید برای wheelها | `python -V`؛ روی ۳.۱۱ تا ۳.۱۳ بمانید |
| سرویس بعد از ری‌استارت ابرک بالا نمی‌آید | `enable` نشده | `sudo systemctl enable nobi` |
| `journal.db` صفر/گم می‌شود | `ReadWritePaths` اجازه نمی‌دهد، یا مسیر روی دیسک mount نشده | `ls -l /srv/nobi-data` و `journalctl -u nobi \| grep -i permission` |

برای دیدن لاگ زنده:

```bash
sudo journalctl -u nobi -f
```

### اگر ابرک بوت نشد: حالت Rescue

پنل → عملیات ابرک → **حالت Rescue**. با دیسک نجات وارد می‌شوید، فایل‌سیستم را بررسی/تعمیر
می‌کنید، و **بعد باید از حالت Rescue خارج شوید** وگرنه سرور همان‌جا می‌ماند.

### تغییر اندازه، و هزینهٔ خاموش بودن

- **تغییر اندازه (resize)** فقط وقتی ابرک **خاموش** است ممکن است.
- در حالت **Stop** هزینهٔ RAM و CPU هم پرداخت می‌شود ولی سرور سریع روشن می‌شود.
- در حالت **Terminate** فقط IP و دیسک رزرو می‌ماند (و هزینه‌شان کم می‌شود) ولی روشن شدن دوباره
  طولانی‌تر است.

---

## ۱۲. چک‌لیست

استقرار وقتی «تمام» است که همهٔ این‌ها تیک بخورند، نه وقتی صفحه باز شد:

- [ ] ابرک اوبونتو ۲۴.۰۴ بالا است و `sudo apt-get upgrade` اجرا شده
- [ ] کد در `/opt/nobi` است و کاربر `nobi` مالکش است
- [ ] `/etc/nobi/nobi.env` با `mode 640` و مالک `root:nobi`، شامل `NBX_API_TOKEN` و `NBX_TRUSTED_PROXIES=127.0.0.1`
- [ ] `NBX_JOURNAL_DB` و `NBX_PAPER_STATE` روی دیسک دادهٔ جدا هستند، نه روی دیسک ریشه
- [ ] `systemctl enable --now nobi` و سرویس بعد از ری‌استارت هم بالا می‌آید (`sudo reboot` را امتحان کنید)
- [ ] برنامه فقط روی `127.0.0.1:8000` گوش می‌دهد
- [ ] `https://دامنه` گواهی معتبر دارد و HTTP به HTTPS ریدایرکت می‌شود
- [ ] فایروال آروان: ۲۲ از IP خودتان، ۸۰ و ۴۴۳ از همه، بقیه بسته؛ فایروال **متصل** به ابرک
- [ ] `check_deploy.py --public https://دامنه` خروجی `12 ok · 0 fail` می‌دهد و مخصوصاً `readonly  anonymous POST -> 401`
- [ ] حداقل یک حساب `owner` ساخته شده و با آن در مرورگر وارد شده‌اید
- [ ] یک حساب `viewer` با کد دعوت ساخته‌اید و دکمه‌های تغییر برایش ۴۰۳ می‌دهند
- [ ] `auth.db` روی دیسک داده است (نه داخل `/opt/nobi`) و در بکاپ هست
- [ ] بکاپ دستی یک‌بار گرفته و **بازیابی** شده است (بکاپ آزمایش‌نشده، بکاپ نیست)
- [ ] `sudo certbot renew --dry-run` سبز است
- [ ] هیچ توکنی در مخزن، در URL، یا در صفحهٔ داشبورد نیست

---

### پیوست: فایل‌های استقرار در مخزن

| فایل | چه می‌کند |
|---|---|
| `scripts/deploy_arvan.sh` | نصب گام‌به‌گام و idempotent |
| `scripts/check_deploy.py` | سنجش استقرار (۱۲ چک، خروجی غیرصفر اگر چیزی باز باشد) |
| `deploy/nobi.service` | واحد systemd با hardening و `ReadWritePaths` محدود |
| `deploy/nginx.conf` | سایت TLS با هدرهای `X-Forwarded-*` درست |
| `deploy/nobi.env.example` | الگوی `/etc/nobi/nobi.env` با توضیح هر خط |

> **دربارهٔ تست‌شدن:** واحد systemd با `systemd-analyze verify` بررسی شده و پاک است.
> پیکربندی nginx روی این ماشین nginx در دسترس نبود، پس **syntax آن را من تست نکرده‌ام**؛
> قبل از `reload` حتماً `sudo nginx -t` بزنید (اسکریپت هم همین کار را می‌کند و در صورت
> خطا متوقف می‌شود). یک نکتهٔ نسخه‌ای که در فایل رعایت شده: اوبونتو ۲۴.۰۴ nginx 1.24 دارد
> و دستور `http2 on;` در آن وجود ندارد، برای همین HTTP/2 روی خط `listen` فعال شده است.

مستندات آروان که این راهنما بر آن‌ها تکیه دارد:

- ابرک (ساخت، مدیریت، اسنپ‌شات): `https://docs.arvancloud.ir/fa/cloud-server/instance/`
- فایروال (قوانین، پیش‌فرض‌ها، اتصال به ابرک): `https://docs.arvancloud.ir/fa/cloud-server/firewall/`
- دیسک و mount: `https://docs.arvancloud.ir/fa/cloud-server/volume/` و `…/volume/partition/`
- اتصال و کاربر پیش‌فرض هر تصویر: `https://docs.arvancloud.ir/fa/cloud-server/instance/connection/`
- API و Terraform (اگر خواستید ساخت را خودکار کنید): `https://docs.arvancloud.ir/fa/developer-tools/`

### کش کندل و جای داده‌ها

`NBX_CANDLE_CACHE=/srv/nobi-data/candle-cache` و `NBX_BT_DIR=/srv/nobi-data/backtest-reports`
را در `/etc/nobi/nobi.env` نگه دارید. این مسیرها عمداً بیرون از checkout هستند: سرویس با `ProtectSystem=strict` اجرا می‌شود و فقط `/srv/nobi-data`
برای نوشتن باز است. پیش‌تر `candle_cache` و `backtest` پوشه‌هایشان را کنار سورس خودشان و **در زمان import**
می‌ساختند، پس اولین import (از `nobitex_client` و `main`) با `PermissionError` می‌شکست و سرویس سه بار ری‌استارت و
متوقف می‌شد. اکنون اگر مسیر قابل نوشتن نباشد، برنامه با یک هشدار به مسیر موقت برمی‌گردد و بالا
می‌آید؛ اما مسیر درست همان دیسک داده است تا با بازسازی ابرک از ایمیج از دست نرود. اسکریپت نصب،
این متغیر و پوشه‌اش را برای envهای موجود هم اضافه می‌کند.

### حساب کاغذی: نوشتن اتمیک و بازیابی

`NBX_PAPER_STATE` با فایل موقت + `fsync` + `os.replace` نوشته می‌شود و نسخهٔ قبلی به‌عنوان
`.bak` کنار آن می‌ماند. اگر فایل اصلی نیمه‌کاره یا ناخوانا باشد، ابتدا `.bak` بارگذاری می‌شود؛
اگر آن هم نبود، فایل آسیب‌دیده به `*.corrupt-<ts>.json` **منتقل** می‌شود (پاک نمی‌شود) و
`state_recovery` در `/api/paper/metrics` نشان می‌دهد که حساب از سرمایهٔ تنظیم‌شده شروع شده است.
پیش از این، هر خطای خواندن بی‌صدا به «شروع از صفر» تبدیل می‌شد.

### کش مشخصات و لوگوی نمادها

برای این قابلیت، `NBX_COIN_CACHE=/srv/nobi-data/coin-cache` را در `/etc/nobi/nobi.env` نگه دارید؛ مسیر داخل checkout با `ProtectSystem=strict` قابل نوشتن نیست. اسکریپت نصب، این متغیر را به env قدیمی هم اضافه می‌کند و به رازهای موجود دست نمی‌زند. `NBX_COINGECKO_API_KEY` اختیاری و مخصوص کلید Demo است؛ فقط در env سرور تنظیم شود، نه UI یا مخزن. بعد از تغییر env، سرویس را restart کنید.

این کش قابل بازسازی است و بخشی از اطلاعات حساب/معامله نیست. خطای دریافت آن نباید مانع کار بازار شود؛ کش قبلی، سپس لوگوهای همراه برنامه و در آخر متن نماد نمایش داده می‌شوند. کارت `coin_metadata` در تنظیمات وضعیت واقعی پوشش را نشان می‌دهد. راهنمای کامل: [مشخصات و لوگوی نمادها](coin-metadata.md).

## نصب helper محدود ویزارد مرورگری (v1.9.0)

فرایند وب هرگز root نیست. عملیات privileged از طریق **یک سوکت یونیکس**
(`nobi-installer.socket`) به سرویس root جداگانهای (`nobi-installer.service`)
میرسد — نه `sudo`. دلیل: یونیت وب `NoNewPrivileges=yes` دارد (بیت setuid روی
`sudo` بیکار میشود) و `ProtectSystem=strict` دارد (`/etc` را فقطخواندنی میکند).
هیچ قانون `sudoers`ای نصب نمیشود؛ اگر از نسخهٔ قبل باقی مانده است:
`sudo rm -f /etc/sudoers.d/nobi-helper`.

`scripts/deploy_arvan.sh` همهٔ اینها را انجام میدهد. بهصورت دستی:

```bash
sudo install -d -o root -g root -m 0755 /usr/local/share/nobi-installer
sudo install -o root -g root -m 0444 deploy/nobi.service /usr/local/share/nobi-installer/nobi.service
sudo install -o root -g root -m 0444 deploy/nginx.conf  /usr/local/share/nobi-installer/nginx.conf
sudo install -o root -g root -m 0755 deploy/nobi-privileged-helper /usr/local/libexec/nobi-privileged-helper
sudo install -o root -g root -m 0755 deploy/nobi-installer-helper.py /usr/local/libexec/nobi-installer-helper.py
sudo install -o root -g root -m 0755 deploy/nobi-helper-client /usr/local/bin/nobi-helper-client
sudo install -o root -g root -m 0644 deploy/nobi-installer.socket /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/nobi-installer.service /etc/systemd/system/
echo 'nobitrader.example.ir' | sudo tee /etc/nobi/domain >/dev/null
sudo chown root:root /etc/nobi/domain && sudo chmod 0444 /etc/nobi/domain
sudo systemctl daemon-reload && sudo systemctl enable --now nobi-installer.socket
sudo python3 /usr/local/libexec/nobi-privileged-helper --protocol 1 check
```

قالبها **عمداً بیرون از checkout** و `root:root 0444` هستند: `deploy_arvan.sh`
مالکیت `/opt/nobi` را به کاربر سرویس میدهد، و کپی کردن یک یونیت systemd
قابلنوشتنتوسطسرویس به `/etc` بهعنوان root، یک ارتقای امتیاز بود. helper علاوه بر
مسیر، مالک و mode فایل منبع را هم بررسی میکند. دامنه از فایل root-owned
`/etc/nobi/domain` خوانده میشود، نه از body درخواست مرورگر.

قبل از Apply، Host preflight را در Settings اجرا کنید؛ این بار علاوه بر وجود فایل،
خودِ پروتکل helper را هم (`check`) امتحان میکند. restart/reload/rollback تأیید جدا
میخواهند و Live Mode را فعال نمیکنند.
