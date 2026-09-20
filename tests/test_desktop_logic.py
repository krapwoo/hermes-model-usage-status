from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "support" / "run_desktop_helpers.mjs"
PLUGIN = ROOT / "desktop" / "plugin.js"
FIXTURES = ROOT / "tests" / "fixtures" / "credit_usage_states.json"

FORBIDDEN_KEYS = {"Authorization", "extra_usage", "accessToken", "refreshToken"}


def call_helper(name: str, *args):
    result = subprocess.run(
        ["node", "--experimental-vm-modules", str(RUNNER), str(PLUGIN)],
        input=json.dumps({"function": name, "args": args}),
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def unavailable_currency_credits() -> dict:
    return {
        "status": "unavailable", "freshness": None, "unit": "currency",
        "currency": None, "minor_unit_scale": None, "balance": None,
        "spend": None, "active": False, "low": False, "exhausted": False,
        "observed_at": None, "source": None, "error_code": "provider_unavailable",
    }


def unavailable_credit_units() -> dict:
    return {
        "status": "unavailable", "freshness": None, "unit": "credits",
        "currency": None, "minor_unit_scale": None, "balance": None,
        "spend": None, "active": False, "low": False, "exhausted": False,
        "observed_at": None, "source": None, "error_code": "unsupported",
    }


def claude_credits(used_minor, limit_minor, *, active, low, exhausted,
                    freshness="current", currency="USD", scale=2) -> dict:
    return {
        "status": "current", "freshness": freshness, "unit": "currency",
        "currency": currency, "minor_unit_scale": scale, "balance": None,
        "spend": {"used_minor": used_minor, "limit_minor": limit_minor,
                  "remaining_percent": max(limit_minor - used_minor, 0) / limit_minor * 100,
                  "resets_at": None},
        "active": active, "low": low, "exhausted": exhausted,
        "observed_at": 100, "source": "claude-oauth-usage", "error_code": None,
    }


def codex_credits(*, balance=None, spend=None, active, low, exhausted,
                   freshness="current", error_code=None) -> dict:
    return {
        "status": "current", "freshness": freshness, "unit": "credits",
        "currency": None, "minor_unit_scale": None, "balance": balance,
        "spend": spend, "active": active, "low": low, "exhausted": exhausted,
        "observed_at": 100, "source": "codex-app-server", "error_code": error_code,
    }


class DesktopCreditLogicTests(unittest.TestCase):
    def test_currency_scale_is_provider_reported_and_exact(self) -> None:
        self.assertEqual(call_helper("formatCurrencyMinor", 1840, "USD", 2, "en-US"), "$18.40")
        self.assertEqual(call_helper("formatCurrencyMinor", 10000, "USD", 2, "en-US"), "$100")
        self.assertEqual(call_helper("formatCurrencyMinor", 18400, "USD", 3, "en-US"), "$18.400")
        self.assertNotEqual(call_helper("formatCurrencyMinor", 1840, "JPY", 2, "en-US"), "¥1,840")

    def test_format_currency_minor_is_total_for_malformed_input(self) -> None:
        self.assertIsNone(call_helper("formatCurrencyMinor", -1, "USD", 2, "en-US"))
        self.assertIsNone(call_helper("formatCurrencyMinor", 1.5, "USD", 2, "en-US"))
        self.assertIsNone(call_helper("formatCurrencyMinor", 100, "usd", 2, "en-US"))
        self.assertIsNone(call_helper("formatCurrencyMinor", 100, "US$", 2, "en-US"))
        self.assertIsNone(call_helper("formatCurrencyMinor", 100, "USD", -1, "en-US"))
        self.assertIsNone(call_helper("formatCurrencyMinor", 100, "USD", 7, "en-US"))
        self.assertIsNone(call_helper("formatCurrencyMinor", 100, "USD", 1.5, "en-US"))
        self.assertIsNone(call_helper("formatCurrencyMinor", None, "USD", 2, "en-US"))

    def test_controlling_allowance_selection(self) -> None:
        windows = [
            {"label": "5h", "duration_minutes": 300, "remaining_percent": 80},
            {"label": "Week", "duration_minutes": 10080, "remaining_percent": 0},
        ]
        self.assertEqual(call_helper("selectCompactAllowance", windows)["label"], "Week")
        tie = [dict(windows[0], remaining_percent=42), dict(windows[1], remaining_percent=42)]
        self.assertEqual(call_helper("selectCompactAllowance", tie)["label"], "Week")

    def test_select_compact_allowance_ignores_model_windows_and_malformed_entries(self) -> None:
        self.assertIsNone(call_helper("selectCompactAllowance", None))
        self.assertIsNone(call_helper("selectCompactAllowance", []))
        only_model_shaped = [{"label": "Opus week", "duration_minutes": 10080, "remaining_percent": 1}]
        # A model-limit-shaped window is only excluded from selection because callers pass
        # provider.windows (account windows), never provider.model_limits[*].windows; the
        # helper itself has no way to distinguish them beyond duration, so this documents
        # that scoping responsibility lives with the caller (compactLabel), not the helper.
        self.assertEqual(call_helper("selectCompactAllowance", only_model_shaped)["label"], "Opus week")
        malformed = [{"label": "5h", "duration_minutes": 300, "remaining_percent": "not-a-number"}]
        self.assertIsNone(call_helper("selectCompactAllowance", malformed))

    def test_compact_labels_never_exceed_allowance_plus_credit_summary(self) -> None:
        provider = {
            "status": "current",
            "windows": [
                {"label": "5h", "duration_minutes": 300, "remaining_percent": 42},
                {"label": "Week", "duration_minutes": 10080, "remaining_percent": 60},
            ],
            "model_limits": [{
                "id": "claude:opus", "label": "Opus",
                "windows": [{"label": "Opus week", "duration_minutes": 10080,
                             "remaining_percent": 1}],
            }],
            "credits": {
                "status": "current", "freshness": "current", "unit": "currency",
                "currency": "USD", "minor_unit_scale": 2, "balance": None,
                "spend": {"used_minor": 1840, "limit_minor": 10000,
                          "remaining_percent": 81.6, "resets_at": None},
                "active": True, "low": False, "exhausted": False,
                "observed_at": 100, "source": "claude-oauth-usage", "error_code": None,
            },
        }
        self.assertEqual(call_helper("compactLabel", "claude", provider, "en-US"),
                         "Claude 5h 42% · Credits $18.40/$100")

    def test_codex_balance_uses_native_credit_copy(self) -> None:
        credits = {
            "status": "current", "freshness": "current", "unit": "credits",
            "currency": None, "minor_unit_scale": None,
            "balance": {"available": True, "amount_credits": "9.5", "unlimited": False},
            "spend": None, "active": True, "low": False, "exhausted": False,
            "observed_at": 100, "source": "codex-app-server", "error_code": None,
        }
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "9.5 credits left")
        self.assertEqual(presentation["compact"], "9.5 credits left")
        self.assertNotIn("$", json.dumps(presentation))

    def test_stale_exhausted_codex_balance_remains_consequential_without_active(self) -> None:
        provider = {
            "status": "stale",
            "windows": [{"label": "Week", "duration_minutes": 10080,
                         "remaining_percent": 0}],
            "model_limits": [],
            "credits": {
                "status": "current", "freshness": "stale", "unit": "credits",
                "currency": None, "minor_unit_scale": None,
                "balance": {"available": True, "amount_credits": "0", "unlimited": False},
                "spend": None, "active": False, "low": True, "exhausted": True,
                "observed_at": 100, "source": "codex-app-server", "error_code": "timeout",
            },
        }
        label = call_helper("compactLabel", "codex", provider, "en-US")
        self.assertEqual(label, "Codex Week 0% · 0 credits left · Exhausted · stale")

    # --- creditPresentation(): absent/malformed is always Unavailable, never Off/zero/Exhausted ---

    def test_absent_or_malformed_credits_are_unavailable_never_zero_off_or_exhausted(self) -> None:
        for credits in (None, {}, {"status": "current"}, {"status": "weird"}, "not-an-object", 42, []):
            with self.subTest(credits=credits):
                presentation = call_helper("creditPresentation", "claude", credits, "en-US")
                self.assertEqual(presentation, {
                    "primary": "Unavailable", "supporting": None, "monthly": None,
                    "compact": None, "consequential": False,
                })

    # --- Claude currency states ---

    def test_claude_zero_spend_is_current_but_not_consequential(self) -> None:
        credits = claude_credits(0, 10000, active=False, low=False, exhausted=False)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "$100 left", "supporting": None, "monthly": "$100 monthly limit",
            "compact": "Credits $0/$100", "consequential": False,
        })

    def test_claude_active_spend_is_consequential(self) -> None:
        credits = claude_credits(1840, 10000, active=True, low=False, exhausted=False)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "$81.60 left", "supporting": None, "monthly": "$100 monthly limit",
            "compact": "Credits $18.40/$100", "consequential": True,
        })

    def test_claude_low_qualifies_the_fact_not_a_new_fact(self) -> None:
        credits = claude_credits(8500, 10000, active=True, low=True, exhausted=False)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$15 left · Low")
        self.assertEqual(presentation["compact"], "Credits $85/$100 · Low")
        self.assertTrue(presentation["consequential"])

    def test_claude_exhausted_suppresses_low(self) -> None:
        credits = claude_credits(10000, 10000, active=True, low=True, exhausted=True)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$0 left · Exhausted")
        self.assertEqual(presentation["compact"], "Credits $100/$100 · Exhausted")
        self.assertNotIn("Low", presentation["primary"])
        self.assertNotIn("Low", presentation["compact"])

    def test_claude_over_cap_clamps_remaining_to_zero_and_is_exhausted(self) -> None:
        credits = claude_credits(12000, 10000, active=True, low=True, exhausted=True)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$0 left · Exhausted")
        self.assertEqual(presentation["compact"], "Credits $120/$100 · Exhausted")

    def test_claude_off_never_shows_a_number(self) -> None:
        credits = {
            "status": "off", "freshness": "current", "unit": "currency",
            "currency": None, "minor_unit_scale": None, "balance": None, "spend": None,
            "active": False, "low": False, "exhausted": False,
            "observed_at": 100, "source": "claude-oauth-usage", "error_code": None,
        }
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "Off", "supporting": None, "monthly": None,
            "compact": None, "consequential": False,
        })

    def test_claude_stale_credit_is_unchanged_by_presentation_but_labeled_by_compact(self) -> None:
        credits = claude_credits(1840, 10000, active=True, low=False, exhausted=False, freshness="stale")
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$81.60 left")
        provider = {"status": "current", "windows": [
            {"label": "5h", "duration_minutes": 300, "remaining_percent": 90},
            {"label": "Week", "duration_minutes": 10080, "remaining_percent": 80},
        ], "model_limits": [], "credits": credits}
        label = call_helper("compactLabel", "claude", provider, "en-US")
        self.assertTrue(label.endswith(" · stale"), label)

    def test_claude_unavailable_credits_never_enter_compact_label(self) -> None:
        provider = {
            "status": "current",
            "windows": [{"label": "5h", "duration_minutes": 300, "remaining_percent": 90},
                        {"label": "Week", "duration_minutes": 10080, "remaining_percent": 80}],
            "model_limits": [],
            "credits": unavailable_currency_credits(),
        }
        self.assertEqual(call_helper("compactLabel", "claude", provider, "en-US"),
                         "Claude 5h 90% · Week 80%")

    def test_non_consequential_credits_preserve_allowance_only_output_byte_for_byte(self) -> None:
        provider_without_credits = {
            "status": "current",
            "windows": [{"label": "5h", "duration_minutes": 300, "remaining_percent": 90},
                        {"label": "Week", "duration_minutes": 10080, "remaining_percent": 80}],
            "model_limits": [],
        }
        provider_with_inactive_credits = dict(
            provider_without_credits,
            credits=claude_credits(0, 10000, active=False, low=False, exhausted=False),
        )
        without = call_helper("compactLabel", "claude", provider_without_credits, "en-US")
        with_inactive = call_helper("compactLabel", "claude", provider_with_inactive_credits, "en-US")
        self.assertEqual(without, with_inactive)
        self.assertEqual(without, "Claude 5h 90% · Week 80%")

    def test_no_valid_account_allowance_shows_only_credit_fact(self) -> None:
        provider = {
            "status": "current", "windows": [], "model_limits": [],
            "credits": claude_credits(1840, 10000, active=True, low=False, exhausted=False),
        }
        self.assertEqual(call_helper("compactLabel", "claude", provider, "en-US"),
                         "Claude Credits $18.40/$100")

    # --- Codex native-credit states ---

    def test_codex_unlimited_balance(self) -> None:
        credits = codex_credits(balance={"available": True, "amount_credits": None, "unlimited": True},
                                 active=False, low=False, exhausted=False)
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "Unlimited", "supporting": None, "monthly": None,
            "compact": "Unlimited", "consequential": False,
        })

    def test_codex_zero_balance_without_spend_is_plain_exhausted(self) -> None:
        credits = codex_credits(balance={"available": True, "amount_credits": "0", "unlimited": False},
                                 active=False, low=False, exhausted=True)
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "0 credits left · Exhausted")
        self.assertEqual(presentation["compact"], "0 credits left · Exhausted")
        self.assertTrue(presentation["consequential"])

    def test_codex_hidden_balance_shows_available_with_support_copy(self) -> None:
        credits = codex_credits(balance={"available": True, "amount_credits": None, "unlimited": False},
                                 active=False, low=False, exhausted=False)
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "Available")
        self.assertIsNotNone(presentation["supporting"])
        self.assertFalse(presentation["consequential"])

    def test_codex_off_never_shows_a_number(self) -> None:
        credits = {
            "status": "off", "freshness": "current", "unit": "credits",
            "currency": None, "minor_unit_scale": None, "balance": None, "spend": None,
            "active": False, "low": False, "exhausted": False,
            "observed_at": 100, "source": "codex-app-server", "error_code": None,
        }
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "Off", "supporting": None, "monthly": None,
            "compact": None, "consequential": False,
        })

    def test_codex_stale_is_unchanged_by_presentation_but_labeled_by_compact(self) -> None:
        credits = codex_credits(balance={"available": True, "amount_credits": "9.5", "unlimited": False},
                                 active=True, low=False, exhausted=False, freshness="stale")
        provider = {"status": "current",
                    "windows": [{"label": "Week", "duration_minutes": 10080, "remaining_percent": 80}],
                    "model_limits": [], "credits": credits}
        label = call_helper("compactLabel", "codex", provider, "en-US")
        self.assertTrue(label.endswith(" · stale"), label)

    def test_codex_unavailable_credits_never_enter_compact_label(self) -> None:
        provider = {
            "status": "current",
            "windows": [{"label": "Week", "duration_minutes": 10080, "remaining_percent": 80}],
            "model_limits": [],
            "credits": unavailable_credit_units(),
        }
        self.assertEqual(call_helper("compactLabel", "codex", provider, "en-US"), "Codex Week 80%")

    def test_codex_monthly_low_with_balance(self) -> None:
        credits = codex_credits(
            balance={"available": True, "amount_credits": "9.5", "unlimited": False},
            spend={"used_credits": "8", "limit_credits": "10", "remaining_percent": 20.0, "resets_at": 6500},
            active=True, low=True, exhausted=False,
        )
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "9.5 credits left · Monthly limit low")
        self.assertEqual(presentation["monthly"], "10 credits monthly limit")

    def test_codex_monthly_reached_with_balance(self) -> None:
        credits = codex_credits(
            balance={"available": True, "amount_credits": "9.5", "unlimited": False},
            spend={"used_credits": "10", "limit_credits": "10", "remaining_percent": 0.0, "resets_at": 6500},
            active=True, low=True, exhausted=True,
        )
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "9.5 credits left · Monthly limit reached")
        self.assertNotIn("Low", presentation["primary"])

    def test_codex_monthly_reached_without_any_balance(self) -> None:
        credits = codex_credits(balance=None, spend=None, active=True, low=False, exhausted=True)
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "Monthly limit reached")
        self.assertTrue(presentation["consequential"])


class DesktopRefreshFailureTests(unittest.TestCase):
    def test_refresh_usage_applies_only_a_successful_result(self) -> None:
        result = call_helper("refreshUsage", {"$resolve": {"mode": "refreshed"}})
        self.assertEqual(result, {"ok": True})

    def test_refresh_usage_swallows_rejection_without_an_unhandled_promise(self) -> None:
        # If refreshUsage let the rejection propagate, the harness's own
        # `await helper(...)` at the top level would reject unhandled, Node
        # would exit non-zero, and this call_helper() would raise
        # CalledProcessError instead of returning a value.
        result = call_helper("refreshUsage", {"$reject": "network down"})
        self.assertEqual(result, {"ok": False})


class DesktopFixtureTests(unittest.TestCase):
    def test_fixture_matrix_is_schema_v2_and_secret_free_and_helper_safe(self) -> None:
        fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertEqual(
            set(fixtures.keys()),
            {"allowance_only", "active", "low", "exhausted", "off", "unlimited",
             "hidden", "unavailable", "stale_auth"},
        )

        def assert_no_forbidden_keys(value) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(key, FORBIDDEN_KEYS, key)
                    assert_no_forbidden_keys(item)
            elif isinstance(value, list):
                for item in value:
                    assert_no_forbidden_keys(item)

        for name, snapshot in fixtures.items():
            with self.subTest(fixture=name):
                self.assertEqual(snapshot["schema_version"], 2)
                assert_no_forbidden_keys(snapshot)
                for provider_id, provider in snapshot["providers"].items():
                    label = call_helper("compactLabel", provider_id, provider, "en-US")
                    self.assertIsInstance(label, str)
                    presentation = call_helper(
                        "creditPresentation", provider_id, provider["credits"], "en-US"
                    )
                    self.assertIsInstance(presentation, dict)
                    self.assertIn("primary", presentation)


if __name__ == "__main__":
    unittest.main()
