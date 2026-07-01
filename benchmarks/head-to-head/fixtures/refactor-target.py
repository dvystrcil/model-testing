"""Metric aggregation for sweep results.

`aggregate` dispatches on an operation name via a long if/elif chain — the
design smell this fixture exists to exercise. Behavior (including the ValueError
on unknown ops and the empty-list handling) must be preserved by any refactor.
"""


def aggregate(op, values):
    if not values:
        # every op returns 0.0 on empty input except "count", which returns 0
        if op == "count":
            return 0
        return 0.0

    if op == "sum":
        total = 0.0
        for v in values:
            total += v
        return total
    elif op == "mean":
        total = 0.0
        for v in values:
            total += v
        return total / len(values)
    elif op == "max":
        best = values[0]
        for v in values[1:]:
            if v > best:
                best = v
        return best
    elif op == "min":
        best = values[0]
        for v in values[1:]:
            if v < best:
                best = v
        return best
    elif op == "count":
        return len(values)
    elif op == "range":
        lo = values[0]
        hi = values[0]
        for v in values[1:]:
            if v < lo:
                lo = v
            if v > hi:
                hi = v
        return hi - lo
    else:
        raise ValueError(f"unknown op: {op}")


# --- tests (run me: python -m pytest refactor-target.py, or python refactor-target.py) ---

def _test():
    assert aggregate("sum", [1, 2, 3]) == 6.0
    assert aggregate("mean", [2, 4]) == 3.0
    assert aggregate("max", [3, 1, 2]) == 3
    assert aggregate("min", [3, 1, 2]) == 1
    assert aggregate("count", [9, 9, 9]) == 3
    assert aggregate("range", [1, 5, 3]) == 4
    assert aggregate("sum", []) == 0.0
    assert aggregate("count", []) == 0
    try:
        aggregate("median", [1, 2, 3])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on unknown op")
    print("all tests passed")


if __name__ == "__main__":
    _test()
