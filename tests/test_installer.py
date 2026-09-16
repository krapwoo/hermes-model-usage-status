from __future__ import annotations

import json
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.install import InstallerError, _is_our_status_line, install
from scripts.uninstall import uninstall


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append([str(part) for part in args])
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")


class InstallerTests(unittest.TestCase):
    def test_status_line_ownership_requires_exact_snapshot_command(self) -> None:
        owned = {
            "type": "command",
            "command": "python3 /old/.hermes/plugins/model-usage-status/model_usage_status/claude_snapshot.py",
        }
        composed = {
            "type": "command",
            "command": "other-meter && python3 /old/.hermes/plugins/model-usage-status/model_usage_status/claude_snapshot.py",
        }

        self.assertTrue(_is_our_status_line(owned))
        self.assertFalse(_is_our_status_line(composed))

    def test_status_line_ownership_rejects_embedded_shell_composition(self) -> None:
        command_substitution = {
            "type": "command",
            "command": 'python3 "$(other-meter)/plugins/model-usage-status/model_usage_status/claude_snapshot.py"',
        }

        self.assertFalse(_is_our_status_line(command_substitution))

    def test_install_preserves_settings_and_excludes_private_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hermes_home = home / ".hermes"
            claude_dir = home / ".claude"
            claude_dir.mkdir(parents=True)
            settings_path = claude_dir / "settings.json"
            settings_path.write_text(json.dumps({"theme": "dark", "nested": {"keep": True}}), encoding="utf-8")
            runner = FakeRunner()

            result = install(
                source=REPO_ROOT,
                hermes_home=hermes_home,
                claude_config_dir=claude_dir,
                runner=runner,
            )

            target = (hermes_home / "plugins" / "model-usage-status").resolve()
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "installed")
            self.assertTrue((target / "plugin.yaml").is_file())
            self.assertTrue((target / "desktop" / "plugin.js").is_file())
            self.assertFalse((target / ".git").exists())
            self.assertFalse((target / "tests").exists())
            self.assertFalse((target / "plugin-data").exists())
            self.assertFalse((target / "quarantine").exists())
            self.assertEqual(settings["theme"], "dark")
            self.assertEqual(settings["nested"], {"keep": True})
            self.assertEqual(settings["statusLine"]["type"], "command")
            self.assertEqual(settings["statusLine"]["refreshInterval"], 60)
            self.assertEqual(
                settings["statusLine"]["command"],
                f"python3 {target / 'model_usage_status' / 'claude_snapshot.py'}",
            )
            self.assertEqual(runner.calls[0][:3], ["hermes", "plugins", "validate"])
            self.assertEqual(
                runner.calls[-1],
                ["hermes", "plugins", "enable", "model-usage-status", "--no-allow-tool-override"],
            )

    def test_install_is_idempotent_and_updates_its_own_old_status_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hermes_home = home / ".hermes"
            claude_dir = home / ".claude"
            claude_dir.mkdir(parents=True)
            settings_path = claude_dir / "settings.json"
            settings_path.write_text(
                json.dumps({
                    "statusLine": {
                        "type": "command",
                        "command": "python3 /old/.hermes/plugins/model-usage-status/model_usage_status/claude_snapshot.py",
                        "refreshInterval": 30,
                    }
                }),
                encoding="utf-8",
            )
            runner = FakeRunner()

            install(source=REPO_ROOT, hermes_home=hermes_home, claude_config_dir=claude_dir, runner=runner)
            first = settings_path.read_text(encoding="utf-8")
            install(source=REPO_ROOT, hermes_home=hermes_home, claude_config_dir=claude_dir, runner=runner)
            second = settings_path.read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertFalse(any(hermes_home.glob(".model-usage-status.*")))

    def test_install_shell_quotes_snapshot_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Hermes Home ") as directory:
            root = Path(directory)
            hermes_home = root / "Hermes Home" / ".hermes"
            claude_dir = root / ".claude"

            install(source=REPO_ROOT, hermes_home=hermes_home, claude_config_dir=claude_dir, runner=FakeRunner())

            settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            arguments = shlex.split(settings["statusLine"]["command"])
            self.assertEqual(arguments, [
                "python3",
                str(hermes_home.resolve() / "plugins" / "model-usage-status" / "model_usage_status" / "claude_snapshot.py"),
            ])

    def test_install_recursively_excludes_unlisted_private_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(REPO_ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            private_files = (
                source / "model_usage_status" / ".env",
                source / "dashboard" / "debug.log",
                source / "desktop" / "plugin-data" / "usage-cache.json",
                source / "desktop" / "credentials.json",
            )
            for path in private_files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("secret", encoding="utf-8")
            hermes_home = root / ".hermes"

            install(source=source, hermes_home=hermes_home, claude_config_dir=root / ".claude", runner=FakeRunner())

            target = hermes_home / "plugins" / "model-usage-status"
            for path in private_files:
                relative = path.relative_to(source)
                self.assertFalse((target / relative).exists(), str(relative))
            self.assertTrue((target / "dashboard" / "dist" / "index.js").is_file())

    def test_conflicting_status_line_refuses_without_mutating_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hermes_home = home / ".hermes"
            claude_dir = home / ".claude"
            target = hermes_home / "plugins" / "model-usage-status"
            target.mkdir(parents=True)
            marker = target / "existing.txt"
            marker.write_text("keep", encoding="utf-8")
            claude_dir.mkdir(parents=True)
            settings_path = claude_dir / "settings.json"
            original = json.dumps({"statusLine": {"type": "command", "command": "other-meter"}})
            settings_path.write_text(original, encoding="utf-8")
            runner = FakeRunner()

            with self.assertRaisesRegex(InstallerError, "status_line_conflict"):
                install(source=REPO_ROOT, hermes_home=hermes_home, claude_config_dir=claude_dir, runner=runner)

            self.assertEqual(settings_path.read_text(encoding="utf-8"), original)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(runner.calls, [])

    def test_uninstall_removes_only_plugin_and_its_own_status_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            hermes_home = home / ".hermes"
            claude_dir = home / ".claude"
            runner = FakeRunner()
            install(source=REPO_ROOT, hermes_home=hermes_home, claude_config_dir=claude_dir, runner=runner)
            data_dir = hermes_home / "plugin-data" / "model-usage-status"
            data_dir.mkdir(parents=True)
            (data_dir / "usage-cache.json").write_text("{}", encoding="utf-8")

            result = uninstall(hermes_home=hermes_home, claude_config_dir=claude_dir, runner=runner)

            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse((hermes_home / "plugins" / "model-usage-status").exists())
            self.assertTrue(data_dir.exists())
            settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertNotIn("statusLine", settings)


if __name__ == "__main__":
    unittest.main()
