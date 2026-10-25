# تحویل کار — تکمیل nobitrader

[English](HANDOFF.en.md) · **فارسی**

**آخرین به‌روزرسانی:** ۲۰۲۶-۱۰-۲۵ — مرحله ۲۷ (جداسازی قراردادهای UI CI)
**نسخه کاری:** ۱٫۶٫۵
**هدف:** ADR-0027؛ ابتدا محصول پایدار، سپس اجرای واقعی مرحله‌ای و fail-closed.

## وضعیت

- [x] فاز A: تحلیل/Paper پایدار، backup/restore، تاریخچه صفحه‌بندی‌شده و CI مسدودکننده.
- [x] B1: signer مستقل Ed25519؛ هیچ secret در log/ledger.
- [x] B2: disabled/dry-run/live، ledger، idempotency، unknown-state و reconciliation.
- [x] B3-primitive: فهرست سفارش و لغو بر اساس `clientOrderId` با cancel-unknown بدون retry.
- [x] B3-route: روت‌های خصوصی status/list/cancel/reconcile؛ place عمداً وجود ندارد.
- [x] B4: dry-run place با preflight اجباری و audit actor؛ بدون ارسال شبکه.
- [x] B5: دروازه شاهد ۳۰ نمونه/۵ روز/صفر ambiguity؛ `applied:false`.
- [x] B6: backup هر سه DB، سازگاری قالب قدیمی و snapshot روزانهٔ بدون secret.
- [ ] اجرای soak واقعی پنج‌روزه در محیط متصل به نوبیتکس.
- [x] W1: ویزارد اولین نصب و ساخت owner با توکن یک‌بارمصرف کنسول.
- [x] W2: مسیرهای داده، scan/TTL و disabled/dry-run از UI owner؛ managed.env بدون secret.
- [ ] W2b: بررسی و تولید پیکربندی reverse proxy/systemd با helper غیر-root.
- [x] W3: ۱۳ gate تست/lint/parity/build/deploy-check از allowlist فقط-owner.
- [x] W3b: backup/restore/restart و systemd/nginx با helper محدود سیستم‌عامل.
- [ ] روی سرور واقعی: helper و sudoers باید root-owned نصب و `NBX_INSTALL_HELPER` تنظیم شود.
- [x] endpoint واقعی `/api/live/place` فقط-owner با CSRF، تأیید صریح، preflight و promotion gate.
- [ ] اجرای soak واقعی ۵روزه/۳۰نمونه؛ تا آن زمان live mode فعال نشود.
- [x] تب مستقل کاربران با مدیریت نقش/وضعیت/نشست/دعوت و activity فقط-owner.
- [x] تب خطایابی، error log، diagnostics و devsuite فقط-owner؛ mutationها با CSRF.
- [x] audit خلاصه و بدون secret برای self-test، diagnostics و devsuite؛ chip صریح owner-only.
- [x] ماتریس کامل دسترسی: settings/cache فقط-owner؛ audit/notify/live-private فقط operator/owner؛ token بدون نقش انسانی.
- [x] capability مستقل `live_place` برای operator منتخب با limit-only، سقف مالی/تعداد/نماد و بودجه اتمیک.
- [x] تب «حساب من» برای همه نقش‌ها: capability/policy/usage، session، activity، password و preferences.
- [x] Signal Intelligence v1: metrics، funnel، cohort، MFE/MAE، detail و review operator/owner.
- [x] اصلاح سازگاری CI: بستن صریح SQLite در Windows، آزمون permission مختص POSIX و probe سازگار با dialog نصب اولیه.
- [ ] soak واقعی و contract test خصوصی؛ نیازمند محیط ایران و کلید READ/TRADE محدود به IP.
- [ ] بازبینی امنیتی مستقل پیش از سرمایه واقعی.

## تصمیم‌های قفل‌شده

1. live پیش‌فرض خاموش است و عبارت تأیید صریح می‌خواهد.
2. هیچ timeout ثبت/لغو کور retry نمی‌شود؛ ابتدا reconciliation.
3. یک idempotency key با payload متفاوت fail-closed است.
4. route ثبت واقعی تا preflight، نقش و audit کامل عرضه نمی‌شود.
5. secret وارد repository، مرورگر، diagnostics، error text یا backup نمی‌شود.

## فایل‌های این مراحل

- ADR-0027 تا ADR-0030، فارسی و انگلیسی
- `server/nobitex_signing.py`
- `server/live_execution.py`
- `server/tests/test_nobitex_signing.py`
- `server/tests/test_live_execution.py`
- `scripts/state_backup.py` و `scripts/fetch_history.py`

## آخرین تأیید

```text
ruff: passed
mypy: success, 115 source files
pytest: 968 passed, 1 dependency deprecation warning
ESLint/dashboard lint: passed
broker/preflight parity: passed
mobile artifact: rebuilt for 1.6.0
```

## محدودیت خارجی

- probe مرورگر محلی در sandbox قبلی به‌علت بسته‌شدن Chromium اجرا نشد؛ job CI از `--with-deps` استفاده می‌کند.
- soak و API خصوصی بدون credential و IP مناسب قابل ادعا نیستند.

## قدم بعدی

1. شاخه سخت‌سازی‌شده ۱٫۶٫۰ push شود و پیش از جابه‌جایی تگ، CI راه‌دور سبز باشد؛
2. استقرار با قرار دادن همه مسیرهای قابل‌تغییر روی volume پایدار `/var/lib/nobi` انجام شود؛
3. soak واقعی پنج‌روزه/۳۰ نمونه‌ای Paper روی میزبان قابل‌دسترسی Nobitex اجرا شود؛
4. contract test محدود API خصوصی با کلید IP-limited اجرا شود؛
5. پیش از سرمایه واقعی بازبینی امنیتی مستقل تکمیل شود.
