"""Unauthenticated Coinbase Exchange public-market adapter."""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .rules import MIN_CANDLES, PRODUCTS

PUBLIC_API_BASE = "https://api.exchange.coinbase.com"
USER_AGENT = "sherweb-strategy-public-scanner/1.0"
DEFAULT_TIMEOUT_SECONDS = 20
Transport = Callable[..., Any]


def request_json(url: str, params: dict[str, Any] | None = None) -> Any:
    if not url.startswith(PUBLIC_API_BASE + "/"):
        raise ValueError("public Coinbase API URL required")
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"Coinbase public API returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    if not math.isfinite(number):
        return math.nan
    if positive and number <= 0:
        return math.nan
    if nonnegative and number < 0:
        return math.nan
    return number


def normalize_market_data(candles: Any, ticker: Any) -> dict[str, Any] | None:
    if not isinstance(candles, list) or len(candles) < MIN_CANDLES:
        return None
    normalized: list[tuple[float, float, float, float, float, float]] = []
    for candle in candles:
        if not isinstance(candle, list) or len(candle) < 6:
            return None
        timestamp = _number(candle[0], nonnegative=True)
        low = _number(candle[1], positive=True)
        high = _number(candle[2], positive=True)
        open_price = _number(candle[3], positive=True)
        close = _number(candle[4], positive=True)
        volume = _number(candle[5], nonnegative=True)
        if not all(math.isfinite(value) for value in (timestamp, low, high, open_price, close, volume)):
            return None
        if high < low or not (low <= open_price <= high) or not (low <= close <= high):
            return None
        normalized.append((timestamp, low, high, open_price, close, volume))
    normalized.sort(key=lambda item: item[0])
    timestamps = [item[0] for item in normalized]
    if any(later - earlier != 3600 for earlier, later in zip(timestamps, timestamps[1:])):
        return None
    ticker = ticker if isinstance(ticker, dict) else {}
    bid = _number(ticker.get("bid"), positive=True)
    ask = _number(ticker.get("ask"), positive=True)
    if not (math.isfinite(bid) and math.isfinite(ask)) or ask < bid:
        return None
    return {
        "timestamps": timestamps,
        "lows": [item[1] for item in normalized],
        "highs": [item[2] for item in normalized],
        "opens": [item[3] for item in normalized],
        "closes": [item[4] for item in normalized],
        "volumes": [item[5] for item in normalized],
        "bid": bid,
        "ask": ask,
    }


def fetch_market_inputs(product_id: str, *, transport: Transport = request_json) -> dict[str, Any] | None:
    if product_id not in PRODUCTS:
        raise ValueError(f"unsupported product: {product_id}")
    try:
        candles = transport(f"{PUBLIC_API_BASE}/products/{product_id}/candles", {"granularity": 3600})
        try:
            ticker = transport(f"{PUBLIC_API_BASE}/products/{product_id}/ticker", None)
        except Exception:
            ticker = {}
        return normalize_market_data(candles, ticker)
    except Exception:
        return None
