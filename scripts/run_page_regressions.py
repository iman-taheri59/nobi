#!/usr/bin/env python3
"""Run reviewed-page regressions in a private, offline process.

No deployment env files, real accounts, network or OS helpers. This is NOT the
server's devsuite endpoint. Requires the normal project/test dependencies.
Usage: python scripts/run_page_regressions.py -q
"""

from __future__ import annotations


def main() -> int:
    import os
    import socket
    import sys
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    repo = Path(__file__).resolve().parents[1]
    private = Path(tempfile.mkdtemp(prefix="nobi-page-tests-"))
    for key in list(os.environ):
        if key.startswith(("NBX_", "NOBITEX_")):
            del os.environ[key]
    for key, name in {
        "NBX_JOURNAL_DB": "journal.db",
        "NBX_AUTH_DB": "auth.db",
        "NBX_LIVE_DB": "live.db",
        "NBX_PAPER_STATE": "paper.json",
        "NBX_CANDLE_CACHE": "candles",
        "NBX_BT_DIR": "backtests",
        "NBX_EXPORT_DIR": "exports",
        "NBX_COIN_CACHE": "coin-cache",
        "NBX_MANAGED_ENV": "managed.env",
        "NBX_SETUP_TOKEN_FILE": "setup.token",
        "NBX_OPERATIONS_AUDIT": "operations.jsonl",
    }.items():
        os.environ[key] = str(private / name)
    os.environ.update(
        NBX_LIVE_MODE="disabled",
        NBX_COIN_METADATA_ENABLED="0",
        NBX_SCAN_LOOP_S="0",
        NBX_AUTO_INTERVAL_S="86400",
        PYTHONDONTWRITEBYTECODE="1",
    )
    sys.path[:0] = [str(repo / "server"), str(repo)]
    # Read the actual loader code but make its fixed deployment .env path absent.
    # Afterwards all loader defaults point at PRIVATE scratch paths.
    with patch.object(Path, "is_file", lambda self: False):
        import _env
    _env.ENV_PATH = private / ".env"
    _env.MANAGED_ENV_PATH = private / "managed.env"
    _env.STATUS.update(path=str(_env.ENV_PATH), exists=False, loaded=False, vars=0, error=None)
    _env.MANAGED_STATUS.update(
        path=str(_env.MANAGED_ENV_PATH), exists=False, loaded=False, vars=0, error=None
    )

    def no_network(*args, **kwargs):
        raise OSError("network disabled by isolated test runner")

    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.getaddrinfo = no_network
    # Helpers must be mocked by individual tests. Prevent accidental OS control.
    import subprocess

    real_popen = subprocess.Popen

    def guarded_popen(args, *a, **kw):
        head = str(args[0]) if isinstance(args, (tuple, list)) and args else str(args)
        if any(word in head for word in ("systemctl", "nobi-helper", "nobi-privileged", "sudo")):
            raise AssertionError("real OS helper forbidden in isolated tests")
        return real_popen(args, *a, **kw)

    subprocess.Popen = guarded_popen
    os.chdir(repo)
    import pytest

    tests = [
        "server/tests/test_review_phase1_regressions.py",
        "server/tests/test_review_phase2_regressions.py",
        "server/tests/test_review_phase3_regressions.py",
        "server/tests/test_api_conformance.py::test_a_partially_filled_market_order_is_not_reported_as_full",
        "server/tests/test_state_backup.py",
        "server/tests/test_operations_api.py",
        "server/tests/test_install_api.py",
        "server/tests/test_install_wizard.py",
        "server/tests/test_upgrade_rollback_contract.py",
        "server/tests/test_owner_settings_access.py",
        "server/tests/test_journal.py",
        "server/tests/test_signal_intelligence.py",
        "server/tests/test_signal_intelligence_v11.py",
        "server/tests/test_live_execution.py",
        "server/tests/test_release_version.py",
        "server/tests/test_docs_bilingual.py",
    ]
    return pytest.main([*tests, *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
