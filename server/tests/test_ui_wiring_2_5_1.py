"""UI wiring guards for the 2.5.1 header / signals changes.

These are source-level checks (no browser): each one pins a contract that
broke silently before — the timeframe selector was anchored on an element
that stopped existing on 2026-10-20 and nothing noticed for 16 days.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "nbx-app.js").read_text(encoding="utf-8")


def test_timeframe_selector_anchor_exists_in_markup():
    assert 'id="candleHeader"' in INDEX
    assert "$('candleHeader')" in APP
    # the old anchor must not come back as the only path
    assert "$('candleChart')?.previousElementSibling" not in APP


def test_header_has_user_menu_instead_of_loose_buttons():
    for el in ("userMenuBtn", "userMenu", "userMenuAccount", "logoutBtn"):
        assert f'id="{el}"' in INDEX, el
    # name/role are desktop-only inside the menu button
    assert re.search(r'id="userName" class="hidden lg:inline', INDEX)
    assert re.search(r'id="userRole" class="hidden lg:inline', INDEX)
    # the logout button lives inside the menu
    menu = INDEX[INDEX.index('id="userMenu"'):]
    assert menu.index('id="logoutBtn"') < menu.index("</div>", menu.index('id="logoutBtn"'))


def test_mobile_package_is_gone():
    for needle in ("mobilePkgBtn", "openMobileModal", "/download/nobitex-mobile.html", "nobitex-trader-robat.zip"):
        assert needle not in APP, needle
    main = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
    assert "/download/" not in main
    assert not (ROOT / "server" / "package_bundle.py").exists()


def test_signals_toolbar_and_i18n_keys():
    for el in ("sigSearch", "sigDirFilter", "sigFilterCount"):
        assert f'id="{el}"' in INDEX, el
    for key in ("sg.search", "sg.searchPh", "sg.filterAll", "sg.filterLong", "sg.filterNeutral", "sg.filterExit", "sg.filterCount", "sg.filterEmpty"):
        assert INDEX.count(f"'{key}':") == 2, key  # fa + en
    assert "applySignalFilter(" in APP


def test_header_language_switch_hides_active_language():
    assert "hb.classList.toggle('hidden', on)" in INDEX
