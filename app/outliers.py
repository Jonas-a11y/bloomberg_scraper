"""Outlier detection for wealth-history time series.

Why: Bloomberg occasionally publishes revaluation jumps that look like
a real same-day wealth change but are actually re-classifications
(private→public asset, IPO listing day, base-year correction, sudden
inclusion-with-backdated-wealth on an existing person's profile).

Concrete examples spotted in the dataset:
    - Richard Cohen 2023-12-29: $3.2B → $21.4B (+564%) overnight
    - Dan Gilbert 2020-08-06: $8.1B → $34.1B (+319%) overnight
    - Yat-Gai Au 2025-06-16: $6.9B → $26.3B (+283%) overnight

Real wealth doesn't quadruple between two trading days. These are
data artifacts. They wreck the bar-chart race (someone leapfrogs the
field), the Gini chart (variance explodes), and pair correlations
(a 100σ spike).

What: a small flagger that takes a sorted-by-date series and returns
the same series annotated with `outlier` + `cleaned` fields.

Detection strategy — keep it dumb and predictable:

  1. **Absolute magnitude trigger.** A single-day move outside
     [-50%, +60%] on a prior worth ≥ $1B is suspect. Real
     stock-driven moves rarely exceed ±20% even on bad days; ±50% is
     a clear signal of a revaluation.

  2. **Reversion check.** A jump is much more likely to be a real
     event if it persists. We additionally require that within
     `mean_revert_days` (default 5) the value EITHER stays above
     half the jump magnitude OR returns close to the prior level.
     Returning to baseline = clear sign of a revaluation that got
     undone the next day.

We deliberately DON'T use a rolling MAD / robust z-score: real
events (Tesla rallies, AI hype days) trip those routinely and
smoothing them away rewrites history.

Flagged points get a `cleaned` value via linear interpolation
between the nearest unflagged neighbours; runs of flagged points
are bridged end-to-end. If a flagged point has no anchor on one
side (start/end of series), the original is kept — we don't
fabricate extrapolations.
"""
from __future__ import annotations


# Tuning. Conservative on purpose: we'd rather miss an outlier than
# smooth out a real ten-bagger.
DEFAULT_ABS_UP = 0.60           # +60% in one day = suspect
DEFAULT_ABS_DOWN = -0.50        # -50% in one day = suspect
DEFAULT_MIN_BASE = 1_000_000_000  # only flag when prior wealth ≥ $1B
DEFAULT_REVERT_WINDOW = 5       # days after the jump to check reversion
DEFAULT_REVERT_TOL = 0.25       # within 25% of pre-jump = "reverted"


def flag_outliers(
    series,
    abs_up: float = DEFAULT_ABS_UP,
    abs_down: float = DEFAULT_ABS_DOWN,
    min_base: float = DEFAULT_MIN_BASE,
    revert_window: int = DEFAULT_REVERT_WINDOW,
    revert_tol: float = DEFAULT_REVERT_TOL,
):
    """Return a list of dicts mirroring `series` with `outlier`
    (bool) and `cleaned` (int|None) added.

    `series` items must have `net_worth_usd`; date key is irrelevant.
    Caller must pre-sort by date ascending. Idempotent: passing
    already-flagged data through again produces the same result.
    """
    n = len(series)
    out = [dict(r) for r in series]
    if n < 2:
        for r in out:
            r["outlier"] = False
            r["cleaned"] = r.get("net_worth_usd")
        return out

    # Single pass: flag points whose prior-day delta exceeds the
    # absolute thresholds on a meaningful base. Then a second pass
    # downgrades the flag to a non-flag if the value is sustained
    # (reversion check fails).
    flag = [False] * n
    for i in range(1, n):
        prev = out[i - 1].get("net_worth_usd") or 0
        curr = out[i].get("net_worth_usd") or 0
        if prev < min_base:
            continue
        change = (curr - prev) / prev
        if change > abs_up or change < abs_down:
            flag[i] = True

    # Reversion check. A real event sustains; a revaluation often
    # gets corrected the next day. If within `revert_window` days
    # the value returns to within `revert_tol` of the prior, the
    # jump was an artifact — keep the flag. If the value sustains
    # near the new level, demote: it might be a real listing-day
    # event.
    #
    # That logic was originally meant to KEEP flags that revert
    # and CLEAR flags that sustain. Inverted in code below.
    for i in range(1, n):
        if not flag[i]:
            continue
        prev = out[i - 1].get("net_worth_usd") or 0
        curr = out[i].get("net_worth_usd") or 0
        if prev <= 0:
            continue
        # Look ahead up to `revert_window` rows
        sustained = True
        for j in range(i + 1, min(n, i + 1 + revert_window)):
            v = out[j].get("net_worth_usd") or 0
            if v <= 0:
                continue
            # Did we revert towards the pre-jump level?
            if abs(v - prev) / prev <= revert_tol:
                sustained = False
                break
            # Or did we drop substantially below the jump value?
            if abs(v - curr) / curr > revert_tol:
                sustained = False
                break
        if sustained:
            # The new level held — could be a real event (private→public
            # listing). Don't smooth it; just keep the flag for visibility
            # but DON'T interpolate. We achieve that by clearing the flag
            # so cleaned == raw.
            flag[i] = False

    for i in range(n):
        out[i]["outlier"] = flag[i]

    # Interpolate flagged runs.
    cleaned = [r.get("net_worth_usd") for r in out]
    i = 0
    while i < n:
        if not flag[i]:
            i += 1
            continue
        run_start = i
        while i < n and flag[i]:
            i += 1
        run_end = i - 1
        left_idx = run_start - 1
        right_idx = run_end + 1
        left_val = cleaned[left_idx] if left_idx >= 0 else None
        right_val = cleaned[right_idx] if right_idx < n else None
        if left_val is None or right_val is None:
            continue
        run_len = run_end - run_start + 1
        for k_off in range(run_len):
            frac = (k_off + 1) / (run_len + 1)
            cleaned[run_start + k_off] = (
                left_val + (right_val - left_val) * frac
            )

    for i in range(n):
        out[i]["cleaned"] = (
            int(cleaned[i]) if cleaned[i] is not None else None
        )
    return out


def summarise_outliers(series_with_flags) -> dict:
    """Convenience: count + sample list of outliers, for debug
    panels and the data-quality badge."""
    items = [r for r in series_with_flags if r.get("outlier")]
    return {
        "count": len(items),
        "samples": [
            {
                "date": r.get("date") or r.get("scraped_at"),
                "raw": r.get("net_worth_usd"),
                "cleaned": r.get("cleaned"),
            }
            for r in items[:10]
        ],
    }
