"""Revision metrics computation using exact Decimal calculations."""

from decimal import Decimal
from typing import NamedTuple, Optional

from econ_monitor.db.models import RevisionDirection, RevisionType


class RevisionMetrics(NamedTuple):
    """Calculated metrics for a detected revision."""

    absolute_revision: Optional[Decimal]
    relative_revision_pct: Optional[Decimal]
    direction: Optional[str]
    revision_type: str


def compute_revision_metrics(
    previous: Optional[Decimal],
    new: Optional[Decimal],
) -> RevisionMetrics:
    """Compute revision metrics between two consecutive vintages per A6.5.

    - If previous is None and new is not None: became_available
    - If previous is not None and new is None: became_missing
    - If previous == new: no change (handled by detector, but if passed: value_revision with 0)
    - If both not None and differ: value_revision
        - absolute_revision = new - previous
        - relative_revision_pct = ((new - previous) / abs(previous)) * 100
          (stored as None when previous is zero or None)
        - direction: 'up' if new > previous, 'down' if new < previous, else None
    """
    if previous is None and new is not None:
        return RevisionMetrics(
            absolute_revision=None,
            relative_revision_pct=None,
            direction=None,
            revision_type=RevisionType.BECAME_AVAILABLE,
        )

    if previous is not None and new is None:
        return RevisionMetrics(
            absolute_revision=None,
            relative_revision_pct=None,
            direction=None,
            revision_type=RevisionType.BECAME_MISSING,
        )

    if previous is None and new is None:
        # Both are missing
        return RevisionMetrics(
            absolute_revision=None,
            relative_revision_pct=None,
            direction=None,
            revision_type=RevisionType.VALUE_REVISION,
        )

    # Both values are non-null Decimals
    abs_rev = new - previous

    # relative_revision_pct: None when previous is zero, using abs(previous) in denominator
    if previous == Decimal("0"):
        rel_rev_pct = None
    else:
        rel_rev_pct = (abs_rev / abs(previous)) * Decimal("100")

    if new > previous:
        direction = RevisionDirection.UP
    elif new < previous:
        direction = RevisionDirection.DOWN
    else:
        direction = None

    return RevisionMetrics(
        absolute_revision=abs_rev,
        relative_revision_pct=rel_rev_pct,
        direction=direction,
        revision_type=RevisionType.VALUE_REVISION,
    )


def format_revision_change(
    absolute_revision: Optional[Decimal],
    change_unit_label: Optional[str] = None,
    relative_revision_pct: Optional[Decimal] = None,
    precision: int = 2,
) -> str:
    """Format a change with its unit label, e.g. '+0.30 percentage points' or '-1.25%'.

    For percent-valued series, relative percentage is a percent of the previous rate
    and is labeled to avoid confusing it with percentage points.
    """
    if absolute_revision is None:
        return "No numeric revision"

    sign = "+" if absolute_revision > Decimal("0") else ""
    abs_str = f"{sign}{absolute_revision:.{precision}f}"

    label = change_unit_label.strip() if change_unit_label else ""
    if label:
        res = f"{abs_str} {label}"
    else:
        res = abs_str

    if relative_revision_pct is not None:
        rel_sign = "+" if relative_revision_pct > Decimal("0") else ""
        rel_str = f"({rel_sign}{relative_revision_pct:.{precision}f}% of previous rate)"
        return f"{res} {rel_str}"

    return res
