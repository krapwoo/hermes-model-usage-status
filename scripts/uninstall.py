from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from scripts.install import PLUGIN_ID, _is_our_status_line


def _write_settings(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def uninstall(
    *,
    hermes_home: Path,
    claude_config_dir: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, str]:
    hermes_home = Path(hermes_home).expanduser().resolve()
    claude_config_dir = Path(claude_config_dir).expanduser().resolve()
    try:
        runner(
            ["hermes", "plugins", "disable", PLUGIN_ID],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        pass

    target = hermes_home / "plugins" / PLUGIN_ID
    legacy_desktop = hermes_home / "desktop-plugins" / PLUGIN_ID
    if target.exists():
        shutil.rmtree(target)
    if legacy_desktop.exists():
        shutil.rmtree(legacy_desktop)

    settings_path = claude_config_dir / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            settings = None
        if isinstance(settings, dict) and _is_our_status_line(settings.get("statusLine")):
            updated = dict(settings)
            updated.pop("statusLine", None)
            _write_settings(settings_path, updated)

    return {"status": "uninstalled", "target": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Uninstall the Hermes model usage status plugin.")
    parser.parse_args()
    home = Path.home()
    hermes_home = Path(os.environ.get("HERMES_HOME", home / ".hermes"))
    claude_config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    result = uninstall(hermes_home=hermes_home, claude_config_dir=claude_config_dir)
    print(f"Plugin {result['status']}: {result['target']}")
    print("Provider credentials and plugin-data were left untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
