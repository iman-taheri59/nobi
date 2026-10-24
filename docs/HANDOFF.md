# تحویل کار — تکمیل nobitrader

[English](HANDOFF.en.md) · **فارسی**

**آخرین به‌روزرسانی:** ۲۰۲۶-۱۰-۲۵ — مرحله ۳ (فاز A، کد کامل)
**مبنای کار:** `main` در `be3d4fe` + تغییرات نسخه ۱٫۲٫۰
**هدف:** ADR-0027؛ ابتدا ۱٫۲ پایدار، سپس ۲٫۰ معاملهٔ واقعی.

## وضعیت فعلی

- [x] دامنه و معیار پذیرش در ADR-0027 فارسی/انگلیسی.
- [x] backup/restore سازگار SQLite با checksum و سه آزمون.
- [x] تاریخچهٔ UDF صفحه‌بندی‌شده، resumable، deduplicated و gap-aware با چهار آزمون.
- [x] نسخه ۱٫۲٫۰ در backend، package، dashboard و آرتیفکت موبایل.
- [x] Windows CI مسدودکننده.
- [x] تمام gateهای قابل اجرای محلی: ۸۹۷ pytest، ruff، mypy، ESLint، dashboard lint، Python/JS parity، intrabar، display، adaptive و icons.
- [x] smoke API با mock exchange: health، signals، orders، backup و restore.
- [ ] probe مرورگری محلی: Chromium sandbox این محیط بسته شد؛ همان job در CI با `playwright install --with-deps` تعریف شده است.
- [ ] soak واقعی ۲۴ ساعته — نیازمند محیط متصل به نوبیتکس.
- [ ] فاز B: signer، live execution، reconciliation، audit و بازبینی امنیتی مستقل.

## تصمیم‌های قفل‌شده

1. نسخه ۱٫۲ محصول پایدار تحلیل/Paper است؛ live در آن خاموش می‌ماند.
2. gateهای adaptive تا شاهد کافی `shadow` و `applied:false` می‌مانند.
3. تست‌ها به شبکهٔ نوبیتکس وابسته نمی‌شوند.
4. secret در repository، مرورگر، log یا backup بدون رمز قرار نمی‌گیرد.
5. نبود credential یا soak با دادهٔ واقعی با mock یا ادعای ساختگی جایگزین نمی‌شود.

## فایل‌های اصلی این تحویل

- `docs/adr/fa/ADR-0027-تکمیل-مرحله‌ای-محصول.md` و نسخه انگلیسی
- `scripts/state_backup.py`
- `scripts/fetch_history.py`
- `server/tests/test_state_backup.py`
- `server/tests/test_fetch_history.py`
- `.github/workflows/ci.yml`

## نتایج تأیید

```text
ruff: passed
mypy: success, 93 source files
pytest: 897 passed, 1 dependency deprecation warning
ESLint/dashboard lint: 0 errors
broker/intrabar/preflight parity: passed
display: 306 checks passed
adaptive card/icons: passed
API smoke: health + signals + orders + backup + restore passed
```

## ریسک‌ها/مسدودکننده‌ها

- soak واقعی و contract test API خصوصی بدون محیط و credential کاربر ممکن نیست.
- probe مرورگر در sandbox حاضر به‌علت بسته‌شدن Chromium اجرا نشد؛ CI بسته‌های سیستمی را نصب می‌کند.
- فعال‌سازی live پیش از signer، reconciliation، audit و بازبینی امنیتی مستقل ممنوع است.

## قدم بعدی دقیق

1. push شاخه و اجرای CI؛
2. استقرار ۱٫۲ در محیط کاربر و soak بیست‌وچهارساعته؛
3. ثبت نتیجه soak در ADR/HANDOFF؛
4. فقط پس از پذیرش فاز A، آغاز ADR اجرای واقعی و contract test با credential محدود.
