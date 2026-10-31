#!/usr/bin/env python3
"""Resolve a Nobitex symbol to its CoinGecko id — offline, by a human.

`fa_engine` needs a CoinGecko id per base asset to read market cap, volume and
supply. The map is `KNOWN_CG_IDS`: fourteen majors built into the engine plus
`server/coin_registry.json`. A symbol with no entry gets `cg = None`, the
provider call is skipped, and the score is built from five neutral 50s and one
market-wide Fear&Greed value — reported honestly as `profile: "none"`, but with
no measurement in it.

ADR-0025 refuses to close that gap at runtime, and the reason is measured:
2,626 of 17,425 symbols (15%) match more than one coin on CoinGecko, and the
free tier answers 429 after three consecutive calls. `DOGS` matches four coins,
`NOT` matches two. Taking the first is a guess, and a guess produces a
fundamental score somebody reads.

So the resolution happens here instead: this script asks CoinGecko once, shows
every candidate with enough context to choose between them, and writes a line
into the registry **only when a human says so**. It never picks for you.

    # which of these symbols are unmapped?
    python3 scripts/resolve_fa.py --check SYMBOL [SYMBOL ...]
    python3 scripts/resolve_fa.py --check --file symbols.txt

    # what does CoinGecko say about one of them?
    python3 scripts/resolve_fa.py --resolve DOGS

    # a human decided; record it
    python3 scripts/resolve_fa.py --add DOGS dogs-2

    # what the registry looks like now
    python3 scripts/resolve_fa.py --show

Every network call is one you asked for. `--check` and `--show` touch no
network at all.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "server" / "coin_registry.json"
ENGINE = ROOT / "server" / "fa_engine.py"

CG_SEARCH = "https://api.coingecko.com/api/v3/search"
CG_TIMEOUT_S = 20

#: A Nobitex market is BASE+QUOTE, and the quote is one of these.
QUOTES = ("USDT", "IRT")

FA_ENGINE_BUILTINS = re.compile(r"_BUILTIN_CG_IDS\s*=\s*\{(.*?)\n\}", re.S)


class Fail(Exception):
    """A problem worth printing without a traceback."""


# --------------------------------------------------------------------------- #
# The map, read from the two places that own it.
# --------------------------------------------------------------------------- #

def _builtin_ids() -> dict[str, str]:
    """The majors `fa_engine` carries in a literal, so an unreadable registry
    still answers for the big coins."""
    match = FA_ENGINE_BUILTINS.search(ENGINE.read_text(encoding="utf-8"))
    if not match:
        raise Fail(f"could not read _BUILTIN_CG_IDS from {ENGINE}")
    return dict(re.findall(r'"([A-Z0-9]+)"\s*:\s*"([a-z0-9-]+)"', match.group(1)))


def load_registry() -> dict[str, str]:
    if not REGISTRY.is_file():
        raise Fail(f"{REGISTRY} is missing")
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Fail(f"{REGISTRY} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise Fail(f"{REGISTRY} must hold an object, found {type(data).__name__}")
    return {str(k).upper(): str(v) for k, v in data.items() if k and v}


def known_ids() -> dict[str, str]:
    """`KNOWN_CG_IDS`, assembled the same way `fa_engine` assembles it:
    registry first, built-ins as the fallback."""
    return {**_builtin_ids(), **load_registry()}


def base_of(symbol: str) -> str:
    for quote in QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


# --------------------------------------------------------------------------- #
# CoinGecko.
# --------------------------------------------------------------------------- #

def cg_search(query: str) -> list[dict]:
    """Every coin CoinGecko matches `query` against, in its own order.

    The order is *not* a recommendation: `DOGS` returns `doggsnout-skeptic`
    first. That is why the caller shows all of them and decides nothing.
    """
    url = f"{CG_SEARCH}?query={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "nobitrader-resolve-fa/1.0",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=CG_TIMEOUT_S) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Fail("CoinGecko rate-limited us (429). Wait a minute and try one symbol "
                       "at a time — that limit is the whole reason this is offline work.") from e
        raise Fail(f"CoinGecko answered HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise Fail(f"could not reach CoinGecko: {e}") from e
    coins = payload.get("coins")
    return coins if isinstance(coins, list) else []


def _line(coin: dict) -> str:
    """One candidate, with the three things a human needs to tell them apart."""
    cid = str(coin.get("id", ""))
    name = str(coin.get("name", ""))
    sym = str(coin.get("symbol", "")).upper()
    rank = coin.get("market_cap_rank")
    rank_s = f"rank {rank}" if isinstance(rank, int) else "unranked"
    return f"    {cid:28} {name:28} {sym:8} {rank_s}"


# --------------------------------------------------------------------------- #
# Writing.
# --------------------------------------------------------------------------- #

def _atomic_write(path: Path, text: str) -> None:
    """Replace the registry in one step: a half-written JSON would take the
    engine's mapping with it, and the engine degrades to the fourteen majors."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def add_mapping(symbol: str, cg_id: str, force: bool) -> int:
    symbol = symbol.upper()
    base = base_of(symbol)
    registry = load_registry()
    known = known_ids()

    if not re.fullmatch(r"[A-Z0-9]{2,12}", symbol):
        raise Fail(f"symbol must be 2-12 uppercase letters/digits, got {symbol!r}")
    if not re.fullmatch(r"[a-z0-9-]{2,48}", cg_id):
        raise Fail(f"id must be 2-48 lowercase letters/digits/hyphens, got {cg_id!r}")
    if base in known and known[base] == cg_id:
        print(f"{base} is already mapped to {cg_id} — nothing to do")
        return 0
    if base in known and not force:
        raise Fail(f"{base} is already mapped to {known[base]!r}. "
                   f"Re-run with --force to replace it, or fix it by hand.")

    registry[base] = cg_id
    # Sort so a new line does not move the whole file and make the diff unreadable.
    ordered = {k: registry[k] for k in sorted(registry)}
    _atomic_write(REGISTRY, json.dumps(ordered, ensure_ascii=False, indent=2) + "\n")
    print(f"added {base} -> {cg_id}  ({REGISTRY.relative_to(ROOT)})")
    print("commit it: the mapping is a decision, and the commit is the record")
    return 0


# --------------------------------------------------------------------------- #
# Reporting.
# --------------------------------------------------------------------------- #

def cmd_check(symbols: list[str]) -> int:
    known = known_ids()
    unmapped = [s for s in symbols if base_of(s) not in known]
    print(f"{len(symbols)} symbol(s) checked against {len(known)} mapped base assets")
    for s in symbols:
        base = base_of(s)
        if base in known:
            print(f"  {s:12} {base:8} -> {known[base]}")
        else:
            print(f"  {s:12} {base:8} -> UNMAPPED (resolve it, do not guess it)")
    if unmapped:
        print(f"\n{len(unmapped)} unmapped: {', '.join(unmapped)}")
        print(f"next: python3 {Path(__file__).name} --resolve {unmapped[0]}")
        return 1
    print("\nevery symbol is mapped")
    return 0


def cmd_resolve(symbol: str) -> int:
    base = base_of(symbol.upper())
    known = known_ids()
    if base in known:
        print(f"{base} is already mapped to {known[base]}")
        return 0
    print(f"asking CoinGecko about {base!r} (one call)")
    coins = cg_search(base)
    if not coins:
        print(f"CoinGecko matched nothing for {base!r}. It may be listed under another "
              "name — search https://www.coingecko.com by hand and add it with --add.")
        return 1
    # Every candidate, always. An earlier version showed the first eight and hid the
    # rest behind `--top`, which is how the answer for WBTC stayed invisible: the real
    # coin (`wrapped-bitcoin`) is the 14th of 25 rows, after 24 wrapped derivatives.
    # A short list is not a courtesy here — it is a guess with fewer rows.
    print(f"\n{len(coins)} candidate(s). None is chosen for you:")
    for c in coins:
        print(_line(c))
    print("\npick one by hand, then:")
    print(f"  python3 {Path(__file__).name} --add {symbol} <id-from-the-list-above>")
    if len(coins) > 1:
        print(f"\n{len(coins)} matches — ADR-0025 measured this at 15% of all symbols. "
              "The first row is not the right answer just because it is first.")
    return 0


def cmd_show() -> int:
    registry = load_registry()
    builtin = _builtin_ids()
    known = {**builtin, **registry}
    print(f"registry  {REGISTRY.relative_to(ROOT)}: {len(registry)} entries")
    print(f"built-in  {ENGINE.relative_to(ROOT)}: {len(builtin)} majors")
    print(f"union     KNOWN_CG_IDS: {len(known)} base assets")
    only_builtin = sorted(set(builtin) - set(registry))
    if only_builtin:
        print(f"\nin the engine literal only: {', '.join(only_builtin)}")
        print("  (fine — the registry wins where they overlap, the literal is the floor)")
    return 0


# --------------------------------------------------------------------------- #
# Entry point.
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Never called by the dashboard: fa_engine reads the registry, not this.",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", nargs="+", metavar="SYMBOL",
                   help="report which of these symbols have no mapping (no network)")
    g.add_argument("--resolve", metavar="SYMBOL",
                   help="ask CoinGecko for the candidates behind one unmapped symbol")
    g.add_argument("--add", nargs=2, metavar=("SYMBOL", "CG_ID"),
                   help="record a mapping a human has confirmed")
    g.add_argument("--show", action="store_true",
                   help="print the registry and the engine's built-in map")
    ap.add_argument("--file", type=Path,
                    help="read the symbol list from a file, one per line (with --check)")
    ap.add_argument("--force", action="store_true",
                    help="let --add replace an existing mapping")
    args = ap.parse_args(argv)

    symbols = list(args.check or [])
    if args.file:
        if not args.check:
            ap.error("--file goes with --check")
        if not args.file.is_file():
            print(f"no such file: {args.file}", file=sys.stderr)
            return 2
        symbols += [ln.strip().upper() for ln in
                    args.file.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.lstrip().startswith("#")]

    try:
        if symbols:
            seen: dict[str, None] = {}
            for s in symbols:
                seen.setdefault(s, None)
            return cmd_check(list(seen))
        if args.resolve:
            return cmd_resolve(args.resolve)
        if args.add:
            return add_mapping(args.add[0], args.add[1], args.force)
        if args.show:
            return cmd_show()
    except Fail as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    ap.error("nothing to do")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
