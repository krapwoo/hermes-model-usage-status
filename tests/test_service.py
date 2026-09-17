from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from model_usage_status.claude_snapshot import default_output_path, record_observation  # noqa: E402
from model_usage_status.service import (  # noqa: E402
    ClaudeAuthenticationRequired,
    UsageService,
    authentication_status,
    fetch_claude_rate_limits,
    launch_reauthentication,
    parse_codex_response_lines,
)


AUTHENTICATED = {"state": "authenticated", "action_available": False}


def authenticated(_provider: str) -> dict:
    return dict(AUTHENTICATED)


def unavailable_claude() -> dict:
    raise RuntimeError("claude_usage_unavailable")


def codex_result(used: int = 57) -> dict:
    return {
        "rateLimits": {
            "limitId": "codex",
            "primary": {
                "usedPercent": used,
                "windowDurationMins": 10080,
                "resetsAt": 5000,
            },
            "secondary": None,
        },
        "rateLimitsByLimitId": {},
    }


class CodexProtocolTests(unittest.TestCase):
    def test_response_parser_ignores_notifications_and_returns_request_one(self) -> None:
        lines = [
            json.dumps({"id": 0, "result": {"userAgent": "test"}}),
            json.dumps({"method": "remoteControl/status/changed", "params": {}}),
            json.dumps({"id": 1, "result": codex_result()}),
        ]

        self.assertEqual(parse_codex_response_lines(lines), codex_result())

    def test_response_parser_raises_safe_code_for_provider_error(self) -> None:
        lines = [json.dumps({"id": 1, "error": {"message": "token=secret-value"}})]

        with self.assertRaisesRegex(RuntimeError, "codex_provider_error"):
            parse_codex_response_lines(lines)


class ClaudeSnapshotTests(unittest.TestCase):
    def test_explicit_snapshot_output_override_never_falls_back_to_production(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "isolated-observation.json"
            with patch.dict(os.environ, {"HERMES_MODEL_USAGE_CLAUDE_SNAPSHOT": str(target)}):
                self.assertEqual(default_output_path(), target)

    def test_no_rate_limits_does_not_overwrite_last_good_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude.json"
            existing = {
                "schema_version": 1,
                "provider": "claude",
                "observed_at": 100,
                "activity_marker": "marker",
                "rate_limits": {
                    "five_hour": {"used_percentage": 10, "resets_at": 2000},
                    "seven_day": {"used_percentage": 20, "resets_at": 3000},
                },
            }
            path.write_text(json.dumps(existing), encoding="utf-8")

            changed = record_observation({"session_id": "new"}, path=path, now=200)

            self.assertFalse(changed)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), existing)

    def test_snapshot_contains_only_sanitized_usage_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude.json"
            payload = {
                "session_id": "private-session",
                "transcript_path": "/private/transcript.jsonl",
                "cost": {"total_api_duration_ms": 900},
                "context_window": {"total_input_tokens": 100, "total_output_tokens": 20},
                "rate_limits": {
                    "five_hour": {"used_percentage": 10, "resets_at": 2000},
                    "seven_day": {"used_percentage": 20, "resets_at": 3000},
                },
            }

            changed = record_observation(payload, path=path, now=1000)
            stored = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(changed)
            self.assertEqual(set(stored), {
                "schema_version",
                "provider",
                "observed_at",
                "activity_marker",
                "observation_source",
                "rate_limits",
            })
            self.assertNotIn("private-session", path.read_text(encoding="utf-8"))
            self.assertNotIn("transcript", path.read_text(encoding="utf-8"))

    def test_unchanged_provider_activity_does_not_rewrite_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude.json"
            payload = {
                "session_id": "session-1",
                "cost": {"total_api_duration_ms": 900},
                "context_window": {"total_input_tokens": 100, "total_output_tokens": 20},
                "rate_limits": {
                    "five_hour": {"used_percentage": 10, "resets_at": 2000},
                    "seven_day": {"used_percentage": 20, "resets_at": 3000},
                },
            }
            self.assertTrue(record_observation(payload, path=path, now=1000))
            before = path.read_text(encoding="utf-8")

            changed = record_observation(payload, path=path, now=1100)

            self.assertFalse(changed)
            self.assertEqual(path.read_text(encoding="utf-8"), before)


class ProviderAuthenticationTests(unittest.TestCase):
    def test_oauth_usage_rejection_requires_reauthentication(self) -> None:
        class UsageRejected(RuntimeError):
            response = SimpleNamespace(status_code=401)

        error = UsageRejected("provider rejected credentials")

        with patch("agent.account_usage._fetch_anthropic_account_usage", side_effect=error):
            with self.assertRaises(ClaudeAuthenticationRequired):
                fetch_claude_rate_limits()

    def test_expired_stored_oauth_without_a_usage_response_requires_reauthentication(self) -> None:
        with (
            patch("agent.account_usage._fetch_anthropic_account_usage", return_value=None),
            patch("agent.anthropic_credentials.read_claude_code_credentials", return_value={"expiresAt": 1}),
            patch("agent.anthropic_credentials.is_claude_code_token_valid", return_value=False),
        ):
            with self.assertRaises(ClaudeAuthenticationRequired):
                fetch_claude_rate_limits()

    def test_successful_status_is_authenticated_without_action(self) -> None:
        result = authentication_status(
            "claude",
            runner=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
            which=lambda name: f"/usr/local/bin/{name}",
        )

        self.assertEqual(result, AUTHENTICATED)

    def test_explicit_logged_out_status_requires_authentication(self) -> None:
        result = authentication_status(
            "codex",
            runner=lambda *args, **kwargs: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="Not logged in",
            ),
            which=lambda name: f"/usr/local/bin/{name}",
        )

        self.assertEqual(result, {"state": "required", "action_available": True})

    def test_transient_status_failure_hides_authentication_action(self) -> None:
        for message in ("temporary provider failure", "rate limit exceeded"):
            with self.subTest(message=message):
                result = authentication_status(
                    "claude",
                    runner=lambda *args, **kwargs: SimpleNamespace(
                        returncode=1,
                        stdout="",
                        stderr=message,
                    ),
                    which=lambda name: f"/usr/local/bin/{name}",
                )

                self.assertEqual(result, {"state": "unavailable", "action_available": False})

    def test_missing_cli_is_unavailable_without_action(self) -> None:
        result = authentication_status(
            "claude",
            runner=lambda *args, **kwargs: self.fail("runner must not be called"),
            which=lambda _name: None,
        )

        self.assertEqual(result, {"state": "unavailable", "action_available": False})

    def test_login_launch_uses_secret_free_provider_command_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def launcher(args, **kwargs):
                calls.append((args, kwargs))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            result = launch_reauthentication(
                "claude",
                data_dir=Path(directory),
                status_checker=lambda _provider: {"state": "required", "action_available": True},
                runner=launcher,
                which=lambda name: f"/Applications/{name}",
                platform="darwin",
            )

            command_path = Path(calls[0][0][-1])
            command = command_path.read_text(encoding="utf-8")
            self.assertEqual(result, {"provider": "claude", "state": "login_started"})
            self.assertEqual(calls[0][0][:3], ["open", "-a", "Terminal"])
            self.assertIn("/Applications/claude auth login", command)
            self.assertNotIn("token", command.lower())
            self.assertNotIn("password", command.lower())
            self.assertEqual(command_path.stat().st_mode & 0o777, 0o700)


class UsageServiceTests(unittest.TestCase):
    def test_oauth_rejection_overrides_a_false_healthy_cli_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def rejected() -> dict:
                raise ClaudeAuthenticationRequired("claude_authentication_required")

            service = UsageService(
                Path(directory),
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=rejected,
                now=lambda: 1000,
                auth_checker=authenticated,
            )

            snapshot = service.refresh()

            claude = snapshot["providers"]["claude"]
            self.assertEqual(claude["error_code"], "authentication_required")
            self.assertEqual(
                claude["authentication"],
                {"state": "required", "action_available": True},
            )

            with patch("model_usage_status.service.launch_reauthentication") as launcher:
                launcher.return_value = {"provider": "claude", "state": "login_started"}
                service.launch_reauthentication("claude")
                status_checker = launcher.call_args.kwargs["status_checker"]
                self.assertEqual(
                    status_checker("claude"),
                    {"state": "required", "action_available": True},
                )

    def test_refresh_uses_live_claude_usage_without_status_line_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = UsageService(
                Path(directory),
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=lambda: {
                    "observation_source": "usage",
                    "rate_limits": {
                        "five_hour": {"used_percentage": 0, "resets_at": 2000},
                        "seven_day": {"used_percentage": 100, "resets_at": 3000},
                    },
                },
                now=lambda: 1000,
                auth_checker=authenticated,
            )

            snapshot = service.refresh()

            claude = snapshot["providers"]["claude"]
            self.assertEqual(claude["status"], "current")
            self.assertEqual(claude["source"], "Claude Code /usage")
            self.assertEqual(
                [window["remaining_percent"] for window in claude["windows"]],
                [100.0, 0.0],
            )

    def test_refresh_combines_codex_with_current_claude_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "claude-observation.json").write_text(
                json.dumps({
                    "observed_at": 900,
                    "rate_limits": {
                        "five_hour": {"used_percentage": 10, "resets_at": 2000},
                        "seven_day": {"used_percentage": 20, "resets_at": 3000},
                    },
                }),
                encoding="utf-8",
            )
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=unavailable_claude,
                now=lambda: 1000,
                auth_checker=authenticated,
            )

            snapshot = service.refresh()

            self.assertEqual(snapshot["providers"]["codex"]["windows"][0]["remaining_percent"], 43.0)
            self.assertEqual(snapshot["providers"]["claude"]["status"], "current")
            self.assertEqual(
                [window["remaining_percent"] for window in snapshot["providers"]["claude"]["windows"]],
                [90.0, 80.0],
            )
            self.assertEqual(snapshot["providers"]["claude"]["authentication"], AUTHENTICATED)
            self.assertTrue((data_dir / "usage-cache.json").exists())

    def test_old_claude_observation_is_labeled_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "claude-observation.json").write_text(
                json.dumps({
                    "observed_at": 1,
                    "rate_limits": {
                        "five_hour": {"used_percentage": 10, "resets_at": 5000},
                        "seven_day": {"used_percentage": 20, "resets_at": 6000},
                    },
                }),
                encoding="utf-8",
            )
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=unavailable_claude,
                now=lambda: 1000,
            )

            snapshot = service.refresh()

            self.assertEqual(snapshot["providers"]["claude"]["status"], "stale")
            self.assertEqual(snapshot["providers"]["claude"]["error_code"], "observation_stale")

    def test_failed_codex_refresh_preserves_cache_only_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            now = [1000]
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=unavailable_claude,
                now=lambda: now[0],
            )
            first = service.refresh()
            self.assertEqual(first["providers"]["codex"]["status"], "current")
            now[0] = 1100
            service.codex_fetcher = lambda: (_ for _ in ()).throw(RuntimeError("secret token"))

            second = service.refresh()

            self.assertEqual(second["providers"]["codex"]["status"], "stale")
            self.assertEqual(second["providers"]["codex"]["error_code"], "provider_unavailable")
            self.assertNotIn("secret", json.dumps(second))

    def test_concurrent_refreshes_share_one_provider_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0
            gate = threading.Event()
            started = threading.Event()

            def fetcher() -> dict:
                nonlocal calls
                calls += 1
                started.set()
                gate.wait(timeout=2)
                return codex_result()

            service = UsageService(
                Path(directory),
                codex_fetcher=fetcher,
                claude_fetcher=unavailable_claude,
                now=lambda: 1000,
            )
            results: list[dict] = []
            threads = [threading.Thread(target=lambda: results.append(service.refresh())) for _ in range(2)]
            for thread in threads:
                thread.start()
            self.assertTrue(started.wait(timeout=1))
            time.sleep(0.05)
            gate.set()
            for thread in threads:
                thread.join(timeout=2)

            self.assertEqual(calls, 1)
            self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
