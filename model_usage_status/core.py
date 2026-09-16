from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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


def merge_refresh_result(
    previous: dict[str, Any] | None,
    fresh: dict[str, Any] | None,
    now: int,
    error_code: str | None = None,
) -> dict[str, Any]:
    if fresh is not None:
        result = _expire_snapshot(fresh, now)
        if error_code is not None:
            result["error_code"] = error_code
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
    return result
