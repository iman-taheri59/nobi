# mypy: check_untyped_defs = False
"""Private-runner-only log/diagnostic regressions. No real log, account or network."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from collections import deque

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from test_install_api import _owner_client

import diagnostics as D
import errorlog as L
import journal as J
import main


@pytest.fixture()
def store(tmp_path, monkeypatch):
    db = tmp_path / "log.db"
    monkeypatch.setattr(J, "DB_PATH", db)
    monkeypatch.setattr(L, "_memory", deque(maxlen=L.MAX_MEMORY))
    monkeypatch.setattr(L, "_seq", 0)
    c = J.connect(db)
    L._init(c)
    c.close()
    return db


class RejectInsert:
    def __init__(self, c):
        self.c = c
    def __getattr__(self, name):
        return getattr(self.c, name)
    def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO error_log"):
            raise sqlite3.OperationalError("fixture insert refused")
        return self.c.execute(sql, params)


def deny_writes(monkeypatch):
    real = J.connect
    monkeypatch.setattr(J, "connect", lambda *a, **k: RejectInsert(real(*a, **k)))


def test_failed_insert_is_visible_even_when_select_succeeds_empty(store, monkeypatch):
    deny_writes(monkeypatch)
    row = L.capture(source="fixture", message="memory-only")
    p = L.snapshot()
    assert row["id"] < 0 and row["persisted"] is False
    assert p["counts"]["total"] == p["filtered_count"] == p["n"] == 1
    assert p["errors"][0]["message"] == "memory-only"
    assert p["storage"]["backend"] == "sqlite+pending-memory"
    assert p["storage"]["degraded"] and p["storage"]["sqlite_readable"]


def test_sql_and_ring_do_not_duplicate_persisted_rows(store, monkeypatch):
    a = L.capture(message="persisted")
    deny_writes(monkeypatch)
    b = L.capture(message="pending")
    p = L.snapshot()
    assert a["id"] > 0 > b["id"]
    assert p["counts"]["total"] == 2
    assert {r["message"] for r in p["errors"]} == {"persisted", "pending"}
    assert p["storage"]["pending_memory"] == 1


def test_sql_primary_does_not_resurrect_deleted_rows(store):
    L.capture(message="not resurrected")
    c = sqlite3.connect(store)
    c.execute("DELETE FROM error_log")
    c.commit()
    c.close()
    p = L.snapshot()
    assert p["errors"] == [] and p["counts"]["total"] == 0


@pytest.mark.parametrize("since", [0, 101, 150000])
@pytest.mark.parametrize("fail_read", [False, True])
def test_since_filter_has_identical_meaning_in_memory_and_sql(store, monkeypatch, since, fail_read):
    monkeypatch.setattr(L.time, "time", lambda: 100)
    L.capture(message="old")
    if fail_read:
        monkeypatch.setattr(J, "connect_readonly", lambda *a, **k: (_ for _ in ()).throw(OSError("read refused")))
    p = L.snapshot(since=since)
    assert p["n"] == (1 if since <= 100 else 0)
    assert p["counts"]["total"] == 1  # observed total, not filtered count
    assert p["filtered_count"] == p["n"]
    assert p["storage"]["sqlite_readable"] is (not fail_read)


@pytest.mark.parametrize("q, count", [("%",1),("_",1),("MiXeD",1),("missing",0),("نمونه",1)])
@pytest.mark.parametrize("memory", [False, True])
def test_literal_case_insensitive_search_matches_on_both_backends(store, monkeypatch, q, count, memory):
    L.capture(message="100% mixed نمونه")
    L.capture(message="under_score")
    if memory:
        monkeypatch.setattr(J, "connect_readonly", lambda *a, **k: (_ for _ in ()).throw(OSError("read refused")))
    p = L.snapshot(q=q)
    assert p["n"] == count
    assert p["counts"]["total"] == 2


def test_snapshot_counts_filter_limits_and_storage_are_one_cohort(store):
    for i in range(7):
        L.capture(level="error" if i < 4 else "warning", source="fixture", message=f"event {i}")
    p = L.snapshot(limit=2, level="error")
    assert p["counts"]["total"] == 7 and p["counts"]["warning"] == 3
    assert p["filtered_count"] == 4 and p["filtered_counts"]["error"] == 4
    assert p["n"] == 2 and p["truncated"]
    assert not p["storage"]["degraded"] and p["storage"]["backend"] == "sqlite"
    assert p["storage"]["durability_verified"] is False


def test_read_does_not_initialize_missing_db_or_schema(tmp_path, monkeypatch):
    db = tmp_path / "absent" / "log.db"
    monkeypatch.setattr(J, "DB_PATH", db)
    monkeypatch.setattr(L, "_memory", deque(maxlen=500))
    p = L.snapshot()
    assert not db.parent.exists() and p["storage"]["degraded"]
    assert p["storage"]["backend"] == "memory" and p["errors"] == []


def test_read_cap_is_visible_instead_of_unbounded_read_or_claimed_full_counts(store):
    c = sqlite3.connect(store)
    c.executemany("INSERT INTO error_log(ts,level,source,message) VALUES(100,'info','fixture',?)",
                  [(str(i),) for i in range(L.MAX_DB+2)])
    c.commit()
    c.close()
    p = L.snapshot()
    assert p["counts"]["total"] == L.MAX_DB
    assert p["storage"]["db_read_truncated"] and p["storage"]["degraded"]


def test_unique_sqlite_selftest_succeeds_without_claiming_crash_durability(store):
    a, b = L.self_test(), L.self_test()
    assert a["ok"] and a["readable"] and a["writable"] and a["sqlite_verified"]
    assert a["wrote"] != b["wrote"] and a["storage"] == "sqlite"
    assert a["durability_verified"] is False
    messages = [r["message"] for r in L.recent()]
    assert len(set(messages)) == 2 and all("self-test marker " in m for m in messages)


def test_old_id_collision_cannot_pass_selftest(store, monkeypatch):
    c = sqlite3.connect(store)
    c.execute("INSERT INTO error_log(id,ts,level,source,message) VALUES(1,1,'info','old.fixture','not a marker')")
    c.commit()
    c.close()
    deny_writes(monkeypatch)
    r = L.self_test()
    assert r["wrote"] < 0 and not r["writable"] and not r["sqlite_verified"] and not r["readable"]
    assert r["storage"] == "memory"
    assert asyncio.run(D.check_errorlog(D.Ctx())).status == "fail"


def test_written_but_unreadable_selftest_is_not_success(store, monkeypatch):
    monkeypatch.setattr(J, "connect_readonly", lambda *a, **k: (_ for _ in ()).throw(OSError("read refused")))
    r = L.self_test()
    assert r["writable"] and not r["sqlite_verified"] and not r["ok"]
    assert r["storage"] == "sqlite-unverified"


def test_clear_failure_preserves_memory_and_returns_route_failure(store, monkeypatch):
    L.capture(message="retain me")
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("fixture unavailable")
    monkeypatch.setattr(J, "connect", fail)
    with pytest.raises(sqlite3.OperationalError):
        L.clear()
    assert len(L._memory) == 1
    with pytest.raises(HTTPException) as err:
        asyncio.run(main.errors_clear(Request({"type":"http", "headers":[]})))
    assert err.value.status_code == 503 and len(L._memory) == 1


def test_clear_success_only_on_private_store(store):
    L.capture(message="fixture remove")
    assert L.clear() == 1
    assert L.snapshot()["counts"]["total"] == 0 and not L._memory


@pytest.mark.parametrize("key", ["api_key","api-key","password","Authorization","Cookie","session_id","NBX_API_TOKEN","NOBITEX_API_SECRET","private_key"])
def test_recursive_structured_secrets_are_redacted(key):
    sentinel = "NONSECRET-FIXTURE-SENTINEL"
    value = {"nested":[{key:sentinel}],"safe":"keep me"}
    result = L.scrub(value)
    assert sentinel not in result and "keep me" in result and "[redacted]" in result
    assert json.loads(result)["safe"] == "keep me"
    assert sentinel not in L.scrub(json.dumps(value))
    assert sentinel not in L.scrub(repr(value))


@pytest.mark.parametrize("field", ["source","message","detail","client"])
def test_all_text_fields_scrub_on_write_and_legacy_read(store, field):
    sentinel = "NONSECRET-FIXTURE-SENTINEL"
    args = {field:"password="+sentinel}
    r = L.capture(**args)
    assert sentinel not in str(r)
    c = sqlite3.connect(store)
    c.execute(f"UPDATE error_log SET {field}=? WHERE id=?", ("api_key="+sentinel,r["id"]))
    c.commit()
    raw = c.execute(f"SELECT {field} FROM error_log WHERE id=?",(r["id"],)).fetchone()[0]
    c.close()
    assert sentinel in raw  # legacy bytes remain; no historical rewrite
    assert sentinel not in json.dumps(L.snapshot())


def test_cycle_and_oversized_context_are_bounded():
    value = {"safe":"ok"}
    value["cycle"] = value
    result = L.scrub(value)
    assert len(result)<=4000 and "truncated" in result
    assert len(L.scrub({str(i):"x"*5000 for i in range(1000)}))<=4000


def test_journal_diagnostic_is_readonly_and_never_advances_stage(store, monkeypatch):
    c = J.connect(store)
    J.set_meta(c,"strategy_stage",{"stage":"run","streak":2,"msi_status":"degraded"})
    before = [tuple(x) for x in c.execute("SELECT * FROM journal_meta")]
    def forbidden(*args, **kwargs):
        raise AssertionError("journal diagnostic must not evaluate/init/write")
    monkeypatch.setattr(J,"connect",forbidden)
    monkeypatch.setattr(J,"init",forbidden)
    monkeypatch.setattr(J,"lifecycle",forbidden)
    for _ in range(4):
        r = asyncio.run(D.check_journal(D.Ctx()))
        assert r.status=="ok" and r.data["read_only"] and r.data["stage"]=="run"
    assert [tuple(x) for x in c.execute("SELECT * FROM journal_meta")] == before
    c.close()


def test_owner_route_returns_scoped_metadata_and_keeps_csrf(store, monkeypatch, tmp_path):
    client,csrf = _owner_client(monkeypatch,tmp_path)
    L.capture(message="fixture needle",context={"api_key":"NONSECRET-FIXTURE-SENTINEL"})
    r=client.get("/api/errors?limit=1&q=needle").json()
    assert r["n"]==1 and r["filtered_count"]==1 and r["storage"]["sqlite_readable"]
    assert r["filters"]["q"]=="needle" and "NONSECRET-FIXTURE-SENTINEL" not in json.dumps(r)
    assert client.post("/api/errors/selftest").status_code==403
    r=client.post("/api/errors/selftest",headers={"X-CSRF-Token":csrf}).json()
    assert r["sqlite_verified"] is True
    client.close()


def test_memory_ring_never_crosses_configured_store_paths(store, monkeypatch, tmp_path):
    deny_writes(monkeypatch)
    L.capture(message="first store memory only")
    other = tmp_path / "other.db"
    monkeypatch.setattr(J, "DB_PATH", other)
    assert L.snapshot()["errors"] == []
    monkeypatch.setattr(J, "DB_PATH", store)
    assert L.snapshot()["errors"][0]["message"] == "first store memory only"
    assert "_store" not in json.dumps(L.snapshot())


def test_delete_refusal_after_successful_read_preserves_ring(store, monkeypatch):
    L.capture(message="keep after refused delete")
    real = J.connect
    class RefuseDelete(RejectInsert):
        def execute(self, sql, params=()):
            if sql.startswith("DELETE FROM error_log"):
                raise sqlite3.OperationalError("fixture delete refused")
            return self.c.execute(sql, params)
    monkeypatch.setattr(J,"connect",lambda *a,**k: RefuseDelete(real(*a,**k)))
    with pytest.raises(sqlite3.OperationalError):
        L.clear()
    assert len(L._memory)==1 and L.snapshot()["counts"]["total"]==1


def test_unterminated_large_quoted_sensitive_value_is_not_echoed():
    result = L.scrub('{"password":"NONSECRET-FIXTURE-SENTINEL' + 'x'*40000)
    assert "NONSECRET-FIXTURE-SENTINEL" not in result and "[redacted]" in result
    assert len(result)<=4000


def test_global_and_filtered_day_counts_use_one_snapshot_clock(store, monkeypatch):
    c = sqlite3.connect(store)
    c.execute("INSERT INTO error_log(ts,level,source,message) VALUES(100,'info','fixture','boundary')")
    c.commit()
    c.close()
    times = iter([86500,86501,86502])
    monkeypatch.setattr(L.time,"time",lambda: next(times))
    p=L.snapshot()
    assert p["generated_at"]==86500
    assert p["counts"]["last_24h"]==p["filtered_counts"]["last_24h"]==1


def test_clear_counts_pending_memory_once_without_double_counting_sql(store, monkeypatch):
    L.capture(message="persisted event")
    deny_writes(monkeypatch)
    L.capture(message="unpersisted event")
    assert L.snapshot()["counts"]["total"]==2
    assert L.clear()==2
    assert L.snapshot()["counts"]["total"]==0
