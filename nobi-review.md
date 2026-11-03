# گزارش بررسی پروژه nobiTrader / nobi

[English](nobi-review.en.md)

- تاریخ بررسی: ۲۰۲۶-۱۱-۰۲
- نسخهٔ پروژه: `2.1.8`
- commit: `21eee89`
- دامنه: بررسی کد، تست‌های موجود، اجرای تست‌های آفلاین، و بازتولید چند نقص بدون اتصال به صرافی و بدون سفارش واقعی.
- وضعیت تغییرات کد: هیچ فایل trackedـی در مخزن تغییر نکرد؛ `git status --short` خالی است.

## خلاصهٔ اجرایی

پروژه از نظر ساختار تست، مستندات ADR، تفکیک واحدها، CSRF/auth، و «سه‌-state» بودن داده‌ها قابل‌توجه است؛ اما چند نقص مهم در مرزهای اجرای معامله دیده می‌شود که اگر مسیر زنده یا اجرای خودکار فعال شود، می‌تواند به تصمیم مالی نادرست منجر شود. مهم‌ترین‌ها:

1. پیش‌بررسی سفارش زنده از equity/cash/positions حساب کاغذی استفاده می‌کند، نه موجودی واقعی صرافی.
2. توقف زیان روزانه عملاً اعمال نمی‌شود؛ حتی پس از زیان ۵٪، موتور ریسک همچنان سفارش جدید را باز می‌کند.
3. اجرای خودکار می‌تواند سیگنال تایم‌فریم دیگر یا بسیار کهنه را مبنای سفارش کند.
4. حسابداری `sell`/short در هر دو موتور کاغذی ناسازگار است؛ در JS حتی علامت سود/زیان بسته‌شدن برعکس می‌شود.
5. دروازهٔ ارتقا به live تلاش‌های rejected را هم «نمونه» می‌شمارد و وضعیت `accepted` را با fill واقعی اشتباه می‌گیرد.

این‌ها در ادامه با اولویت، شاهد کد، و بازتولید آمده‌اند.

---

## یافته‌ها

### 1) بحرانی، مشروط به فعال‌سازی live: پیش‌بررسی زنده به حساب کاغذی متکی است

**محل‌ها:**

- `server/main.py:3290-3292` — `_live_preflight` صرفاً `risk_whatif` را صدا می‌زند.
- `server/main.py:3183` و `server/main.py:3244-3255` — `risk_whatif` از `br.equity`، `br.cash` و `br.positions` استفاده می‌کند؛ `br` همان paper broker است.
- `server/live_api.py:160-196` — مسیر `/api/live/place` پس از preflight و policy، سفارش واقعی را ارسال می‌کند.

**اثر:** اگر `NBX_LIVE_MODE=live` شود، اندازه و مجازبودن سفارش بر اساس حساب شبیه‌سازی‌شده سنجیده می‌شود، نه wallet واقعی نوبیتکس. در نتیجه ممکن است سفارشی از نظر کاغذی «مجاز» باشد ولی در صرافی به‌دلیل موجودی ناکافی رد شود، یا بدتر از آن، سقف ریسک واقعی حساب رعایت نشده باشد. برای `sell` نیز بررسی موجودی دارایی پایه در این مسیر دیده نمی‌شود.

**پیشنهاد:** قبل از هر سفارش زنده، یک snapshot موجودی/ریسک مستقل از `/v2/wallets` و سفارش‌های باز صرافی ساخته شود و preflight زنده فقط از همان تغذیه شود. حساب کاغذی نباید منبع حقیقت مسیر live باشد.

---

### 2) بالا: توقف زیان روزانه فقط هشدار است، نه دروازه

**محل‌ها:**

- `server/risk_engine.py:291-293` — اگر `daily_pnl <= -3` باشد فقط `warnings.append("daily-loss-halt")` اجرا می‌شود.
- `server/risk_engine.py:347-357` — `close_trade` و `triggers` وجود دارند، اما در مسیرهای اجرای کاغذی/زنده فراخوانی نمی‌شوند.
- `server/paper_trading.py:597-627` و `server/paper_trading.py:629-687` — بستن.position یا TP1، `risk.close_trade` را صدا نمی‌زند.
- `nbx-core.js:715` و جستجوی `dailyPnl` — در موتور مرورگر نیز مقدار روزانه به‌روزرسانی نمی‌شود.

**بازتولید:**

```text
daily_pnl = -5.0
halted = false
plan.action = "open"
plan.blocked = false
plan.size_quote = 25000.0
warnings = ["daily-loss-halt", "single-asset-cap-resized"]
```

**اثر:** سیاست «توقف پس از زیان روزانه» که در ADR-0008 آمده، در عمل اجرا نمی‌شود. حتی اگر دستی هم مقداردهی شود، موتور فقط هشدار می‌دهد و جلوی سفارش را نمی‌گیرد.

**پیشنهاد:** `daily-loss-halt` باید به یک gate واقعی در `preflight` تبدیل شود؛ realized/unrealized روزانه از رویدادهای بسته‌شدن و mark-to-market تغذیه شود؛ و آزمون رگرسیون برای «زیان ۳٪ ⇒ عدم صدور سفارش جدید» اضافه شود.

---

### 3) بالا: اجرای خودکار می‌تواند سیگنال کهنه یا تایم‌فریم اشتباه را معامله کند

**محل‌ها:**

- `server/main.py:1812-1823` — `_signal_lookup` اگر نماد در تایم‌فریم درخواستی نباشد، به `_cached["signals"]` یعنی آخرین تایم‌فریم اسکن‌شده fallback می‌کند.
- `server/main.py:4264-4272` — `auto_tick` هم pool و هم lookup را با همین fallback می‌سازد.
- `server/main.py:4359-4382` — حلقهٔ auto همیشه `auto_tick()` را با تایم‌فریم پیش‌فرض `15m` صدا می‌زند.
- در مقابل، مسیر queue از `server/queue_policy.py` و متادیتای کندل بسته/انقضا استفاده می‌کند؛ auto از آن محافظت عبور نمی‌کند.

**بازتولید:** با یک snapshot مربوط به `D` که ۵۰ روز عمر دارد، `auto_tick("15m")` نتیجه داد:

```text
considered=1, placed=1
submitted=("TESTUSDT", "buy", 250.0, 100.0, 98.0, 102.4, 104.0), source="AUTO"
```

**اثر:** اجرای خودکار می‌تواند بر اساس ATR/جهت/قیمت تایم‌فریم دیگر یا دادهٔ بسیار قدیمی سفارش کاغذی بدهد. این هم اعتبار بک‌تست/شواهد را خراب می‌کند و هم اگر بعداً به live وصل شود خطرناک است.

**پیشنهاد:** auto باید مانند queue فقط از cache دقیق همان تایم‌فریم، کندل بسته، `signal_key`، و انقضای مبتنی بر زمان سرور استفاده کند. fallback به آخرین تایم‌فریم برای اجرای سفارش باید حذف شود.

---

### 4) بالا: حسابداری sell/short در موتورهای کاغذی ناسازگار است

**محل‌ها:**

- `server/paper_trading.py:494` — برای sell، `total_cost = notional_usd - fee_usd` و سپس همان مقدار از cash کم می‌شود.
- `server/paper_trading.py:586` — `self.cash -= total_cost` برای هر دو سمت.
- `server/paper_trading.py:611-616` — بستن short از نظر فرمول PnL جهت درست دارد، اما بازگشت principal و cash با مدل دارایی/بدهی همخوان نیست.
- `server/paper_trading.py:657-660` — TP1 برای short همچنان فرمول لانگ `(notional_tp1 - cost_basis)` را دارد.
- `server/paper_trading.py:689-698` — mark-to-market همهٔ پوزیشن‌های باز را به‌صورت دارایی مثبت اضافه می‌کند.
- `nbx-core.js:886` و `nbx-core.js:898-902` — در مرورگر، sell مانند buy از cash کم می‌شود و `closePosition` برای هر دو سمت `(outQuote - size_quote)` را حساب می‌کند.

**بازتولید JS:** فروش ۱ واحد در ۱۰۰، بستن در ۹۰، کارمزد ۰٫۲٪ هر پا:

```text
accepted = true
realized_pnl = -10.18
expected_net_pnl ≈ +9.62
```

**بازتولید Python:** در همان سناریو، equity هنگام mark در قیمت ۹۰ به‌جای حدود `100009.8`، مقدار `99990.2` می‌شود؛ یعنی جهت short در ارزش‌گذاری باز برعکس است.

**اثر:** آمار عملکرد، equity curve، drawdown، profit factor و تصمیم‌های بعدی برای سمت sell قابل اعتماد نیست. اگر پروژه فقط لانگ/spot است، باید sellِ بدون موجودی صریحاً رد شود؛ اگر short مدنظر است، باید مدل بدهی/وثیقه/کارمزد جداگانه داشته باشد.

**پیشنهاد:** یا `sell` بدون دارایی پایه رد شود، یا یک مدل حسابداری صریح برای short/spot اضافه شود. آزمون‌های فعلی بیشتر هندسهٔ stop/TP را تست می‌کنند، نه چرخهٔ کامل cash/equity/PnL را.

---

### 5) بالا، مشروط به live: شواهد ارتقا و وضعیت accepted قابل اتکا نیستند

**محل‌ها:**

- `server/live_execution.py:160-186` — `promotion_evidence` همهٔ ردیف‌های `live_execution` را می‌شمارد؛ فقط `unknown` و `cancel-unknown` را مبهم می‌داند.
- `server/live_execution.py:229-235` — اگر پاسخ صرافی `status:"ok"` باشد، حالت محلی `accepted` می‌شود، حتی اگر خود order وضعیت `Rejected` یا `matchedAmount:0` داشته باشد.
- `server/live_api.py:172-177` — `/live/place` به همین `ready` متکی است.

**بازتولید:** ۳۰ رکورد `rejected` در ۵ روز:

```text
ready = true
samples = 30
days = 5
states = {"rejected": 30}
```

و برای پاسخ صرافی با `order.status="Rejected"` و `matchedAmount="0"`:

```text
local_state = "accepted"
```

**اثر:** دروازهٔ ارتقا می‌تواند با تلاش‌های ناموفق هم سبز شود. همچنین `accepted` نباید به‌معنای fill یا حتی پذیرش نهایی order تلقی شود.

**پیشنهاد:** شواهد ارتقا فقط باید dry-runهای معتبر و ترجیحاً سناریوهای متنوع بازار را بشمارند، نه rejectedها را. وضعیت order باید از فیلدهای خود order و reconciliation با صرافی استخراج شود: `accepted`, `rejected`, `partial`, `filled`, `canceled`, `unknown`.

---

### 6) متوسط: رتبه‌بندی نقدشوندگی تومان و دلار را با هم مخلوط می‌کند

**محل‌ها:**

- `server/main.py:1081-1082` — universe بر اساس `vol24` خام مرتب می‌شود.
- `server/main.py:1194-1197` — امتیاز نقدشوندگی از `log10(vol24 + 1)` ساخته می‌شود.
- در مقابل، `server/market_overview.py:273-280` صریحاً volume را به USD نرمال می‌کند.

**بازتولید:** دو بازار با گردش دلاری یکسان `1,000,000 USD` و نرخ `100,000 تومان/دلار`:

```text
BTCUSDT: raw_vol24=1,000,000       liquidity_score=74
BTCIRT:  raw_vol24=100,000,000,000 liquidity_score=94
```

**اثر:** بازارهای IRT به‌طور مصنوعی نقدشونده‌تر/برتر رتبه‌بندی می‌شوند و universe top-60 نیز می‌تواند جهت‌دار شود.

**پیشنهاد:** پیش از sort و liquidity score، `vol24` با نرخ معتبر USDTIRT به USD تبدیل شود؛ اگر نرخ نیست، امتیاز/رتبه به‌جاسازی نشود و وضعیت unavailable اعلام شود.

---

### 7) متوسط: FA fallback همچنان امتیاز می‌گیرد و cache ورودی depth را نادیده می‌گیرد

**محل‌ها:**

- `server/fa_engine.py:189-209` — کلید cache فقط `symbol` و bucket پریمیوم است؛ `nobil_depth_score` در کلید نیست.
- `server/fa_engine.py:351-353` — depth روی امتیاز نهایی اثر دارد.
- `server/fa_engine.py:363-370` — fallback/none برچسب می‌خورد، ولی همچنان `FAResult.score` برمی‌گرداند.
- `server/main.py:1235-1250` — rescan همان score را در composite می‌گذارد، حتی اگر `fresh=false` یا profile برابر fallback/none باشد.

**بازتولید:**

```text
depth=0   -> score=65.0
depth=100 -> score=65.0, cached=true
profile=fallback, fresh=false
```

در حالت circuit-open نیز BTC امتیاز fallback حدود `75.0` می‌گیرد.

**اثر:** امتیاز بنیادی می‌تواند از دادهٔ اندازه‌گیری‌نشده یا cache ناسازگار با depth فعلی ساخته شود و همچنان در تصمیم ترکیبی وزن بگیرد.

**پیشنهاد:** اگر FA واقعاً fresh/measured نیست، composite باید طبق همان.contract به `ta+liq` renormalize شود، نه اینکه score fallback را مانند دادهٔ معتبر مصرف کند. کلید cache باید همهٔ ورودی‌های مؤثر بر score را پوشش دهد یا score از cache جدا و بخش وابسته به depth دوباره محاسبه شود.

---

### 8) متوسط: متریک‌های ژورنال در تایم‌فریم بالا و خروج_partial سازگار نیستند

**محل‌ها:**

- `server/journal.py:64-68` و `server/journal.py:97-99` — H1 قرار است یک‌ساعته باشد، اما `bars_for` کف یک کندل دارد.
- `server/journal.py:490-503` — `r_multiple` خروج پله‌ای را ترکیب می‌کند، ولی `return_pct` فقط `(exit_price / entry - 1)` است.

**بازتولید افق‌ها:**

```text
15m: H1 = 3600s
1h:  H1 = 3600s
4h:  H1 = 14400s
D:   H1 = 86400s
```

**بازتولید خروج partial:**

```text
r_multiple = 0.65
stored_return_pct = 0.2
leg_weighted_gross_return_pct ≈ 1.3
```

**اثر:** H1 در تایم‌فریم‌های 4h/D دیگر «یک ساعت» نیست و با H2/Daily هم‌پوشان می‌شود. همچنین `return_pct` پس از TP1 با بازده واقعی سبدِ خروج‌پله‌ای همخوان نیست و می‌تواند کالیبراسیون/IC را گمراه کند.

**پیشنهاد:** یا افق‌ها بر اساس زمان واقعی و دادهٔ تایم‌فریم پایین‌تر محاسبه شوند، یا برچسب/مستندات صریحاً بگویند H1 در تایم‌فریم بالا یعنی «نزدیک‌ترین کندل». برای بازده، یا weighted exit return ذخیره شود یا نام فیلد به `final_leg_return_pct` تغییر کند.

---

### 9) پایین: آسیب‌پذیری‌های moderate در زنجیرهٔ ابزار توسعهٔ npm

**مشاهده:** `npm audit --audit-level=high` با exit 0 گذشت، اما ۵ یافتهٔ moderate گزارش شد:

```text
minimatch < 11.0.1
GHSA-g3hv-75wg-m249
از مسیر eslint / @eslint/config-array / @eslint-community/eslint-utils / @eslint/js
```

**اثر:** این‌ها در devDependencies هستند و لزوماً در runtime داشبورد قرار نمی‌گیرند، ولی می‌توانند در CI/ابزار پردازش glob اثر داشته باشند.

**پیشنهاد:** به‌جای `npm audit fix --force` که ممکن است ESLint را به نسخهٔ ناسازگار downgrade کند، زنجیرهٔ ESLint/minimatch به نسخهٔ اصلاح‌شدهٔ سازگار ارتقا یابد و تست‌های lint/CI دوباره اجرا شوند.

---

## نکات مثبتی که بررسی شدند و نقص تلقی نشدند

- مرز واحدها در `server/units.py` و تست‌های tick/parity جدی گرفته شده است.
- مسیر queue از کندل بسته، زمان سرور، انقضا و idempotency استفاده می‌کند.
- auth/CSRF و مسیرهای owner/operator در تست‌ها پوشش خوبی دارند.
- backtest هزینه/لغزش را مدل می‌کند و در صورت نبود هزینه، certification را رد می‌کند.
- بسیاری از وضعیت‌های UI/API بین live/cache/synthetic/unavailable تفکیک شده‌اند.
- جهت `srcCurrency/dstCurrency` در سفارش‌های Nobitex بررسی شد؛ طبق مستندات رسمی، src دارایی پایه و dst ارز مظنه است و برای buy/sell نباید جابه‌جا شود. بنابراین این مورد به‌عنوان باگ گزارش نشد. مرجع: [nobitex/docs-api](https://github.com/nobitex/docs-api/blob/master/source/includes/_market_trade.md).

---

## نتیجهٔ اجراها

| بررسی | نتیجه |
|---|---|
| `pytest server/tests` | `1663 passed, 1 warning` |
| `ruff check server scripts` | پاس |
| `mypy` | پاس، ۱۵۵ فایل |
| `npm run lint` و `lint:dashboard` | پاس |
| ۲۳ اسکریپت `npm run test:*` | همگی پاس |
| `check_preflight_parity.py` | پاس، ۲۸ حالت × ۱۰ gate |
| `npm audit --audit-level=high` | exit 0، ولی ۵ moderate dev finding |
| ساخت CSS و standalone mobile | پاس؛ `nobitex-mobile.html` تفاوت tracked نداشت |
| `probe_live.py --selftest` | به‌دلیل نبود Chromium/Playwright browser اجرا نشد |
| `pip-audit` کامل روی requirements اصلی | در مفسر هدف تکمیل نشد؛ محدودیت محیط پایین را ببینید |

### محدودیت محیط تست

مفسر هدف پروژه Python 3.12 است، اما محیط موجود Python 3.11.2 بود. تلاش برای نصب ۳٫۱۲ از مسیرهای مجاز شبکه ناموفق شد. برای اجرای تست‌های backend، فقط `numpy==2.5.4` به `numpy==2.4.6` تغییر داده شد و بقیهٔ pinهای اصلی نصب شدند. بنابراین نتیجهٔ pytest/ruff/mypy معتبر است، اما دقیقاً روی ماتریس Python 3.12 + numpy 2.5.4 اجرا نشده است.

همچنین `pip-audit` کامل به‌دلیل نیاز requirements اصلی به Python 3.12 در این محیط تکمیل نشد. بررسی مستقیم metadata نسخه‌های pin‌شده از PyPI برای ۱۳ بستهٔ اصلی، advisory شناخته‌شده‌ای نشان نداد؛ این جایگزین audit کامل وابستگی‌های انتقالی نیست.

---

## ترتیب پیشنهادی اصلاح

1. **قبل از هر فعال‌سازی live:** جداسازی preflight زنده از حساب کاغذی، اصلاح promotion evidence، و تفکیک accepted/filled/rejected.
2. **قبل از اتکا به auto:** حذف fallback تایم‌فریم و اعمال همان queue freshness/expiry برای auto.
3. **قبل از اتکا به آمار عملکرد:** اصلاح حسابداری sell/short و daily PnL/halt.
4. **برای کیفیت سیگنال:** نرمال‌سازی نقدشوندگی به USD و اصلاح رفتار FA fallback/cache.
5. **برای گزارش‌گری:** اصلاح H1 و return_pct در ژورنال.
6. **نگهداری:** ارتقای کنترل‌شدهٔ زنجیرهٔ ESLint/minimatch و تکمیل pip-audit در Python 3.12.

---

## پیوست بازتولید

اسکریپت بازتولید خارج از مخزن نگهداری شده است:

```text
/home/user/nobi-review/reproduce_findings.py
/home/user/nobi-review/reproductions.log
```

اجرا از ریشهٔ مخزن:

```bash
PYTHONPATH=server .venv/bin/python /home/user/nobi-review/reproduce_findings.py
```

این اسکریپت هیچ سفارش واقعی ارسال نمی‌کند و state را فقط زیر `/home/user/nobi-review/repro-state` می‌نویسد.
