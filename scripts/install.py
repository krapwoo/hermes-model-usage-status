from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

PLUGIN_ID = "model-usage-status"
STATUS_LINE_MARKER = f"/plugins/{PLUGIN_ID}/model_usage_status/claude_snapshot.py"
DISTRIBUTABLE_FILES = (
    "plugin.yaml",
    "__init__.py",
    "model_usage_status/__init__.py",
    "model_usage_status/claude_snapshot.py",
    "model_usage_status/core.py",
    "model_usage_status/service.py",
    "model_usage_status/storage.py",
    "dashboard/manifest.json",
    "dashboard/plugin_api.py",
    "dashboard/dist/index.js",
    "desktop/plugin.js",
    "README.md",
    "LICENSE",
)


class InstallerError(RuntimeError):
    pass


def _status_line_command(snapshot_path: Path) -> str:
    return " ".join(("python3", shlex.quote(str(snapshot_path))))


def _read_settings(path: Path) -> tuple[dict[str, Any], bytes | None]:
    if not path.exists():
        return {}, None
    original = path.read_bytes()
    try:
        value = json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallerError("claude_settings_invalid") from error
    if not isinstance(value, dict):
        raise InstallerError("claude_settings_invalid")
    return value, original


def _is_our_status_line(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("type") != "command":
        return False
    command = value.get("command")
    if not isinstance(command, str):
        return False
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    if len(arguments) != 2 or arguments[0] != "python3":
        return False
    snapshot_path = Path(arguments[1])
    return (
        snapshot_path.is_absolute()
        and arguments[1].replace("\\", "/").endswith(STATUS_LINE_MARKER)
        and command == _status_line_command(snapshot_path)
    )


def _prepare_settings(settings: dict[str, Any], command: str) -> dict[str, Any]:
    existing = settings.get("statusLine")
    if existing is not None and not _is_our_status_line(existing):
        raise InstallerError("status_line_conflict")
    updated = dict(settings)
    updated["statusLine"] = {
        "type": "command",
        "command": command,
        "refreshInterval": 60,
    }
    return updated


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _copy_distribution(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for relative in DISTRIBUTABLE_FILES:
        origin = source / relative
        if not origin.is_file():
            raise InstallerError(f"missing_distribution_path:{relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)


def _checked_run(runner: Callable[..., Any], args: list[str], error_code: str) -> None:
    try:
        result = runner(args, capture_output=True, check=False, text=True)
    except OSError as error:
        raise InstallerError(error_code) from error
    if result.returncode != 0:
        raise InstallerError(error_code)


def install(
    *,
    source: Path,
    hermes_home: Path,
    claude_config_dir: Path,
    runner: Callable[..., Any] = subprocess.run,
    dry_run: bool = False,
) -> dict[str, str]:
    source = Path(source).resolve()
    hermes_home = Path(hermes_home).expanduser().resolve()
    claude_config_dir = Path(claude_config_dir).expanduser().resolve()
    target = hermes_home / "plugins" / PLUGIN_ID
    settings_path = claude_config_dir / "settings.json"
    command = _status_line_command(target / "model_usage_status" / "claude_snapshot.py")

    settings, original_settings = _read_settings(settings_path)
    updated_settings = _prepare_settings(settings, command)
    _checked_run(runner, ["hermes", "plugins", "validate", str(source)], "plugin_validation_failed")
    if dry_run:
        return {"status": "validated", "target": str(target)}

    plugins_dir = target.parent
    plugins_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{PLUGIN_ID}.install-", dir=plugins_dir))
    backup = plugins_dir / f".{PLUGIN_ID}.backup-{uuid.uuid4().hex}"
    had_target = target.exists()
    settings_written = False
    try:
        _copy_distribution(source, temporary)
        if had_target:
            os.replace(target, backup)
        os.replace(temporary, target)
        _atomic_write_json(settings_path, updated_settings)
        settings_written = True
        _checked_run(
            runner,
            ["hermes", "plugins", "enable", PLUGIN_ID, "--no-allow-tool-override"],
            "plugin_enable_failed",
        )
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        if backup.exists():
            os.replace(backup, target)
        if settings_written:
            if original_settings is None:
                settings_path.unlink(missing_ok=True)
            else:
                settings_path.write_bytes(original_settings)
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)
        legacy_desktop = hermes_home / "desktop-plugins" / PLUGIN_ID
        if legacy_desktop.exists():
            shutil.rmtree(legacy_desktop)

    return {"status": "installed", "target": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Hermes model usage status plugin without a build step.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    home = Path.home()
    hermes_home = Path(os.environ.get("HERMES_HOME", home / ".hermes"))
    claude_config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    try:
        result = install(
            source=args.source,
            hermes_home=hermes_home,
            claude_config_dir=claude_config_dir,
            dry_run=args.dry_run,
        )
    except InstallerError as error:
        print(f"Installation stopped: {error}")
        if str(error) == "status_line_conflict":
            print("Claude already has a different statusLine. Remove or integrate it manually, then rerun.")
        return 1
    print(f"Plugin {result['status']}: {result['target']}")
    if not args.dry_run:
        print("Restart the Hermes backend, then reload Hermes Desktop plugins.")
        print("Authenticate Claude and Codex locally if their popovers request it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
