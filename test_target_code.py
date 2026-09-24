
from target_code import add

def test_int_addition():
    assert add(2, 3) == 5

def test_string_number_addition():
    assert add("2", 3) == 5

def test_invalid_input_raises():
    try:
        add("banana", 3)
        assert False, "expected ValueError"
    except ValueError:
        pass
