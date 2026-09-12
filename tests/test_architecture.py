"""Import-boundary checks: negotiation logic must not know which phone or model vendor runs it."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

VENDOR_SDKS = ("anthropic", "google", "africastalking", "twilio", "openai", "websockets")
# The only source files permitted to import a vendor SDK.  Adapters may use HTTP directly,
# but keeping this explicit prevents a future SDK import from leaking into app logic.
ALLOWED_VENDOR_FILES = {
    "anthropic": "llm/anthropic.py",
    "google": "speech/google.py",
    "africastalking": "telephony/africastalking.py",
    "twilio": "telephony/twilio.py",
}

# path (relative to app/) -> module prefixes it must never import
RULES = {
    "agent": ("app.telephony", "app.llm.anthropic", *VENDOR_SDKS),
    "conversation.py": ("app.telephony", "app.llm", "app.agent", *VENDOR_SDKS),
    "policy.py": ("app.telephony", "app.llm", "app.agent", *VENDOR_SDKS),
    "numbers.py": ("app.telephony", "app.llm", "app.agent", *VENDOR_SDKS),
    "llm/base.py": ("app.telephony", "app.agent", *VENDOR_SDKS),
    "llm/fake.py": ("app.telephony", "app.agent", *VENDOR_SDKS),
    "telephony": ("app.llm", "app.agent", "anthropic", "google", "openai"),
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


def test_vendor_sdks_are_confined_to_allowed_adapter_files():
    offenders = [
        f"{path.relative_to(APP)} imports {module}"
        for path in sorted(APP.rglob("*.py"))
        for module in imported_modules(path)
        for vendor, allowed_path in ALLOWED_VENDOR_FILES.items()
        if violates(module, vendor) and path.relative_to(APP).as_posix() != allowed_path
    ]
    assert offenders == []
