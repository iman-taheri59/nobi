"""Product name consistency without renaming deployment paths or the exchange."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import main

ROOT = Path(__file__).resolve().parents[2]
NAME = "nobiTrader"


@pytest.mark.parametrize("page", ["index.html", "nobitex-mobile.html"])
def test_page_and_mobile_install_titles(page):
    html = (ROOT / page).read_text(encoding="utf-8")
    assert f"<title>{NAME}</title>" in html
    assert f'<meta name="apple-mobile-web-app-title" content="{NAME}">' in html
    assert f'<meta name="application-name" content="{NAME}">' in html


@pytest.mark.parametrize("file", ["index.html", "nbx-app.js", "nobitex-mobile.html"])
def test_all_locale_titles_and_overrides_use_exact_product_name(file):
    text = (ROOT / file).read_text(encoding="utf-8")
    titles = re.findall(r"'app.title'\s*:\s*'([^']+)'", text)
    assert len(titles) >= 2
    assert set(titles) == {NAME}


def test_manifest_long_and_short_names():
    data = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert data["name"] == data["short_name"] == NAME
    assert data["start_url"] == "./index.html"  # existing installed route stays valid


def test_api_documentation_title():
    assert main.app.title == NAME
    assert main.app.openapi()["info"]["title"] == NAME


def test_existing_mobile_url_downloads_with_new_product_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    response = TestClient(main.app).get("/download/nobitex-mobile.html")
    assert response.status_code == 200
    assert 'filename="nobiTrader-mobile.html"' in response.headers["content-disposition"]
    assert "<title>nobiTrader</title>" in response.text
    auth.reset_caches()


def test_exchange_names_and_service_identity_are_not_renamed():
    text = (ROOT / "nbx-app.js").read_text(encoding="utf-8")
    assert "Live Nobitex data" in text
    assert "/download/nobitex-mobile.html" in text
    unit = (ROOT / "deploy/nobi.service").read_text()
    assert "Description=nobiTrader trading dashboard (FastAPI)" in unit
    assert "WorkingDirectory=/opt/nobi/server" in unit
