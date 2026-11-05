# بررسی جامع Nobi / Nobitex Market Analyzer
# Comprehensive Technical & Quantitative Audit — Persian and English

> **Remediation update / به‌روزرسانی اصلاحات — 2026-11-04:** The original findings below are the historical baseline. The closing addendum records the remediated tree and final tests; it does not establish profitability or live readiness. / یافته‌های اصلی شرح baseline تاریخی‌اند؛ الحاقیهٔ پایانی وضعیت اصلاح‌شده و تست‌های نهایی را ثبت می‌کند و اثبات سودآوری یا آمادگی live نیست.

**مخزن / Repository:** `https://github.com/iman-taheri59/nobi`  
**نسخه / Version:** `2.2.2`  
**کامیت بررسی‌شده / Audited commit:** `2b5ea130f53959d357c5c8a664bfb1a52bd781e0`  
**تاریخ بررسی / Audit date:** 2026-11-03  
**وضعیت سورس / Source status:** هیچ فایل پروژه ویرایش نشد؛ working tree تمیز ماند.

> این سند دو بخش مستقل فارسی و انگلیسی دارد. منظور از «پیاده‌شده»، وجود مسیر واقعی در کد است؛ منظور از «اثبات‌شده»، وجود شاهد معتبر روی دادهٔ واقعی و خارج از نمونه است. این دو در تمام گزارش از هم جدا شده‌اند.

---

> **یادداشت بایگانی (۲۰۲۶-۱۱-۰۵):** این سند عکس لحظه‌ای بازبینی روی commit `2b5ea13` (2.2.2) است و بازنویسی نمی‌شود. وضعیت فعلی هر یافته در [`README.md`](README.md) همین پوشه است.

# بخش اول — گزارش فارسی

## ۱. جمع‌بندی مدیریتی

Nobi از نظر مهندسی نرم‌افزار، پروژه‌ای نسبتاً بالغ و آزمون‌پذیر است: مسیر داده و منشأ آن تا حد زیادی شفاف است، کندل بسته استفاده می‌شود، cache ساختگی به دادهٔ واقعی تبدیل نمی‌شود، دروازه‌های پیش‌از-اجرای Python و JavaScript تست تطبیق دارند، و بک‌تست Walk-Forward هزینه و لغزش را مدل می‌کند. اجرای کامل تست‌ها نیز موفق بود: **۱٬۷۴۲ تست پایتون پاس شدند** و gateهای lint/type/parity نیز سبز شدند.

اما از نظر اعتبار کمی و مالی، خروجی فعلی را **هنوز نباید سیگنال معاملاتی اثبات‌شده یا آمادهٔ ارتقای Live دانست**. دلیل اصلی، چند خطای اثرگذار در فرمول‌ها و چند ناسازگاری در معنای امتیاز و نتیجه است:

1. **حجم خام IRT و USDT هم‌واحد نیست**، ولی هم برای انتخاب ۶۰ بازار اول و هم برای امتیاز نقدشوندگی مستقیم مقایسه می‌شود. این کار رتبه‌بندی و نمونهٔ تحلیل را به IRT سوق می‌دهد.
2. **امتیاز TVL تقریباً از ۲٫۱۵ هزار دلار اشباع می‌شود**؛ در نتیجه TVL پنج هزار، پنج میلیون و یک میلیارد دلار همگی امتیاز on-chain برابر ۱۰۰ می‌گیرند، درحالی‌که veto مربوط به TVL کم تا ده میلیون دلار فعال است.
3. **مسیرهای Backtest، Journal و Paper یک معاملهٔ یکسان را یکسان مدل نمی‌کنند**: TP2 در Journal و Paper اجرا نمی‌شود، هدف‌های پیش‌فرض بک‌تست با Live فرق دارند، trailing یکسان نیست و تعریف `return_pct` با R کامل معامله تفاوت دارد.
4. **IC پایه و IC زنده از cohort، افق و label یکسان استفاده نمی‌کنند**؛ بنابراین MSI می‌تواند اختلافِ تعریف متریک را با drift واقعی اشتباه بگیرد.
5. **EMA200 از ۶۰ کندل امتیاز می‌گیرد، RSI در بازار صعودیِ بدون زیان در Python برابر NaN می‌شود و parity عددی کامل TA بین Python و JavaScript وجود ندارد.**
6. آستانهٔ `LONG/NEUTRAL` روی **TA score** اعمال می‌شود، نه composite نهایی. بنابراین ردیفی با composite بالا می‌تواند NEUTRAL و ردیفی با composite پایین می‌تواند LONG باشد. اگر این تصمیم آگاهانه است، نام‌ها و API باید این تفکیک را واضح کنند.
7. شواهد موجود، سودآوری واقعی را ثابت نمی‌کنند: ماتریس adaptive مورخ ۲۰۲۶-۱۰-۲۲ هر ۱۲ سلول را `inconclusive` و روی reference data گزارش کرده است؛ سند اعتبارسنجی پنج‌روزه نیز scan loop خاموش و `runs=0` دارد.

**داوری نهایی:** زیرساخت مناسبی برای ساخت یک سیستم پژوهشی معتبر وجود دارد، ولی پیش از اعتماد به رتبه‌بندی یا ارتقای استراتژی، ابتدا باید صحت واحدها، مدل نتیجه، معنای تصمیم نهایی و روش آماری ارزیابی اصلاح شود. سبزبودن تست‌های نرم‌افزاری، اثبات alpha یا سودآوری نیست.

---

## ۲. دامنه، روش و محدودیت شواهد

### ۲.۱ کارهای انجام‌شده

- خواندن پیاده‌سازی TA، FA، composite، veto، Universe، candle cache، freshness، backtest، journal، Paper broker، risk، portfolio، benchmark، market overview، drift و Signal Intelligence.
- تطبیق کد با کاتالوگ‌ها و ADRها؛ قابلیت‌های صرفاً مستندشده به‌عنوان پیاده‌شده حساب نشدند.
- اجرای مستقل تست‌ها و probeهای عددی روی fixtureهای کنترل‌شده؛ هیچ درخواست معامله یا سفارش واقعی انجام نشد.
- بررسی وابستگی‌های Python و npm در حد ابزارهای audit موجود.

| بررسی | نتیجه |
|---|---|
| `pytest -q server/tests` | **1742 passed, 1 warning in 91.84s** |
| `ruff check server scripts` | Pass |
| `mypy` | Pass؛ 160 فایل بدون issue |
| `npm run lint` و `lint:dashboard` | Pass |
| `npm run test:js` و `test:intrabar` | Pass |
| Preflight parity | توافق 28 سناریو × 10 gate |
| Tick/intrabar parity | توافق 9 vector |
| `pip-audit -r server/requirements.txt` | آسیب‌پذیری شناخته‌شده‌ای گزارش نشد |
| `npm audit --audit-level=high` | صفر high/critical؛ پنج مورد moderate در زنجیرهٔ ابزار توسعه |

### ۲.۲ چه چیزی اثبات نشده است

- روی این checkout، اسکن زندهٔ Nobitex و آزمون end-to-end با فید واقعی انجام نشد؛ بنابراین این گزارش کیفیت دادهٔ فعلی صرافی یا deployment واقعی را تأیید نمی‌کند.
- تست کامل مرورگر/Layout با Playwright اجرا نشد؛ تست‌های JS/jsdom و parity اجرا شدند.
- هیچ نمونهٔ معتبر و کافی از سود خالص Live یا Paper در اختیار نبود. نتایج fixture فقط برای بازتولید فرمول و باگ است، نه ارزیابی بازده.
- parity کامل عددی اندیکاتورها و scoreهای TA بین Python و JavaScript در تست‌های پروژه ثابت نشده است؛ probe مستقل در یک حالت مرزی اختلاف واقعی نشان داد.

---

## ۳. فرآیند واقعی تصمیم‌گیری در کد

مسیر اصلی در وضعیت فعلی چنین است:

1. **Universe:** در `server/main.py::_load_universe_locked` دادهٔ `/market/stats` خوانده و Rial به Toman تبدیل می‌شود. `volumeDst/valueDst` به‌صورت خام در `vol24` ذخیره، سپس همهٔ بازارها بر اساس همان مقدار مرتب و ۶۰ مورد اول نگه داشته می‌شوند.
2. **کندل:** `server/nobitex_client.py` دادهٔ UDF را مرتب و در cache با TTL ذخیره می‌کند؛ cache ساختگی پذیرفته نمی‌شود. در scan، سیاست `queue_policy.closed_rows` کندل در حال شکل‌گیری را حذف می‌کند.
3. **TA:** `server/ta_engine.py` از OHLCV چهار component می‌سازد، regime را با ADX تعیین و TA score را محاسبه می‌کند.
4. **Liquidity proxy:** در `main.rescan` مقدار خام `vol24` به فرمول لگاریتمی داده می‌شود.
5. **FA:** `server/fa_engine.py` دادهٔ CoinGecko، DefiLlama، Fear & Greed و premium را به پنج component و چند veto تبدیل می‌کند.
6. **Composite:** در `main.composite_score` امتیاز TA، FA و liquidity ترکیب می‌شود. در `apply_vetoes` سقف و تغییر direction اعمال می‌شود.
7. **Ranking/gates:** ردیف‌ها بر اساس composite مرتب می‌شوند. gateهای spread/depth/staleness عمداً در حالت shadow هستند و ردیف را حذف نمی‌کنند.
8. **Journal/Paper:** سیگنال‌ها با timestamp بستن کندل ثبت می‌شوند. مدل Journal جدا از Paper broker نتیجه را تخمین می‌زند؛ ورود واقعی Paper از preflight و risk عبور می‌کند.
9. **ارزیابی:** Backtest فقط TA را Walk-Forward می‌کند؛ Journal نتایج H1/H2/H3، IC، calibration و family grouping می‌سازد؛ Signal Intelligence و Drift آمار توصیفی/آزمون‌های drift ارائه می‌کنند.

نکتهٔ مهم: `EXIT` در این پروژه لزوماً دستور بستن پوزیشن یا سیگنال Short نیست. Backtest فقط LONG باز می‌کند و Paper نیز خروج را عمدتاً از stop/target/trailing/time-stop انجام می‌دهد، نه صرفاً از تغییر label سیگنال به EXIT.

---

## ۴. تحلیل تکنیکال: فرمول‌های واقعی و ارزیابی آن‌ها

فایل اصلی: `server/ta_engine.py`؛ معادل مرورگر: `nbx-core.js`.

### ۴.۱ اندیکاتورهای پیاده‌شده

| اندیکاتور | پیاده‌سازی فعلی |
|---|---|
| EMA | دوره‌های 9، 21، 50 و 200 با `ewm(span=n, adjust=False)` |
| RSI(14) | EWM با `alpha=1/14` روی gain/loss؛ نزدیک به Wilder RMA، ولی seed و حالت صفر-loss اهمیت دارند |
| MACD | EMA12 − EMA26، signal=EMA9، histogram=MACD−signal |
| ATR(14) | EWM با `alpha=1/14` روی True Range |
| ADX/DMI(14) | +DI/−DI، DX و EWM روی DX |
| Bollinger | SMA20 و انحراف معیار نمونه‌ای `ddof=1`، باندهای ±2σ و %B |
| Relative volume | میانگین ۲۹ کندل قبلی و مقایسه با حجم کندل فعلی |
| Breakout ساده | بستن قیمت بالاتر از بیشترین close در ۱۹ کندل قبلی |

فرمول‌های پایه:

- `TR = max(H−L, |H−C_prev|, |L−C_prev|)`
- `+DI = 100 × RMA(+DM)/ATR` و `−DI = 100 × RMA(−DM)/ATR`
- `DX = 100 × |+DI−(−DI)| / (+DI+(−DI))` و `ADX = RMA14(DX)`
- `MID = SMA20(C)`، `σ = sample_std20(C)`، `%B = (C−(MID−2σ))/(4σ)`
- `stopDistance = 2 × ATR14`

### ۴.۲ چهار component و امتیاز نهایی

**Trend، از صفر شروع می‌شود:**

- `+20` اگر `Close > EMA21`
- `+20` اگر `EMA9 > EMA21`
- `+25` اگر `EMA21 > EMA50`
- `+15` اگر `Close > EMA200`
- `+20` اگر `ADX > 22` و `+DI > −DI`
- خروجی به بازهٔ 0 تا 100 بریده می‌شود.

**Momentum، از ۵۰ شروع می‌شود:**

- RSI: اگر `55 < RSI < 72` مقدار `+20`؛ اگر `RSI ≥ 72` مقدار `+5`؛ اگر `RSI < 45` مقدار `−15`.
- MACD histogram: اگر نسبت به کندل قبل بزرگ‌تر و مثبت باشد `+25`؛ اگر کوچک‌تر و منفی باشد `−10`.

**Volume، از ۵۰ شروع می‌شود:**

- اگر `V_now > 1.4 × V_avg` مقدار `+30`؛ اگر `V_now < 0.6 × V_avg` مقدار `−15`.
- اگر close صعودی و `V_now > V_avg` باشد `+10` دیگر اضافه می‌شود.

**Structure، از ۵۰ شروع می‌شود:**

- `%B > 0.70` مقدار `+25` و `%B < 0.25` مقدار `−15`.
- بستن بالاتر از بیشترین close نوزده کندل قبلی `+10`.

امتیاز نهایی:

```text
raw_TA = 0.40×Trend + 0.25×Momentum + 0.15×Volume + 0.20×Structure
TA = raw_TA × (1.00 if ADX > 25 else 0.85)
TA = round(clip(TA, 0, 100), 1)
LONG    if TA ≥ 68
NEUTRAL if 55 ≤ TA < 68
EXIT    if TA < 55
```

### ۴.۳ نظر فنی دربارهٔ فرمول‌ها

**نقاط قوت**

- فرمول‌ها ساده، قابل توضیح و deterministic هستند؛ این برای audit و replay بهتر از یک black box ناشناخته است.
- Volume نسبی درون یک نماد است و برخلاف liquidity مقطعی، به واحد مطلق قیمت وابسته نیست.
- stop بر اساس ATR با نوسان سازگار است و منطق trend/momentum/volume/structure قابل تفکیک است.
- نسخهٔ vectorised و scalar در Python تست برابری دارند و محاسبات فقط از گذشته استفاده می‌کنند.

**ضعف‌ها و خطاهای مهم**

1. **Warmup ناکافی:** با ۶۰ کندل، EMA200 عددی تولید می‌کند و ۱۵ امتیاز trend را می‌دهد. به‌دلیل seed از close اول، وزن باقی‌ماندهٔ seed پس از حدود ۶۰ کندل هنوز تقریباً ۵۵٪ است؛ این EMA200 معنای روند بلندمدت استاندارد ندارد. `MIN_WARMUP=60` نیز همین سیاست را به backtest می‌برد.
2. **RSI صفر-loss:** در Python مقدار loss=0 به NaN تبدیل می‌شود. در fixture کاملاً صعودی، RSI آخر NaN شد و سهم RSI در momentum حذف شد. در JS نیز `rs=100` منجر به RSI≈99 می‌شود، نه قرارداد مشترک 100 یا 50 برای flat. نتیجهٔ probe: امتیاز Python **77.0** و JavaScript **78.3**.
3. **Parity ناقص:** Bollinger در Python نمونه‌ای و در JS جمعیتی است؛ seed RMA و TR اول نیز کاملاً یکسان نیست. fixtureهای فعلی preflight/tick parity صحت عددی کل TA را ثابت نمی‌کنند.
4. **وزن‌ها و آستانه‌ها کالیبره نشده‌اند:** 68/55، وزن‌های 40/25/15/20 و ضریب RANGE=0.85 قواعد heuristic هستند. ابزار adaptive فقط اندازه‌گیری می‌کند و چیزی را به Live اعمال نکرده است.
5. **Range strategy واقعی وجود ندارد:** در RANGE فقط کل امتیاز در 0.85 ضرب می‌شود؛ سیگنال mean-reversion مستقل ساخته نمی‌شود. بنابراین سیستم عمدتاً مدل long trend/momentum است، نه مجموعه‌ای از استراتژی‌های regime-specific.
6. **Score احتمال نیست:** امتیاز 70 به‌معنای 70٪ احتمال موفقیت نیست و توزیع آن بین نمادها و تایم‌فریم‌ها یکسان نیست. calibration موجود این امتیاز را به احتمال معتبر تبدیل نکرده است.

**قابلیت‌های فنی غایب:** MTA واقعی، divergence، Ichimoku، VWAP، Supertrend، OBV/MFI/CMF، Volume Profile، الگوهای کندلی، فیبوناچی/هارمونیک و Hurst در مسیر production دیده نشد. کاتالوگ خود پروژه نیز بخش بزرگی از این موارد را پیاده‌نشده معرفی می‌کند.

---

## ۵. تحلیل فاندامنتال: داده‌ها، فرمول‌ها و محدودیت‌ها

فایل اصلی: `server/fa_engine.py`.

### ۵.۱ داده‌های واقعاً استفاده‌شده

- CoinGecko: market cap، FDV، total volume، total/max supply و genesis date.
- DefiLlama `/protocols`: تلاش برای match بر اساس name/symbol و استخراج TVL.
- Alternative.me Fear & Greed Index.
- USDT/IRT premium و liquidity proxy داخلی.
- برای بخشی از نمادها، پروفایل‌های عددی fallback در کد.

خبر/NLP واقعی، CryptoPanic در مسیر اجرا، شاخص‌های کلان اقتصادی واقعی، unlock calendar واقعی، holder concentration، فعالیت آدرس‌ها و اکثر معیارهای on-chain پیاده نشده‌اند. docstring ماژول به برخی از این قابلیت‌ها اشاره می‌کند، اما پیاده‌سازی فعلی کوچک‌تر است. `LLAMA_CHAINS_MAPPED` تعریف شده ولی در match فعلی استفاده نمی‌شود.

### ۵.۲ فرمول componentها

**Market**

پایه بر اساس market cap:

| Market cap | mkt پایه |
|---|---:|
| ≥ $50B | 85 |
| ≥ $10B | 74 |
| ≥ $1B | 62 |
| ≥ $100M | 50 |
| کمتر | 38 |

اگر `volume/mcap > 0.05` مقدار `+6` اضافه می‌شود؛ اگر `>0.5` باشد، به‌جای آن `−10` اعمال می‌شود.

**Tokenomics**

```text
tok = min(100, 130 × mcap/FDV)
اگر total_supply/max_supply < 0.6 باشد: tok -= 12
```

در fallback، ضریب 130 با 95 و TVL با فرمول دیگری جایگزین می‌شود؛ بنابراین live و fallback برای یک ورودی یکسان، همان معنی را ندارند.

**On-chain**

```text
اگر TVL > 0:
    onc = 40 + min(60, 18 × log10(TVL + 1))
اگر نماد mapped باشد و TVL نداشته باشد:
    onc = 55
BTC/LTC/DOGE: onc += 12
```

اگر `0 < TVL ≤ $10M`، veto شدید `thin-tvl` اضافه می‌شود.

**Sentiment و Macro**

```text
sentiment = 40 + 0.40 × FGI
اگر FGI ≥ 85: sentiment -= 10
اگر FGI ≤ 15: veto شدید extreme-fear

macro = 40 اگر premium > +4%
macro = 62 اگر premium < −4%
macro = 55 در غیر این صورت
premium > +4% همچنین veto غیرشدید premium-stretched دارد.
```

**ترکیب FA و liquidity adjustment**

```text
FA0 = 0.25×Market + 0.25×Tokenomics + 0.20×OnChain
    + 0.20×Sentiment + 0.10×Macro
FA = clip(min(FA0, FA0 + (nobitex_liquidity_score−50)×0.20), 0, 100)
```

عبارت `min` باعث می‌شود liquidity بالای ۵۰ هیچ پاداشی ندهد و فقط مقدار زیر ۵۰ جریمه ایجاد کند. اگر penalty-only بودن عمدی است، نام و مستند باید این را بگوید؛ اگر قرار بوده adjustment دوطرفه باشد، فرمول اشتباه است.

### ۵.۳ مشکلات اثرگذار FA

1. **اشباع TVL:** اشباع در `10^(60/18)−1 ≈ $2,153` رخ می‌دهد. در probe مستقل، TVLهای `$5,000`، `$5,000,000` و `$1,000,000,000` همگی `onchain=100` دادند؛ دو مورد اول همزمان `thin-tvl` گرفتند. این یعنی تفکیک مقیاس on-chain از همان ناحیه‌ای حذف می‌شود که veto آن را ضعیف می‌داند.
2. **Mapping ناقص/پرخطر:** match با symbol می‌تواند protocol هم‌نام یا chain اشتباه بگیرد و mapping زنجیرهٔ تعریف‌شده مصرف نمی‌شود. TVL باید فقط برای asset class قابل‌اطلاق و با mapping صریح استفاده شود.
3. **دادهٔ ناموجود به عدد تبدیل می‌شود:** نماد بدون mapping در probe امتیاز عددی `52.5` با `profile=none` و `fresh=false` گرفت. برچسب صادقانه است، اما composite همچنان ۲۵٪ وزن به این عدد می‌دهد. شکست DefiLlama/FNG نیز می‌تواند به مقدار neutral ختم شود، درحالی‌که freshness کلی لزوماً وضعیت همهٔ componentها را توضیح نمی‌دهد.
4. **Cache نقدشوندگی:** کلید cache ده‌دقیقه‌ای فقط symbol و premium bucket دارد و `nobil_depth_score` در آن نیست. در probe، تغییر depth از 20 به 100 بدون پاک‌کردن cache، امتیاز 55.8 را ثابت نگه داشت. با پاک‌کردن cache، depthهای 50/80/100 همگی 61.8 دادند؛ تأیید دیگری بر حذف پاداش مثبت.
5. **Fallback ناسازگار:** ضریب TVL در fallback برابر 5.2 با سقف 55 است، در live برابر 18 با سقف 60؛ tokenomics و مقدار no-TVL نیز فرق دارد. Fallback می‌تواند prior باشد، ولی نباید با live یک «مدل اندازه‌گیری یکسان» تلقی شود.

---

## ۶. Liquidity، Composite و Veto

### ۶.۱ فرمول نقدشوندگی

در `server/main.py::rescan`:

```text
L = round(clip(50 + 4 × log10(vol24 + 1), 0, 100))
```

این امتیاز، **depth یا spread دفتر سفارش نیست**؛ فقط proxy گردش ۲۴ساعته است. `vol24` برای IRT برحسب Toman و برای USDT برحسب USDT است. حجم صفر یا غایب نیز به 50 می‌رسد، نه «نامعلوم».

### ۶.۲ اختلال واحد با مثال عددی

با نرخ فرضی `100,000 Toman/USDT`، برای گردش واقعی یک‌میلیون‌دلاری:

- جفت USDT: `vol24=1,000,000` و `L≈74`
- جفت IRT: `vol24=100,000,000,000` و `L≈94`

اختلاف تقریبی قبل از clip برابر `4×log10(USDTIRT)` است؛ یعنی حدود ۲۰ امتیاز صرفاً به‌خاطر واحد. در composite، این اختلاف می‌تواند حدود چهار امتیاز وزنی ایجاد کند. مهم‌تر از آن، `rows.sort(key=vol24)` پیش از انتخاب top60 انجام می‌شود و می‌تواند Universe را از ابتدا به IRT سوگیری دهد.

`server/market_overview.py` برخلاف این مسیر، برای جمع‌کردن گردش، IRT را با FX به USD تبدیل می‌کند؛ یعنی راه‌حل واحد در پروژه وجود دارد ولی به Universe/liquidity تعمیم نیافته است.

### ۶.۳ Composite و direction

```text
اگر FA عددی موجود باشد:
    Composite = 0.55×TA + 0.25×FA + 0.20×L
اگر FA=None باشد:
    Composite = (0.55×TA + 0.20×L)/0.75
```

- veto عادی: composite حداکثر 67 و LONG به NEUTRAL تبدیل می‌شود.
- veto شدید (`thin-tvl`، `extreme-fear`، `unlock-7d>5%`): composite حداکثر 45 و direction برابر EXIT.

نکتهٔ ساختاری: `direction_for` پیش از composite و فقط روی TA اجرا می‌شود؛ composite دیگر از آستانهٔ 68/55 عبور داده نمی‌شود. مثال بدون veto:

- `TA=68, FA=30, L=50` نتیجه `Composite=54.9` ولی direction اولیه LONG است.
- `TA=60, FA=90, L=90` نتیجه `Composite=73.5` ولی direction اولیه NEUTRAL است.

این طراحی لزوماً اشتباه نیست—می‌تواند «TA مسئول direction و composite مسئول ranking» باشد—ولی API و UI باید دقیقاً همین را بگویند. در غیر این صورت کاربر composite را امتیاز تصمیم نهایی می‌فهمد، درحالی‌که چنین نیست.

---

## ۷. ریسک، اجرا، Backtest و مدل نتیجه

### ۷.۱ سایزینگ و gateهای واقعی

در `server/risk_engine.py`:

```text
risk_amount = equity × risk_pct/100       (پیش‌فرض 0.75%)
stop_distance = 2 × ATR
quantity = risk_amount / stop_distance
notional = quantity × entry
```

Live plan پیش‌فرض: `TP1=entry+1.2R` و `TP2=entry+2R`، با `R=stop_distance`. Gateهای واقعی شامل halted، market closed، concurrent cap، per-order notional cap، minimum notional، risk cap، heat cap، minimum cash و funds هستند. نرخ FX معتبر برای IRT الزامی است و عدم وجود آن نباید به تقسیم بر یک تبدیل شود.

**محدودیت‌ها:**

- `single-asset-cap` در preflight و sizing روی **همین سفارش** است، نه مجموع پوزیشن‌های همان دارایی. چند ورود هم‌نماد می‌توانند تمرکز تجمیعی بسازند.
- `close_trade` و `triggers` برای daily/weekly loss و سه stop متوالی تعریف شده‌اند، اما call site واقعی در مسیر Paper/main برای به‌روزرسانی daily PnL و فعال‌کردن trigger دیده نشد. heat باز از پوزیشن‌ها sync می‌شود و kill switch دستی کار می‌کند؛ اما ضمانت توقف خودکار روزانه وجود ندارد. UI جدید نیز این محدودیت را افشا می‌کند.
- `loss_at_stop_usd` فقط risk قیمت و entry fee را جمع می‌کند؛ exit fee، slippage و gap در «worst case» نیستند.

### ۷.۲ هزینه و لغزش

- `server/fees.py`: کارمزد ثابت `0.2%` برای هر leg در backtest/Paper.
- Backtest: لغزش پیش‌فرض `6 bps = 0.06%` در هر ورود/خروج.
- دور کامل با این فرض‌ها تقریباً `0.52%` هزینه دارد، پیش از gap و اثر عمق.
- نرخ واقعی fee tier صرافی verify نشده است؛ بنابراین نمی‌توان 0.2% را برای همهٔ بازارها تضمیناً «محافظه‌کارانه» دانست.

### ۷.۳ ناسازگاری مدل‌ها

| موضوع | Risk/Paper | Journal | Backtest |
|---|---|---|---|
| مبنای سیگنال | TA/FA/Liquidity و veto در مسیر scan | composite ثبت‌شده | **فقط TA**؛ صریحاً `matches_live=false` |
| TP1/TP2 پیش‌فرض | 1.2R / 2R | 1.2R / 2R | 1.5R / 2.5R؛ grid نیز اغلب هدف‌های دیگر |
| اجرای TP2 | **در `evaluate_positions` وجود ندارد** | **در `simulate_long` وجود ندارد** | پیاده‌شده |
| پس از TP1 | خروج 50٪ و stop به `entry×1.002` | همان | خروج 50٪ و stop دقیقاً به entry |
| هزینه | حساب اصلی fee دارد؛ TP1 روی mid و بدون execution book | ندارد؛ gross | fee و slippage ورود/خروج |
| خروج زمانی | time-stop 48h با حرکت کمتر از 1٪ | پایان افق | پایان window/پایان داده |

**Probe ژورنال:** برای entry=100 و ATR=1، stop=98، TP1=102.4 و TP2=104 است. یک کندل آینده با high=110 و close=110 نتیجهٔ `R=3.1` داد. اگر نیمهٔ دوم روی TP2 بسته می‌شد، R ناخالص باید `0.5×1.2 + 0.5×2 = 1.6` می‌بود. همین ردیف `return_pct=10%` گزارش کرد، چون `return_pct` فقط قیمت خروج باقی‌مانده را با entry مقایسه می‌کند و proceeds مربوط به TP1 را شامل نمی‌شود.

### ۷.۴ Backtest: نقاط قوت و ضعف‌ها

**قوت‌ها:** انتخاب پارامتر روی train و گزارش روی eval، stop-first در ابهام intrabar، fee/slippage، افشای TA-only، و رد promotion روی synthetic/reference data.

**ضعف‌ها:**

1. **TA-only بودن اساسی است:** چون سری تاریخی FA و liquidity ذخیره نشده، PASS فقط دربارهٔ TA است، نه استراتژی composite فعلی.
2. **Entry روی همان close:** محاسبات lookahead ندارند، اما استفاده از close نهایی کندل و fill روی همان close، فرض اجرایی خوش‌بینانه است. حساسیت به next-open و latency باید سنجیده شود.
3. **ورود تکراری:** دو LONG متوالی در probe دو معاملهٔ جدا روی همان نماد ساختند. cap سفارش، تمرکز تجمیعی را کنترل نمی‌کند؛ این یا pyramiding مستند یا یک شکاف ریسک است.
4. **Objective ناپایدار در نمونهٔ کم:** حداقل پیش‌فرض سه معاملهٔ train و PF بدون-loss برابر 99.99 می‌تواند انتخاب خوش‌شانس را تشویق کند. objective برابر `ExpectancyR + 0.25×(PF−1) − 0.01×MaxDD%` است.
5. **R denominator:** `risk_amount` بر اساس stop اولیه و بدون slippage/fee است، ولی numerator خالص هزینه است؛ بنابراین R صریحاً «net PnL / initial price-risk» است، نه نسبت به worst-case کامل.
6. **Backtest پرتفوی واقعی نیست:** مسیرها عمدتاً single-symbol هستند و heat/cash/daily risk کامل Live را بازتولید نمی‌کنند.

---

## ۸. IC، Calibration، Attribution و Drift

### ۸.۱ چرا IC و MSI قابل مقایسه نیستند

- `journal.backtest_ic`: روی **TA score همهٔ ردیفهای قابل‌امتیاز** و بازده close-to-close در افق ثابت.
- `journal.ic_for_window`: روی **composite فقط سیگنال‌های LONG** در H2 و `return_pct` مدل خروج.
- `return_pct` پس از TP1 لزوماً بازده کامل معامله نیست؛ R نیز gross و فاقد TP2 است.
- انتخاب فقط LONG باعث range restriction می‌شود و IC را با baseline همهٔ scoreها ناسازگار می‌کند.
- آستانهٔ نمونه روی تعداد ردیفها است، نه نمونهٔ مستقل مؤثر یا تعداد نمادهای واقعاً وارد میانگین IC.

نتیجه: کاهش یا افزایش MSI می‌تواند ناشی از تفاوت cohort، label، horizon و مکانیزم خروج باشد، نه drift مدل.

### ۸.۲ استقلال نمونه و آمار

سیگنال‌های per-bar و forward returnهای overlapping خودهمبسته‌اند. CI نرمال روی `R` و آزمون‌های mean/proportion در Drift فرض استقلال دارند که برای این داده برقرار نیست. حداقل 20/30/100 ردیف، به‌تنهایی اعتبار آماری ایجاد نمی‌کند.

نکتهٔ مثبت: `scripts/measure_real_ic.py` یک block bootstrap مشترک در زمان برای نمادها دارد؛ اما این روش به CIهای production Journal/Drift تعمیم نیافته و دادهٔ مرجع آن نیز نمی‌تواند Nobitex را certify کند.

### ۸.³ Drift

- اگر همهٔ یافته‌ها `insufficient` باشند، overall می‌تواند `stable` ثبت شود؛ «نمونه نداریم» نباید «باثباتیم» شود.
- cohort جاری بر اساس signal timestamp انتخاب و فقط resolvedها نگه داشته می‌شوند؛ در انتهای پنجره، right-censoring/maturity bias ایجاد می‌شود، به‌ویژه H3.
- در cohort mode، فیلتر `config_hash` مانند مسیر global به queryهای outcomes/scores اعمال نمی‌شود و نسخه‌ها می‌توانند مخلوط شوند.
- PSI و Benjamini–Hochberg پیاده‌شده‌اند و binهای PSI قابل audit هستند؛ این نقاط قوت، مشکل استقلال/بازهٔ بلوغ را حل نمی‌کند.

### ۸.۴ Calibration و Attribution

- Calibration decile و hit-rate توصیفی مفید است، ولی با نمونهٔ کم و overlapping به معنای احتمال کالیبره‌شده نیست. Brier score، log loss و CI لازم است.
- Family بر اساس بزرگ‌ترین مقدار خام component انتخاب می‌شود. UI صریحاً می‌گوید این causal attribution یا سهم وزن‌دار نیست؛ برچسب درست است. برای بهبود وزن‌ها باید ablation/permutation با blocked validation انجام شود.

### ۸.۵ هویت پیکربندی و Replay

`journal.CONFIG_HASH` فقط از `RISK_K`، `TP1_R`، `TP2_R`، `TRAIL_R` و `LONG_MIN` ساخته می‌شود. وزن‌های TA/FA/liquidity، آستانهٔ NEUTRAL، regime gate، فرمول TVL، mappingها، provider schema و نسخهٔ داده در آن نیستند. در نتیجه دو موتور با رفتار متفاوت می‌توانند hash یکسان داشته باشند. گزارش Backtest هش پارامتری خود را صادقانه `legacy` و «نه هویت یکتای run/dataset» می‌نامد، اما برای replay کامل باید این محدودیت به همهٔ مسیرها تعمیم یابد: full config hash، dataset hash با timestamp/coverage، engine version و source-unit/FX باید همراه هر نتیجه ذخیره شوند.

---

## ۹. Market Overview، Benchmark و Portfolio

- **Market Overview:** median، mean، breadth، dispersion، turnover-weighted change و concentration محاسبه می‌کند؛ USDTIRT را کنار می‌گذارد، IRT/USDT هم‌دارایی را dedup می‌کند و گردش را به USD تبدیل می‌کند. اینکه «شاخص بازار نیست» صریح نوشته شده و نقطهٔ قوت است. خروجی، خلاصهٔ coverage فعلی است نه شاخص سرمایه‌گذاری‌شده.
- **Benchmark:** هم‌ترازی timestamp، log return، relative change، capture و alpha ساده ارائه می‌دهد. نمونهٔ کوچک و autocorrelation هنوز CI ندارند؛ alpha نباید بدون آزمون معناداری تفسیر شود.
- **Portfolio/ERC:** covariance و correlation بر پنجرهٔ حدود ۶۰ مشاهده، shrinkage و iteration محدود استفاده می‌شود. این ابزار تحلیلی به execution broker متصل نیست؛ correlation صفر برای دادهٔ غایب و نبود کنترل liquidity/capacity می‌تواند وزن‌ها را خوش‌بینانه کند.
- این ماژول‌ها ارزش تحلیلی دارند، اما نباید برای جبران ضعف دادهٔ اساسی در Universe/FA یا اثبات سود استراتژی استفاده شوند.

---

## ۱۰. اولویت‌بندی یافته‌ها

شدت‌ها مربوط به صحت تصمیم و ارزیابی هستند، نه رتبهٔ امنیت شبکه.

| ID | شدت | یافته | اثر | اقدام اول |
|---|---|---|---|---|
| F01 | زیاد | حجم خام mixed-unit در Universe و liquidity | سوگیری IRT در نمونه و ranking | تبدیل به USD پیش از sort/score؛ missing≠zero |
| F02 | زیاد | اشباع TVL و mapping ضعیف | onchain=100 برای TVLهای کاملاً متفاوت و veto متناقض | N/A برای غیرقابل‌اطلاق؛ mapping صریح؛ مقیاس بازطراحی‌شده |
| F03 | زیاد | TP2/return/IC ناسازگار | R، IC، MSI و ارزیابی سود غیرقابل اتکا | یک outcome contract مشترک و labelهای جداگانه |
| F04 | زیاد | نمونهٔ غیرمستقل و baseline متفاوت | CI و promotion خوش‌بینانه | cohort یکسان، effective N، block bootstrap |
| F05 | زیاد | TA warmup/RSI/parity | تفاوت سیگنال Python/JS و امتیاز EMA200 ناپایدار | warmup≥200/ترجیحاً بیشتر، edge-case tests، parity کامل |
| F06 | زیاد | direction روی TA و composite روی ranking | ظاهراً تناقض در verdict نهایی | تفکیک `ta_direction` و `final_action` یا آستانه روی composite |
| F07 | متوسط‌روبه‌بالا | cap تجمیعی و triggerهای loss عملیاتی نیستند | تمرکز و توقف خودکار ناقص | exposure تجمیعی و wiring تست‌شده |
| F08 | متوسط | FA cache/fallback/provider freshness | عدد کهنه یا prior با وزن کامل | کلید کامل، reliability weight، status per provider |
| F09 | متوسط | فرض‌های اجرایی Backtest/Paper | expectancy و R غیرواقعی | next-open، depth/latency، هندسهٔ یکسان |
| F10 | متوسط | Config/data identity ناقص | replay و مقایسهٔ نسخه‌ها دشوار | full config hash + dataset hash + engine version |
| F11 | متوسط | Drift stable/censoring/config cohort | پیام وضعیت گمراه‌کننده | overall=insufficient و maturity cutoff |
| F12 | افشاشده | Paper metrics و family attribution محدود | تفسیر بیش از حد در صورت نادیده‌گرفتن label | متریک round-trip/net و ablation؛ برچسب‌ها حفظ شوند |

---

## ۱۱. نقاط قوت که باید حفظ شوند

1. **کندل بسته و timestamp درست:** حذف forming candle و ارزیابی از کندل بعد، look-ahead آشکار را کاهش می‌دهد.
2. **Data honesty:** synthetic cache پذیرفته نمی‌شود؛ reference/synthetic نمی‌تواند promotion PASS بگیرد؛ freshness per-symbol و منشأ داده گزارش می‌شود.
3. **Backtest صادق در دامنهٔ خود:** TA-only و `matches_live=false` صریح است؛ خطای اصلی، تعمیم نتایج به composite خواهد بود نه پنهان‌بودن دامنه.
4. **Preflight مشترک:** تطبیق 28×10 و استفادهٔ preview/executor از یک منطق، کار بسیار خوبی است.
5. **FX و واحد در مرز اجرا:** الزام FX برای IRT و رد حدس‌زدن نرخ، از اشتباهات بزرگ جلوگیری می‌کند.
6. **Walk-Forward و هزینه:** train/eval جدا و fee/slippage مدل شده‌اند؛ stop-first انتخاب محافظه‌کارانه‌ای برای candle ambiguity است.
7. **ژورنال immutable و چرخه‌های پیوندی:** ثبت ورودی، void، outcome و scan linkage برای پژوهش آینده ارزشمند است.
8. **افشای محدودیت‌ها:** gross R، نبود independence، retained events، attribution غیرعلّی و adaptive-not-applied در بخش‌های جدید صریح شده‌اند.
9. **کیفیت مهندسی و تست:** مجموعهٔ بزرگ تست، typing، lint و parityهای فعلی از regressionهای زیادی جلوگیری می‌کند.
10. **Block bootstrap آزمایشی موجود:** می‌توان آن را به‌جای اختراع دوباره، در ارزیابی production یکپارچه کرد.

---

## ۱۲. راهکارهای بهبود ارزیابی سیگنال و تحلیل بازار

### ۱۲.۱ اصلاح قرارداد تصمیم

یک `DecisionSnapshot` واحد بسازید که حداقل شامل این موارد باشد:

- raw features و componentها؛ TA، FA، liquidity و composite؛
- `ta_direction`، `rank_score`، `final_action` و دلیل هر veto/gate؛
- quote currency، واحد volume، FX و timestamp هر منبع؛
- engine version، full config hash، dataset hash و آی‌دی scan؛
- freshness/coverage و سیاست missing data.

سپس یکی از دو قرارداد را صریح انتخاب کنید:

- **مدل تفکیک‌شده:** TA direction تعیین‌کنندهٔ candidacy، composite فقط ranking؛ یا
- **مدل نهایی یکپارچه:** direction و execution eligibility هر دو از composite، veto، data quality و risk ساخته شوند.

### ۱۲.۲ یکسان‌سازی مدل معامله و نتیجه

یک کتابخانهٔ مشترک برای stop، TP1، TP2، trailing، partial fill، fee، slippage و time-stop بسازید و در Backtest/Journal/Paper استفاده کنید. سه متریک را جدا نگه دارید:

1. **forward close-to-close return** برای IC پیش‌بینی؛
2. **weighted net trade return/R** برای ارزیابی استراتژی؛
3. **mark-to-market MFE/MAE** برای رفتار مسیر.

TP2 باید در Journal و Paper وجود داشته باشد یا صریحاً از قرارداد حذف شود. targetهای پیش‌فرض Backtest باید همان Live باشند و تغییر آن‌ها فقط به‌صورت آزمایش نسخه‌دار انجام شود.

### ۱۲.۳ اصلاح داده و ویژگی‌ها

- `vol24_quote` و `turnover_usd` را جدا ذخیره کنید. بدون FX، رتبه‌بندی mixed-quote انجام نشود.
- Liquidity را با spread، depth در چند bps، expected slippage و capacity تکمیل کنید؛ برای آن‌ها timestamp لازم است.
- FA را بر اساس asset class کنید: TVL فقط برای DeFi/L1/L2 قابل‌اطلاق؛ برای meme/stable/PoW معیار جدا یا N/A.
- Unlock واقعی، circulating supply، دادهٔ on-chain و رویدادها باید source و effective time داشته باشند تا backtest point-in-time ممکن شود.
- برای دادهٔ غایب، وزن component را renormalize یا reliability کاهش دهید؛ neutral عددی نباید به‌صورت measurement وارد شود.
- higher-timeframe trend و breadth فقط از کندل‌های بستهٔ همان لحظه اضافه شوند؛ تشکیل‌دهندهٔ آینده نباید وارد feature شود.

### ۱۲.۴ روش آماری پیشنهادی

1. نمونه‌ها را بر اساس زمان تصمیم و maturity cutoff انتخاب کنید؛ سیگنال نارس در denominator قرار نگیرد.
2. برای H1/H2/H3، overlap را با non-overlapping cohort، sample uniqueness یا block bootstrap کنترل کنید.
3. برای انتخاب پارامتر از nested walk-forward یا inner validation استفاده کنید و holdout نهایی را فریز کنید.
4. CI را روی expectancyR، IC، hit-rate و PF با block/cluster bootstrap بسازید؛ تعداد ردیف را با effective sample size همراه کنید.
5. Drift overall را وقتی نمونه کافی نیست `insufficient` کنید؛ cohortها باید config/engine/data یکسان داشته باشند.
6. معیارهای اصلی را خالص هزینه گزارش کنید: net expectancy، PF، max drawdown، turnover، exposure، tail loss، capacity و حساسیت به fee/slippage.
7. نتیجه را با buy-and-hold، cash، baseline ساده و مدل بدون هر component مقایسه کنید. ablation معنادارتر از انتساب component غالب است.

### ۱۲.۵ کالیبراسیون و مدل‌سازی

- ابتدا baseline ساده و قابل توضیح بسازید: logistic/ridge یا gradient boosting کوچک با time-series validation؛ مدل پیچیده فقط پس از ثابت‌شدن مزیت.
- اگر قرار است score احتمال باشد، روی validation جدا با Platt/isotonic کالیبره و Brier/log loss گزارش کنید؛ calibration روی train ممنوع.
- برای labeling می‌توان از future net return یا triple-barrier استفاده کرد، اما هزینه و زمان بلوغ باید در label باشد.
- feature importance فقط با blocked ablation/permutation؛ SHAP روی دادهٔ in-sample مدرک causal نیست.
- تعداد indicatorها را افزایش ندهید مگر اینکه ablation خارج از نمونه، منفعت خالص و پایدار نشان دهد.

---

## ۱۳. نقشهٔ راه پیشنهادی و دروازهٔ پذیرش

### فاز صفر — صحت پایه، پیش از هر بهینه‌سازی

- اصلاح F01، F02، F03 و F05؛ افزودن regression test برای مثال‌های همین گزارش.
- پیاده‌سازی TP2 مشترک، تعریف دقیق return/R، warmup استاندارد و parity عددی Python/JS.
- تصمیم صریح دربارهٔ direction/composite و غیرفعال‌کردن وزن FA برای دادهٔ غایب.

### فاز یک — یکپارچه‌سازی و ریسک

- یک DecisionSnapshot و یک OutcomeModel در همهٔ مسیرها.
- cap تجمیعی asset/cluster، wiring واقعی daily/weekly/consecutive-stop یا حذف صریح ادعای عملیاتی.
- full config/data hash و provenance برای replay.

### فاز دو — دادهٔ تاریخی و اجرای واقع‌گرایانه

- ذخیرهٔ point-in-time FA/liquidity/orderbook تا backtest composite ممکن شود.
- next-open/latency، spread/depth، partial fill، fee tier و stress هزینه.
- تست حساسیت به forming window و delay در scan.

### فاز سه — اعتبارسنجی، نه صرفاً تست نرم‌افزار

یک استراتژی فقط وقتی برای promotion نامزد شود که:

- داده از venue واقعی و دورهٔ مشخص باشد و reference/synthetic نباشد؛
- Decision/Outcome model دقیقاً با Live مطابقت داشته باشد؛
- نتایج OOS و holdout پس از کل هزینه‌ها مثبت و در چند regime پایدار باشند؛
- CIهای مبتنی بر نمونهٔ مؤثر، نه فقط n=30، از عملکرد حمایت کنند؛
- نتیجه در stress هزینه/لغزش، delay و تغییر پارامتر فرو نریزد؛
- shadow/Paper با همان سیگنال‌ها reconciliation داشته باشد و تفاوت execution قابل توضیح باشد.

عددهای فعلی مثل PF≥1.3، حداقل سه window و 30 معامله می‌توانند policy اولیه بمانند، اما به‌تنهایی اثبات کافی نیستند و نباید «آستانهٔ بهینه» نامیده شوند.

---

## ۱۴. نتیجهٔ نهایی فارسی

Nobi یک **پلتفرم پژوهش و شبیه‌سازی با مهندسی دفاعی خوب** است، نه هنوز یک موتور سیگنال با برتری اثبات‌شده. بهترین سرمایه‌گذاری بعدی، افزودن indicator یا مدل پیچیده‌تر نیست؛ بلکه اصلاح واحدها و TVL، یکسان‌سازی قرارداد نتیجه، تکمیل ریسک تجمیعی، و ساخت شاهد خارج از نمونه با نمونهٔ مؤثر و هزینهٔ واقعی است. تا زمان انجام این کارها، رتبه‌بندی و score باید exploratory تلقی شوند و PASS نرم‌افزاری یا Backtest TA-only نباید مجوز Live تفسیر شود.

---


# الحاقیهٔ remediation — 2026-11-04

یافته‌های F01 تا F12 و مثال‌های عددی بالا مربوط به baseline هستند. در working tree فعلی، اصلاحات زیر اعمال و regression شدند:

- **F01/F02/F08:** USD turnover برای ranking/liquidity، FA cache حساس به depth/premium، provider/staleness provenance، حذف neutral ساختگی برای دادهٔ ناموجود و TVL مبتنی بر mapping صریح؛
- **F03/F09:** TP2 در Paper و Journal، PnL صحیح TP1 سمت sell، return/R وزنی دو leg، default مشترک 1.2R/2R و ثبت execution assumptions؛
- **F05:** warmup و TA parity عددی Python/JS برای primitives و component/direction/regime/risk/diagnostics؛
- **F07:** اتصال daily/weekly/consecutive-stop triggers به Paper/preflight/backtest/JS و توقف ورود چهارم پس از سومین stop، بدون توقف مدیریت موقعیت باز؛
- **F08:** دادهٔ ناموجود و stale دیگر evidence کامل یا score با وزن ساختگی نیست؛
- **F10:** dataset hash/range، engine/source identity و effective config در Journal/Backtest؛ این identity به‌تنهایی replay کامل نیست؛
- **سایر موارد گزارش دوم:** pagination، gap-open، post-TP1 ambiguity، daily H1، خالص‌سازی Paper GET، freshness صحیح health، live account preflight جدا و fail-closed، عدم شمارش dry-run/ambiguous به‌عنوان promotion evidence، انتخاب grid فقط از IS، fee scenario با provenance و correlation جفت‌به‌جفت بر اساس timestamp مشترک.

**نتیجهٔ نهایی:** `1776 passed` در full Python suite؛ mypy بدون خطا در 165 فایل؛ Ruff/ESLint/dashboard lint پاک؛ JS broker/intrabar و تمام ۲۰ هدف DOM/Node 22 پاس شدند. `nobitex-mobile.html` بازسازی و byte-contract آن پاس شد. جزئیات کامل در `/home/user/nobi-review-of-reviews-and-capoy-fa.md` بخش ۹ است.

**محدودیت باقی‌مانده:** backtest هنوز TA-only و execution هنوز signal-close/OHLC بدون depth/latency/partial fill است. tier واقعی کارمزد، point-in-time FA/liquidity، frozen holdout، effective-sample inference و archive کامل payload هنوز وجود ندارد. retention صرافی با pagination قابل عبور نیست. بنابراین این remediation صحت نرم‌افزاری را بالا می‌برد، اما سودآوری، آمادگی live یا deployment را اثبات نمی‌کند. هیچ deploy یا سفارش واقعی انجام نشده است.

