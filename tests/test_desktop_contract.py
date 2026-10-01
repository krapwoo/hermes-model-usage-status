from __future__ import annotations

import re
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "desktop" / "plugin.js"


class DesktopPluginContractTests(unittest.TestCase):
    def test_plugin_registers_exactly_two_status_bar_items_and_no_other_surface(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertEqual(source.count("area: STATUSBAR_AREAS.right"), 2)
        self.assertIn("id: 'claude'", source)
        self.assertIn("id: 'codex'", source)
        self.assertNotIn("ROUTES_AREA", source)
        self.assertNotIn("SIDEBAR_NAV_AREA", source)
        self.assertNotIn("area: 'panes'", source)

    def test_status_items_use_host_menu_so_refresh_remains_interactive(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertNotIn("Popover", source)
        self.assertEqual(source.count("data: statusItem({"), 2)
        self.assertIn("variant: 'menu'", source)
        self.assertIn('menuContent:', source)
        self.assertIn("menuAlign: 'end'", source)

    def test_status_menu_actions_are_keyboard_navigable_and_stay_open(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertEqual(source.count('jsxs(DropdownMenuItem'), 2)
        self.assertEqual(source.count('onSelect: event =>'), 2)
        self.assertEqual(source.count('event.preventDefault()'), 2)
        self.assertNotIn("jsxs('button'", source)

    def test_status_items_opt_into_the_status_bar_visibility_menu(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("id: 'model-usage-status:claude'", source)
        self.assertIn("id: 'model-usage-status:codex'", source)
        self.assertIn("toggleLabel: 'Claude model usage'", source)
        self.assertIn("toggleLabel: 'Codex model usage'", source)

    def test_plugin_uses_only_allowed_import_specifiers(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")
        specifiers = re.findall(r"from\s+['\"]([^'\"]+)['\"]", source)

        self.assertTrue(specifiers)
        self.assertTrue(set(specifiers) <= {"@hermes/plugin-sdk", "react", "react/jsx-runtime"})

    def test_plugin_has_one_shared_five_minute_query_and_manual_refresh(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertEqual(source.count("useQuery({"), 1)
        self.assertIn("refetchInterval: 300_000", source)
        self.assertIn("ctx.rest", source)
        self.assertIn("'/usage'", source)
        self.assertIn("'/refresh'", source)

    def test_query_key_is_versioned_for_corrected_backend_contracts(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertRegex(source, r"const DATA_CONTRACT_VERSION = \d+")
        self.assertIn("const QUERY_KEY = ['model-usage-status', DATA_CONTRACT_VERSION]", source)

    def test_plugin_labels_claude_five_hour_and_weekly_windows(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("Claude", source)
        self.assertIn("window.label", source)
        self.assertIn("window.remaining_percent", source)
        self.assertIn("unavailable", source)
        self.assertIn("stale", source)

    def test_reauthentication_is_provider_specific_and_required_only(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("provider?.authentication?.state === 'required'", source)
        self.assertIn("`Reauthenticate ${name}`", source)
        self.assertIn("`/authentication/${providerId}`", source)
        self.assertIn("method: 'POST'", source)
        self.assertIn("queryClient.invalidateQueries({ queryKey: QUERY_KEY })", source)

    def test_every_backend_call_is_pinned_to_the_local_primary_backend(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")

        # Usage must read the same local machine/account state no matter which
        # Hermes profile or remote connection the desktop window is on. One
        # shared `call()` helper injects the target so /usage, /refresh, and
        # /authentication/<provider> all resolve local-primary uniformly.
        self.assertIn("function call(path, options)", source)
        self.assertEqual(source.count("target: 'local-primary'"), 1)
        self.assertRegex(source, r"rest\(path,\s*\{\s*\.\.\.options,\s*target:\s*'local-primary'\s*\}\)")

    def test_manifest_entry_is_present_in_public_source(self) -> None:
        entry = PLUGIN.parents[1] / "dashboard" / "dist" / "index.js"

        self.assertTrue(entry.is_file())

    def test_credit_contract_version_and_existing_surfaces(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("const DATA_CONTRACT_VERSION = 4", source)
        self.assertEqual(source.count("area: STATUSBAR_AREAS.right"), 2)
        self.assertEqual(source.count("useQuery({"), 1)
        self.assertIn("'/usage'", source)
        self.assertIn("'/refresh'", source)
        self.assertNotIn("Progress", source)

    def test_popover_has_text_only_credits_before_provider_source(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("function CreditsSection", source)
        self.assertIn("children: 'Credits'", source)
        self.assertIn("creditPresentation", source)
        self.assertLess(source.index("jsx(CreditsSection"), source.index("provider?.source"))

    def test_refresh_failure_is_caught_and_never_overwrites_cached_data(self) -> None:
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("async function refreshUsage(requestRefresh)", source)
        helper = source[source.index("async function refreshUsage("):]
        helper = helper[:helper.index("\n}\n") + 2]
        # setQueryData must only ever run on the success path, before the catch.
        self.assertLess(helper.index("queryClient.setQueryData"), helper.index("catch"))
        self.assertIn("return { ok: false }", helper)
        # refresh() must still disable/re-enable Refresh via finally, and must
        # never let refreshUsage's promise reach the caller unhandled.
        self.assertIn("await refreshUsage(", source)
        self.assertIn("finally {\n      setRefreshing(false)\n    }", source)


if __name__ == "__main__":
    unittest.main()
