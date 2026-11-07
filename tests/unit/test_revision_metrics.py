"""Unit tests for pure revision metrics calculations and edge cases."""

from decimal import Decimal

from econ_monitor.revisions.metrics import (
    compute_revision_metrics,
    format_revision_change,
)


def test_standard_value_revision_up():
    """Synthetic test: value revision upward."""
    res = compute_revision_metrics(Decimal("100.0"), Decimal("102.5"))
    assert res.revision_type == "value_revision"
    assert res.direction == "up"
    assert res.absolute_revision == Decimal("2.5")
    assert res.relative_revision_pct == Decimal("2.5")


def test_standard_value_revision_down():
    """Synthetic test: value revision downward."""
    res = compute_revision_metrics(Decimal("200.0"), Decimal("190.0"))
    assert res.revision_type == "value_revision"
    assert res.direction == "down"
    assert res.absolute_revision == Decimal("-10.0")
    assert res.relative_revision_pct == Decimal("-5.0")


def test_previous_value_zero():
    """Synthetic test: when previous value is zero, relative_revision_pct is None."""
    res = compute_revision_metrics(Decimal("0.0"), Decimal("1.5"))
    assert res.revision_type == "value_revision"
    assert res.direction == "up"
    assert res.absolute_revision == Decimal("1.5")
    assert res.relative_revision_pct is None


def test_negative_previous_value():
    """Synthetic test: negative previous value uses abs(previous) in denominator."""
    # From -10.0 to -8.0 -> abs revision is +2.0. Relative is +2.0 / |-10.0| * 100 = +20%
    res = compute_revision_metrics(Decimal("-10.0"), Decimal("-8.0"))
    assert res.revision_type == "value_revision"
    assert res.direction == "up"
    assert res.absolute_revision == Decimal("2.0")
    assert res.relative_revision_pct == Decimal("20.0")


def test_became_available_transition():
    """Synthetic test: transition from None to value is became_available with None metrics."""
    res = compute_revision_metrics(None, Decimal("50.0"))
    assert res.revision_type == "became_available"
    assert res.direction is None
    assert res.absolute_revision is None
    assert res.relative_revision_pct is None


def test_became_missing_transition():
    """Synthetic test: transition from value to None is became_missing with None metrics."""
    res = compute_revision_metrics(Decimal("50.0"), None)
    assert res.revision_type == "became_missing"
    assert res.direction is None
    assert res.absolute_revision is None
    assert res.relative_revision_pct is None


def test_format_revision_change_labels():
    """Synthetic test: unit formatting helper with percentage points and relative rates."""
    fmt1 = format_revision_change(
        Decimal("0.3"),
        change_unit_label="percentage points",
        relative_revision_pct=Decimal("7.5"),
    )
    assert "+0.30 percentage points (+7.50% of previous rate)" in fmt1

    fmt2 = format_revision_change(
        Decimal("-5.25"),
        change_unit_label="Index 2017=100",
    )
    assert "-5.25 Index 2017=100" in fmt2

    fmt3 = format_revision_change(None)
    assert fmt3 == "No numeric revision"
