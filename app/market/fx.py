"""Live FX rates fetched from yfinance.

Yahoo's screener reports ``marketCap`` in the listing's ``currency``
(EUR for SAP.DE, JPY for 7203.T, …) WITHOUT converting to USD even
though downstream callers expect USD. We need live FX to compare a
EUR-listed company against a USD-listed one without accidentally
making Lasertec at ¥3,800B look like a $3.8T company.

Cache is in-memory, hour-scoped — intraday FX moves don't matter
for billion-dollar comparisons.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_FX_TTL_SEC = 60 * 60  # refresh hourly
_fx_cache = {"ts": 0.0, "rates": {"USD": 1.0}}


def fx_rates(currencies):
    """Return ``{ccy: usd_per_unit}`` for the requested currencies,
    fetched in one batched yfinance call. Cached for an hour. Anything
    we can't price returns no entry — caller decides whether to drop
    the row."""
    needed = {(c or "").upper() for c in currencies if c}
    needed.discard("USD")
    needed.discard("")
    if not needed:
        return _fx_cache["rates"]
    fresh = (time.time() - _fx_cache["ts"]) < _FX_TTL_SEC
    missing = needed - set(_fx_cache["rates"].keys())
    if fresh and not missing:
        return _fx_cache["rates"]

    try:
        import yfinance as yf
    except ImportError:
        return _fx_cache["rates"]

    syms = " ".join(f"{c}USD=X" for c in (needed | missing))
    try:
        bundle = yf.Tickers(syms)
        for c in (needed | missing):
            ticker = bundle.tickers.get(f"{c}USD=X")
            if not ticker:
                continue
            try:
                price = ticker.fast_info.get("lastPrice")
            except Exception:
                price = None
            if price and price > 0:
                _fx_cache["rates"][c] = float(price)
    except Exception as e:
        logger.warning(f"FX fetch failed: {e}")
    _fx_cache["ts"] = time.time()
    return _fx_cache["rates"]


def to_usd(cap, currency, rates):
    """Convert a market cap to USD using the rate table from
    ``fx_rates``. Returns None if the currency is unknown — caller
    drops the row rather than reporting a misleading number."""
    if cap is None:
        return None
    ccy = (currency or "").upper()
    if not ccy or ccy == "USD":
        return cap
    rate = rates.get(ccy)
    if not rate:
        return None
    return cap * rate
