"""The downloadable ZIP is generated from the current checkout, not a stale binary."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

import main
import package_bundle
from security import reset_rate_limits


def test_bundle_excludes_secrets_and_runtime_state(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    (root / "server").mkdir(parents=True)
    (root / "server" / "__pycache__").mkdir()
    (root / "node_modules").mkdir()
    (root / "server" / "candle_cache").mkdir()
    (root / "README.md").write_text("current readme", encoding="utf-8")
    (root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (root / "nbx-app.js").write_text("/* code */", encoding="utf-8")
    (root / "server" / "main.py").write_text("x = 1\n", encoding="utf-8")
    for secret in (".env", "server/auth.db", "server/setup.token",
                   "server/operations-audit.jsonl", "server/managed.env"):
        (root / secret).write_text("TOP-SECRET", encoding="utf-8")
    (root / "server" / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"\x00")
    (root / "node_modules" / "lib.js").write_text("lib", encoding="utf-8")
    (root / "server" / "candle_cache" / "BTC.parquet").write_bytes(b"\x00")
    (root / "nobitex-trader-robat.zip").write_bytes(b"stale snapshot")

    names = [rel for rel, _ in package_bundle.iter_files(root)]
    assert "README.md" in names and "server/main.py" in names and "nbx-app.js" in names
    assert not any(name.endswith((".db", ".jsonl", ".pyc", ".parquet")) for name in names)
    assert not any("node_modules" in name or "__pycache__" in name or "candle_cache" in name
                   for name in names)
    for forbidden in (".env", "server/auth.db", "server/setup.token",
                      "server/operations-audit.jsonl", "server/managed.env",
                      "nobitex-trader-robat.zip"):
        assert forbidden not in names, forbidden

    data = package_bundle.build_archive(root)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        contents = zf.namelist()
        assert "README-DOWNLOAD.txt" in contents
        assert "version: 9.9.9" in zf.read("README-DOWNLOAD.txt").decode()
    assert b"TOP-SECRET" not in data


def test_download_route_serves_a_fresh_zip() -> None:
    reset_rate_limits()
    client = TestClient(main.app)
    response = client.get("/download/nobitex-trader-robat.zip")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = zf.namelist()
        assert "README.md" in names and "README-DOWNLOAD.txt" in names
        assert "server/main.py" in names
        assert not any(n.endswith(".db") or n.endswith(".env") for n in names)
        assert zf.read("README.md") == (package_bundle.ROOT / "README.md").read_bytes()
