
## Amendment — 2026-10-20: the modes moved from the browser to the server

The execution mode used to be a string in page memory: clicking “auto” changed a
chip and a toast and nothing read it; worse, a hard-coded `symMode` table decided
each symbol in advance, so the “per-symbol mode” the hint promised could not be
changed at all. A second control cycled modes in the signal list and offered
`blocked` — a gate's verdict, not a mode — as a fourth choice.

Now:

- **The mode is server state:** `GET/POST /api/exec/mode`; a default plus
  per-symbol overrides, persisted in the journal KV table (`exec_mode`). Both
  routes answer one payload (`_exec_mode_payload`) so the page sees the
  executor's real counters after a change, not an empty object.
- **`auto` is a real executor:** `auto_tick` sizes each `LONG` signal with
  `risk.evaluate` and sends the paper order through `broker.place_limit` and the
  same ten gates. The order event carries `source=AUTO`.
- **The limits are deliberate:** at most one open position per market, and a
  30-minute cooldown after every attempt (placed or refused) so a refused plan is
  not retried on every pass.
- **The page shows only what the server said:** the per-symbol select on each
  queue row (and the signal chip) reads the server value back; with no server it
  refuses to change and says so.
- **`auto` does not claim live trading:** the API answer and the page both say
  `executor: "paper"` and that nothing is sent to Nobitex — the repository has no
  `POST /market/orders` path.
