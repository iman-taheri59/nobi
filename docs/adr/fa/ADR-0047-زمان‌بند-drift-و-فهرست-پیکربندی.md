# ADR-0047 — زمان‌بند Drift و فهرست پیکربندی

**فارسی** · [English](../en/ADR-0047-drift-scheduler-and-config-inventory.md)

**وضعیت:** پذیرفته‌شده برای ۱٫۸٫۰  
**تاریخ:** ۲۰۲۶-۱۰-۲۵

زمان‌بندی Drift پیش‌فرض خاموش و فقط در اختیار مالک است. فاصله‌های مجاز ۶، ۱۲ و ۲۴ ساعت‌اند؛ startup یا پردازش تأخیردار حداکثر یک run سررسیدشده را اجرا می‌کند و backlog را پشت‌سرهم replay نمی‌کند. task زیر lifespan سرور supervision می‌شود، اجرای هم‌زمان را رد و زمان/خطای آخر را ثبت می‌کند. این task فقط تحلیل می‌کند و config، کنترل ریسک یا سفارش را تغییر نمی‌دهد.

ردیف singleton جدول `drift_schedule` وضعیت فعال‌بودن، فاصله، پنجره، horizon، cohort mode، actor، زمان‌ها و خطای آخر را پایدار می‌کند. inventory پیکربندی تعداد signal هر `config_hash` را در baseline/current هم‌اندازه مقایسه و وضعیت را continued، new یا missing اعلام می‌کند. تغییر schedule فقط برای مالک با CSRF و audit است؛ خوانندهٔ احرازشده schedule و inventory را می‌بیند.
