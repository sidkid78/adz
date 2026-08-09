def add(a, b):
    def _to_number(val):
        if isinstance(val, bool):
            raise ValueError(f"Cannot convert boolean {val} to number")
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            try:
                return int(val)
            except ValueError:
                return float(val)
        raise ValueError(f"Cannot convert {type(val).__name__} to number")

    return _to_number(a) + _to_number(b)