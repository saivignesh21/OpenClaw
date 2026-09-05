from solution import parse_bool
def test_parse_bool():
    assert parse_bool('true') is True
    assert parse_bool('no') is False
