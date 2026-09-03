"""
Unit tests for the guardrail logic itself. Run with:
    pytest tests/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.guardrails.static_check import (
    check_shell_command,
    check_python_code,
    check_file_write,
    run_static_check,
)


def test_blocks_rm_rf():
    result = check_shell_command("rm -rf /workspace")
    assert not result.allowed
    assert "rm -rf" in result.reason


def test_blocks_sudo():
    result = check_shell_command("sudo apt-get install nmap")
    assert not result.allowed


def test_allows_benign_shell_command():
    result = check_shell_command("pytest test_calculator.py")
    assert result.allowed


def test_blocks_fork_bomb():
    result = check_shell_command(":(){ :|:& };:")
    assert not result.allowed


def test_blocks_socket_import():
    code = "import socket\nsocket.socket()"
    result = check_python_code(code)
    assert not result.allowed
    assert "socket" in result.reason


def test_blocks_eval_call():
    code = "x = eval('1+1')"
    result = check_python_code(code)
    assert not result.allowed
    assert "eval" in result.reason


def test_allows_benign_python():
    code = "def add(a, b):\n    return a + b\n"
    result = check_python_code(code)
    assert result.allowed


def test_blocks_path_escape_on_file_write():
    result = check_file_write("../../etc/passwd", "malicious content")
    assert not result.allowed


def test_blocks_absolute_path_on_file_write():
    result = check_file_write("/etc/passwd", "malicious content")
    assert not result.allowed


def test_allows_relative_file_write():
    result = check_file_write("calculator.py", "def add(a, b): return a + b")
    assert result.allowed


def test_dispatch_unknown_action_type():
    result = run_static_check({"type": "not_a_real_type", "content": ""})
    assert not result.allowed


def test_dispatch_shell_action():
    result = run_static_check({"type": "shell", "content": "echo hello"})
    assert result.allowed


def test_dispatch_python_action_blocks_subprocess_absent_by_default():
    # subprocess is intentionally NOT in the blocklist by default since
    # it's often legitimately needed (e.g., invoking pytest). This test
    # documents that choice explicitly rather than leaving it implicit.
    code = "import subprocess\nsubprocess.run(['pytest'])"
    result = run_static_check({"type": "python", "content": code})
    assert result.allowed  # documents current behavior; tighten if needed
