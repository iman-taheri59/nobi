# یادداشت امنیتی وابستگیها

## چرا پینها ارتقا یافتند (اکتبر ۲۰۲۶)

`pip-audit -r server/requirements.txt` روی پینهای قبلی (مربوط به دسامبر
۲۰۲۴/ژانویه ۲۰۲۵) **۲۸ شناسهٔ آسیبپذیری** گزارش میکرد:

| بسته | نسخهٔ قدیمی | نسخهٔ فعلی | نمونه شناسهها |
|---|---|---|---|
| `starlette` (ترانزیتی از FastAPI) | ۰٫۴۱٫۳ | ۱٫۱۰٫۰ | PYSEC-2026-1941/1942 (پردازش `Range` و فرم توسط کاربر ناشناس)، PYSEC-2026-161/248 |
| `cryptography` | ۴۴٫۰٫۰ | ۵۰٫۰٫۲ | GHSA-537c-gmf6-5ccf و چندین PYSEC-2026 |
| `python-dotenv` | ۱٫۰٫۱ | ۱٫۲٫۴ | PYSEC-2026-2270 (پیروی از symlink در `set_key`) |
| `pyarrow` | `>=18` (بدون پین) | ۲۳٫۰٫۱ | PYSEC-2026-113 |

`fastapi` نمیتوانست با `starlette` امنتر از ۰٫۴۲ کار کند، بنابراین
`fastapi==0.162.0` و `pydantic==2.14.1` هم هماهنگ ارتقا یافتند. تمام ۹۸۸ تست
pytest و `ruff`/`mypy` با پشتهٔ جدید پاس شدند.

## دروازهها

- CI هر push اجرا میکند: `pip-audit -r server/requirements.txt` و
  `npm audit --audit-level=high`.
- `.github/dependabot.yml` هفتگی برای pip، npm و github-actions پیشنهاد ارتقا
  میفرستد.
- نسخهٔ `starlette` عمداً بهصورت صریح پین شده تا `pip-audit` همان نسخهای را
  ببیند که در production پاسخ میدهد.
- `pyarrow` قبلاً `>=18` بود (تکرارناپذیر)؛ حالا پین است.

لطفاً پیش از ارتقای major بعدی FastAPI/Starlette، تستهای `server/tests` را کامل
اجرا کنید؛ `TestClient` API بین نسخهها تغییر میکند.
