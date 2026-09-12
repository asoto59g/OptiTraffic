"""Pearson helper unit tests."""

from src.simulate import _pearson


def test_pearson_perfect() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [2.0, 4.0, 6.0, 8.0]
    r = _pearson(xs, ys)
    assert r is not None
    assert abs(r - 1.0) < 1e-9


def test_pearson_too_few() -> None:
    assert _pearson([1.0, 2.0], [1.0, 2.0]) is None


def test_pearson_constant() -> None:
    assert _pearson([1.0, 1.0, 1.0], [2.0, 3.0, 4.0]) is None
