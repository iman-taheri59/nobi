#!/usr/bin/env python3
"""Download paginated Nobitex OHLC history without inventing missing candles."""
from __future__ import annotations

import argparse
import itertools
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BASE_URL = "https://apiv2.nobitex.ir"
MAX_PER_PAGE = 500
RESOLUTION_SECONDS = {
    "1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600,
    "180": 10800, "240": 14400, "360": 21600, "720": 43200,
    "D": 86400, "1D": 86400, "2D": 172800, "3D": 259200,
}


@dataclass(frozen=True)
class DownloadResult:
    rows: list[dict[str, float | int]]
    gaps: list[dict[str, int]]
    pages: int


def parse_time(value: str) -> int:
    if value.isdigit():
        return int(value)
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=UTC).timestamp())


def udf_rows(payload: dict) -> list[dict[str, float | int]]:
    if payload.get("s") == "no_data":
        return []
    if payload.get("s") != "ok":
        raise ValueError(f"UDF response is not usable: {payload.get('errmsg') or payload.get('s')}")
    keys = ("t", "o", "h", "l", "c", "v")
    lengths = {len(payload.get(key, [])) for key in keys}
    if len(lengths) != 1:
        raise ValueError("UDF arrays have different lengths")
    return [
        {"t": int(payload["t"][i]), "o": float(payload["o"][i]),
         "h": float(payload["h"][i]), "l": float(payload["l"][i]),
         "c": float(payload["c"][i]), "v": float(payload["v"][i])}
        for i in range(next(iter(lengths), 0))
    ]


def find_gaps(rows: list[dict[str, float | int]], step: int) -> list[dict[str, int]]:
    gaps: list[dict[str, int]] = []
    timestamps = sorted({int(row["t"]) for row in rows})
    for previous, current in itertools.pairwise(timestamps):
        if current - previous > step:
            gaps.append({"after": previous, "before": current,
                         "missing": (current - previous) // step - 1})
    return gaps


def download(
    symbol: str,
    resolution: str,
    from_ts: int,
    to_ts: int,
    fetch_page: Callable[[dict[str, str | int]], dict],
    *,
    checkpoint: Path | None = None,
    pause_s: float = 1.05,
    max_pages: int = 10000,
) -> DownloadResult:
    if resolution not in RESOLUTION_SECONDS:
        raise ValueError(f"unsupported resolution: {resolution}")
    if from_ts >= to_ts:
        raise ValueError("from must be earlier than to")
    collected: dict[int, dict[str, float | int]] = {}
    start_page = 1
    if checkpoint and checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        identity = (saved.get("symbol"), saved.get("resolution"), saved.get("from"), saved.get("to"))
        if identity != (symbol.upper(), resolution, from_ts, to_ts):
            raise ValueError("checkpoint belongs to another request")
        start_page = int(saved.get("next_page", 1))
        collected = {int(row["t"]): row for row in saved.get("rows", [])}

    pages = 0
    for page in range(start_page, max_pages + 1):
        payload = fetch_page({"symbol": symbol.upper(), "resolution": resolution,
                              "from": from_ts, "to": to_ts, "page": page})
        batch = udf_rows(payload)
        pages += 1
        for row in batch:
            timestamp = int(row["t"])
            if from_ts <= timestamp <= to_ts:
                collected[timestamp] = row
        if checkpoint:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            partial = checkpoint.with_suffix(checkpoint.suffix + ".partial")
            partial.write_text(json.dumps({
                "symbol": symbol.upper(), "resolution": resolution,
                "from": from_ts, "to": to_ts, "next_page": page + 1,
                "rows": sorted(collected.values(), key=lambda row: int(row["t"])),
            }, ensure_ascii=False), encoding="utf-8")
            partial.replace(checkpoint)
        if len(batch) < MAX_PER_PAGE:
            break
        if pause_s:
            time.sleep(pause_s)
    else:
        raise RuntimeError(f"history exceeded max_pages={max_pages}")

    rows = sorted(collected.values(), key=lambda row: int(row["t"]))
    return DownloadResult(rows, find_gaps(rows, RESOLUTION_SECONDS[resolution]), pages)


def write_result(path: Path, result: DownloadResult, metadata: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata, "gaps": result.gaps, "rows": result.rows}
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--resolution", required=True, choices=RESOLUTION_SECONDS)
    parser.add_argument("--from", dest="from_value", required=True)
    parser.add_argument("--to", dest="to_value", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--pause", type=float, default=1.05)
    args = parser.parse_args()
    import httpx

    client = httpx.Client(timeout=20, headers={"User-Agent": "TraderBot/nobitrader-history-1.2"})

    def fetch(params: dict[str, str | int]) -> dict:
        response = client.get(f"{args.base_url.rstrip('/')}/market/udf/history", params=params)
        response.raise_for_status()
        return dict(response.json())

    from_ts, to_ts = parse_time(args.from_value), parse_time(args.to_value)
    result = download(args.symbol, args.resolution, from_ts, to_ts, fetch,
                      checkpoint=args.checkpoint, pause_s=max(0.0, args.pause))
    write_result(args.output, result, {
        "symbol": args.symbol.upper(), "resolution": args.resolution,
        "from": from_ts, "to": to_ts, "source": "nobitex-udf",
        "price_unit": "exchange-native-rial-for-IRT-markets",
        "pages": result.pages, "downloaded_at": datetime.now(UTC).isoformat(),
    })
    if args.checkpoint and args.checkpoint.exists():
        args.checkpoint.unlink()
    print(f"{len(result.rows)} candles, {len(result.gaps)} gaps -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
