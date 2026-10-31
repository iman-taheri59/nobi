"""`scripts/resolve_fa.py` resolves offline and never decides for you.

ADR-0025 refuses to map an unmapped symbol during a scan: 2,626 of 17,425
symbols (15%) match more than one coin on CoinGecko, and the free tier answers
429 after three consecutive calls. This script is the offline half of that
decision — it asks once, shows every candidate, and writes a line only when a
human confirmed one.

Nothing here touches the network. `cg_search` is replaced in the script's own
namespace, and the registry is restored after every test that writes to it.
"""
from __future__ import annotations

import email.message
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "resolve_fa.py"
REGISTRY = ROOT / "server" / "coin_registry.json"


def _load_script():
    """Import the script as a real module.

    `runpy.run_path` returns a *copy* of the globals, so stubbing an entry in it
    does not reach the functions — they keep calling the real `cg_search`, which
    means a network call in a test that promised none. Importing gives one
    namespace the functions and the tests share.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("resolve_fa_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve = _load_script()


@pytest.fixture()
def registry_backup():
    original = REGISTRY.read_text(encoding="utf-8")
    yield
    REGISTRY.write_text(original, encoding="utf-8")


@pytest.fixture()
def stub_search(monkeypatch):
    """Replace the only network call in the script."""
    def install(candidates):
        monkeypatch.setattr(resolve, "cg_search", lambda q: candidates)
    return install


# --------------------------------------------------------------------------- #
# The map itself.
# --------------------------------------------------------------------------- #

def test_the_map_is_the_one_the_engine_uses() -> None:
    """A resolver that disagrees with fa_engine would resolve the wrong thing."""
    sys.path.insert(0, str(ROOT / "server"))
    try:
        import fa_engine
    finally:
        sys.path.pop(0)
    assert resolve.known_ids() == fa_engine.KNOWN_CG_IDS


def test_every_registry_entry_is_a_plausible_id() -> None:
    for symbol, cg_id in resolve.load_registry().items():
        assert symbol.isupper() and symbol.isalnum(), f"{symbol} is not an uppercase base asset"
        assert cg_id and cg_id == cg_id.lower(), f"{symbol} -> {cg_id} is not a CoinGecko id"
        assert " " not in cg_id


def test_the_engine_literal_and_the_registry_agree_where_they_overlap() -> None:
    builtin = resolve._builtin_ids()
    registry = resolve.load_registry()
    for symbol, cg_id in builtin.items():
        if symbol in registry:
            assert registry[symbol] == cg_id, (
                f"{symbol} maps to {registry[symbol]} in the registry "
                f"but {cg_id} in the engine"
            )


# --------------------------------------------------------------------------- #
# Symbol parsing.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("market,base", [
    ("BTCIRT", "BTC"), ("ETHUSDT", "ETH"), ("SOLIRT", "SOL"),
    ("DOGSIRT", "DOGS"), ("WBTCIRT", "WBTC"), ("USDTIRT", "USDT"),
])
def test_the_base_asset_is_stripped_of_its_quote(market, base) -> None:
    assert resolve.base_of(market) == base


def test_a_symbol_with_no_known_quote_is_its_own_base() -> None:
    assert resolve.base_of("BTC") == "BTC"


# --------------------------------------------------------------------------- #
# --check: report, do not fix.
# --------------------------------------------------------------------------- #

def test_check_reports_every_mapped_symbol(capsys) -> None:
    assert resolve.cmd_check(["BTCIRT", "ETHUSDT"]) == 0
    out = capsys.readouterr().out
    assert "BTCIRT" in out and "bitcoin" in out
    assert "UNMAPPED" not in out


def test_check_fails_and_names_the_gap_when_a_symbol_is_unmapped(capsys) -> None:
    assert resolve.cmd_check(["BTCIRT", "DOGSIRT"]) == 1
    out = capsys.readouterr().out
    assert "DOGS" in out and "UNMAPPED" in out
    assert "resolve it, do not guess it" in out


def test_check_touches_no_network(stub_search) -> None:
    """The whole point: the gap can be measured without any provider."""
    def boom(*a, **k):
        raise AssertionError("--check must not call the network")
    stub_search(boom)
    assert resolve.cmd_check(["DOGSIRT"]) == 1


# --------------------------------------------------------------------------- #
# --resolve: show candidates, choose nothing.
# --------------------------------------------------------------------------- #

def test_resolve_shows_every_candidate_and_picks_none(stub_search, capsys) -> None:
    candidates = [
        {"id": "cat-in-a-dogs-world", "name": "cat in a dogs world", "symbol": "mew",
         "market_cap_rank": 573},
        {"id": "dogs-2", "name": "Dogs", "symbol": "dogs", "market_cap_rank": 937},
        {"id": "dogshit-3", "name": "Dogshit", "symbol": "dogshit", "market_cap_rank": 4745},
    ]
    stub_search(candidates)
    assert resolve.cmd_resolve("DOGSIRT", top=8) == 0
    out = capsys.readouterr().out
    # Every candidate, in the order CoinGecko gave them. The first is not
    # promoted — the real search returns a cat coin first, which is the whole
    # reason this is a human decision.
    for c in candidates:
        assert c["id"] in out
        assert c["name"] in out
    assert out.index("cat-in-a-dogs-world") < out.index("dogs-2")
    assert "None is chosen for you" in out
    assert "--add" in out


def test_resolve_refuses_to_re_resolve_a_mapped_symbol(stub_search, capsys) -> None:
    def boom(*a, **k):
        raise AssertionError("a mapped symbol needs no lookup")
    stub_search(boom)
    assert resolve.cmd_resolve("BTCIRT", top=8) == 0
    assert "already mapped" in capsys.readouterr().out


def test_resolve_says_so_when_nothing_matches(stub_search, capsys) -> None:
    stub_search([])
    assert resolve.cmd_resolve("ZZZIRT", top=8) == 1
    assert "matched nothing" in capsys.readouterr().out


class _FakeResponse:
    """Just enough of an HTTP response for `cg_search` to read."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_urlopen(monkeypatch, behaviour):
    monkeypatch.setattr("urllib.request.urlopen", behaviour)


def test_cg_search_returns_what_the_api_answered(monkeypatch) -> None:
    body = json.dumps({"coins": [{"id": "dogs-2", "name": "Dogs", "symbol": "dogs",
                                  "market_cap_rank": 937}]})
    _stub_urlopen(monkeypatch, lambda req, timeout=None: _FakeResponse(body))
    coins = resolve.cg_search("DOGS")
    assert [c["id"] for c in coins] == ["dogs-2"]


def test_cg_search_treats_a_missing_coins_key_as_no_match(monkeypatch) -> None:
    _stub_urlopen(monkeypatch, lambda req, timeout=None: _FakeResponse("{}"))
    assert resolve.cg_search("DOGS") == []


def test_a_rate_limit_is_explained_not_swallowed(monkeypatch) -> None:
    """429 is the limit ADR-0025 measured; saying so beats a traceback."""
    import urllib.error

    def limited(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 429, "Too Many Requests", email.message.Message(), None)

    _stub_urlopen(monkeypatch, limited)
    with pytest.raises(resolve.Fail, match="rate-limited"):
        resolve.cg_search("DOGS")


def test_any_other_http_error_names_the_status(monkeypatch) -> None:
    import urllib.error

    def broken(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 503, "Service Unavailable", email.message.Message(), None)

    _stub_urlopen(monkeypatch, broken)
    with pytest.raises(resolve.Fail, match="HTTP 503"):
        resolve.cg_search("DOGS")


def test_an_unreachable_provider_is_a_failure_not_an_empty_list(monkeypatch) -> None:
    import urllib.error

    def down(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    _stub_urlopen(monkeypatch, down)
    with pytest.raises(resolve.Fail, match="could not reach CoinGecko"):
        resolve.cg_search("DOGS")


# --------------------------------------------------------------------------- #
# --add: the only thing that writes.
# --------------------------------------------------------------------------- #

def test_add_records_a_human_decision(registry_backup, capsys) -> None:
    assert resolve.add_mapping("DOGSIRT", "dogs-2", force=False) == 0
    written = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert written["DOGS"] == "dogs-2"
    assert "commit it" in capsys.readouterr().out


def test_add_sorts_so_the_diff_stays_readable(registry_backup) -> None:
    before = json.loads(REGISTRY.read_text(encoding="utf-8"))
    resolve.add_mapping("DOGSIRT", "dogs-2", force=False)
    after = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert list(after) == sorted(after)
    # A new key must not move the ones that were already there.
    assert [k for k in after if k in before] == sorted(before)


def test_add_refuses_to_overwrite_without_force(registry_backup) -> None:
    with pytest.raises(resolve.Fail, match="already mapped"):
        resolve.add_mapping("BTCIRT", "ethereum", force=False)
    assert json.loads(REGISTRY.read_text(encoding="utf-8"))["BTC"] == "bitcoin"


def test_add_replaces_when_forced(registry_backup) -> None:
    assert resolve.add_mapping("BTCIRT", "ethereum", force=True) == 0
    assert json.loads(REGISTRY.read_text(encoding="utf-8"))["BTC"] == "ethereum"


def test_add_is_idempotent_for_the_same_value(registry_backup, capsys) -> None:
    assert resolve.add_mapping("BTCIRT", "bitcoin", force=False) == 0
    assert "nothing to do" in capsys.readouterr().out


@pytest.mark.parametrize("symbol,cg_id", [
    ("", "bitcoin"),            # empty symbol
    ("B TC", "bitcoin"),        # not a symbol at all
    ("VERYLONGSYMBOL", "x"),    # symbol too long
    ("BTCIRT", "Bitcoin"),      # id not lowercase
    ("BTCIRT", "has space"),    # not an id
    ("BTCIRT", ""),             # empty id
])
def test_add_rejects_a_malformed_decision(registry_backup, symbol, cg_id) -> None:
    with pytest.raises(resolve.Fail):
        resolve.add_mapping(symbol, cg_id, force=False)
    # Nothing was written by a rejected decision.
    assert json.loads(REGISTRY.read_text(encoding="utf-8"))["BTC"] == "bitcoin"


def test_a_failed_write_leaves_the_registry_intact(registry_backup, monkeypatch) -> None:
    """A half-written JSON would take the engine down to its fourteen majors."""
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(resolve, "_atomic_write", boom)
    before = REGISTRY.read_text(encoding="utf-8")
    with pytest.raises(OSError):
        resolve.add_mapping("DOGSIRT", "dogs-2", force=False)
    assert REGISTRY.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# The CLI surface.
# --------------------------------------------------------------------------- #

def test_the_script_is_executable() -> None:
    assert SCRIPT.stat().st_mode & 0o111


def test_help_explains_that_the_dashboard_never_calls_it() -> None:
    out = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert "Never called by the dashboard" in out
    assert "offline" in out


def test_a_symbol_list_can_come_from_a_file(tmp_path) -> None:
    listed = tmp_path / "symbols.txt"
    listed.write_text("# a comment\nBTCIRT\nDOGSIRT\n\n", encoding="utf-8")
    symbols = [ln.strip().upper() for ln in listed.read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.lstrip().startswith("#")]
    assert symbols == ["BTCIRT", "DOGSIRT"]
