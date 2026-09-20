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
from model_usage_status.core import normalize_claude_oauth_result, normalize_codex_result  # noqa: E402
from model_usage_status.service import (  # noqa: E402
    CLAUDE_TIMEOUT_SECONDS,
    CODEX_TIMEOUT_SECONDS,
    ClaudeAuthenticationRequired,
    ProviderUsageUnavailable,
    UsageService,
    authentication_status,
    fetch_claude_rate_limits,
    launch_reauthentication,
    parse_codex_response_lines,
)


AUTHENTICATED = {"state": "authenticated", "action_available": False}


def authenticated(_provider: str) -> dict:
    return dict(AUTHENTICATED)


def unavailable_claude(_observed_at: int) -> dict:
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


def normalized_claude(observed_at: int) -> dict:
    return normalize_claude_oauth_result({
        "five_hour": {"utilization": 0.1, "resets_at": "2026-09-20T01:00:00Z"},
        "seven_day": {"utilization": 0.2, "resets_at": "2026-09-25T01:00:00Z"},
        "extra_usage": {
            "is_enabled": True, "used_credits": 1840, "monthly_limit": 10000,
            "currency": "USD", "decimal_places": 2,
        },
    }, observed_at)


def write_passive_observation(data_dir: Path, observed_at: int) -> None:
    (data_dir / "claude-observation.json").write_text(json.dumps({
        "schema_version": 1,
        "provider": "claude",
        "observed_at": observed_at,
        "activity_marker": "fixture",
        "observation_source": "status_line",
        "rate_limits": {
            "five_hour": {"used_percentage": 10, "resets_at": 2000},
            "seven_day": {"used_percentage": 20, "resets_at": 3000},
        },
    }), encoding="utf-8")


def write_schema_two_cache(data_dir: Path, *, generated_at: int,
                           credit_observed_at: int,
                           credit_freshness: str) -> None:
    claude = normalized_claude(credit_observed_at)
    claude["credits"]["freshness"] = credit_freshness
    codex = normalize_codex_result(codex_result(), observed_at=credit_observed_at)
    (data_dir / "usage-cache.json").write_text(json.dumps({
        "schema_version": 2,
        "generated_at": generated_at,
        "providers": {"claude": claude, "codex": codex},
    }), encoding="utf-8")


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


class ClaudeOAuthUsageTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "five_hour": {"utilization": 0.1, "resets_at": "2026-09-20T01:00:00Z"},
            "seven_day": {"utilization": 0.2, "resets_at": "2026-09-25T01:00:00Z"},
            "extra_usage": {
                "is_enabled": True, "used_credits": 1840, "monthly_limit": 10000,
                "currency": "USD", "decimal_places": 2,
            },
        }

    def test_one_oauth_request_returns_only_normalized_provider_state(self) -> None:
        calls = []

        def request_json(url: str, headers: dict, timeout: float) -> dict:
            calls.append((url, headers, timeout))
            return self.payload()

        provider = fetch_claude_rate_limits(
            100,
            token_resolver=lambda: "oauth-secret-token",
            oauth_checker=lambda token: token == "oauth-secret-token",
            request_json=request_json,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://api.anthropic.com/api/oauth/usage")
        self.assertEqual(calls[0][2], 15.0)
        self.assertEqual(calls[0][1]["Authorization"], "Bearer oauth-secret-token")
        serialized = json.dumps(provider)
        self.assertNotIn("oauth-secret-token", serialized)
        self.assertNotIn("extra_usage", serialized)
        self.assertEqual(provider["credits"]["spend"]["used_minor"], 1840)

    def test_default_path_imports_the_installed_hermes_credential_resolver(self) -> None:
        with (
            patch("agent.anthropic_credentials.resolve_anthropic_token",
                  return_value="oauth-secret-token"),
            patch("agent.anthropic_credentials._is_oauth_token", return_value=True),
            patch("model_usage_status.service._claude_request_json",
                  return_value=self.payload()),
        ):
            provider = fetch_claude_rate_limits(100)
        self.assertEqual(provider["credits"]["status"], "current")

    def test_401_403_429_and_timeout_use_safe_errors(self) -> None:
        class ResponseError(RuntimeError):
            def __init__(self, status_code: int) -> None:
                super().__init__("provider body containing secret-value")
                self.response = SimpleNamespace(status_code=status_code)

        for error, expected in (
            (ResponseError(401), ClaudeAuthenticationRequired),
            (ResponseError(403), ClaudeAuthenticationRequired),
            (ResponseError(429), ProviderUsageUnavailable),
            (TimeoutError("secret-value"), ProviderUsageUnavailable),
        ):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(expected) as raised:
                    fetch_claude_rate_limits(
                        100,
                        token_resolver=lambda: "oauth-secret-token",
                        oauth_checker=lambda _token: True,
                        request_json=lambda *_args: (_ for _ in ()).throw(error),
                    )
                self.assertNotIn("secret-value", str(raised.exception))

    def test_non_dict_response_body_is_malformed(self) -> None:
        with self.assertRaises(ProviderUsageUnavailable) as raised:
            fetch_claude_rate_limits(
                100,
                token_resolver=lambda: "oauth-secret-token",
                oauth_checker=lambda _token: True,
                request_json=lambda *_args: ["not", "a", "dict"],
            )
        self.assertEqual(raised.exception.credit_error_code, "malformed")

    def test_missing_or_non_oauth_token_is_unsupported(self) -> None:
        def must_not_request(*_args: object) -> dict:
            self.fail("request_json must not be called without a usable token")

        with self.assertRaises(ProviderUsageUnavailable) as raised:
            fetch_claude_rate_limits(
                100,
                token_resolver=lambda: None,
                oauth_checker=lambda _token: True,
                request_json=must_not_request,
            )
        self.assertEqual(raised.exception.credit_error_code, "unsupported")

        with self.assertRaises(ProviderUsageUnavailable) as raised:
            fetch_claude_rate_limits(
                100,
                token_resolver=lambda: "sk-ant-api-not-oauth",
                oauth_checker=lambda _token: False,
                request_json=must_not_request,
            )
        self.assertEqual(raised.exception.credit_error_code, "unsupported")


class ProviderAuthenticationTests(unittest.TestCase):
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
            def rejected(_observed_at: int) -> dict:
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
            data_dir = Path(directory)

            def claude_fetcher(observed_at: int) -> dict:
                return fetch_claude_rate_limits(
                    observed_at,
                    token_resolver=lambda: "oauth-secret-token",
                    oauth_checker=lambda token: token == "oauth-secret-token",
                    request_json=lambda *_args: {
                        "five_hour": {"utilization": 0.0, "resets_at": "2026-09-20T01:00:00Z"},
                        "seven_day": {"utilization": 1.0, "resets_at": "2026-09-25T01:00:00Z"},
                        "extra_usage": None,
                    },
                )

            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=claude_fetcher,
                now=lambda: 1000,
                auth_checker=authenticated,
            )

            snapshot = service.refresh()

            claude = snapshot["providers"]["claude"]
            self.assertEqual(claude["status"], "current")
            self.assertEqual(claude["source"], "claude-oauth-usage")
            self.assertEqual(
                [window["remaining_percent"] for window in claude["windows"]],
                [100.0, 0.0],
            )
            credits = claude["credits"]
            self.assertEqual(credits["status"], "unavailable")
            self.assertIsNone(credits["observed_at"])
            self.assertIsNone(credits["source"])

            cache_text = (data_dir / "usage-cache.json").read_text(encoding="utf-8")
            self.assertNotIn("oauth-secret-token", cache_text)
            self.assertNotIn("Authorization", cache_text)
            self.assertNotIn("extra_usage", cache_text)

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


class CacheAdmissionTests(unittest.TestCase):
    def valid_snapshot(self) -> dict:
        return {
            "schema_version": 2,
            "generated_at": 100,
            "providers": {
                "claude": normalized_claude(100),
                "codex": normalize_codex_result(codex_result(), observed_at=100),
            },
        }

    def _read_cache_for(self, snapshot: dict):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "usage-cache.json").write_text(json.dumps(snapshot), encoding="utf-8")
            codex_calls: list[None] = []
            claude_calls: list[int] = []
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_calls.append(None) or codex_result(),
                claude_fetcher=lambda observed_at: claude_calls.append(observed_at) or normalized_claude(observed_at),
                now=lambda: 100,
                auth_checker=authenticated,
            )
            result = service._read_cache()
            self.assertEqual(codex_calls, [])
            self.assertEqual(claude_calls, [])
            return result

    def test_valid_schema_v2_snapshot_is_admitted(self) -> None:
        self.assertIsNotNone(self._read_cache_for(self.valid_snapshot()))

    def test_provider_id_mismatch_or_wrong_type_is_rejected(self) -> None:
        for bad_id in (["claude"], {"id": "claude"}, "codex", None):
            with self.subTest(bad_id=bad_id):
                snapshot = self.valid_snapshot()
                snapshot["providers"]["claude"]["id"] = bad_id
                self.assertIsNone(self._read_cache_for(snapshot))

    def test_provider_credits_not_a_dict_is_rejected(self) -> None:
        for bad_credits in (None, "current", ["current"], 42):
            with self.subTest(bad_credits=bad_credits):
                snapshot = self.valid_snapshot()
                snapshot["providers"]["codex"]["credits"] = bad_credits
                self.assertIsNone(self._read_cache_for(snapshot))

    def test_credit_unit_unhashable_or_non_canonical_is_rejected(self) -> None:
        for provider_id, bad_unit in (
            ("claude", "credits"), ("codex", "currency"), ("codex", ["credits"]),
        ):
            with self.subTest(provider_id=provider_id, bad_unit=bad_unit):
                snapshot = self.valid_snapshot()
                snapshot["providers"][provider_id]["credits"]["unit"] = bad_unit
                self.assertIsNone(self._read_cache_for(snapshot))

    def test_credit_block_missing_required_key_is_rejected(self) -> None:
        for missing_key in ("status", "observed_at", "error_code", "unit", "freshness"):
            with self.subTest(missing_key=missing_key):
                snapshot = self.valid_snapshot()
                del snapshot["providers"]["claude"]["credits"][missing_key]
                self.assertIsNone(self._read_cache_for(snapshot))

    def test_provider_container_not_a_dict_is_rejected(self) -> None:
        for bad_provider in (["claude"], "claude", 1, None):
            with self.subTest(bad_provider=bad_provider):
                snapshot = self.valid_snapshot()
                snapshot["providers"]["claude"] = bad_provider
                self.assertIsNone(self._read_cache_for(snapshot))

    def test_invalid_cache_forces_get_to_refresh_with_canonical_unavailable_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            snapshot = self.valid_snapshot()
            snapshot["providers"]["claude"]["id"] = ["claude"]
            (data_dir / "usage-cache.json").write_text(json.dumps(snapshot), encoding="utf-8")
            calls = []
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: calls.append("codex") or codex_result(),
                claude_fetcher=lambda _observed_at: (_ for _ in ()).throw(
                    ProviderUsageUnavailable("provider_unavailable")
                ),
                now=lambda: 200,
                auth_checker=authenticated,
            )

            result = service.get()

            self.assertEqual(calls, ["codex"])
            self.assertEqual(result["providers"]["claude"]["status"], "unavailable")
            self.assertIsNone(result["providers"]["claude"]["observed_at"])
            self.assertEqual(result["schema_version"], 2)


class CreditServiceTests(unittest.TestCase):
    def test_legacy_cache_forces_refresh_and_schema_two_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "usage-cache.json").write_text(json.dumps({
                "schema_version": 1, "generated_at": 999,
                "providers": {},
            }), encoding="utf-8")
            calls = []
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: calls.append("codex") or codex_result(),
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: 1000,
                auth_checker=authenticated,
            )

            result = service.get()

            self.assertEqual(calls, ["codex"])
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(json.loads((data_dir / "usage-cache.json").read_text())["schema_version"], 2)

    def test_passive_allowance_fallback_cannot_refresh_credits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            now = [100]
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: now[0],
                auth_checker=authenticated,
            )
            service.refresh()
            now[0] = 200
            service.claude_fetcher = lambda _observed_at: (_ for _ in ()).throw(
                ProviderUsageUnavailable("timeout")
            )
            write_passive_observation(data_dir, observed_at=200)

            second = service.refresh()

            self.assertEqual(second["providers"]["claude"]["windows"][0]["remaining_percent"], 90.0)
            self.assertEqual(second["providers"]["claude"]["credits"]["freshness"], "stale")
            self.assertEqual(second["providers"]["claude"]["credits"]["observed_at"], 100)

    def test_claude_credits_recover_to_current_after_live_read_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            now = [100]
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: now[0],
                auth_checker=authenticated,
            )
            service.refresh()

            now[0] = 200
            service.claude_fetcher = lambda _observed_at: (_ for _ in ()).throw(
                ProviderUsageUnavailable("timeout")
            )
            write_passive_observation(data_dir, observed_at=200)
            bounded_stale = service.refresh()
            self.assertEqual(bounded_stale["providers"]["claude"]["credits"]["freshness"], "stale")
            self.assertEqual(bounded_stale["providers"]["claude"]["credits"]["observed_at"], 100)

            now[0] = 300
            service.claude_fetcher = lambda observed_at: normalized_claude(observed_at)
            recovered = service.refresh()

            credits = recovered["providers"]["claude"]["credits"]
            self.assertEqual(credits["status"], "current")
            self.assertEqual(credits["freshness"], "current")
            self.assertEqual(credits["observed_at"], 300)

    def test_stale_credit_expires_on_cached_read_without_waiting_for_cache_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_schema_two_cache(data_dir, generated_at=995, credit_observed_at=99,
                                    credit_freshness="stale")
            codex_calls: list[None] = []
            claude_calls: list[int] = []
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_calls.append(None) or codex_result(),
                claude_fetcher=lambda observed_at: claude_calls.append(observed_at) or normalized_claude(observed_at),
                now=lambda: 1000,
                auth_checker=authenticated,
            )

            credits = service.get()["providers"]["claude"]["credits"]

            self.assertEqual(credits["status"], "unavailable")
            self.assertIsNone(credits["observed_at"])
            self.assertIsNone(credits["source"])
            self.assertEqual(codex_calls, [])
            self.assertEqual(claude_calls, [])

    def test_concurrent_refresh_owner_generated_at_survives_aging_race(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_schema_two_cache(data_dir, generated_at=750, credit_observed_at=1,
                                    credit_freshness="current")
            gate = threading.Event()
            started = threading.Event()

            def slow_codex_fetcher() -> dict:
                started.set()
                gate.wait(timeout=2)
                return codex_result()

            service = UsageService(
                data_dir,
                codex_fetcher=slow_codex_fetcher,
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: 1000,
                auth_checker=authenticated,
            )

            owner_result: dict = {}
            owner_thread = threading.Thread(
                target=lambda: owner_result.update(snapshot=service.refresh())
            )
            owner_thread.start()
            self.assertTrue(started.wait(timeout=1))

            reader_view = service.get()

            gate.set()
            owner_thread.join(timeout=2)

            self.assertEqual(owner_result["snapshot"]["generated_at"], 1000)
            on_disk = json.loads((data_dir / "usage-cache.json").read_text())
            self.assertEqual(on_disk["generated_at"], 1000)
            self.assertEqual(reader_view["providers"]["claude"]["credits"]["status"], "unavailable")

    def test_waiter_ages_credits_past_900_seconds_after_owner_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            gate = threading.Event()
            started = threading.Event()
            waiting = threading.Event()
            now_box = [100]

            def slow_codex_fetcher() -> dict:
                started.set()
                gate.wait(timeout=2)
                return codex_result()

            service = UsageService(
                data_dir,
                codex_fetcher=slow_codex_fetcher,
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: now_box[0],
                auth_checker=authenticated,
            )

            owner_thread = threading.Thread(target=service.refresh)
            owner_thread.start()
            self.assertTrue(started.wait(timeout=1))

            # Wrap the owner's in-flight event so we can observe the waiter actually
            # parking inside done.wait() before releasing the owner: a deterministic
            # barrier instead of a timing-only sleep between thread starts.
            owner_event = service._refresh_done
            original_wait = owner_event.wait

            def observed_wait(timeout: float | None = None) -> bool:
                waiting.set()
                return original_wait(timeout)

            owner_event.wait = observed_wait

            # Simulate wall-clock time passing while the waiter is parked in done.wait():
            # the owner captured now=100 before it ever blocked, but by the time the
            # waiter is released it must judge freshness using its own current clock.
            now_box[0] = 1100

            waiter_result: dict = {}
            waiter_thread = threading.Thread(
                target=lambda: waiter_result.update(snapshot=service.refresh())
            )
            waiter_thread.start()
            self.assertTrue(waiting.wait(timeout=1))

            gate.set()
            owner_thread.join(timeout=2)
            waiter_thread.join(timeout=2)

            credits = waiter_result["snapshot"]["providers"]["claude"]["credits"]
            self.assertEqual(credits["status"], "unavailable")
            self.assertIsNone(credits["observed_at"])
            self.assertIsNone(credits["source"])

    def test_waiter_wait_budget_covers_both_provider_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            codex_calls: list[None] = []
            claude_calls: list[int] = []
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_calls.append(None) or codex_result(),
                claude_fetcher=lambda observed_at: claude_calls.append(observed_at) or normalized_claude(observed_at),
                now=lambda: 100,
                auth_checker=authenticated,
            )
            service._refreshing = True
            fresh_event = threading.Event()
            captured: dict = {}

            def capturing_wait(timeout: float | None = None) -> bool:
                captured["timeout"] = timeout
                return True

            fresh_event.wait = capturing_wait
            service._refresh_done = fresh_event

            service.refresh()

            self.assertEqual(captured["timeout"], CODEX_TIMEOUT_SECONDS + CLAUDE_TIMEOUT_SECONDS + 5)
            self.assertEqual(codex_calls, [])
            self.assertEqual(claude_calls, [])


if __name__ == "__main__":
    unittest.main()
