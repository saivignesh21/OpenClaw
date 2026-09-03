"""A tiny calculator module with one deliberately broken function."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    # BUG: this should raise ZeroDivisionError-safe behavior or return
    # a clear error, but instead it silently returns 0, which the test
    # suite expects to be a raised ValueError.
    if b == 0:
        return 0
    return a / b
