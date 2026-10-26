"""Build the downloadable project archive from the *current* checkout.

Why this is a module and not a committed ``nobitex-trader-robat.zip``
----------------------------------------------------------------------
The repository used to ship a 280 KB binary snapshot from 2026-10-19. It kept
being served long after the code moved on: users downloaded a dashboard whose
README still claimed "100% offline" (a claim the current README explicitly
retracts) and that lacked every security fix made afterwards. A binary in git
also cannot be reviewed in a diff.

The route now builds the archive on demand from the files on disk. Two rules:

* **Only source and documentation.** Runtime databases, ``.env`` files, setup
  tokens, caches, ``node_modules`` and generated reports never enter the zip.
  The exclusion list is deliberately a deny-list of *names/parts* plus an
  allow-list of suffixes, so a newly added ``*.db`` cannot slip in.
* **Bounded.** Individual files and the total archive have ceilings; a stray
  log file must not turn a download into an out-of-memory event.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: File extensions that may be published. Everything else (PNG screenshots,
#: SQLite databases, binaries, bytecode) is skipped without being named.
ALLOWED_SUFFIXES = {
    ".md", ".txt", ".html", ".js", ".mjs", ".cjs", ".css", ".svg", ".json",
    ".py", ".toml", ".yml", ".yaml", ".sh", ".service", ".socket", ".example",
    ".sudoers", ".conf", ".gitignore", ".dockerignore", ".src.css",
}

#: Exact filenames that are never published, even when the suffix is allowed.
EXCLUDED_NAMES = {
    ".env", "managed.env", "setup.token", "operations-audit.jsonl",
    "nobitex-trader-robat.zip", "package-lock.json.bak",
    "nobi-helper-client", "nobi-privileged-helper", "nobi.env",
}

#: Directory names that are never traversed.
EXCLUDED_PARTS = {
    ".git", ".github", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", ".venv", "venv", "candle_cache",
    "backtest_reports", "coin_cache", "exports", "backups", "htmlcov",
    ".playwright-cli", ".cache", "dist", "build", "coverage", ".next",
}

#: Files with these suffixes are allowed, but names containing a secret-ish
#: token are still refused (``.env.local``, ``auth.db-wal`` …).
SECRET_HINTS = (".env", ".db", "token", "credential", "secret", "private_key")

MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 12_000_000
MAX_FILES = 1200


def _publishable(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = set(rel.parts[:-1])
    if parts & EXCLUDED_PARTS:
        return False
    name = path.name
    if name in EXCLUDED_NAMES or (name.startswith(".") and name not in {
        ".gitignore", ".dockerignore", ".env.example",
    }):
        return False
    low = name.lower()
    if any(hint in low for hint in SECRET_HINTS) and not low.endswith(".example"):
        return False
    suffix = path.suffix.lower()
    if name.endswith(".src.css"):
        suffix = ".src.css"
    return suffix in ALLOWED_SUFFIXES


def iter_files(root: Path = ROOT):
    """Yield ``(relative_posix_path, Path)`` for publishable files, sorted."""
    found: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if not _publishable(path, root):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        found.append((path.relative_to(root).as_posix(), path))
    found.sort(key=lambda item: item[0])
    return found[:MAX_FILES]


def build_archive(root: Path = ROOT) -> bytes:
    """Return the zip as bytes. Never reads a committed binary snapshot."""
    from datetime import UTC, datetime

    out = io.BytesIO()
    total = 0
    included = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel, path in iter_files(root):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                break
            zf.writestr(rel, data)
            included += 1
        version = (root / "VERSION").read_text(encoding="utf-8").strip() \
            if (root / "VERSION").is_file() else "unknown"
        note = (
            "nobiTrader downloadable snapshot\n"
            f"version: {version}\n"
            f"built:   {datetime.now(UTC).isoformat(timespec='seconds')}\n"
            f"files:   {included}\n\n"
            "Generated from the running checkout, not from a committed binary.\n"
            "No .env file, database, API key or setup token is included.\n"
            "The dashboard needs the FastAPI backend for market data.\n"
        )
        zf.writestr("README-DOWNLOAD.txt", note)
    return out.getvalue()


__all__ = ["EXCLUDED_NAMES", "MAX_TOTAL_BYTES", "build_archive", "iter_files"]
