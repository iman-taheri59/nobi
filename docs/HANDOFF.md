# تحویل کار — تکمیل nobitrader

[English](HANDOFF.en.md) · **فارسی**

**آخرین به‌روزرسانی:** ۲۰۲۶-۱۰-۲۵ — مرحله ۳۲ (سخت‌سازی زمان‌بند drift)
**نسخه کاری:** ۱٫۸٫۰ (منتشرنشده؛ انتشار فقط پس از CI سبز)

### وضعیت مرحلهٔ ۳۲

- زمان‌بندی فقط‌مالک پیش‌فرض خاموش با فاصلهٔ ۶/۱۲/۲۴ ساعت و بدون replay انباشته پیاده شد.
- next/last/error پایدار، task تحت supervision و inventory پیکربندی اضافه شد.
- APIهای schedule/inventory، CSRF، audit، recovery و تست due/not-due موجودند.

### وضعیت مرحلهٔ ۳۱

- پنجره‌های baseline/current هم‌اندازه و بدون هم‌پوشانی، outcome، PSI و نرخ‌های pipeline پیوندی را مقایسه می‌کنند.
- findingهای اصلاح‌شده با BH و suppression کف نمونه در runهای immutable ثبت می‌شوند.
- اجرای اپراتور/مالک و review افزایشی با CSRF و audit و UI دوزبانه پیاده شده است.
- ADR-0046 و تست‌های آمار و integrity افزوده شده‌اند.

### وضعیت مرحلهٔ ۳۰

- linkage قطعی هنگام insert دفتر با `scan_cycle_id`؛ کندل تکراری provenance چرخهٔ نخست را بازنویسی نمی‌کند.
- schema چرخه/candidate/reason و migration `legacy-unlinked` idempotent است؛ candidateهای شکست upstream و recovery چرخهٔ رهاشده بر اثر crash صریح هستند.
- API نسخه‌دار، cursor پایدار، CSV/JSON امن و audit export پیاده‌سازی شد.
- coverage، Wilson 95%، CI تقریبی expectancy و maturity تفکیک‌شده در UI/API موجود است.
- ADR-0045 فارسی/انگلیسی و تست‌های v1.1 افزوده شد.
- gate محلی: Ruff و mypy سبز؛ pytest برابر ۹۷۳ passed؛ lint/parity/display/icon/adaptive جاوااسکریپت سبز؛ standalone بازسازی شد و smoke احرازشدهٔ jsdom گذشت. Chromium در sandbox اجرا نشد، پس probe مرورگر gate الزامی CI شاخه می‌ماند.
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

## Stage 33 — Drift PSI evidence detail
- Added persisted per-bin PSI counts, proportions, bounds, and contributions.
- Added exact optional `cohort_key` filtering to drift run detail.
- Config inventory now distinguishes overlapping low-sample configurations as `not-comparable`.
- Release remains blocked until the remaining multidimensional cohort/UI scope is complete and the final gate is green.

## مرحله ۳۴ — موجودی محدود cohort
- مسیر `/api/v1/signal-intelligence/drift/cohorts` با ابعاد تکی و ترکیب‌های محدود و مجاز افزوده شد.
- cardinality حداکثر ۱۰۰ است و `truncated` صریح دارد؛ نمونه کم `not-comparable` و سرکوب می‌شود.
- یافته‌های آماری cohort و UI آن مرحله بعد است؛ انتشار همچنان مسدود است.

## مرحله ۳۵ — رابط عملیاتی Drift
- نمودار دوزبانه binهای PSI، جدول جمعیت cohort، خروجی CSV و کنترل‌های زمان‌بند افزوده شد.
- mutation زمان‌بند از headerهای احراز هویت و CSRF استفاده می‌کند و در سرور فقط Owner مجاز است.
- standalone دوباره تولید شد و gate کامل Python و UI محلی موفق بود (`983 passed`).

## مرحله ۳۶ — شواهد آماری cohort
- اجرای Drift حالت cohort مجاز می‌پذیرد و expectancy، win-rate و PSI هر cohort قابل مقایسه را ذخیره می‌کند.
- آزمون‌های global و cohort یک خانواده اصلاح BH دارند و binهای PSI قابل ممیزی هستند.
- اجرای موفق خنثی اکنون `stable` است؛ full gate محلی با `984 passed` موفق شد.

## مرحله ۳۷ — نصبگر کامل سرور از مرورگر (نسخه کاری ۱٫۹٫۰)
- مراحل قابل ادامه نصب و تاریخچه actionها در SQLite افزوده شد.
- preflight مالک/CSRF و مرز helper ثابت برای نصب، validation، restart/reload و rollback systemd/nginx اضافه شد.
- sudo دارای argv دقیق و بدون wildcard است؛ خروجی محدود/redacted و mutationها lock می‌شوند.
- Paper پیش‌فرض می‌ماند و انتشار به CI کاملاً سبز وابسته است.
