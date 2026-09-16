from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .core import prepare_claude_observation
    from .storage import atomic_write_json, read_json
except ImportError:  # Direct execution by Claude Code's status-line command.
    from core import prepare_claude_observation
    from storage import atomic_write_json, read_json


def record_observation(payload: dict[str, Any], path: Path, now: int) -> bool:
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict) or not any(
        isinstance(limits.get(name), dict)
        and isinstance(limits[name].get("used_percentage"), (int, float))
        and not isinstance(limits[name].get("used_percentage"), bool)
        for name in ("five_hour", "seven_day")
    ):
        return False

    previous = read_json(path)
    observation = prepare_claude_observation(payload, previous=previous, now=now)
    if observation == previous:
        return False
    atomic_write_json(path, observation)
    return True


def default_output_path() -> Path:
    override = os.environ.get("HERMES_MODEL_USAGE_CLAUDE_SNAPSHOT")
    if override:
        return Path(override).expanduser()
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    return hermes_home / "plugin-data" / "model-usage-status" / "claude-observation.json"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            record_observation(payload, path=default_output_path(), now=int(time.time()))
    except Exception:
        # A status-line observer must never interfere with Claude Code.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
