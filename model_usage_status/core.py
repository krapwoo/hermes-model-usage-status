from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


_CLAUDE_SOURCE_LABELS = {
    "status_line": "Claude Code status-line rate_limits",
    "usage": "Claude Code /usage",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _timestamp(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _remaining_percent(used: Any) -> tuple[float, float] | None:
    number = _number(used)
    if number is None:
        return None
    clamped = min(100.0, max(0.0, number))
    return round(clamped, 1), round(100.0 - clamped, 1)


def _window_label(duration_minutes: int | None, fallback: str) -> str:
    if duration_minutes == 300:
        return "5h"
    if duration_minutes == 10080:
        return "Week"
    if duration_minutes and duration_minutes % 60 == 0:
        return f"{duration_minutes // 60}h"
    if duration_minutes:
        return f"{duration_minutes}m"
    return fallback


_CREDIT_ERROR_CODES = {
    "auth_rejected", "timeout", "rate_limited", "provider_unavailable",
    "malformed", "unsupported", None,
}
_TRANSIENT_CREDIT_CODES = {"timeout", "rate_limited", "provider_unavailable"}
_DECIMAL_CREDITS = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_CURRENCY_CODE = re.compile(r"[A-Z]{3}\Z")
_MAX_CREDIT_INTEGER = 10 ** 15


def _sanitized_credit_unit(value: Any, default: str = "credits") -> str:
    return value if value in {"currency", "credits"} else default


def _sanitized_credit_error_code(value: Any) -> str:
    return value if value in _CREDIT_ERROR_CODES and value is not None else "provider_unavailable"


def unavailable_credits(unit: str, error_code: str) -> dict[str, Any]:
    if unit not in {"currency", "credits"}:
        raise ValueError("invalid_credit_unit")
    if error_code not in _CREDIT_ERROR_CODES or error_code is None:
        raise ValueError("invalid_credit_error_code")
    return {
        "status": "unavailable", "freshness": None, "unit": unit,
        "currency": None, "minor_unit_scale": None, "balance": None,
        "spend": None, "active": False, "low": False, "exhausted": False,
        "observed_at": None, "source": None, "error_code": error_code,
    }


def _non_negative_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= _MAX_CREDIT_INTEGER else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or value > _MAX_CREDIT_INTEGER or int(value) != value:
            return None
        return int(value)
    return None


def _credit_scale(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not (0 <= value <= 6):
        return None
    return value


def _credit_decimal(value: Any) -> str | None:
    if not isinstance(value, str) or not _DECIMAL_CREDITS.fullmatch(value):
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return value if parsed.is_finite() and parsed >= 0 else None


def _remaining(used: Decimal, limit: Decimal) -> float | None:
    if limit <= 0:
        return None
    return float(max(Decimal(0), (limit - used) * Decimal(100) / limit))


def _iso_to_epoch(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def _normalize_claude_credits(extra_usage: Any, observed_at: int) -> dict[str, Any]:
    if not isinstance(extra_usage, dict):
        return unavailable_credits("currency", "unsupported")

    is_enabled = extra_usage.get("is_enabled")
    if is_enabled is False:
        return {
            "status": "off", "freshness": "current", "unit": "currency",
            "currency": None, "minor_unit_scale": None, "balance": None,
            "spend": None, "active": False, "low": False, "exhausted": False,
            "observed_at": int(observed_at), "source": "claude-oauth-usage", "error_code": None,
        }
    if is_enabled is not True:
        return unavailable_credits("currency", "malformed")

    currency = extra_usage.get("currency")
    if not isinstance(currency, str) or not _CURRENCY_CODE.fullmatch(currency):
        return unavailable_credits("currency", "malformed")

    scale = _credit_scale(extra_usage.get("decimal_places"))
    used_minor = _non_negative_integer(extra_usage.get("used_credits"))
    limit_minor = _non_negative_integer(extra_usage.get("monthly_limit"))
    if scale is None or used_minor is None or limit_minor is None:
        return unavailable_credits("currency", "malformed")

    if limit_minor == 0:
        return unavailable_credits("currency", "unsupported")

    remaining_percent = _remaining(Decimal(used_minor), Decimal(limit_minor))
    remaining_percent = 0.0 if remaining_percent is None else round(remaining_percent, 1)
    exhausted = remaining_percent <= 0.0
    low = remaining_percent <= 20.0

    return {
        "status": "current", "freshness": "current", "unit": "currency",
        "currency": currency, "minor_unit_scale": scale, "balance": None,
        "spend": {
            "used_minor": used_minor, "limit_minor": limit_minor,
            "remaining_percent": remaining_percent, "resets_at": None,
        },
        "active": True, "low": low, "exhausted": exhausted,
        "observed_at": int(observed_at), "source": "claude-oauth-usage", "error_code": None,
    }


def _optional_credit_decimal(container: dict[str, Any], key: str) -> tuple[str | None, bool]:
    if key not in container or container[key] is None:
        return None, False
    value = _credit_decimal(container[key])
    return (None, True) if value is None else (value, False)


def _codex_spend(individual_limit: Any) -> tuple[dict[str, Any] | None, bool]:
    if individual_limit is None:
        return None, False
    if not isinstance(individual_limit, dict):
        return None, True

    limit_credits, limit_malformed = _optional_credit_decimal(individual_limit, "limit")
    used_credits, used_malformed = _optional_credit_decimal(individual_limit, "used")
    if limit_malformed or used_malformed:
        return None, True
    resets_at = _timestamp(individual_limit.get("resetsAt"))
    raw_percent = individual_limit.get("remainingPercent")

    if raw_percent is not None:
        if isinstance(raw_percent, bool) or not isinstance(raw_percent, (int, float)):
            return None, True
        percent_value = float(raw_percent)
        if not math.isfinite(percent_value) or not (0.0 <= percent_value <= 100.0):
            return None, True
        remaining_percent: float | None = percent_value
    elif limit_credits is not None and used_credits is not None and Decimal(limit_credits) > 0:
        remaining_percent = _remaining(Decimal(used_credits), Decimal(limit_credits))
    else:
        remaining_percent = None

    if limit_credits is not None and used_credits is not None:
        limit_dec = Decimal(limit_credits)
        used_dec = Decimal(used_credits)
        if limit_dec > 0 and used_dec >= limit_dec:
            remaining_percent = 0.0

    return {
        "used_credits": used_credits,
        "limit_credits": limit_credits,
        "remaining_percent": remaining_percent,
        "resets_at": resets_at,
    }, False


def _normalize_codex_credits(
    result: dict[str, Any], account: dict[str, Any], observed_at: int
) -> dict[str, Any]:
    # result is reserved for top-level provider signals (e.g. ordinaryUsageAllowed); unused today.
    raw_credits = account.get("credits")
    if not isinstance(raw_credits, dict):
        return unavailable_credits("credits", "unsupported")

    has_credits = raw_credits.get("hasCredits")
    unlimited = raw_credits.get("unlimited")
    balance_raw = raw_credits.get("balance")
    if not isinstance(has_credits, bool) or not isinstance(unlimited, bool):
        return unavailable_credits("credits", "malformed")

    if has_credits is False:
        if unlimited or balance_raw is not None:
            return unavailable_credits("credits", "malformed")
        return {
            "status": "off", "freshness": "current", "unit": "credits",
            "currency": None, "minor_unit_scale": None, "balance": None,
            "spend": None, "active": False, "low": False, "exhausted": False,
            "observed_at": int(observed_at), "source": "codex-app-server", "error_code": None,
        }

    if unlimited and balance_raw is not None:
        return unavailable_credits("credits", "malformed")

    balance_amount: str | None = None
    if unlimited:
        balance = {"available": True, "amount_credits": None, "unlimited": True}
    else:
        if balance_raw is not None:
            balance_amount = _credit_decimal(balance_raw)
            if balance_amount is None:
                return unavailable_credits("credits", "malformed")
        balance = {
            "available": balance_amount is not None,
            "amount_credits": balance_amount,
            "unlimited": False,
        }

    spend, spend_malformed = _codex_spend(account.get("individualLimit"))
    if spend_malformed:
        return unavailable_credits("credits", "malformed")

    spend_control_reached = account.get("spendControlReached")
    if not isinstance(spend_control_reached, bool):
        return unavailable_credits("credits", "malformed")

    remaining_percent = spend["remaining_percent"] if spend else None
    balance_zero = balance_amount is not None and Decimal(balance_amount) == 0
    exhausted = bool(
        (remaining_percent is not None and remaining_percent <= 0.0)
        or balance_zero
        or spend_control_reached
    )
    low = remaining_percent is not None and remaining_percent <= 20.0

    return {
        "status": "current", "freshness": "current", "unit": "credits",
        "currency": None, "minor_unit_scale": None, "balance": balance,
        "spend": spend, "active": True, "low": low, "exhausted": exhausted,
        "observed_at": int(observed_at), "source": "codex-app-server", "error_code": None,
    }


def age_credit_state(credits: dict[str, Any], now: int) -> dict[str, Any]:
    unit = _sanitized_credit_unit(credits.get("unit"))
    observed_at = credits.get("observed_at")
    if not isinstance(observed_at, int) or isinstance(observed_at, bool):
        return unavailable_credits(unit, _sanitized_credit_error_code(credits.get("error_code")))
    age = now - observed_at
    if age < 0 or age > 900:
        return unavailable_credits(unit, _sanitized_credit_error_code(credits.get("error_code")))
    return deepcopy(credits)


def merge_credit_refresh_result(
    previous: dict[str, Any] | None,
    fresh: dict[str, Any] | None,
    now: int,
    error_code: str | None,
) -> dict[str, Any]:
    if fresh is not None:
        return deepcopy(fresh)

    if not isinstance(previous, dict):
        return unavailable_credits("credits", _sanitized_credit_error_code(error_code))

    aged = age_credit_state(previous, now)
    if aged["status"] == "unavailable":
        aged = dict(aged)
        aged["error_code"] = _sanitized_credit_error_code(error_code)
        return aged

    if error_code in _TRANSIENT_CREDIT_CODES:
        aged = deepcopy(aged)
        aged["freshness"] = "stale"
        aged["error_code"] = error_code
        return aged

    return unavailable_credits(
        _sanitized_credit_unit(aged.get("unit")), _sanitized_credit_error_code(error_code)
    )


def _codex_window(limit_id: str, slot: str, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    percentages = _remaining_percent(raw.get("usedPercent"))
    if percentages is None:
        return None
    used, remaining = percentages
    duration = _timestamp(raw.get("windowDurationMins"))
    return {
        "id": f"{limit_id}:{slot}",
        "label": _window_label(duration, slot.title()),
        "duration_minutes": duration,
        "used_percent": used,
        "remaining_percent": remaining,
        "resets_at": _timestamp(raw.get("resetsAt")),
    }


def _codex_windows(snapshot: dict[str, Any], limit_id: str) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for slot in ("primary", "secondary"):
        window = _codex_window(limit_id, slot, snapshot.get(slot))
        if window is not None:
            windows.append(window)
    return windows


def build_codex_messages() -> list[dict[str, Any]]:
    return [
        {
            "method": "initialize",
            "id": 0,
            "params": {
                "clientInfo": {"name": "hermes-model-usage-status", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        },
        {"method": "initialized"},
        {
            "method": "account/rateLimits/read",
            "id": 1,
            "params": {
                "excludeResetCreditDetails": True,
                "supportsLunaReserve": False,
            },
        },
    ]


def normalize_codex_result(result: dict[str, Any], observed_at: int) -> dict[str, Any]:
    account = result.get("rateLimits")
    account = account if isinstance(account, dict) else {}
    account_id = str(account.get("limitId") or "codex")
    windows = _codex_windows(account, account_id)

    model_limits: list[dict[str, Any]] = []
    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        for key in sorted(by_id):
            raw = by_id[key]
            if not isinstance(raw, dict):
                continue
            limit_id = str(raw.get("limitId") or key)
            if limit_id == account_id:
                continue
            model_windows = _codex_windows(raw, limit_id)
            if not model_windows:
                continue
            model_limits.append(
                {
                    "id": limit_id,
                    "label": str(raw.get("limitName") or limit_id),
                    "windows": model_windows,
                }
            )

    return {
        "id": "codex",
        "status": "current" if windows else "unavailable",
        "observed_at": int(observed_at),
        "source": "Codex app-server account/rateLimits/read",
        "windows": windows,
        "model_limits": model_limits,
        "error_code": None,
        "credits": _normalize_codex_credits(result, account, observed_at),
    }


def _claude_window(window_id: str, label: str, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    percentages = _remaining_percent(raw.get("used_percentage"))
    if percentages is None:
        return None
    used, remaining = percentages
    duration = 300 if window_id == "five_hour" else 10080
    return {
        "id": f"claude:{window_id}",
        "label": label,
        "duration_minutes": duration,
        "used_percent": used,
        "remaining_percent": remaining,
        "resets_at": _timestamp(raw.get("resets_at")),
    }


def normalize_claude_payload(payload: dict[str, Any], observed_at: int) -> dict[str, Any]:
    limits = payload.get("rate_limits")
    limits = limits if isinstance(limits, dict) else {}
    windows = [
        window
        for window in (
            _claude_window("five_hour", "5h", limits.get("five_hour")),
            _claude_window("seven_day", "Week", limits.get("seven_day")),
        )
        if window is not None
    ]
    return {
        "id": "claude",
        "status": "current" if windows else "unavailable",
        "observed_at": int(observed_at),
        "source": _CLAUDE_SOURCE_LABELS.get(
            payload.get("observation_source"),
            _CLAUDE_SOURCE_LABELS["status_line"],
        ),
        "windows": windows,
        "model_limits": [],
        "error_code": None,
        "credits": unavailable_credits("currency", "unsupported"),
    }


def _claude_oauth_window(window_id: str, label: str, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    utilization = _number(raw.get("utilization"))
    if utilization is None or not (0 <= utilization <= 1):
        return None
    percentages = _remaining_percent(utilization * 100)
    if percentages is None:
        return None
    used, remaining = percentages
    duration = 300 if window_id == "five_hour" else 10080
    return {
        "id": f"claude:{window_id}",
        "label": label,
        "duration_minutes": duration,
        "used_percent": used,
        "remaining_percent": remaining,
        "resets_at": _iso_to_epoch(raw.get("resets_at")),
    }


def normalize_claude_oauth_result(payload: dict[str, Any], observed_at: int) -> dict[str, Any]:
    windows = [
        window
        for window in (
            _claude_oauth_window("five_hour", "5h", payload.get("five_hour")),
            _claude_oauth_window("seven_day", "Week", payload.get("seven_day")),
        )
        if window is not None
    ]
    return {
        "id": "claude",
        "status": "current" if windows else "unavailable",
        "observed_at": int(observed_at),
        "source": "claude-oauth-usage",
        "windows": windows,
        "model_limits": [],
        "error_code": None,
        "credits": _normalize_claude_credits(payload.get("extra_usage"), observed_at),
    }


def _activity_marker(payload: dict[str, Any]) -> str:
    context = payload.get("context_window")
    context = context if isinstance(context, dict) else {}
    cost = payload.get("cost")
    cost = cost if isinstance(cost, dict) else {}
    marker = {
        "session": payload.get("session_id"),
        "api_ms": cost.get("total_api_duration_ms"),
        "input": context.get("total_input_tokens"),
        "output": context.get("total_output_tokens"),
        "current": context.get("current_usage"),
    }
    encoded = json.dumps(marker, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def prepare_claude_observation(
    payload: dict[str, Any],
    previous: dict[str, Any] | None,
    now: int,
    source_key: str = "status_line",
) -> dict[str, Any]:
    limits = payload.get("rate_limits")
    limits = limits if isinstance(limits, dict) else {}
    sanitized_limits: dict[str, dict[str, float | int | None]] = {}
    for key in ("five_hour", "seven_day"):
        raw = limits.get(key)
        if not isinstance(raw, dict):
            continue
        used = _number(raw.get("used_percentage"))
        if used is None:
            continue
        sanitized_limits[key] = {
            "used_percentage": round(min(100.0, max(0.0, used)), 1),
            "resets_at": _timestamp(raw.get("resets_at")),
        }

    marker = _activity_marker(payload)
    same_activity = isinstance(previous, dict) and previous.get("activity_marker") == marker
    observed_at = previous.get("observed_at") if same_activity else int(now)
    if not isinstance(observed_at, int):
        observed_at = int(now)

    return {
        "schema_version": 1,
        "provider": "claude",
        "observed_at": observed_at,
        "activity_marker": marker,
        "observation_source": (
            source_key if source_key in _CLAUDE_SOURCE_LABELS else "status_line"
        ),
        "rate_limits": sanitized_limits,
    }


def _active_windows(windows: Any, now: int) -> list[dict[str, Any]]:
    if not isinstance(windows, list):
        return []
    active: list[dict[str, Any]] = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        resets_at = _timestamp(window.get("resets_at"))
        if resets_at is not None and resets_at <= now:
            continue
        active.append(deepcopy(window))
    return active


def _expire_snapshot(snapshot: dict[str, Any], now: int) -> dict[str, Any]:
    result = deepcopy(snapshot)
    result["windows"] = _active_windows(result.get("windows"), now)
    model_limits: list[dict[str, Any]] = []
    for model in result.get("model_limits", []):
        if not isinstance(model, dict):
            continue
        current = deepcopy(model)
        current["windows"] = _active_windows(current.get("windows"), now)
        if current["windows"]:
            model_limits.append(current)
    result["model_limits"] = model_limits
    if not result["windows"] and snapshot.get("windows"):
        result["status"] = "expired"
    return result


_CREDIT_UNIT_BY_PROVIDER = {"claude": "currency", "codex": "credits"}


def _tag_credit_unit(credits: dict[str, Any] | None, unit: str) -> dict[str, Any] | None:
    if credits is None:
        return None
    if credits.get("unit") not in {"currency", "credits"}:
        credits = dict(credits)
        credits["unit"] = unit
    return credits


def _merged_provider_credits(
    previous_credits: dict[str, Any] | None,
    fresh_credits: dict[str, Any] | None,
    now: int,
    unit: str,
    credit_code: str | None,
) -> dict[str, Any]:
    if previous_credits is None and fresh_credits is None:
        return unavailable_credits(unit, _sanitized_credit_error_code(credit_code))
    return merge_credit_refresh_result(previous_credits, fresh_credits, now, credit_code)


def merge_refresh_result(
    previous: dict[str, Any] | None,
    fresh: dict[str, Any] | None,
    now: int,
    error_code: str | None = None,
    *,
    credit_unit: str | None = None,
    credit_error_code: str | None = None,
) -> dict[str, Any]:
    provider_id = None
    if isinstance(fresh, dict):
        provider_id = fresh.get("id")
    elif isinstance(previous, dict):
        provider_id = previous.get("id")
    unit = credit_unit or _CREDIT_UNIT_BY_PROVIDER.get(provider_id, "credits")
    credit_code = credit_error_code
    previous_credits = _tag_credit_unit(
        previous.get("credits") if isinstance(previous, dict) else None, unit
    )
    fresh_credits = _tag_credit_unit(
        fresh.get("credits") if isinstance(fresh, dict) else None, unit
    )

    if fresh is not None:
        result = _expire_snapshot(fresh, now)
        if error_code is not None:
            result["error_code"] = error_code
        result["credits"] = _merged_provider_credits(previous_credits, fresh_credits, now, unit, credit_code)
        return result

    if previous is None:
        return {
            "id": "unknown",
            "status": "unavailable",
            "observed_at": None,
            "source": None,
            "windows": [],
            "model_limits": [],
            "error_code": error_code or "provider_unavailable",
            "credits": unavailable_credits(unit, _sanitized_credit_error_code(credit_code)),
        }

    result = _expire_snapshot(previous, now)
    had_reading = bool(previous.get("windows") or previous.get("model_limits"))
    has_active_reading = bool(result.get("windows") or result.get("model_limits"))
    if had_reading and not has_active_reading:
        result["status"] = "expired"
    elif has_active_reading:
        result["status"] = "stale"
    else:
        result["status"] = "unavailable"
    result["error_code"] = error_code or "provider_unavailable"
    result["credits"] = _merged_provider_credits(
        previous_credits, None, now, unit, credit_code or "provider_unavailable"
    )
    return result
