# ADR-0037 — helper محدود سیستم‌عامل و بازیابی مالک

[English](../en/ADR-0037-constrained-helper-and-restore.md) · **فارسی**

**وضعیت:** پذیرفته — W3b در ۱٫۳٫۳
**تاریخ:** ۲۰۲۶-۱۰-۲۵

## تصمیم

- وب‌اپ همیشه non-root می‌ماند. helper جدا، root-owned و با sudoers محدود فقط دو action ثابت `apply-config` و `restart` دارد.
- helper فقط JSON با schema دقیق می‌پذیرد، دامنه DNS را validate می‌کند و subprocessهای مطلق و allowlisted را بدون shell اجرا می‌کند.
- systemd و nginx از templateهای root-owned در `/usr/local/share/nobi-installer` تولید می‌شوند (نه checkout قابل‌نوشتن سرویس)؛ مقصدها ثابت‌اند. `systemd-analyze verify` و `nginx -t` پیش از reload اجرا و در خطا فایل قبلی rollback می‌شود.
- backup با SQLite online backup، checksum و manifest موجود ساخته می‌شود؛ `.env` و secret در archive نیست.
- restore فقط archive با نام allowlisted در پوشه backup مدیریت‌شده و عبارت دقیق `RESTORE <file>` را می‌پذیرد. restart عبارت دقیق `RESTART` می‌خواهد.
- endpointها فقط owner+CSRF، دارای timeout، خروجی محدود و audit mode 0600 هستند.

## پیامدها

وب‌اپ هیچ package یا command دلخواه اجرا نمی‌کند. نصب اولیه خود helper و policy sudoers عمداً یک اقدام مدیریتی سیستم‌عامل است؛ نبود helper با 503 fail-closed می‌شود. restore نشست‌ها را تغییر می‌دهد و پس از آن restart لازم است.
