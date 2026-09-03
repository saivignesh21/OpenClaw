import pytest
from calculator import add, subtract, multiply, divide


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 2) == 3


def test_multiply():
    assert multiply(4, 3) == 12


def test_divide():
    assert divide(10, 2) == 5


def test_divide_by_zero_raises():
    # This is the currently FAILING test: divide() silently returns 0
    # instead of raising ValueError. The agent's objective is to fix
    # calculator.py so this test passes.
    with pytest.raises(ValueError):
        divide(10, 0)
