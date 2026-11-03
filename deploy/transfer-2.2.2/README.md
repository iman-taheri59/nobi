# ارتقا سریع nobi از 2.2.1 به 2.2.2

این بسته **دلتا** است، نه کل سورس: فقط ۱۷ فایل تغییریافته/اضافه‌شده بین `2.2.1` و `2.2.2` را دارد. تغییر این نسخه فقط رابط کاربری/تشخیص صف است؛ وابستگی‌ها، nginx، env، یونیت systemd، داده‌ها، Live/Auto و مسیر سفارش واقعی تغییر نمی‌کنند.

**پیش‌نیاز:** سرور باید دقیقاً `2.2.1` باشد و فایل‌های runtime آن با `RUNTIME_PRECHECK.sha256` مطابقت داشته باشند. اسکریپت در غیر این صورت متوقف می‌شود.

## 1) انتقال بسته

کل workspace را به‌صورت patch دانلود و به سرور منتقل کنید، مثلاً `/root/nobi-workspace.patch`. سپس فقط این پوشه را استخراج کنید:

```bash
mkdir -p /root/nobi-transfer-2.2.2
cd /root/nobi-transfer-2.2.2
git init -q
grep -n 'deploy/transfer-2.2.2/nobi-2.2.2-delta.tar.gz.b64' /root/nobi-workspace.patch | head
git apply --include='*deploy/transfer-2.2.2/*' --check /root/nobi-workspace.patch
git apply --include='*deploy/transfer-2.2.2/*' /root/nobi-workspace.patch
test -f deploy/transfer-2.2.2/nobi-2.2.2-delta.tar.gz.b64
```

اگر `grep` چیزی چاپ نکرد، patch قدیمی/ناقص است؛ ادامه ندهید.

## 2) ارتقا

```bash
sudo bash deploy/transfer-2.2.2/apply-2.2.2.sh
```

اسکریپت این کارها را می‌کند: precheck، decode/verify دلتا، backup فایل‌های تغییریافته و env/nginx/unit، stop سرویس، overlay دلتا، start سرویس، health check. مسیرها پیش‌فرض هستند و با env قابل تغییرند:

```text
APP_DIR=/opt/nobi
SERVICE=nobi
SERVICE_USER=nobi
ENV_FILE=/etc/nobi/nobi.env
NGINX_SITE=/etc/nginx/sites-available/nobi
UNIT_FILE=/etc/systemd/system/nobi.service
DATA_DIR=/srv/nobi-data
HEALTH_URL=http://127.0.0.1:8000/api/health
```

اگر backup داده هم می‌خواهید، کندتر می‌شود ولی امن‌تر است:

```bash
sudo DATA_BACKUP=1 bash deploy/transfer-2.2.2/apply-2.2.2.sh
```

## 3) تأیید

```bash
cat /opt/nobi/VERSION
curl -fsS 'https://capoy.ir/api/health?upgrade=2.2.2'
```

انتظار: `VERSION = 2.2.2` و health نسخهٔ `2.2.2` را نشان دهد. در مرورگر hard reload کنید. بخش جدید «کاندیداهای فیلترشده» فقط وقتی دیده می‌شود که کاندیدایی در پیش‌نمایش اجرایی رد شده باشد.

## 4) Rollback

اسکریپت مسیر backup را چاپ می‌کند. برای برگشت:

```bash
sudo bash deploy/transfer-2.2.2/rollback-2.2.2.sh /root/nobi-backup-<stamp>
```

این فقط فایل‌های دلتا را به حالت backup برمی‌گرداند و فایل اضافه‌شدهٔ `2.2.2` را حذف می‌کند. env/nginx/unit/data را دست نمی‌زند، چون این نسخه آن‌ها را تغییر نداده است.

## ایمنی

- Live/Auto را فعال نکنید.
- هیچ سفارش واقعی ارسال نکنید.
- `MIN_RR`، پیش‌نمایش اجرایی، سایزینگ و اهداف تغییر نکرده‌اند.
- این بسته secret، env زنده، دیتابیس، paper state یا کلید خصوصی ندارد.
