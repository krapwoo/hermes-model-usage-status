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

    def test_manifest_entry_is_present_in_public_source(self) -> None:
        entry = PLUGIN.parents[1] / "dashboard" / "dist" / "index.js"

        self.assertTrue(entry.is_file())


if __name__ == "__main__":
    unittest.main()
