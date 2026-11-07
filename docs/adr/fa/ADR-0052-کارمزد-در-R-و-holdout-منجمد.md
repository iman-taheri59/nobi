# ADR-0052 — کارمزد در R (ستون‌های جدید، بدون بازنویسی تاریخچه) و holdout منجمد با گزارش هفتگی

**فارسی** · [English](../en/ADR-0052-fees-in-r-and-frozen-holdout.md)

**وضعیت:** پذیرفته — کد روی `main`؛ با deploy بعد از soak فعال می‌شود؛ هیچ رفتار معاملاتی را تغییر نمی‌دهد
**تاریخ:** ۲۰۲۶-۱۱-۰۷
**مرتبط:** ADR-0008 (`fees.py` تنها جای کارمزد)، ADR-0050/0051، `docs/analysis/journal-diagnostic-2026-11-0{6,7}.md`

## زمینه

هر دو گزارش تشخیصی با این جمله تمام می‌شدند: «کارمزد در هیچ عددی نیست». با کارمزد ۰٫۲٪ هر طرف (`fees.FEE_PCT`، برچسب `default-assumption-unverified-account-tier`)، رفت‌وبرگشت ۰٫۴٪ است و در افق ۱h با stop ≈۱٫۳٪ یعنی **۰٫۳ R** — همان مرتبهٔ بزرگیِ R̄هایی که دربارهٔ آن‌ها بحث می‌کنیم. بدون کارمزد، یک بازوی «کمی مثبت» می‌تواند واقعاً منفی باشد.

مسئلهٔ دوم: گزارش ۶ نوامبر روی ۳۸ ساعت داده نوشته شد و آستانه‌های ADR-0050 از همان پنجره آمدند؛ گزارش ۷ نوامبر نشان داد جهت یافته‌ها با رژیم بازار برمی‌گردد. بدون یک خط برش ثبت‌شده، هر گزارش بعدی می‌تواند ناخواسته روی داده‌ای ارزیابی شود که آستانه‌هایش از همان داده آمده‌اند.

## تصمیم

### ۱) کارمزد در R — فقط ستون‌های جدید
- `simulate_long` سه فیلد تازه برمی‌گرداند: `return_net_pct = return_pct − fee`, `r_net = r_multiple − fee / stop%`, `fee_pct` (= `2 × FEE_PCT × 100`، فعلاً ۰٫۴). چون هر دو leg روی کل notional کارمزد می‌دهند، تقسیم خروج به TP1/TP2 مجموع را تغییر نمی‌دهد.
- در `signal_outcomes` و `signal_shadow_outcomes` سه ستون `return_net_pct`, `r_net`, `fee_pct` با migration idempotent اضافه می‌شود. **ردیف‌های تاریخی NULL می‌مانند**؛ هیچ ردیفی بازنویسی نمی‌شود. `fee_pct` روی هر ردیف ثبت می‌شود تا اگر فردا tier واقعی حساب تأیید شد، ردیف‌های قدیم با فرض قدیم خوانده شوند.
- `r_multiple`/`return_pct` ناخالص می‌مانند؛ همهٔ مصرف‌کننده‌های موجود (MSI، funnel، drift) بدون تغییر. `journal_diagnostic.py` §3 کنار R̄، `R̄net` را با n خودش چاپ می‌کند.

### ۲) holdout منجمد
- یک کلید در `journal_meta`: `holdout_cutoff = {cutoff_ts, frozen_at, note, config_hash, previous}`. `journal.freeze_holdout()` اگر مقدار موجود باشد **خطا می‌دهد** مگر `force=True`، و در آن صورت مقدار قبلی داخل رکورد جدید نگه داشته می‌شود — جابه‌جاکردن برش بعد از نگاه‌کردن به داده، مخفی نمی‌ماند.
- CLI: `scripts/journal_holdout.py show | freeze --at ISO [--note] [--force]`. برش رسمی: **`2026-11-10T07:20:00Z`** (لحظهٔ deploy بعد از soak) — همهٔ دادهٔ قبل از آن in-sample است (گزارش‌های ۶ و ۷ نوامبر و آستانه‌های ADR-0050/0051)، همهٔ دادهٔ بعد از آن held-out.
- `journal_diagnostic.py` در سرآیند می‌گوید پنجرهٔ گزارش کاملاً بعد از برش است یا ردیف in-sample دارد.

### ۳) تکرار هفتگی بدون دست
`deploy/nobi-journal-diagnostic.{service,timer}`: دوشنبه‌ها 00:30Z، با کاربر `nobi`، یک کپی سازگار (sqlite online backup) در `mktemp` می‌گیرد، گزارش ۱۴ روز اخیر را در `/srv/nobi-data/reports/journal-diagnostic-YYYY-MM-DD.txt` می‌نویسد و کپی را پاک می‌کند؛ نگه‌داری ۱۸۰ روز. فقط‌خواندنی، `Nice=10`, `IOSchedulingClass=idle`؛ هیچ دسترسی به سرویس، env یا Telegram ندارد.

## پیامدها

- اعداد «ناخالص» گزارش‌های قبلی معتبر می‌مانند و قابل مقایسه‌اند؛ از deploy به بعد هر گزارش هر دو را دارد.
- معیار برد ADR-0051 **روی `r_net`** سنجیده می‌شود، نه `r_multiple`.
- اگر tier واقعی کارمزد تأیید شود (`FEE_ACCOUNT_TIER_VERIFIED`)، فقط `fees.py` عوض می‌شود؛ ردیف‌های جدید مقدار جدید را ثبت می‌کنند و ردیف‌های قدیم مقدار قدیم را دارند.
- holdout «منجمد» است نه «مخفی»: دادهٔ held-out دیده می‌شود، ولی آستانه‌ها از روی آن تنظیم نمی‌شوند؛ هر تنظیم تازه = برش تازه با `--force` و ADR amendment.
- نصب timer جزو runbook انتشار 2.5.1 است (`cp deploy/nobi-journal-diagnostic.* /etc/systemd/system/ && systemctl enable --now nobi-journal-diagnostic.timer`) و در دورهٔ soak نصب نمی‌شود.
