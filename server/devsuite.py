"""Run the project's real gates from the dashboard — when running from source.

`diagnostics.py` answers "is this process healthy right now". This module
answers the other question: "is this checkout correct" — the same three
commands CI runs (`pytest`, `ruff`, `mypy`), started from a button on the
errors tab so nobody has to get a venv, a working directory and a shell right
at the same time just to see a red line.

Deliberate limits
-----------------
* **Source checkouts only.** If `server/tests/` or `pyproject.toml` is not next
  to the code, this is a deployed build: the module reports `available: false`
  and the UI says so. It never pretends to have run something.
* **Off by default everywhere else.** `NBX_DEV_TOOLS=0` disables it in a source
  checkout too. The endpoint is also behind `require_client`, so it is
  loopback-or-token like every other mutating route.
* **One run at a time.** A second request while one is in flight gets `busy`
  instead of a second pytest competing for the same SQLite file.
* **Threaded, not streamed.** `asyncio.create_subprocess_exec` does not exist
  under the selector event loop that Windows can end up with; `subprocess.run`
  in a worker thread does. The cost is that output arrives when the gate
  finishes rather than line by line — which is why the UI polls a run id
  instead of holding a request open for twenty seconds.
* **A result is what the tool said, not what we hoped.** `summary` is a regex
  over the tool's own output. When the regex misses, `parsed` is false and the
  UI shows the raw tail rather than an invented pass count. A gate that cannot
  be summarised has not passed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 900
KEEP_CHARS = 12_000          # the tail the UI renders; the whole log is not a web page
HISTORY = 10


@dataclass(frozen=True)
class Gate:
    name: str
    fa: str
    argv: tuple[str, ...]     # after the interpreter
    module: str               # import-checked before the run; empty for npm tasks
    fa_fix: str
    runner: str = "python"    # python | npm; never shell


GATES: tuple[Gate, ...] = (
    Gate("pytest", "تست‌های Python", ("-m", "pytest", "-q", "server/tests"), "pytest",
         "python -m pip install -r server/requirements-dev.txt"),
    Gate("ruff", "لینت Python", ("-m", "ruff", "check", "server", "scripts"), "ruff",
         "python -m pip install -r server/requirements-dev.txt"),
    Gate("mypy", "تایپ‌های Python", ("-m", "mypy"), "mypy",
         "python -m pip install -r server/requirements-dev.txt"),
    Gate("eslint", "لینت JavaScript", ("run", "lint", "--silent"), "",
         "npm ci", "npm"),
    Gate("dashboard-lint", "لینت کد درون صفحه", ("run", "lint:dashboard", "--silent"), "",
         "npm ci", "npm"),
    Gate("js-broker", "برابری بروکر مرورگر", ("run", "test:js", "--silent"), "",
         "npm ci", "npm"),
    Gate("intrabar", "قاعده خروج intrabar", ("run", "test:intrabar", "--silent"), "",
         "npm ci", "npm"),
    Gate("display", "نمایش مالی و زبان", ("run", "test:display", "--silent"), "",
         "npm ci", "npm"),
    Gate("adaptive-card", "کارت آستانه تطبیقی", ("run", "test:adaptive-card", "--silent"), "",
         "npm ci", "npm"),
    Gate("icons", "آیکون‌ها", ("run", "test:icons", "--silent"), "",
         "npm ci", "npm"),
    Gate("preflight-parity", "برابری preflight", ("scripts/check_preflight_parity.py",), "",
         "runtime dependency", "python"),
    Gate("build", "ساخت کامل آرتیفکت", ("run", "build", "--silent"), "",
         "npm ci", "npm"),
    Gate("deploy-check", "بررسی استقرار", ("scripts/check_deploy.py",), "",
         "runtime dependency", "python"),
)
BY_NAME = {g.name: g for g in GATES}


@dataclass
class Run:
    id: str
    gates: list[str]
    state: str = "running"          # running | done | timeout | failed
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    commit: str = ""
    dirty: bool = False

    def as_dict(self, tail: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id, "gates": self.gates, "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": (round(((self.finished_at or time.time()) - self.started_at) * 1000)),
            "error": self.error, "commit": self.commit, "dirty": self.dirty,
        }
        d["results"] = [{k: v for k, v in r.items() if k != "output"} for r in self.results]
        if tail:
            d["output"] = "\n\n".join(
                f"$ {' '.join(r['cmd'])}\n{r['output'][-KEEP_CHARS:]}"
                for r in self.results if r.get("output"))
        return d


_runs: dict[str, Run] = {}
_TASKS: set[asyncio.Task[None]] = set()
_current: str | None = None
_lock = asyncio.Lock()


def _enabled_by_env() -> bool:
    raw = (os.getenv("NBX_DEV_TOOLS") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    # Unset: on only where the source tree is, which is the only place the
    # tools exist and the only place the answer means anything.
    return is_source_tree()


def is_source_tree() -> bool:
    return (ROOT / "pyproject.toml").is_file() and (ROOT / "server" / "tests").is_dir()


def available() -> tuple[bool, str]:
    """Can a run happen here? Returns the reason when it cannot."""
    if not is_source_tree():
        return False, "این نصب از کد منبع اجرا نشده — تست‌ها در بستهٔ نهایی وجود ندارند"
    if not _enabled_by_env():
        return False, "با NBX_DEV_TOOLS=0 خاموش شده است"
    missing = [g.name for g in GATES if g.module and importlib.util.find_spec(g.module) is None]
    if missing:
        return False, (f"{', '.join(missing)} نصب نیست — "
                       "python -m pip install -r server/requirements-dev.txt")
    if any(g.runner == "npm" for g in GATES) and shutil.which("npm") is None:
        return False, "npm نصب نیست"
    return True, ""


def _git(*argv: str) -> str:
    try:
        out = subprocess.run(("git", *argv), cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                             timeout=10, check=False)
        return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _parse_pytest(text: str) -> dict[str, Any] | None:
    # pytest -q ends with e.g. "337 passed, 1 warning in 13.94s"
    m = re.search(r"^=*\s*(.+?)\s+in\s+([\d.]+)s\b", text, re.MULTILINE)
    if not m:
        return None
    line, secs = m.group(1), float(m.group(2))
    # "2 failed, 335 passed" -> {"failed": 2, "passed": 335}. `errors` is
    # normalised because pytest writes both "1 error" and "2 errors".
    counts: dict[str, int] = {}
    for n, word in re.findall(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)", line):
        counts[word.rstrip("s")] = int(n)
    return {"counts": counts, "seconds": secs, "line": line}


def _parse_ruff(text: str) -> dict[str, Any] | None:
    if "All checks passed!" in text:
        return {"counts": {"errors": 0}}
    m = re.search(r"Found (\d+) error", text)
    return {"counts": {"errors": int(m.group(1))}} if m else None


def _parse_mypy(text: str) -> dict[str, Any] | None:
    m = re.search(r"Success: no issues found in (\d+) source file", text)
    if m:
        return {"counts": {"errors": 0, "files": int(m.group(1))}}
    m = re.search(r"Found (\d+) errors? in (\d+) file", text)
    if m:
        return {"counts": {"errors": int(m.group(1)), "files": int(m.group(2))}}
    return None


PARSERS = {"pytest": _parse_pytest, "ruff": _parse_ruff, "mypy": _parse_mypy}


def _summarise(name: str, text: str, rc: int) -> tuple[str, dict[str, Any] | None]:
    """A one-line summary the UI can print, or None when the output is opaque."""
    parser = PARSERS.get(name)
    parsed = parser(text) if parser else None
    if parsed is None:
        last = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
        return (last[:160] or f"exit {rc}"), None
    c = parsed["counts"]
    if name == "pytest":
        bits = [f"{v} {k}" for k, v in c.items() if v]
        return (", ".join(bits) or "no tests ran") + f" · {parsed.get('seconds', 0):.1f}s", parsed
    if name == "ruff":
        return ("پاک" if c.get("errors") == 0 else f"{c['errors']} خطا"), parsed
    return ("پاک" if c.get("errors") == 0
            else f"{c['errors']} خطا در {c.get('files', '?')} فایل"), parsed


def _gate_env() -> dict[str, str]:
    """Environment for a gate: isolated storage, so a test cannot edit live state.

    This is not paranoia about the suite. `errorlog` writes through
    `journal.connect()`, and a test that exercises a *failing* health check
    therefore appends a row to whatever database is configured. Run from the
    dashboard, that is the database the person is staring at — they press
    "run tests" and a fabricated error appears in their log. Observed, not
    imagined: it happened while building this module.

    An override, not a default: an explicitly configured `NBX_JOURNAL_DB` in
    the developer's shell is a deliberate choice (a fixture database), and
    `conftest.py` only fills the gap with `setdefault`.
    """
    scratch = Path(tempfile.mkdtemp(prefix="nbx-gate-"))
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "NBX_JOURNAL_DB": str(scratch / "journal.db"),
        "NBX_PAPER_STATE": str(scratch / "paper.json"),
    }


def _run_gate(gate: Gate) -> dict[str, Any]:
    if gate.runner == "npm":
        npm = shutil.which("npm") or "npm"
        cmd = [npm, *gate.argv]
    else:
        cmd = [sys.executable, *gate.argv]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_S,
                              check=False, env=_gate_env())
        out = ((proc.stdout or "") + ("\n" if proc.stderr else "") + (proc.stderr or ""))[-KEEP_CHARS:]
        rc = proc.returncode
        state = ""
    except subprocess.TimeoutExpired as exc:
        out = f"timeout after {TIMEOUT_S}s\n{(exc.stdout or '') if isinstance(exc.stdout, str) else ''}"
        rc, state = -1, "timeout"
    except OSError as exc:
        out, rc, state = f"{type(exc).__name__}: {exc}", -1, "failed"
    summary, parsed = _summarise(gate.name, out, rc)
    return {
        "name": gate.name, "fa": gate.fa, "cmd": cmd, "rc": rc,
        "ok": rc == 0, "state": state,
        # `parsed: false` is the honest half: the exit code decided, not a
        # number scraped out of a log we did not understand.
        "parsed": parsed is not None, "summary": summary, "counts": (parsed or {}).get("counts", {}),
        "ms": round((time.perf_counter() - t0) * 1000), "output": out,
    }


async def start(gate_names: list[str] | None = None) -> Run:
    """Begin a run. The caller gets the id immediately and polls it."""
    global _current
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    names = gate_names or [g.name for g in GATES]
    unknown = [n for n in names if n not in BY_NAME]
    if unknown:
        raise KeyError(f"unknown gate(s): {', '.join(unknown)}")
    async with _lock:
        if _current is not None:
            running = _runs.get(_current)
            if running and running.state == "running":
                raise Busy(running.id)
        run = Run(id=uuid.uuid4().hex[:12], gates=names,
                  commit=_git("rev-parse", "--short", "HEAD"),
                  dirty=bool(_git("status", "--porcelain")))
        _runs[run.id] = run
        _current = run.id
        if len(_runs) > HISTORY:
            for old in sorted(_runs.values(), key=lambda r: r.started_at)[:-HISTORY]:
                _runs.pop(old.id, None)
    # The task must outlive this call, and it must not be garbage collected
    # mid-run: keeping the reference is the difference between "tests are
    # running" and "tests stopped for no reason".
    _TASKS.add(task := asyncio.create_task(_execute(run)))
    task.add_done_callback(_TASKS.discard)
    return run


class Busy(RuntimeError):
    def __init__(self, run_id: str):
        super().__init__(f"a run is already in progress ({run_id})")
        self.run_id = run_id


async def _execute(run: Run) -> None:
    global _current
    try:
        for name in run.gates:
            run.results.append(await asyncio.to_thread(_run_gate, BY_NAME[name]))
            if run.results[-1].get("state") == "timeout":
                run.state = "timeout"
                break
        else:
            run.state = "done"
    except Exception as exc:  # the run object is the error channel
        run.state = "failed"
        run.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        run.finished_at = time.time()
        _current = None


def get(run_id: str) -> Run | None:
    return _runs.get(run_id)


def last() -> Run | None:
    return max(_runs.values(), key=lambda r: r.started_at) if _runs else None


def status() -> dict[str, Any]:
    ok, why = available()
    prev = last()
    return {
        "available": ok, "reason": why,
        "gates": [{"name": g.name, "fa": g.fa,
                   "cmd": ("npm " if g.runner == "npm" else "python ") + " ".join(g.argv)}
                  for g in GATES],
        "busy": bool(_current),
        "last": prev.as_dict(tail=True) if prev else None,
        "root": str(ROOT),
    }
