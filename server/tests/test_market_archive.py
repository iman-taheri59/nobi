"""The out-of-process market archiver: raw bodies, atomic files, bounded retention."""
import datetime as dt
import gzip
import importlib.util
import json
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "market_archive", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "market_archive.py")
assert _SPEC is not None and _SPEC.loader is not None
archive = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(archive)

NOW = dt.datetime(2026, 11, 5, 9, 30, tzinfo=dt.UTC)


def _fake(status_by_url):
    def fetcher(url):
        status, body = status_by_url[url]
        return status, body
    return fetcher


def test_each_endpoint_is_stored_raw_with_hash_and_time(tmp_path):
    body = b'{"status":"ok","stats":{"btc-rls":{"latest":"1"}}}'
    fetcher = _fake({archive.BASE + "/market/stats": (200, body),
                     archive.BASE + "/v3/orderbook/all": (200, b'{"status":"ok"}')})
    assert archive.run(tmp_path, fetcher=fetcher, now=NOW) == 0
    files = sorted((tmp_path / "2026-11-05").iterdir())
    assert [f.name for f in files] == ["orderbook-20261105T093000Z.json.gz", "stats-20261105T093000Z.json.gz"]
    with gzip.open(files[1], "rb") as fh:
        env = json.loads(fh.read())
    assert env["schema"] == "nobi.market-archive.v1"
    assert env["body"] == body.decode()
    assert env["sha256"] == __import__("hashlib").sha256(body).hexdigest()
    assert env["fetched_at"] == "2026-11-05T09:30:00+00:00"
    assert not list(tmp_path.glob("**/*.tmp"))


def test_a_403_or_network_error_is_recorded_not_hidden(tmp_path):
    def fetcher(url):
        if url.endswith("/market/stats"):
            return 403, b"<html>blocked</html>"
        raise TimeoutError("timed out")
    assert archive.run(tmp_path, fetcher=fetcher, now=NOW) == 1
    with gzip.open(tmp_path / "2026-11-05" / "stats-20261105T093000Z.json.gz") as fh:
        assert json.loads(fh.read())["status"] == 403
    with gzip.open(tmp_path / "2026-11-05" / "orderbook-20261105T093000Z.json.gz") as fh:
        ob = json.loads(fh.read())
    assert ob["status"] == 0 and "TimeoutError" in ob["body"]


def test_prune_only_removes_whole_days_outside_the_window(tmp_path):
    # keep_days=5 on 11-05 keeps 10-31 (the boundary day) and removes 10-30 and older.
    for name in ("2026-10-01", "2026-10-30", "2026-10-31", "2026-11-05", "notes", "2026-13-99"):
        (tmp_path / name).mkdir()
    (tmp_path / "README").write_text("x", encoding="utf-8")
    removed = archive.prune(tmp_path, keep_days=5, today=dt.date(2026, 11, 5))
    assert [p.name for p in removed] == ["2026-10-01", "2026-10-30"]
    assert (tmp_path / "2026-10-31").exists()
    assert (tmp_path / "2026-11-05").exists() and (tmp_path / "notes").exists()
    assert (tmp_path / "2026-13-99").exists() and (tmp_path / "README").exists()
    assert archive.prune(tmp_path, keep_days=0, today=dt.date(2026, 11, 5)) == []


def test_user_agent_follows_the_service_convention(monkeypatch):
    monkeypatch.delenv("NBX_UA", raising=False)
    assert archive.user_agent() == "TraderBot/nobi-archive"
    monkeypatch.setenv("NBX_UA", "capoy")
    assert archive.user_agent() == "TraderBot/capoy-archive"
    monkeypatch.setenv("NBX_UA", "TraderBot/custom")
    assert archive.user_agent() == "TraderBot/custom"
