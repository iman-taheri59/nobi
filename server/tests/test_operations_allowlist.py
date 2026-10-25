"""The owner operations center is a fixed argv allowlist, never a shell."""
from __future__ import annotations

import devsuite


def test_operations_cover_all_documented_repository_gates():
    names = {gate.name for gate in devsuite.GATES}
    assert {
        "pytest", "ruff", "mypy", "eslint", "dashboard-lint", "js-broker",
        "intrabar", "display", "adaptive-card", "icons", "preflight-parity",
        "build", "deploy-check",
    } <= names


def test_no_operation_uses_a_shell_or_accepts_free_form_commands():
    for gate in devsuite.GATES:
        assert gate.runner in {"python", "npm"}
        assert isinstance(gate.argv, tuple) and gate.argv
        joined = " ".join(gate.argv)
        assert not any(token in joined for token in (";", "&&", "||", "`", "$("))
        assert gate.name == gate.name.strip().lower()
