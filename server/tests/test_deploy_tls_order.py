# mypy: check_untyped_defs = False
"""The ArvanCloud installer must obtain its certificate before `nginx -t`.

Measured on nginx 1.26: the shipped `deploy/nginx.conf` references
`/etc/letsencrypt/live/<domain>/fullchain.pem`. On a fresh host that file does
not exist, so `nginx -t` exits 1 with `[emerg] cannot load certificate`.
The old script ran `nginx -t` first and `certbot --nginx` second — so `--tls`
could never succeed on a first install. These checks pin the corrected order
without needing nginx, certbot or root on the test machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_arvan.sh"
CONF = ROOT / "deploy" / "nginx.conf"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_certificate_is_obtained_before_the_tls_site_is_installed():
    text = _text()
    cert_step = text.find('step "obtaining a certificate for $DOMAIN')
    site_step = text.find('step "configuring nginx for $DOMAIN"')
    assert cert_step != -1, "the certificate step disappeared"
    assert site_step != -1, "the nginx configuration step disappeared"
    assert cert_step < site_step, (
        "certbot must run before the TLS site is written: `nginx -t` rejects a "
        "config whose ssl_certificate files do not exist yet"
    )


def _code_without_comments() -> str:
    """Comments describe the old, broken path; only executable lines count."""
    return "\n".join(
        line for line in _text().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_certificate_is_obtained_over_http_not_the_tls_site():
    code = _code_without_comments()
    assert "certonly --webroot" in code, (
        "use ACME HTTP-01 over a port-80 server; `certbot --nginx` needs the "
        "TLS site, which is the thing that cannot load yet"
    )
    assert "certbot --nginx" not in code
    assert "-w /var/www/html" in code


def test_the_certificate_files_are_referenced_by_the_template():
    """The reason the order matters: the template points at live/ paths."""
    conf = CONF.read_text(encoding="utf-8")
    assert "ssl_certificate" in conf
    assert "/etc/letsencrypt/live/" in conf
    assert "nobi.example.ir" in conf, "the installer substitutes this placeholder"


def test_reruns_do_not_request_a_second_certificate():
    text = _text()
    assert "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" in text
    assert "certificate already exists" in text


def test_a_port80_fallback_exists_for_hosts_without_a_default_site():
    text = _text()
    assert "nobi-acme" in text, "HTTP-01 needs a port-80 server even without the default site"
    assert "/.well-known/acme-challenge/" in text
    assert text.find("cat > /etc/nginx/sites-available/nobi-acme") \
        < text.find("certbot certonly --webroot")


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="`bash -n` needs a POSIX shell; the five text checks above run everywhere",
)
def test_the_script_is_valid_bash():
    out = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True,
                         text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
