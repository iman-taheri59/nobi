"""The soak snapshot records aggregates and never writes on failed reads."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import live_evidence_snapshot as snapshot  # noqa: E402


def test_collect_and_append_are_secret_free(monkeypatch, tmp_path):
    token = "super-secret-token"

    def fake(url, presented="", timeout=20.0):
        assert presented == token
        if url.endswith("/health"):
            return {"version": "1.2.7"}
        if url.endswith("/status"):
            return {"mode": "dry-run", "configured": False}
        return {
            "ok": True, "samples": 31, "days": 5, "ambiguous": 0,
            "ready": True, "applied": False,
            "states": {"dry-run": 31}, "reasons": [],
        }

    monkeypatch.setattr(snapshot, "get_json", fake)
    row = snapshot.collect("http://example", token)
    path = tmp_path / "evidence.csv"
    snapshot.append_row(path, row)
    content = path.read_text(encoding="utf-8")
    assert token not in content
    rows = list(csv.DictReader(content.splitlines()))
    assert len(rows) == 1
    assert rows[0]["samples"] == "31"
    assert rows[0]["ready"] == "True"


def test_failed_collect_does_not_create_csv(monkeypatch, tmp_path):
    path = tmp_path / "evidence.csv"

    def failed(*_args, **_kwargs):
        raise RuntimeError("snapshot request failed: TimeoutError")

    monkeypatch.setattr(snapshot, "get_json", failed)
    assert snapshot.main(["--url", "http://bad", "--csv", str(path)]) == 1
    assert not path.exists()
