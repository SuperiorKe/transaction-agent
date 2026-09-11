"""Import-boundary checks: negotiation logic must not know which phone or model vendor runs it."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

VENDOR_SDKS = ("anthropic", "google", "africastalking", "twilio", "openai", "websockets")

# path (relative to app/) -> module prefixes it must never import
RULES = {
    "agent": ("app.telephony", "app.llm.anthropic", *VENDOR_SDKS),
    "conversation.py": ("app.telephony", "app.llm", "app.agent", *VENDOR_SDKS),
    "policy.py": ("app.telephony", "app.llm", "app.agent", *VENDOR_SDKS),
    "numbers.py": ("app.telephony", "app.llm", "app.agent", *VENDOR_SDKS),
    "llm/base.py": ("app.telephony", "app.agent", *VENDOR_SDKS),
    "llm/fake.py": ("app.telephony", "app.agent", *VENDOR_SDKS),
    "telephony": ("app.llm", "app.agent", "anthropic", "google", "twilio", "openai"),
    "calls.py": ("app.llm", "app.agent", *VENDOR_SDKS),
    "speech/base.py": ("app.telephony", "app.llm", "app.agent", *VENDOR_SDKS),
    "speech/fake.py": ("app.telephony", "app.llm", "app.agent", *VENDOR_SDKS),
    "speech/google.py": ("app.telephony", "app.llm", "app.agent", "anthropic", "twilio", "openai"),
}


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def files_for(target: str) -> list[Path]:
    path = APP / target
    return sorted(path.rglob("*.py")) if path.is_dir() else [path]


def violates(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


@pytest.mark.parametrize("target", sorted(RULES))
def test_import_boundaries(target):
    offenders = [
        f"{path.relative_to(APP)} imports {module}"
        for path in files_for(target)
        for module in imported_modules(path)
        for prefix in RULES[target]
        if violates(module, prefix)
    ]
    assert offenders == []


def test_no_module_imports_retired_vendors():
    offenders = [
        f"{path.relative_to(APP)} imports {module}"
        for path in sorted(APP.rglob("*.py"))
        for module in imported_modules(path)
        if any(violates(module, v) for v in ("twilio", "openai", "websockets"))
    ]
    assert offenders == []
