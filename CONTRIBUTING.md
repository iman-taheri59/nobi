# مشارکت در nobitrader

## پیش از شروع

1. `AGENTS.md` را بخوانید (قواعد نسخهبندی و زبان پاسخ).
2. `docs/README.md` نقشهٔ مستندات و `docs/HANDOFF.md` وضعیت کارهای باز را نشان میدهد.
3. برای باگهای امنیتی به `SECURITY.md` مراجعه کنید — از issue عمومی استفاده نکنید.

## راهاندازی محیط

```bash
python -m venv .venv
.venv/bin/pip install -r server/requirements-dev.txt
npm ci
npm run build
```

## دروازهها (همان چیزی است که CI اجرا میکند)

```bash
ruff check server scripts
mypy
python -m pytest -q server/tests
pip-audit -r server/requirements.txt
npm run lint
npm run lint:dashboard
npm audit --audit-level=high
npm run test:js
npm run test:intrabar
npm run test:display
npm run test:adaptive-card
npm run test:icons
npm run test:escaping
python scripts/check_preflight_parity.py
npm run build          # nobitex-mobile.html must not change afterwards
```

`nobitex-mobile.html` فایل تولیدی است؛ **هرگز دستی ویرایشش نکنید**. بعد از تغییر
`index.html`, `nbx-*.js` یا CSS حتماً `npm run build` بزنید.

## قواعد کد

- هیچ عددی ساختگی نیست: اگر دادهٔ زندهای نیست، پنل باید صریح بگوید چرا
  (قرارداد ADR-0014). بهجای `—` تنها، یک جمله بنویسید.
- رشتههایی که از سرور/صرافی میآیند، پیش از ورود به `innerHTML` یا attribute
  حتماً با `esc()`/`escText()` پاکسازی شوند؛ تست `npm run test:escaping` این را چک میکند.
- تغییر رفتار مالی (دروازههای ریسک، تبدیل واحد، کارمزد) نیازمند تست همارزی
  Python و JS است.
- هر تغییر نیازمند افزایش نسخه در `VERSION`, `server/main.py`, `package.json`,
  `package-lock.json`, نشان `index.html` و ثبت در `CHANGELOG.md`/`.en` است.

## Pull request

- توضیح دهید چه چیزی اندازهگیری شد (لاگ تست، خروجی `probe_live.py`)، نه فقط چه
  چیزی تغییر کرد.
- اگر استقرار/امنیت را لمس کردید، `docs/adr/` را بهروز کنید و دلیلش را بنویسید.
- CI سبز شرط لازم است، نه کافی؛ ویژگیهای مرتبط با نصبگر سرور را روی یک ماشین
  واقعی Ubuntu تست کنید.
