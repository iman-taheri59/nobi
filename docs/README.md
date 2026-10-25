# مستندات nobitrader

**فارسی** · [English](README.en.md)

نسخهٔ جاری: **1.4.4**. راهنمای [نسخه‌بندی و انتشار GitHub](releases.md) ([English](releases.en.md)) و [تاریخچهٔ تغییرات](../CHANGELOG.md) ([English](../CHANGELOG.en.md)).

## نقشهٔ مستندات

این فایل فقط راهنمای پیدا کردن است؛ خودِ محتوا در پوشه‌های کنارش است.

## اگر می‌خواهی…

| می‌خواهی… | برو به |
|---|---|
| پروژه را راه بیندازی | [`../README.md`](../README.md) |
| بفهمی چرا معماری این‌طور است | [`adr/`](adr) — ۱۸ ADR، هر کدام فارسی و انگلیسی |
| ببینی کدام تصمیم واقعاً پیاده شده | [`adr/README.md`](adr/README.md) — جدول وضعیت با شاهد کد |
| بدانی آخرین بار چه چیزی تأیید شد و چه چیزی نه | [`reviews/`](reviews) |
| بدانی موتورهای تحلیل امروز چه چیزی را واقعاً محاسبه می‌کنند | [`analysis/`](analysis) — کاتالوگ + جدول «پیاده‌شده / فقط کاتالوگ» |
| سرور ارزان برای تست انتخاب کنی | [`ops/`](ops) |
| پروژه را روی ابر آروان (ابرک اوبونتو) نصب کنی | [`deploy-arvan.md`](deploy-arvan.md) — گام‌به‌گام + `scripts/deploy_arvan.sh` + `scripts/check_deploy.py` |
| تصمیم بگیری VPC لازم است یا نه | [`deploy-arvan-vpc.md`](deploy-arvan-vpc.md) — ایزوله‌سازی، هزینه، و معماری دو‌ابرکی |
| تصمیم بگیری نردبان سود زنده چه باشد | [`rr-ladder-tuning.md`](rr-ladder-tuning.md) — روش، دروازهٔ ADR-0009 و قاعدهٔ بازگشت |
| ببینی مسیر قابلیت‌ها کجاست | [`roadmap/fa/capability-roadmap.md`](roadmap/fa/capability-roadmap.md) |

## ADRها (تصمیم‌های معماری)

| # | موضوع |
|---|---|
| 0001 | چشم‌انداز، اهداف و دامنه |
| 0002 | معماری و پشتهٔ فناوری |
| 0003 | اتصال به API نوبیتکس |
| 0004 | پایپ‌لاین و ذخیره‌سازی دادهٔ بازار |
| 0005 | موتور تحلیل تکنیکال |
| 0006 | موتور فاندامنتال |
| 0007 | موتور تصمیم‌گیری و تلفیق سیگنال |
| 0008 | مدیریت ریسک و حجم‌دهی پوزیشن |
| 0009 | اجرا، بک‌تست، امنیت و عملیات |
| 0010 | زیرساخت سخت‌افزاری و سرور |
| 0011 | رابط کاربری و داشبورد |
| 0012 | پایش و گزارش عملکرد سیگنال‌ها |
| 0013 | اجرای ترکیبی خودکار و دستی |
| 0014 | قرارداد صداقت داده — سه حالت هر پنل |
| 0015 | واحد پول، نماد بازار و دروازه‌های صرافی در مرز API |
| 0016 | دروازه‌های ایستا و CI |
| 0017 | تست و بررسی از داخل داشبورد |
| 0018 | حساب کاربری، ورود و عضویت با دعوت |

## بازبینی‌ها — کدام را کِی بخوانم

| فایل | چه چیزی هست |
|---|---|
| [`reviews/2026-10-19-project-review-fa.md`](reviews/2026-10-19-project-review-fa.md) | بازبینی کامل پروژه: وضعیت، باگ‌ها، مسیر |
| [`reviews/2026-10-20-live-path-verification-fa.md`](reviews/2026-10-20-live-path-verification-fa.md) | تأیید نیمهٔ «داده زنده»: چه سنجیده شد، ۲۰ باگ، چه چیزی هنوز اثبات نشده |
| [`reviews/2026-10-20-nobitex-api-conformance-fa.md`](reviews/2026-10-20-nobitex-api-conformance-fa.md) | انطباق با مستندات رسمی نوبیتکس: ۱۶ مغایرت، اصلاح‌ها و باقی‌مانده‌ها |
| [`reviews/2026-10-20-js-broker-parity-fa.md`](reviews/2026-10-20-js-broker-parity-fa.md) | بروکر مرورگر: ۳ دروازه → ۱۰ دروازه، با تست هم‌ارزی Python/JS |
| [`reviews/probe-live-2026-10-20.md`](reviews/probe-live-2026-10-20.md) | خروجی خام پروب با فید زنده (۴۴ پنل) |
| [`reviews/probe-offline-2026-10-20.md`](reviews/probe-offline-2026-10-20.md) | خروجی خام پروب با صرافیِ قطع (۴۴ پنل) |
| [`reviews/2026-10-21-shared-constants-fa.md`](reviews/2026-10-21-shared-constants-fa.md) | ثابت‌های مشترک: کارمزد، سرمایهٔ شروع و دروازهٔ مردهٔ `low-rr` — و دو شکافی که عمداً بسته نشد |
| [`reviews/real-ic-2026-10-19.json`](reviews/real-ic-2026-10-19.json) | اندازه‌گیری IC روی دادهٔ واقعی |

> **قاعده:** بازبینی‌ها عکس لحظه‌ای‌اند و بازنویسی نمی‌شوند. اگر بعداً چیزی
> درست شد، بازبینی تازه نوشته می‌شود که به قبلی ارجاع می‌دهد — تاریخِ ادعاها
> باید قابل ردیابی بماند.

## ops

| فایل | موضوع |
|---|---|
| [`ops/fa/free-server-options-for-testing-and-development.md`](ops/fa/free-server-options-for-testing-and-development.md) | گزینه‌های سرور رایگان/ارزان برای تست (نسخهٔ انگلیسی هم موجود است) |

## چه چیزی دوزبانه است، چه چیزی نه

هر جفت دوزبانه یک سوییچ زبان در خط اول دارد. دروازهٔ `server/tests/test_docs_bilingual.py`
جفت‌بودن ADRها و سوییچ زبان و شمارهٔ نسخه را بررسی می‌کند.

| سند | فارسی | انگلیسی |
|---|---|---|
| README ریشه | [`../README.md`](../README.md) | [`../README.en.md`](../README.en.md) |
| نقشهٔ مستندات | همین فایل | [`README.en.md`](README.en.md) |
| نسخه‌بندی و انتشار | [`releases.md`](releases.md) | [`releases.en.md`](releases.en.md) |
| تاریخچهٔ تغییرات | [`../CHANGELOG.md`](../CHANGELOG.md) | [`../CHANGELOG.en.md`](../CHANGELOG.en.md) |
| ADR-0001 … ADR-0018 | [`adr/fa/`](adr/fa) | [`adr/en/`](adr/en) |
| سرور رایگان تست | [`ops/fa/`](ops/fa) | [`ops/en/`](ops/en) |
| استقرار آروان | [`deploy-arvan.md`](deploy-arvan.md) | **در انتظار** |
| VPC آروان | [`deploy-arvan-vpc.md`](deploy-arvan-vpc.md) | **در انتظار** |
| اجرا روی ویندوز | [`run-windows.md`](run-windows.md) | **در انتظار** |
| راهنمای تست | [`testing.md`](testing.md) | [`testing.en.md`](testing.en.md) |
| مشخصات و لوگوی نمادها | [`coin-metadata.md`](coin-metadata.md) | [`coin-metadata.en.md`](coin-metadata.en.md) |
| نمایش مالی | [`financial-display.md`](financial-display.md) | [`financial-display.en.md`](financial-display.en.md) |
| شاهد خارج‌از‌نمونه | [`evidence.md`](evidence.md) | [`evidence.en.md`](evidence.en.md) |
| تنظیم نردبان سود (`NBX_RR_LEVELS`) | [`rr-ladder-tuning.md`](rr-ladder-tuning.md) | [`rr-ladder-tuning.en.md`](rr-ladder-tuning.en.md) |
| نقشهٔ قابلیت‌ها | [`roadmap/fa/`](roadmap/fa) | **در انتظار** |
| کاتالوگ موتورها | [`analysis/`](analysis) | **در انتظار** |
| بازبینی‌ها (تاریخ‌دار) | [`reviews/`](reviews) | فقط عکس لحظه‌ای — عمداً ترجمه نمی‌شود |

«در انتظار» یک وضعیت صادقانه است، نه یک وعده: این سندها امروز فقط فارسی‌اند و ترجمه‌ای
که از منبعش جلو بزند از نبودنش بدتر است.

## مشخصات و لوگوی نمادها

[`coin-metadata.md`](coin-metadata.md) — اولویت مشخصات نوبیتکس، لوگو و نام جایگزین CoinGecko، کش پایدار و نگاشت صریح هویت ارزها، تنظیم کلید Demo و مسیر داده، وضعیت پوشش و جایگزین آفلاین.
