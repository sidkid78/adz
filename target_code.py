def _parse_number(val):
    if isinstance(val, bool):
        raise ValueError(f"Cannot convert {val!r} to a number")
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            pass
        try:
            return float(val)
        except ValueError:
            pass
    raise ValueError(f"Cannot convert {val!r} to a number")


def add(a, b):
    return _parse_number(a) + _parse_number(b)