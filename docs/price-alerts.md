# هشدار شرطی قیمت و پرمیوم

[English](price-alerts.en.md) · **فارسی**

این راهنما فاز بک‌اند ADR-0049 را پوشش می‌دهد. پنل گرافیکی bell هنوز پیاده نشده است. قواعد در سطح حساب مشترک‌اند و هیچ سفارش Paper/Live یا تغییر ریسکی ایجاد نمی‌کنند.

## نقش‌ها و دسترسی

- `GET /api/alerts/rules` و `GET /api/alerts/events` فقط برای Owner/Operator است.
- ساخت، تغییر وضعیت و حذف rule علاوه بر نقش Owner/Operator به کوکی session و هدر `X-CSRF-Token` معتبر نیاز دارد.
- Viewer برای این مسیرها 403 می‌گیرد. بدون session پاسخ 401 و بدون CSRF پاسخ 403 است.
- هیچ token، cookie یا مقدار Telegram در پاسخ API ذخیره یا نمایش داده نمی‌شود.

## قاعده و ارزیابی

بدنهٔ ساخت rule:

```json
{
  "symbol": "BTCIRT",
  "metric": "last",
  "operator": "above",
  "threshold": 100000
}
```

`metric` مقدار `last` یا `premium_pct` دارد. قاعدهٔ پرمیوم فقط با `symbol: "USDTIRT"` ساخته می‌شود و از سه بازار BTCIRT، BTCUSDT و USDTIRT استفاده می‌کند. `operator` فقط `above` یا `below` است. آستانهٔ قیمت مثبت و آستانهٔ پرمیوم در بازهٔ ۱۰۰- تا ۱۰۰ درصد است.

اولین مشاهدهٔ تازه فقط baseline می‌سازد. هشدار هنگام عبور false→true ثبت می‌شود و ماندن در همان سمت آستانه یا replay همان timestamp رویداد تازه‌ای ندارد. دادهٔ مفقود، نامعتبر، آینده یا stale قاعده را disarm می‌کند؛ اولین دادهٔ سالم پس از outage دوباره فقط baseline است.

بدون rule فعال، scheduler درخواست اضافه‌ای به صرافی نمی‌فرستد. با rule فعال، همان loader مرکزی `/market/stats` استفاده می‌شود. زمان‌های پیش‌فرض `NBX_ALERT_TICK_S=30` و `NBX_ALERT_MAX_AGE_S=90` قابل تنظیم‌اند.

## مسیرهای API

| Method و مسیر | کار |
|---|---|
| `POST /api/alerts/rules` | ساخت rule؛ پاسخ 201 |
| `GET /api/alerts/rules?limit=200` | فهرست ruleها |
| `PATCH /api/alerts/rules/{id}` با `{"enabled": false}` | غیرفعال/فعال‌کردن؛ فعال‌سازی مجدد baseline تازه می‌خواهد |
| `DELETE /api/alerts/rules/{id}` | حذف rule با نگه‌داشتن snapshot رویدادهای تاریخی |
| `GET /api/alerts/events?limit=50` | رویدادهای trigger، جدیدترین اول |
| `GET /api/alerts/events?rule_id={id}` | فیلتر رویدادهای rule هنوزموجود |

شناسهٔ ناموجود 404 و بدنهٔ نامعتبر یا نامنطبق با قرارداد 422 می‌گیرد.

## تحویل و محدودیت‌ها

trigger پیش از تماس Telegram در `journal.db` ثبت می‌شود و در backup همان DB می‌آید. فیلدهای `delivery_*` سه وضعیت را روشن می‌کنند:

- `delivery_sent: null` و `delivery_reason: "pending"`: تحویل هنوز ثبت نشده، مثلاً crash بین trigger و sender؛
- `delivery_sent: true`: Telegram پاسخ موفق داده است؛
- `delivery_sent: false`: ارسال تأیید نشده و reason محدود مثل `not-configured`، `backing-off` یا `http-400` ذخیره شده است.

body خطای Telegram، token و متن درخواست در event ذخیره نمی‌شود. شکست تحویل retry خودکار ندارد تا پیام تکراری یا retry ناشناخته ساخته نشود. هشدار عمق بازار، watchlist شخصی و UI مدیریت در این فاز وجود ندارند.
