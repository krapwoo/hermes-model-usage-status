from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from model_usage_status.core import (  # noqa: E402
    age_credit_state,
    build_codex_messages,
    merge_credit_refresh_result,
    merge_refresh_result,
    normalize_claude_oauth_result,
    normalize_claude_payload,
    normalize_codex_result,
    prepare_claude_observation,
    unavailable_credits,
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


class ClaudeCreditNormalizationTests(unittest.TestCase):
    def oauth_payload(self, extra_usage: object) -> dict:
        return {
            "five_hour": {"utilization": 0.235, "resets_at": "2026-09-20T01:00:00Z"},
            "seven_day": {"utilization": 0.412, "resets_at": "2026-09-25T01:00:00Z"},
            "extra_usage": extra_usage,
        }

    def test_enabled_currency_credit_contract_uses_reported_minor_scale(self) -> None:
        provider = normalize_claude_oauth_result(self.oauth_payload({
            "is_enabled": True,
            "used_credits": 1840.0,
            "monthly_limit": 10000,
            "currency": "USD",
            "decimal_places": 2,
        }), observed_at=100)

        self.assertEqual(provider["status"], "current")
        self.assertEqual([window["remaining_percent"] for window in provider["windows"]], [76.5, 58.8])
        self.assertEqual(provider["credits"], {
            "status": "current", "freshness": "current", "unit": "currency",
            "currency": "USD", "minor_unit_scale": 2, "balance": None,
            "spend": {"used_minor": 1840, "limit_minor": 10000,
                      "remaining_percent": 81.6, "resets_at": None},
            "active": True, "low": False, "exhausted": False,
            "observed_at": 100, "source": "claude-oauth-usage", "error_code": None,
        })

    def test_off_is_not_unavailable(self) -> None:
        credits = normalize_claude_oauth_result(
            self.oauth_payload({"is_enabled": False}), observed_at=100
        )["credits"]
        self.assertEqual(credits["status"], "off")
        self.assertEqual(credits["freshness"], "current")
        self.assertFalse(credits["active"])
        self.assertIsNone(credits["currency"])

    def test_absent_and_non_object_extra_usage_are_unsupported(self) -> None:
        for extra_usage in (None, "not-an-object"):
            with self.subTest(extra_usage=extra_usage):
                credits = normalize_claude_oauth_result(
                    self.oauth_payload(extra_usage), observed_at=100
                )["credits"]
                self.assertEqual(credits, unavailable_credits("currency", "unsupported"))

    def test_enabled_amounts_without_reported_scale_are_malformed(self) -> None:
        credits = normalize_claude_oauth_result(self.oauth_payload({
            "is_enabled": True, "used_credits": 0, "monthly_limit": 10000,
            "currency": "USD",
        }), observed_at=100)["credits"]
        self.assertEqual(credits, unavailable_credits("currency", "malformed"))

    def test_over_cap_clamps_remaining_and_rejects_zero_cap_without_signal(self) -> None:
        over = normalize_claude_oauth_result(self.oauth_payload({
            "is_enabled": True, "used_credits": 12000, "monthly_limit": 10000,
            "currency": "USD", "decimal_places": 2,
        }), observed_at=100)["credits"]
        zero_cap = normalize_claude_oauth_result(self.oauth_payload({
            "is_enabled": True, "used_credits": 0, "monthly_limit": 0,
            "currency": "USD", "decimal_places": 2,
        }), observed_at=100)["credits"]
        self.assertEqual(over["spend"]["remaining_percent"], 0.0)
        self.assertTrue(over["exhausted"])
        self.assertTrue(over["low"])
        self.assertEqual(zero_cap["status"], "unavailable")
        self.assertEqual(zero_cap["error_code"], "unsupported")

    def test_scale_and_minor_amount_validation(self) -> None:
        for scale in (0, 2, 3, 4, 5, 6):
            with self.subTest(valid_scale=scale):
                credits = normalize_claude_oauth_result(self.oauth_payload({
                    "is_enabled": True, "used_credits": 1840.0,
                    "monthly_limit": 10000, "currency": "USD",
                    "decimal_places": scale,
                }), observed_at=100)["credits"]
                self.assertEqual(credits["status"], "current")
                self.assertEqual(credits["minor_unit_scale"], scale)

        for invalid in (True, 1.5, -1, 7):
            with self.subTest(invalid_scale=invalid):
                credits = normalize_claude_oauth_result(self.oauth_payload({
                    "is_enabled": True, "used_credits": 1840,
                    "monthly_limit": 10000, "currency": "USD",
                    "decimal_places": invalid,
                }), observed_at=100)["credits"]
                self.assertEqual(credits["status"], "unavailable")
                self.assertEqual(credits["error_code"], "malformed")

        for invalid in (True, -1, 1.5, float("nan"), float("inf")):
            with self.subTest(invalid_amount=invalid):
                credits = normalize_claude_oauth_result(self.oauth_payload({
                    "is_enabled": True, "used_credits": invalid,
                    "monthly_limit": 10000, "currency": "USD",
                    "decimal_places": 2,
                }), observed_at=100)["credits"]
                self.assertEqual(credits["status"], "unavailable")
                self.assertEqual(credits["error_code"], "malformed")

    def test_passive_payload_never_claims_current_credits(self) -> None:
        provider = normalize_claude_payload({
            "rate_limits": {"five_hour": {"used_percentage": 10, "resets_at": 200}},
        }, observed_at=100)
        self.assertEqual(provider["windows"][0]["remaining_percent"], 90.0)
        self.assertEqual(provider["credits"], unavailable_credits("currency", "unsupported"))

    def test_currency_must_be_exactly_three_uppercase_letters(self) -> None:
        for invalid_currency in ("usd", "US$", "USDD", "", 123, None, ["USD"], True):
            with self.subTest(invalid_currency=invalid_currency):
                credits = normalize_claude_oauth_result(self.oauth_payload({
                    "is_enabled": True, "used_credits": 100, "monthly_limit": 10000,
                    "currency": invalid_currency, "decimal_places": 2,
                }), observed_at=100)["credits"]
                self.assertEqual(credits, unavailable_credits("currency", "malformed"))

    def test_non_boolean_is_enabled_is_malformed_not_off(self) -> None:
        for is_enabled in (None, "true", "False", 1, 0):
            with self.subTest(is_enabled=is_enabled):
                credits = normalize_claude_oauth_result(self.oauth_payload({
                    "is_enabled": is_enabled, "used_credits": 0, "monthly_limit": 10000,
                    "currency": "USD", "decimal_places": 2,
                }), observed_at=100)["credits"]
                self.assertEqual(credits, unavailable_credits("currency", "malformed"))

        missing_flag_credits = normalize_claude_oauth_result(self.oauth_payload({
            "used_credits": 0, "monthly_limit": 10000, "currency": "USD", "decimal_places": 2,
        }), observed_at=100)["credits"]
        self.assertEqual(missing_flag_credits, unavailable_credits("currency", "malformed"))

    def test_astronomically_large_amount_is_malformed_not_a_crash(self) -> None:
        credits = normalize_claude_oauth_result(self.oauth_payload({
            "is_enabled": True, "used_credits": 10 ** 400, "monthly_limit": 10000,
            "currency": "USD", "decimal_places": 2,
        }), observed_at=100)["credits"]
        self.assertEqual(credits, unavailable_credits("currency", "malformed"))


class CodexCreditNormalizationTests(unittest.TestCase):
    def result(self, credits: object, individual_limit: object = None,
               *, spend_control_reached: object = False,
               ordinary_usage_allowed: object = True) -> dict:
        return {
            "ordinaryUsageAllowed": ordinary_usage_allowed,
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 57, "windowDurationMins": 10080, "resetsAt": 5000},
                "secondary": None,
                "credits": credits,
                "individualLimit": individual_limit,
                "spendControlReached": spend_control_reached,
            },
            "rateLimitsByLimitId": {},
        }

    def test_balance_and_monthly_limit_can_coexist(self) -> None:
        provider = normalize_codex_result(self.result(
            {"hasCredits": True, "unlimited": False, "balance": "9.5"},
            {"limit": "12.0", "used": "2.5", "remainingPercent": 79.1666666667,
             "resetsAt": 1792800000},
            ordinary_usage_allowed=False,
        ), observed_at=100)
        self.assertEqual(provider["credits"]["balance"], {
            "available": True, "amount_credits": "9.5", "unlimited": False,
        })
        self.assertEqual(provider["credits"]["spend"], {
            "used_credits": "2.5", "limit_credits": "12.0",
            "remaining_percent": 79.1666666667, "resets_at": 1792800000,
        })
        self.assertTrue(provider["credits"]["active"])

    def test_hidden_off_unlimited_zero_and_reached_states_are_distinct(self) -> None:
        cases = {
            "hidden": ({"hasCredits": True, "unlimited": False, "balance": None}, "current", False, False),
            "off": ({"hasCredits": False, "unlimited": False, "balance": None}, "off", False, False),
            "unlimited": ({"hasCredits": True, "unlimited": True, "balance": None}, "current", False, False),
            "zero": ({"hasCredits": True, "unlimited": False, "balance": "0"}, "current", True, False),
        }
        for name, (raw, status, exhausted, low) in cases.items():
            with self.subTest(name=name):
                credits = normalize_codex_result(self.result(raw), observed_at=100)["credits"]
                self.assertEqual(credits["status"], status)
                self.assertEqual(credits["exhausted"], exhausted)
                self.assertEqual(credits["low"], low)

    def test_reached_spend_control_keeps_truthful_balance(self) -> None:
        credits = normalize_codex_result(self.result(
            {"hasCredits": True, "unlimited": False, "balance": "9.5"},
            {"limit": "12", "used": "12", "remainingPercent": 0, "resetsAt": 200},
            spend_control_reached=True,
        ), observed_at=100)["credits"]
        self.assertEqual(credits["balance"]["amount_credits"], "9.5")
        self.assertTrue(credits["exhausted"])
        self.assertEqual(credits["spend"]["remaining_percent"], 0.0)

    def test_contradictory_and_ambiguous_values_are_malformed(self) -> None:
        cases = [
            {"hasCredits": False, "unlimited": False, "balance": "4"},
            {"hasCredits": True, "unlimited": True, "balance": "4"},
            {"hasCredits": True, "unlimited": False, "balance": "09.5"},
            {"hasCredits": True, "unlimited": False, "balance": "NaN"},
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                credits = normalize_codex_result(self.result(raw), observed_at=100)["credits"]
                self.assertEqual(credits["status"], "unavailable")
                self.assertEqual(credits["error_code"], "malformed")

    def test_limit_thresholds_absence_and_invalid_numbers(self) -> None:
        low = normalize_codex_result(self.result(
            {"hasCredits": True, "unlimited": False, "balance": "9.5"},
            {"limit": "10", "used": "8", "remainingPercent": 20, "resetsAt": 500},
        ), observed_at=100)["credits"]
        no_denominator = normalize_codex_result(self.result(
            {"hasCredits": True, "unlimited": False, "balance": "9.5"}
        ), observed_at=100)["credits"]
        over = normalize_codex_result(self.result(
            {"hasCredits": True, "unlimited": False, "balance": "1"},
            {"limit": "10", "used": "12", "remainingPercent": 0, "resetsAt": 500},
        ), observed_at=100)["credits"]
        reached = normalize_codex_result(self.result(
            {"hasCredits": True, "unlimited": False, "balance": None},
            spend_control_reached=True,
        ), observed_at=100)["credits"]
        absent = normalize_codex_result(self.result(None), observed_at=100)["credits"]

        self.assertTrue(low["low"])
        self.assertEqual(low["spend"]["resets_at"], 500)
        self.assertFalse(no_denominator["low"])
        self.assertEqual(over["spend"]["remaining_percent"], 0.0)
        self.assertTrue(over["exhausted"])
        self.assertTrue(reached["exhausted"])
        self.assertFalse(reached["low"])
        self.assertEqual(absent, unavailable_credits("credits", "unsupported"))

        for invalid in (True, "-1", "1e2", "Infinity"):
            with self.subTest(invalid=invalid):
                credits = normalize_codex_result(self.result({
                    "hasCredits": True, "unlimited": False, "balance": invalid,
                }), observed_at=100)["credits"]
                self.assertEqual(credits["status"], "unavailable")
                self.assertEqual(credits["error_code"], "malformed")

    def test_spend_distinguishes_absent_fields_from_supplied_invalid_decimals(self) -> None:
        absent_fields = normalize_codex_result(self.result(
            {"hasCredits": True, "unlimited": False, "balance": "9.5"},
            {"remainingPercent": 50, "resetsAt": 500},
        ), observed_at=100)["credits"]
        self.assertEqual(absent_fields["status"], "current")
        self.assertEqual(absent_fields["spend"], {
            "used_credits": None, "limit_credits": None,
            "remaining_percent": 50.0, "resets_at": 500,
        })

        for field, bad_value in (
            ("limit", "1e2"), ("limit", "-5"), ("used", "1e2"), ("used", "-5"),
        ):
            with self.subTest(field=field, bad_value=bad_value):
                individual_limit = {"limit": "10", "used": "1", "remainingPercent": 50, "resetsAt": 500}
                individual_limit[field] = bad_value
                credits = normalize_codex_result(self.result(
                    {"hasCredits": True, "unlimited": False, "balance": "9.5"},
                    individual_limit,
                ), observed_at=100)["credits"]
                self.assertEqual(credits["status"], "unavailable")
                self.assertEqual(credits["error_code"], "malformed")

    def test_missing_spend_control_reached_is_malformed(self) -> None:
        raw_result = {
            "ordinaryUsageAllowed": True,
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 57, "windowDurationMins": 10080, "resetsAt": 5000},
                "secondary": None,
                "credits": {"hasCredits": True, "unlimited": False, "balance": "9.5"},
                "individualLimit": None,
            },
            "rateLimitsByLimitId": {},
        }
        credits = normalize_codex_result(raw_result, observed_at=100)["credits"]
        self.assertEqual(credits, unavailable_credits("credits", "malformed"))


class CreditFreshnessTests(unittest.TestCase):
    def current(self, *, unit: str = "credits", observed_at: int = 100,
                status: str = "current", active: bool = True) -> dict:
        result = unavailable_credits(unit, "provider_unavailable")
        result.update({
            "status": status, "freshness": "current", "observed_at": observed_at,
            "source": "codex-app-server" if unit == "credits" else "claude-oauth-usage",
            "error_code": None, "active": active,
        })
        if unit == "credits":
            result["balance"] = {"available": True, "amount_credits": "9.5", "unlimited": False}
        return result

    def test_transient_failure_retains_current_and_off_as_bounded_stale(self) -> None:
        for status, active in (("current", True), ("off", False)):
            with self.subTest(status=status):
                merged = merge_credit_refresh_result(
                    self.current(status=status, active=active), None, now=999,
                    error_code="provider_unavailable",
                )
                self.assertEqual(merged["status"], status)
                self.assertEqual(merged["freshness"], "stale")
                self.assertEqual(merged["observed_at"], 100)

    def test_expired_negative_age_and_auth_rejection_are_unavailable(self) -> None:
        cases = [(1001, "timeout"), (99, "timeout"), (200, "auth_rejected")]
        for now, code in cases:
            with self.subTest(now=now, code=code):
                merged = merge_credit_refresh_result(self.current(), None, now, code)
                self.assertEqual(merged["status"], "unavailable")
                self.assertIsNone(merged["observed_at"])
                self.assertIsNone(merged["balance"])
                self.assertIsNone(merged["source"])
                self.assertEqual(merged["error_code"], code)

    def test_expired_cache_uses_the_current_failure_code_not_a_cache_default(self) -> None:
        for code in ("timeout", "rate_limited", "provider_unavailable"):
            with self.subTest(code=code):
                merged = merge_credit_refresh_result(self.current(), None, now=1001, error_code=code)
                self.assertEqual(merged["error_code"], code)

    def test_age_credit_state_sanitizes_untrusted_cached_unit_and_error_code(self) -> None:
        corrupted_expired = {
            "status": "current", "freshness": "current", "unit": "tokens",
            "observed_at": 100, "error_code": "observation_unavailable",
        }
        self.assertEqual(
            age_credit_state(corrupted_expired, now=1001),
            unavailable_credits("credits", "provider_unavailable"),
        )

        corrupted_bad_observed_at = {
            "status": "current", "freshness": "current", "unit": "tokens",
            "observed_at": "not-a-timestamp", "error_code": "observation_unavailable",
        }
        self.assertEqual(
            age_credit_state(corrupted_bad_observed_at, now=100),
            unavailable_credits("credits", "provider_unavailable"),
        )

    def test_merge_credit_refresh_result_sanitizes_untrusted_unit_and_error_code(self) -> None:
        previous = {
            "status": "current", "freshness": "current", "unit": "tokens",
            "observed_at": 100, "error_code": None,
        }
        merged = merge_credit_refresh_result(previous, None, now=100, error_code="observation_unavailable")
        self.assertEqual(merged, unavailable_credits("credits", "provider_unavailable"))

        merged_no_previous = merge_credit_refresh_result(
            None, None, now=100, error_code="observation_unavailable"
        )
        self.assertEqual(merged_no_previous, unavailable_credits("credits", "provider_unavailable"))

    def test_cached_stale_aging_reuses_the_retained_safe_error_code(self) -> None:
        stale = self.current(observed_at=100)
        stale["freshness"] = "stale"
        stale["error_code"] = "timeout"
        bounded = age_credit_state(stale, now=999)
        expired = age_credit_state(stale, now=1001)
        self.assertEqual(bounded["freshness"], "stale")
        self.assertEqual(expired, unavailable_credits("credits", "timeout"))

    def test_cached_current_observation_with_negative_age_is_unavailable(self) -> None:
        future = self.current(observed_at=101)
        self.assertEqual(
            age_credit_state(future, now=100),
            unavailable_credits("credits", "provider_unavailable"),
        )


class MergeRefreshCreditUnitTests(unittest.TestCase):
    def test_fresh_claude_snapshot_without_credits_key_gets_currency_unit(self) -> None:
        fresh = {
            "id": "claude", "status": "current", "observed_at": 100,
            "source": "Claude Code status-line rate_limits",
            "windows": [], "model_limits": [], "error_code": None,
        }
        merged = merge_refresh_result(None, fresh, now=100)
        self.assertEqual(merged["credits"], unavailable_credits("currency", "provider_unavailable"))

    def test_failed_claude_refresh_without_prior_credits_gets_currency_unit(self) -> None:
        previous = {
            "id": "claude", "status": "unavailable", "observed_at": None,
            "source": None, "windows": [], "model_limits": [], "error_code": "provider_unavailable",
        }
        merged = merge_refresh_result(previous, None, now=200, error_code="timeout")
        self.assertEqual(merged["credits"], unavailable_credits("currency", "provider_unavailable"))

    def test_no_previous_no_fresh_defaults_unit_to_credits(self) -> None:
        merged = merge_refresh_result(None, None, now=100)
        self.assertEqual(merged["credits"], unavailable_credits("credits", "provider_unavailable"))

    def test_credit_unit_keyword_overrides_provider_derived_default(self) -> None:
        previous = {
            "id": "some-future-provider", "status": "unavailable", "observed_at": None,
            "windows": [], "model_limits": [],
        }
        merged = merge_refresh_result(previous, None, now=200, credit_unit="currency")
        self.assertEqual(merged["credits"], unavailable_credits("currency", "provider_unavailable"))

    def test_credit_error_code_keyword_overrides_default_for_failed_refresh(self) -> None:
        previous = {
            "id": "claude", "status": "unavailable", "observed_at": None,
            "windows": [], "model_limits": [],
        }
        merged = merge_refresh_result(previous, None, now=200, credit_error_code="timeout")
        self.assertEqual(merged["credits"], unavailable_credits("currency", "timeout"))

    def test_corrupted_cached_unit_is_retagged_to_the_provider_derived_default(self) -> None:
        previous = {
            "id": "claude", "status": "unavailable", "observed_at": None,
            "windows": [], "model_limits": [],
            "credits": {
                "status": "current", "freshness": "current", "unit": "tokens",
                "currency": "USD", "minor_unit_scale": 2, "balance": None,
                "spend": {"used_minor": 10, "limit_minor": 100,
                          "remaining_percent": 90.0, "resets_at": None},
                "active": True, "low": False, "exhausted": False,
                "observed_at": 100, "source": "claude-oauth-usage", "error_code": None,
            },
        }
        merged = merge_refresh_result(previous, None, now=1001, error_code="timeout")
        self.assertEqual(merged["credits"]["unit"], "currency")


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
