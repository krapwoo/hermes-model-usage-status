from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from model_usage_status.core import (  # noqa: E402
    build_codex_messages,
    merge_refresh_result,
    normalize_claude_payload,
    normalize_codex_result,
    prepare_claude_observation,
)


class CodexNormalizationTests(unittest.TestCase):
    def test_account_window_and_model_specific_windows_stay_separate(self) -> None:
        result = {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 57,
                    "windowDurationMins": 10080,
                    "resetsAt": 1790044716,
                },
                "secondary": None,
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 57,
                        "windowDurationMins": 10080,
                        "resetsAt": 1790044716,
                    },
                    "secondary": None,
                },
                "codex_bengalfox": {
                    "limitId": "codex_bengalfox",
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {
                        "usedPercent": 0,
                        "windowDurationMins": 300,
                        "resetsAt": 1789612844,
                    },
                    "secondary": {
                        "usedPercent": 0,
                        "windowDurationMins": 10080,
                        "resetsAt": 1790199644,
                    },
                },
            },
        }

        provider = normalize_codex_result(result, observed_at=1789590000)

        self.assertEqual(provider["status"], "current")
        self.assertEqual(
            provider["windows"],
            [
                {
                    "id": "codex:primary",
                    "label": "Week",
                    "duration_minutes": 10080,
                    "used_percent": 57.0,
                    "remaining_percent": 43.0,
                    "resets_at": 1790044716,
                }
            ],
        )
        self.assertEqual(len(provider["model_limits"]), 1)
        spark = provider["model_limits"][0]
        self.assertEqual(spark["label"], "GPT-5.3-Codex-Spark")
        self.assertEqual([window["label"] for window in spark["windows"]], ["5h", "Week"])
        self.assertEqual([window["remaining_percent"] for window in spark["windows"]], [100.0, 100.0])

    def test_used_percent_is_clamped_before_remaining_is_calculated(self) -> None:
        result = {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 140, "windowDurationMins": 300, "resetsAt": None},
                "secondary": {"usedPercent": -5, "windowDurationMins": 10080, "resetsAt": None},
            },
            "rateLimitsByLimitId": {},
        }

        provider = normalize_codex_result(result, observed_at=100)

        self.assertEqual([window["remaining_percent"] for window in provider["windows"]], [0.0, 100.0])

    def test_protocol_messages_initialize_then_read_without_a_model_prompt(self) -> None:
        messages = build_codex_messages()

        self.assertEqual([message["method"] for message in messages], [
            "initialize",
            "initialized",
            "account/rateLimits/read",
        ])
        self.assertEqual(messages[-1]["id"], 1)
        self.assertNotIn("prompt", str(messages).lower())


class ClaudeNormalizationTests(unittest.TestCase):
    def test_five_hour_and_weekly_windows_are_both_preserved(self) -> None:
        payload = {
            "rate_limits": {
                "five_hour": {"used_percentage": 23.5, "resets_at": 1790000000},
                "seven_day": {"used_percentage": 41.2, "resets_at": 1790400000},
            }
        }

        provider = normalize_claude_payload(payload, observed_at=1789590000)

        self.assertEqual(provider["status"], "current")
        self.assertEqual([window["label"] for window in provider["windows"]], ["5h", "Week"])
        self.assertEqual([window["remaining_percent"] for window in provider["windows"]], [76.5, 58.8])

    def test_missing_window_is_unavailable_not_zero(self) -> None:
        provider = normalize_claude_payload({"rate_limits": {}}, observed_at=100)

        self.assertEqual(provider["status"], "unavailable")
        self.assertEqual(provider["windows"], [])

    def test_usage_observation_has_explicit_trusted_provenance(self) -> None:
        payload = {
            "rate_limits": {
                "five_hour": {"used_percentage": 0, "resets_at": 1789616400},
                "seven_day": {"used_percentage": 100, "resets_at": 1789675200},
            }
        }

        observation = prepare_claude_observation(
            payload,
            previous=None,
            now=1789598769,
            source_key="usage",
        )
        provider = normalize_claude_payload(observation, observed_at=observation["observed_at"])

        self.assertEqual(observation["observation_source"], "usage")
        self.assertEqual(provider["source"], "Claude Code /usage")
        self.assertEqual([window["remaining_percent"] for window in provider["windows"]], [100.0, 0.0])

    def test_claude_source_cannot_be_spoofed_by_payload_text(self) -> None:
        payload = {
            "observation_source": "untrusted provider text",
            "rate_limits": {"five_hour": {"used_percentage": 10}},
        }

        provider = normalize_claude_payload(payload, observed_at=100)

        self.assertEqual(provider["source"], "Claude Code status-line rate_limits")

    def test_passive_observation_preserves_timestamp_when_activity_marker_is_unchanged(self) -> None:
        payload = {
            "session_id": "session-1",
            "cost": {"total_api_duration_ms": 900},
            "context_window": {"total_input_tokens": 100, "total_output_tokens": 20},
            "rate_limits": {
                "five_hour": {"used_percentage": 10, "resets_at": 2000},
                "seven_day": {"used_percentage": 20, "resets_at": 3000},
            },
        }
        previous = prepare_claude_observation(payload, previous=None, now=1000)

        repeated = prepare_claude_observation(payload, previous=previous, now=1100)

        self.assertEqual(repeated["observed_at"], 1000)
        self.assertNotIn("session_id", repeated)
        self.assertNotIn("cost", repeated)
        self.assertNotIn("context_window", repeated)

    def test_passive_observation_advances_when_provider_activity_changes(self) -> None:
        payload = {
            "session_id": "session-1",
            "cost": {"total_api_duration_ms": 900},
            "context_window": {"total_input_tokens": 100, "total_output_tokens": 20},
            "rate_limits": {
                "five_hour": {"used_percentage": 10, "resets_at": 2000},
                "seven_day": {"used_percentage": 20, "resets_at": 3000},
            },
        }
        previous = prepare_claude_observation(payload, previous=None, now=1000)
        payload["cost"]["total_api_duration_ms"] = 1200

        advanced = prepare_claude_observation(payload, previous=previous, now=1100)

        self.assertEqual(advanced["observed_at"], 1100)


class FreshnessTests(unittest.TestCase):
    def test_failed_refresh_preserves_last_success_only_as_stale(self) -> None:
        previous = {
            "id": "codex",
            "status": "current",
            "observed_at": 100,
            "windows": [{"label": "Week", "remaining_percent": 40.0, "resets_at": 500}],
            "model_limits": [],
        }

        merged = merge_refresh_result(previous, None, now=200, error_code="provider_unavailable")

        self.assertEqual(merged["status"], "stale")
        self.assertEqual(merged["observed_at"], 100)
        self.assertEqual(merged["error_code"], "provider_unavailable")
        self.assertEqual(merged["windows"][0]["remaining_percent"], 40.0)

    def test_failed_refresh_without_a_previous_reading_stays_unavailable(self) -> None:
        previous = {
            "id": "claude",
            "status": "unavailable",
            "observed_at": None,
            "source": None,
            "windows": [],
            "model_limits": [],
            "error_code": "observation_unavailable",
        }

        merged = merge_refresh_result(previous, None, now=200, error_code="observation_unavailable")

        self.assertEqual(merged["status"], "unavailable")
        self.assertEqual(merged["windows"], [])
        self.assertEqual(merged["error_code"], "observation_unavailable")

    def test_past_reset_marks_snapshot_expired(self) -> None:
        previous = {
            "id": "claude",
            "status": "current",
            "observed_at": 100,
            "windows": [
                {"label": "5h", "remaining_percent": 60.0, "resets_at": 150},
                {"label": "Week", "remaining_percent": 40.0, "resets_at": 180},
            ],
            "model_limits": [],
        }

        merged = merge_refresh_result(previous, previous, now=200)

        self.assertEqual(merged["status"], "expired")
        self.assertEqual(merged["windows"], [])


if __name__ == "__main__":
    unittest.main()
