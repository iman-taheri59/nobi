# تحویل کار — تکمیل nobitrader

[English](HANDOFF.en.md) · **فارسی**

**آخرین به‌روزرسانی:** ۲۰۲۶-۱۰-۲۵ — مرحله ۱۱ (W1 ویزارد owner کامل)
**نسخه کاری:** ۱٫۳٫۰
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
- [ ] W2: تنظیمات نصب/مسیرها/reverse proxy از UI.
- [ ] W3: اجرای تمام تست‌ها، build، backup/restore و استقرار از allowlist فقط-owner.
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
mypy: success, 102 source files
pytest: 926 passed, 1 dependency deprecation warning
ESLint/dashboard lint: passed
broker/preflight parity: passed
mobile artifact: rebuilt for 1.2.3
```

## محدودیت خارجی

- probe مرورگر محلی در sandbox قبلی به‌علت بسته‌شدن Chromium اجرا نشد؛ job CI از `--with-deps` استفاده می‌کند.
- soak و API خصوصی بدون credential و IP مناسب قابل ادعا نیستند.

## قدم بعدی

1. انتشار ۱٫۲٫۶ و اجرای CI؛
2. افزودن dry-run place فقط برای اپراتور؛
3. اتصال همان `risk_engine.preflight` پیش از ساخت هر intent؛
4. audit actor/request بدون payload حساس؛
5. soak dry-run، سپس contract test محدود و بازبینی امنیتی.
