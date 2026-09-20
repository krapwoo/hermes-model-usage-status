from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from model_usage_status.storage import atomic_write_json  # noqa: E402

TIME_KEYS = {"generated_at", "observed_at", "resets_at"}


def shifted(value, delta: int):
    if isinstance(value, list):
        return [shifted(item, delta) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in TIME_KEYS and isinstance(item, int):
            result[key] = item + delta
        else:
            result[key] = shifted(item, delta)
    return result


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: write_usage_fixture.py STATE CACHE_PATH")
    state, cache_path = sys.argv[1], Path(sys.argv[2]).expanduser()
    fixtures = json.loads((ROOT / "tests/fixtures/credit_usage_states.json").read_text())
    if state not in fixtures:
        raise SystemExit(f"unknown fixture: {state}")
    snapshot = deepcopy(fixtures[state])
    delta = int(time.time()) - 1000
    atomic_write_json(cache_path, shifted(snapshot, delta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
