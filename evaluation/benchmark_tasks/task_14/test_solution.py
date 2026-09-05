from solution import safe_get
def test_safe_get(): assert safe_get({}, 'missing', 42) == 42
