"""Historical ingestion is paginated, resumable and honest about gaps."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_history as history  # noqa: E402


def _payload(timestamps):
    times = list(timestamps)
    return {"s": "ok", "t": times, "o": times, "h": times,
            "l": times, "c": times, "v": [1] * len(times)}


def test_download_pages_deduplicates_and_reports_gaps(tmp_path):
    first = [1000 + i * 60 for i in range(500)]
    second = [first[-1], first[-1] + 120]
    requested = []

    def fetch(params):
        requested.append(params)
        return _payload(first if params["page"] == 1 else second)

    result = history.download("btcirt", "1", 1000, second[-1], fetch,
                              checkpoint=tmp_path / "checkpoint.json", pause_s=0)
    assert result.pages == 2
    assert len(result.rows) == 501  # overlapping boundary candle appears once
    assert requested[0]["symbol"] == "BTCIRT"
    assert [item["page"] for item in requested] == [1, 2]
    assert result.gaps == [{"after": first[-1], "before": second[-1], "missing": 1}]


def test_checkpoint_resumes_at_next_page(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "symbol": "BTCIRT", "resolution": "1", "from": 1000, "to": 2000,
        "next_page": 3, "rows": [{"t": 1000, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
    }), encoding="utf-8")
    pages = []

    def fetch(params):
        pages.append(params["page"])
        return _payload([1060])

    result = history.download("BTCIRT", "1", 1000, 2000, fetch,
                              checkpoint=checkpoint, pause_s=0)
    assert pages == [3]
    assert [row["t"] for row in result.rows] == [1000, 1060]


def test_malformed_udf_arrays_are_refused():
    try:
        history.udf_rows({"s": "ok", "t": [1], "o": [], "h": [1],
                          "l": [1], "c": [1], "v": [1]})
    except ValueError as exc:
        assert "different lengths" in str(exc)
    else:
        raise AssertionError("misaligned OHLC arrays must never be zipped silently")


def test_no_data_is_a_valid_empty_result():
    assert history.udf_rows({"s": "no_data"}) == []
