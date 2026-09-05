import pytest
from solution import divide
def test_divide(): assert divide(10, 2) == 5
def test_zero():
    with pytest.raises(ValueError): divide(10, 0)
