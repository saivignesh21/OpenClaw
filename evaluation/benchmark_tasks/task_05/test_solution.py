from solution import clamp
def test_clamp():
    assert clamp(-1, 0, 10) == 0
    assert clamp(11, 0, 10) == 10
