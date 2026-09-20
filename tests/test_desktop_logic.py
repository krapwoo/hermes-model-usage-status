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


def collect_text(node) -> list[str]:
    """Recursively collect every string leaf from a serialized {type, props, key}
    JSX-stub tree (as produced by the Node VM harness), in document order."""
    texts: list[str] = []
    if isinstance(node, str):
        texts.append(node)
    elif isinstance(node, dict):
        children = node.get("props", {}).get("children") if isinstance(node.get("props"), dict) else None
        if isinstance(children, list):
            for child in children:
                texts.extend(collect_text(child))
        elif children is not None:
            texts.extend(collect_text(children))
    return texts


def collect_types(node) -> list:
    """Recursively collect every node `type` value from a serialized JSX-stub tree."""
    types: list = []
    if isinstance(node, dict):
        if "type" in node:
            types.append(node["type"])
        children = node.get("props", {}).get("children") if isinstance(node.get("props"), dict) else None
        if isinstance(children, list):
            for child in children:
                types.extend(collect_types(child))
        elif isinstance(children, dict):
            types.extend(collect_types(children))
    return types


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

    # --- Claude currency states: exact spec oracles (design doc lines 245-257) ---

    def test_claude_enabled_zero_spend_matches_the_approved_oracle(self) -> None:
        credits = claude_credits(0, 10000, active=False, low=False, exhausted=False)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "$0 of $100 used", "supporting": "$100 remains this month", "monthly": None,
            "compact": "Credits $0/$100", "consequential": False,
        })

    def test_claude_active_matches_the_approved_oracle(self) -> None:
        credits = claude_credits(1840, 10000, active=True, low=False, exhausted=False)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "$18.40 of $100 used", "supporting": "$81.60 remains this month", "monthly": None,
            "compact": "Credits $18.40/$100", "consequential": True,
        })

    def test_claude_low_matches_the_approved_oracle(self) -> None:
        credits = claude_credits(8500, 10000, active=True, low=True, exhausted=False)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$85 of $100 used · Low")
        self.assertEqual(presentation["supporting"], "$15 remains this month")
        self.assertEqual(presentation["compact"], "Credits $85/$100 · Low")
        self.assertTrue(presentation["consequential"])

    def test_claude_exhausted_matches_the_approved_oracle_and_suppresses_low(self) -> None:
        credits = claude_credits(10000, 10000, active=True, low=True, exhausted=True)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$100 of $100 used · Exhausted")
        self.assertEqual(presentation["supporting"], "$0 remains this month")
        self.assertEqual(presentation["compact"], "Credits $100/$100 · Exhausted")
        self.assertNotIn("Low", presentation["primary"])
        self.assertNotIn("Low", presentation["compact"])

    def test_claude_over_cap_matches_the_approved_oracle(self) -> None:
        credits = claude_credits(12000, 10000, active=True, low=True, exhausted=True)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$120 of $100 used · Exhausted")
        self.assertEqual(presentation["supporting"], "$0 remains this month")
        self.assertEqual(presentation["compact"], "Credits $120/$100 · Exhausted")

    def test_claude_off_uses_the_approved_support_copy(self) -> None:
        credits = {
            "status": "off", "freshness": "current", "unit": "currency",
            "currency": None, "minor_unit_scale": None, "balance": None, "spend": None,
            "active": False, "low": False, "exhausted": False,
            "observed_at": 100, "source": "claude-oauth-usage", "error_code": None,
        }
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "Off", "supporting": "Paid extra usage is not enabled.", "monthly": None,
            "compact": None, "consequential": False,
        })

    def test_claude_stale_appends_stale_to_primary_exactly_once(self) -> None:
        credits = claude_credits(1840, 10000, active=True, low=False, exhausted=False, freshness="stale")
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$18.40 of $100 used · stale")
        self.assertEqual(presentation["primary"].count(" · stale"), 1)
        self.assertEqual(presentation["supporting"], "$81.60 remains this month")
        self.assertNotIn(" · stale", presentation["compact"])
        provider = {"status": "current", "windows": [
            {"label": "5h", "duration_minutes": 300, "remaining_percent": 90},
            {"label": "Week", "duration_minutes": 10080, "remaining_percent": 80},
        ], "model_limits": [], "credits": credits}
        label = call_helper("compactLabel", "claude", provider, "en-US")
        self.assertEqual(label.count(" · stale"), 1)
        self.assertTrue(label.endswith(" · stale"), label)

    def test_claude_stale_off_matches_the_approved_oracle(self) -> None:
        credits = {
            "status": "off", "freshness": "stale", "unit": "currency",
            "currency": None, "minor_unit_scale": None, "balance": None, "spend": None,
            "active": False, "low": False, "exhausted": False,
            "observed_at": 100, "source": "claude-oauth-usage", "error_code": None,
        }
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "Off · stale")
        self.assertEqual(presentation["supporting"], "Paid extra usage is not enabled.")

    def test_claude_amounts_agree_between_label_and_popover_at_usd_scale_3(self) -> None:
        credits = claude_credits(18400, 100000, active=True, low=False, exhausted=False, scale=3)
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "$18.400 of $100 used")
        self.assertEqual(presentation["supporting"], "$81.600 remains this month")
        self.assertEqual(presentation["compact"], "Credits $18.400/$100")
        provider = {"status": "current", "windows": [], "model_limits": [], "credits": credits}
        label = call_helper("compactLabel", "claude", provider, "en-US")
        self.assertIn(presentation["compact"], label)

    def test_claude_amounts_agree_between_label_and_popover_for_a_non_usd_scale(self) -> None:
        # JPY's own standard minor-unit scale is 0, but the provider reports scale 2 here;
        # the renderer must use the provider-reported scale, never the currency's default.
        credits = claude_credits(1840, 10000, active=True, low=False, exhausted=False, currency="JPY")
        presentation = call_helper("creditPresentation", "claude", credits, "en-US")
        self.assertEqual(presentation["primary"], "¥18.40 of ¥100 used")
        self.assertEqual(presentation["supporting"], "¥81.60 remains this month")
        self.assertEqual(presentation["compact"], "Credits ¥18.40/¥100")
        provider = {"status": "current", "windows": [], "model_limits": [], "credits": credits}
        label = call_helper("compactLabel", "claude", provider, "en-US")
        self.assertIn(presentation["compact"], label)

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
        self.assertIsNone(presentation["monthly"])
        self.assertTrue(presentation["consequential"])

    def test_codex_zero_balance_with_unreached_monthly_limit_stays_plain_exhausted(self) -> None:
        # A numeric zero balance is independently exhausted even though the separate
        # monthly spend limit still has remaining headroom; the qualifier must not
        # borrow "Monthly limit" wording it did not earn, and the monthly row must
        # truthfully report its own unreached values.
        credits = codex_credits(
            balance={"available": True, "amount_credits": "0", "unlimited": False},
            spend={"used_credits": "5", "limit_credits": "10", "remaining_percent": 50.0, "resets_at": 6500},
            active=False, low=False, exhausted=True,
        )
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "0 credits left · Exhausted")
        self.assertEqual(presentation["compact"], "0 credits left · Exhausted")
        self.assertNotIn("Monthly limit", presentation["primary"])
        self.assertEqual(presentation["monthly"], {
            "used": "5", "limit": "10", "remainingPercent": 50.0, "resetsAt": 6500, "reached": False,
        })

    def test_codex_hidden_balance_uses_the_approved_support_copy(self) -> None:
        credits = codex_credits(balance={"available": True, "amount_credits": None, "unlimited": False},
                                 active=False, low=False, exhausted=False)
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "Available")
        self.assertEqual(presentation["supporting"], "The provider did not return a balance")
        self.assertFalse(presentation["consequential"])

    def test_codex_off_uses_the_approved_support_copy(self) -> None:
        credits = {
            "status": "off", "freshness": "current", "unit": "credits",
            "currency": None, "minor_unit_scale": None, "balance": None, "spend": None,
            "active": False, "low": False, "exhausted": False,
            "observed_at": 100, "source": "codex-app-server", "error_code": None,
        }
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation, {
            "primary": "Off", "supporting": "Credit-backed usage is not available", "monthly": None,
            "compact": None, "consequential": False,
        })

    def test_codex_stale_off_matches_the_approved_oracle(self) -> None:
        credits = {
            "status": "off", "freshness": "stale", "unit": "credits",
            "currency": None, "minor_unit_scale": None, "balance": None, "spend": None,
            "active": False, "low": False, "exhausted": False,
            "observed_at": 100, "source": "codex-app-server", "error_code": None,
        }
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "Off · stale")
        self.assertEqual(presentation["supporting"], "Credit-backed usage is not available")

    def test_codex_stale_hidden_balance_retains_its_support_copy(self) -> None:
        # A stale hidden-balance state must keep its own explanation; the age line is
        # additional, never a replacement for provider-specific supporting copy.
        credits = codex_credits(balance={"available": True, "amount_credits": None, "unlimited": False},
                                 active=False, low=False, exhausted=False, freshness="stale")
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "Available · stale")
        self.assertEqual(presentation["supporting"], "The provider did not return a balance")

    def test_codex_stale_appends_stale_to_primary_exactly_once(self) -> None:
        credits = codex_credits(balance={"available": True, "amount_credits": "9.5", "unlimited": False},
                                 active=True, low=False, exhausted=False, freshness="stale")
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "9.5 credits left · stale")
        self.assertEqual(presentation["primary"].count(" · stale"), 1)
        self.assertNotIn(" · stale", presentation["compact"])
        provider = {"status": "current",
                    "windows": [{"label": "Week", "duration_minutes": 10080, "remaining_percent": 80}],
                    "model_limits": [], "credits": credits}
        label = call_helper("compactLabel", "codex", provider, "en-US")
        self.assertEqual(label.count(" · stale"), 1)
        self.assertTrue(label.endswith(" · stale"), label)

    def test_codex_unavailable_credits_never_enter_compact_label(self) -> None:
        provider = {
            "status": "current",
            "windows": [{"label": "Week", "duration_minutes": 10080, "remaining_percent": 80}],
            "model_limits": [],
            "credits": unavailable_credit_units(),
        }
        self.assertEqual(call_helper("compactLabel", "codex", provider, "en-US"), "Codex Week 80%")

    def test_codex_monthly_low_with_balance_exposes_the_controlling_spend_values(self) -> None:
        credits = codex_credits(
            balance={"available": True, "amount_credits": "9.5", "unlimited": False},
            spend={"used_credits": "8", "limit_credits": "10", "remaining_percent": 20.0, "resets_at": 6500},
            active=True, low=True, exhausted=False,
        )
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "9.5 credits left · Monthly limit low")
        self.assertEqual(presentation["monthly"], {
            "used": "8", "limit": "10", "remainingPercent": 20.0, "resetsAt": 6500, "reached": False,
        })

    def test_codex_monthly_reached_with_balance_keeps_the_truthful_balance(self) -> None:
        credits = codex_credits(
            balance={"available": True, "amount_credits": "9.5", "unlimited": False},
            spend={"used_credits": "10", "limit_credits": "10", "remaining_percent": 0.0, "resets_at": 6500},
            active=True, low=True, exhausted=True,
        )
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "9.5 credits left · Monthly limit reached")
        self.assertNotIn("Low", presentation["primary"])
        self.assertEqual(presentation["monthly"], {
            "used": "10", "limit": "10", "remainingPercent": 0.0, "resetsAt": 6500, "reached": True,
        })

    def test_codex_monthly_reached_without_any_balance(self) -> None:
        credits = codex_credits(balance=None, spend=None, active=True, low=False, exhausted=True)
        presentation = call_helper("creditPresentation", "codex", credits, "en-US")
        self.assertEqual(presentation["primary"], "Monthly limit reached")
        self.assertIsNone(presentation["monthly"])
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


class DesktopCreditsSectionRenderTests(unittest.TestCase):
    def render(self, provider_id: str, credits) -> dict:
        return call_helper("CreditsSection", {"providerId": provider_id, "credits": credits})

    def test_section_title_and_active_primary_render(self) -> None:
        tree = self.render("claude", claude_credits(1840, 10000, active=True, low=False, exhausted=False))
        texts = collect_text(tree)
        self.assertIn("Credits", texts)
        self.assertIn("$18.40 of $100 used", texts)
        self.assertIn("$81.60 remains this month", texts)
        self.assertFalse(any("progress" in str(t).lower() for t in collect_types(tree)))

    def test_stale_primary_and_supporting_and_age_all_appear(self) -> None:
        credits = codex_credits(
            balance={"available": True, "amount_credits": None, "unlimited": False},
            active=False, low=False, exhausted=False, freshness="stale",
        )
        credits["observed_at"] = 100  # far in the past: deterministically "Observed <N>d ago"
        tree = self.render("codex", credits)
        texts = collect_text(tree)
        self.assertIn("Available · stale", texts)
        self.assertIn("The provider did not return a balance", texts)
        age_lines = [text for text in texts if text.startswith("Observed ")]
        self.assertEqual(len(age_lines), 1, texts)
        self.assertRegex(age_lines[0], r"^Observed \d+d ago$")

    def test_non_stale_state_has_no_age_line(self) -> None:
        tree = self.render("claude", claude_credits(1840, 10000, active=True, low=False, exhausted=False))
        texts = collect_text(tree)
        self.assertFalse(any(text.startswith("Observed ") for text in texts))

    def test_monthly_row_label_and_content_render_with_native_units(self) -> None:
        credits = codex_credits(
            balance={"available": True, "amount_credits": "9.5", "unlimited": False},
            spend={"used_credits": "8", "limit_credits": "10", "remaining_percent": 20.0, "resets_at": 6500},
            active=True, low=True, exhausted=False,
        )
        tree = self.render("codex", credits)
        texts = collect_text(tree)
        self.assertIn("Monthly credit limit", texts)
        self.assertTrue(any("8" in t and "10" in t and "20%" in t for t in texts), texts)
        self.assertFalse(any("$" in t for t in texts))

    def test_monthly_row_marks_reached_while_still_retaining_all_required_fields(self) -> None:
        credits = codex_credits(
            balance={"available": True, "amount_credits": "9.5", "unlimited": False},
            spend={"used_credits": "10", "limit_credits": "10", "remaining_percent": 0.0, "resets_at": 6500},
            active=True, low=True, exhausted=True,
        )
        tree = self.render("codex", credits)
        texts = collect_text(tree)
        # The reached row must still visibly include every required field: used
        # amount, limit amount, the existing percent()-formatted remaining
        # percentage, and the Reached marker, all in one row (not dropped).
        matching = [t for t in texts if "10/10" in t and "0% remaining" in t and "Reached" in t]
        self.assertEqual(len(matching), 1, texts)
        # The reset time line (the monthly row's last child in document order)
        # must still render, unaffected by the Reached marker.
        self.assertNotEqual(texts[-1], "Reset unavailable", texts)
        self.assertNotEqual(texts[-1], matching[0])

    def test_no_monthly_row_without_spend(self) -> None:
        credits = codex_credits(balance={"available": True, "amount_credits": "9.5", "unlimited": False},
                                 active=True, low=False, exhausted=False)
        tree = self.render("codex", credits)
        texts = collect_text(tree)
        self.assertNotIn("Monthly credit limit", texts)

    def test_no_progress_component_anywhere_in_the_tree(self) -> None:
        for provider_id, credits in (
            ("claude", claude_credits(8500, 10000, active=True, low=True, exhausted=False)),
            ("codex", codex_credits(
                balance={"available": True, "amount_credits": "9.5", "unlimited": False},
                spend={"used_credits": "8", "limit_credits": "10", "remaining_percent": 20.0, "resets_at": 6500},
                active=True, low=True, exhausted=False)),
        ):
            with self.subTest(provider_id=provider_id):
                tree = self.render(provider_id, credits)
                for node_type in collect_types(tree):
                    self.assertNotIn("progress", str(node_type).lower())


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
