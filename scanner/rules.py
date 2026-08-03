"""Credential-free strategy rules for public Coinbase market observations."""
from __future__ import annotations

import math
import re
from statistics import mean
from typing import Any

PRODUCTS = (
    "BTC-USD", "ETH-USD", "SOL-USD", "XLM-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "MORPHO-USD",
)
LENDING_APY = 0.04
DEFAULT_ROUND_TRIP_FEE_EST = 0.013
DEFAULT_MIN_NET_UPSIDE = 0.018
DEFAULT_MAX_SPREAD = 0.005
TARGET_HIGH_DISCOUNT = 0.992
TARGET_MAX_CURRENT_MULTIPLE = 1.08
TARGET_SMA_MULTIPLE = 1.06
LENDING_OUTPERFORMANCE_MULTIPLE = 8
MIN_CANDLES = 169
LOOKBACK_24H = 24
LOOKBACK_7D = 168
SMA_LOOKBACK = 72
REASON_MIN_GROSS_UPSIDE = 0.03
REASON_PULLBACK_MIN = -0.12
REASON_PULLBACK_MAX = -0.015
REASON_SMA_RATIO_MIN = 0.985
REASON_VOLUME_RATIO_MIN = 0.8
REASON_REWARD_TO_RISK_PREFERRED = 1.5
FILTER_PULLBACK_MIN = -0.12
FILTER_PULLBACK_MAX = -0.012
FILTER_MIN_24H_CHANGE = -0.07
FILTER_MIN_7D_CHANGE = -0.15
FILTER_SMA_RATIO_MIN = 0.96
FILTER_MIN_REWARD_TO_RISK = 1.2
RISK_WEAK_7D_CHANGE = -0.13
RISK_REBOUND_FROM_LOW = 0.25
RISK_REBOUND_24H_CHANGE = 0.04
MARKET_MIN_PRODUCTS = 8
MARKET_BTC_24H_RISK_OFF = -0.05
MARKET_BTC_7D_RISK_OFF = -0.12
MARKET_BTC_SMA_GAP_RISK = -0.02
MARKET_BREADTH_RISK_OFF = 0.70
MARKET_EXTREME_BREADTH_RISK_OFF = 0.80

PRODUCT_RULE_OVERRIDES: dict[str, dict[str, Any]] = {
    "BTC": {"round_trip_fee_est": 0.010, "min_net_upside": 0.015, "min_gross_upside": 0.025, "max_spread": 0.003},
    "ETH": {"round_trip_fee_est": 0.010, "min_net_upside": 0.015, "min_gross_upside": 0.025, "max_spread": 0.003},
    "MORPHO": {
        "round_trip_fee_est": 0.018,
        "min_net_upside": 0.03,
        "min_gross_upside": 0.048,
        "max_spread": 0.004,
        "risk_note": "MORPHO is smaller and more volatile; consider smaller sizing",
    },
}


def base_asset(product_id: str) -> str:
    return product_id.split("-", 1)[0].upper()


def product_rules(product_id: str) -> dict[str, Any]:
    rules: dict[str, Any] = {
        "round_trip_fee_est": DEFAULT_ROUND_TRIP_FEE_EST,
        "min_net_upside": DEFAULT_MIN_NET_UPSIDE,
        "max_spread": DEFAULT_MAX_SPREAD,
        "min_gross_upside": DEFAULT_MIN_NET_UPSIDE + DEFAULT_ROUND_TRIP_FEE_EST,
        "min_reward_to_risk": FILTER_MIN_REWARD_TO_RISK,
    }
    rules.update(PRODUCT_RULE_OVERRIDES.get(base_asset(product_id), {}))
    rules["min_gross_upside"] = max(
        rules["min_gross_upside"],
        rules["min_net_upside"] + rules["round_trip_fee_est"],
    )
    return rules


def _finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def bid_ask_spread(bid: float, ask: float) -> float:
    if not (_finite_positive(bid) and _finite_positive(ask)) or ask < bid:
        return math.nan
    return (ask - bid) / ((ask + bid) / 2)


def _validated_series(data: dict[str, Any]) -> tuple[list[float], list[float], list[float], list[float]] | None:
    series = [data.get(name) for name in ("closes", "highs", "lows", "volumes")]
    if any(not isinstance(values, list) or len(values) < MIN_CANDLES for values in series):
        return None
    if len({len(values) for values in series}) != 1:
        return None
    closes, highs, lows, volumes = series
    if not all(_finite_positive(value) for value in closes + highs + lows):
        return None
    if not all(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 for value in volumes):
        return None
    return closes, highs, lows, volumes


def build_market_metrics(data: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, float] | None:
    rules = rules or product_rules("")
    validated = _validated_series(data)
    if validated is None:
        return None
    closes, highs, lows, volumes = validated
    current = closes[-1]
    look24 = min(LOOKBACK_24H, len(closes) - 1)
    look7d = min(LOOKBACK_7D, len(closes) - 1)
    high7 = max(highs[-look7d:])
    low7 = min(lows[-look7d:])
    low24 = min(lows[-look24:])
    sma72 = mean(closes[-SMA_LOOKBACK:])
    vol24 = sum(volumes[-LOOKBACK_24H:])
    vol_prev24 = sum(volumes[-2 * LOOKBACK_24H:-LOOKBACK_24H])
    target = min(high7 * TARGET_HIGH_DISCOUNT, current * TARGET_MAX_CURRENT_MULTIPLE, sma72 * TARGET_SMA_MULTIPLE)
    gross_upside = target / current - 1
    invalidation = min(low24, sma72 * 0.94)
    downside = 1 - invalidation / current if 0 < invalidation < current else math.inf
    reward_risk = gross_upside / downside if math.isfinite(downside) and downside > 0 else math.inf
    return {
        "current": current,
        "target": target,
        "gross_upside": gross_upside,
        "net_upside": gross_upside - rules["round_trip_fee_est"],
        "round_trip_fee_est": rules["round_trip_fee_est"],
        "lend_7d": (1 + LENDING_APY) ** (7 / 365) - 1,
        "change24": current / closes[-1 - look24] - 1,
        "change7d": current / closes[-1 - look7d] - 1,
        "high7": high7,
        "low7": low7,
        "low24": low24,
        "pullback_from_high": current / high7 - 1,
        "rebound_from_low": current / low7 - 1,
        "sma72": sma72,
        "invalidation": invalidation,
        "downside_to_invalidation": downside,
        "reward_to_risk": reward_risk,
        "volume_ratio": vol24 / vol_prev24 if vol_prev24 > 0 else 1.0,
        "spread": bid_ask_spread(data.get("bid", math.nan), data.get("ask", math.nan)),
    }


def _pct(value: float) -> str:
    return f"{value:+.2%}"


def strategy_reasons(metrics: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    if metrics["gross_upside"] >= REASON_MIN_GROSS_UPSIDE:
        reasons.append(f"room to recent resistance: gross {_pct(metrics['gross_upside'])}, estimated net {_pct(metrics['net_upside'])}")
    if REASON_PULLBACK_MIN <= metrics["pullback_from_high"] <= REASON_PULLBACK_MAX:
        reasons.append(f"controlled pullback from 7-day high: {_pct(metrics['pullback_from_high'])}")
    if metrics["current"] >= metrics["sma72"] * REASON_SMA_RATIO_MIN:
        reasons.append("not far below the 72-hour average")
    if metrics["volume_ratio"] >= REASON_VOLUME_RATIO_MIN:
        reasons.append(f"24-hour volume is healthy versus the prior day: {metrics['volume_ratio']:.2f}x")
    if metrics["reward_to_risk"] >= REASON_REWARD_TO_RISK_PREFERRED:
        reasons.append(f"reward/risk looks acceptable: {metrics['reward_to_risk']:.2f}x")
    return reasons


def strategy_risk_flags(metrics: dict[str, float], rules: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if math.isfinite(metrics["spread"]) and metrics["spread"] > rules["max_spread"]:
        flags.append(f"wide spread {_pct(metrics['spread'])}")
    if metrics["change24"] < FILTER_MIN_24H_CHANGE:
        flags.append(f"sharp 24-hour drop {_pct(metrics['change24'])}")
    if metrics["change7d"] < RISK_WEAK_7D_CHANGE:
        flags.append(f"weak 7-day trend {_pct(metrics['change7d'])}")
    if metrics["current"] < metrics["sma72"] * FILTER_SMA_RATIO_MIN:
        flags.append("well below the 72-hour average")
    if metrics["reward_to_risk"] < rules["min_reward_to_risk"]:
        flags.append(f"thin reward/risk {metrics['reward_to_risk']:.2f}x")
    if metrics["rebound_from_low"] > RISK_REBOUND_FROM_LOW and metrics["change24"] > RISK_REBOUND_24H_CHANGE:
        flags.append("may be chasing a fast rebound")
    if rules.get("risk_note"):
        flags.append(str(rules["risk_note"]))
    return flags


def clears_strategy(metrics: dict[str, float], rules: dict[str, Any]) -> bool:
    required_fields = {
        "current", "gross_upside", "net_upside", "lend_7d", "reward_to_risk",
        "pullback_from_high", "spread", "change24", "change7d", "sma72",
    }
    if not required_fields.issubset(metrics):
        return False
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in metrics.values()
    ):
        return False
    return (
        metrics["net_upside"] >= rules["min_net_upside"]
        and metrics["gross_upside"] > metrics["lend_7d"] * LENDING_OUTPERFORMANCE_MULTIPLE
        and metrics["gross_upside"] >= rules["min_gross_upside"]
        and metrics["reward_to_risk"] >= rules["min_reward_to_risk"]
        and FILTER_PULLBACK_MIN <= metrics["pullback_from_high"] <= FILTER_PULLBACK_MAX
        and (math.isnan(metrics["spread"]) or metrics["spread"] <= rules["max_spread"])
        and metrics["change24"] > FILTER_MIN_24H_CHANGE
        and metrics["change7d"] > FILTER_MIN_7D_CHANGE
        and metrics["current"] >= metrics["sma72"] * FILTER_SMA_RATIO_MIN
    )


def ranking_score(metrics: dict[str, float], rules: dict[str, Any]) -> float:
    spread_score = 0.5 if math.isnan(metrics["spread"]) else max(0.0, 1 - metrics["spread"] / rules["max_spread"])
    trend_score = max(0.0, min(1.0, (metrics["change7d"] - FILTER_MIN_7D_CHANGE) / 0.20))
    midpoint = (FILTER_PULLBACK_MIN + FILTER_PULLBACK_MAX) / 2
    half_range = (FILTER_PULLBACK_MAX - FILTER_PULLBACK_MIN) / 2
    pullback_score = max(0.0, 1 - abs(metrics["pullback_from_high"] - midpoint) / half_range)
    rr_score = max(0.0, min(1.0, metrics["reward_to_risk"] / 2.0))
    return metrics["net_upside"] * 100 + trend_score + spread_score + pullback_score + rr_score


def buy_price_guidance(metrics: dict[str, float], rules: dict[str, Any]) -> dict[str, float]:
    required = rules["min_gross_upside"]
    maximum = metrics["target"] / (1 + required)
    return {"suggested_limit_buy": min(metrics["current"], maximum), "max_buy_price": maximum, "required_gross_upside": required}


def score_market_input(product_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    if product_id not in PRODUCTS:
        raise ValueError(f"unsupported product: {product_id}")
    rules = product_rules(product_id)
    metrics = build_market_metrics(data, rules)
    if metrics is None:
        return None
    guidance = buy_price_guidance(metrics, rules)
    return {
        "product_id": product_id,
        "qualifies": clears_strategy(metrics, rules),
        "reference_price": metrics["current"],
        "suggested_limit_buy": guidance["suggested_limit_buy"],
        "do_not_chase_above": guidance["max_buy_price"],
        "possible_target": metrics["target"],
        "invalidation": metrics["invalidation"],
        "gross_upside": metrics["gross_upside"],
        "estimated_net_upside": metrics["net_upside"],
        "estimated_round_trip_cost": metrics["round_trip_fee_est"],
        "change_24h": metrics["change24"],
        "change_7d": metrics["change7d"],
        "current_vs_sma72": metrics["current"] / metrics["sma72"] - 1,
        "pullback_from_7d_high": metrics["pullback_from_high"],
        "spread": metrics["spread"],
        "reward_to_risk": metrics["reward_to_risk"],
        "downside_to_invalidation": metrics["downside_to_invalidation"],
        "ranking_score": ranking_score(metrics, rules),
        "reasons": strategy_reasons(metrics),
        "cautions": strategy_risk_flags(metrics, rules),
    }


def _sanitize_public_errors(errors: list[str]) -> list[str]:
    allowed_codes = {"market_data_unavailable", "scoring_failed", "no_score"}
    sanitized: list[str] = []
    for error in errors:
        match = re.fullmatch(r"([A-Z0-9]{2,12}-USD): ([a-z_]+)", error) if isinstance(error, str) else None
        if match and match.group(1) in PRODUCTS and match.group(2) in allowed_codes:
            value = f"{match.group(1)}: {match.group(2)}"
        else:
            value = "public_market_data_unavailable"
        if value not in sanitized:
            sanitized.append(value)
    return sanitized


def market_risk_regime(scored: list[dict[str, Any]], errors: list[str]) -> dict[str, Any]:
    public_errors = _sanitize_public_errors(errors)
    required = {"product_id", "change_24h", "change_7d", "current_vs_sma72"}
    metrics_valid = True
    seen_products: set[str] = set()
    for item in scored:
        if not isinstance(item, dict) or not required.issubset(item):
            metrics_valid = False
            break
        product_id = item["product_id"]
        if product_id not in PRODUCTS or product_id in seen_products:
            metrics_valid = False
            break
        seen_products.add(product_id)
        if not all(
            isinstance(item[field], (int, float))
            and not isinstance(item[field], bool)
            and math.isfinite(item[field])
            for field in ("change_24h", "change_7d", "current_vs_sma72")
        ):
            metrics_valid = False
            break
    if len(scored) < MARKET_MIN_PRODUCTS or not metrics_valid:
        reason = (
            f"market breadth unavailable: only {len(scored)} of {len(PRODUCTS)} products loaded"
            if len(scored) < MARKET_MIN_PRODUCTS
            else "market regime metrics are incomplete or invalid"
        )
        return {
            "risk_off": True,
            "data_complete": False,
            "products_loaded": len(scored),
            "below_sma_fraction": None,
            "btc": None,
            "reasons": [reason],
            "errors": public_errors,
        }
    btc = next((item for item in scored if item["product_id"] == "BTC-USD"), None)
    if btc is None:
        return {"risk_off": True, "data_complete": False, "products_loaded": len(scored), "below_sma_fraction": None, "btc": None, "reasons": ["BTC market regime data unavailable"], "errors": public_errors}
    breadth = sum(item["current_vs_sma72"] < 0 for item in scored) / len(scored)
    reasons: list[str] = []
    if btc["change_24h"] <= MARKET_BTC_24H_RISK_OFF:
        reasons.append(f"BTC 24-hour decline is severe: {_pct(btc['change_24h'])}")
    if btc["change_7d"] <= MARKET_BTC_7D_RISK_OFF:
        reasons.append(f"BTC 7-day decline is severe: {_pct(btc['change_7d'])}")
    if breadth >= MARKET_EXTREME_BREADTH_RISK_OFF:
        reasons.append(f"extreme market weakness: {breadth:.0%} of watched assets below their 72-hour average")
    elif breadth >= MARKET_BREADTH_RISK_OFF and btc["current_vs_sma72"] <= MARKET_BTC_SMA_GAP_RISK:
        reasons.append(f"broad weakness: {breadth:.0%} below the 72-hour average while BTC is {_pct(btc['current_vs_sma72'])} versus its average")
    btc_public = {field: btc[field] for field in ("change_24h", "change_7d", "current_vs_sma72")}
    return {
        "risk_off": bool(reasons),
        "data_complete": True,
        "products_loaded": len(scored),
        "below_sma_fraction": breadth,
        "btc": btc_public,
        "reasons": reasons,
        "errors": public_errors,
    }
