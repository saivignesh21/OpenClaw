"""
Layer 1: deterministic, fast, rule-based inspection.

This runs BEFORE anything touches the sandbox. It should be treated as a
cheap tripwire, not a complete defense — a sufficiently obfuscated payload
can slip past pattern matching. The Docker sandbox (network disabled,
read-only mounts, resource caps) is the layer that must hold even if this
one is bypassed. Defense in depth, not a single point of trust.
"""

import ast
import shlex
from dataclasses import dataclass

# Substrings that are an immediate red flag regardless of context.
BLOCKED_SHELL_PATTERNS = [
    "rm -rf",
    "sudo",
    ":(){ :|:& };:",   # fork bomb
    "mkfs",
    "dd if=",
    "> /dev/sda",
    "chmod -R 777",
    "curl ",
    "wget ",
    "nc -e",
    "/etc/passwd",
    "/etc/shadow",
    "~/.ssh",
    ".aws/credentials",
]

# Python modules that should never appear in agent-generated code inside
# the sandbox. Note: os/subprocess ARE legitimately needed for some dev
# tasks (e.g. running the test suite) — in a real deployment you'd want
# an allow-listed subset of os functions rather than a blanket ban. Kept
# strict here for the demo.
BLOCKED_PY_IMPORTS = {"socket", "requests", "urllib", "ftplib", "smtplib", "telnetlib", "ctypes"}
BLOCKED_PY_CALLS = {"eval", "exec", "compile", "__import__"}


@dataclass
class CheckResult:
    allowed: bool
    reason: str
    checker: str = "static"

    def to_dict(self):
        return {"allowed": self.allowed, "reason": self.reason, "checker": self.checker}


def check_shell_command(command: str) -> CheckResult:
    lowered = command.lower()
    for pattern in BLOCKED_SHELL_PATTERNS:
        if pattern.lower() in lowered:
            return CheckResult(False, f"Blocked shell pattern matched: '{pattern}'")

    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return CheckResult(False, f"Unparseable shell command: {e}")

    if not tokens:
        return CheckResult(False, "Empty command")

    return CheckResult(True, "No blocked patterns found")


def check_python_code(code: str) -> CheckResult:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return CheckResult(False, f"Python syntax error: {e}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in module_names:
                root_module = name.split(".")[0]
                if root_module in BLOCKED_PY_IMPORTS:
                    return CheckResult(False, f"Disallowed import: '{root_module}'")

        if isinstance(node, ast.Call):
            func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if func_name in BLOCKED_PY_CALLS:
                return CheckResult(False, f"Disallowed call: '{func_name}'")

    return CheckResult(True, "No blocked imports or calls found")


def check_file_write(target_path: str, content: str) -> CheckResult:
    path_result = _check_workspace_relative_path(target_path)
    if not path_result.allowed:
        return path_result
    if len(content) > 200_000:
        return CheckResult(False, "File content exceeds size limit (200KB)")
    return CheckResult(True, "Path is within workspace and size is reasonable")


def _check_workspace_relative_path(target_path: str) -> CheckResult:
    """Reject absolute paths and traversal in either Windows or POSIX form."""
    normalized = target_path.replace("\\", "/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or ":" in normalized
        or ".." in parts
    ):
        return CheckResult(False, f"File path escapes workspace: '{target_path}'")
    return CheckResult(True, "Path is within workspace")


def run_static_check(action: dict) -> CheckResult:
    """Dispatch to the correct checker based on the proposed action type."""
    action_type = action.get("type")
    content = action.get("content", "")

    if action_type == "shell":
        return check_shell_command(content)
    if action_type == "python":
        return check_python_code(content)
    if action_type == "file_write":
        return check_file_write(action.get("target_path", ""), content)
    if action_type == "file_read":
        target = action.get("target_path", "")
        return _check_workspace_relative_path(target)

    return CheckResult(False, f"Unknown action type: '{action_type}'")
