# Hermes Model Usage Status

A no-build Hermes Desktop status-bar plugin that shows provider-native remaining usage for:

- **Claude:** 5-hour and weekly windows, plus Claude's provider-reported monthly extra-usage spend and cap
- **Codex:** main weekly window and any model-specific windows returned by Codex, plus Codex's provider-native credit balance and optional monthly credit limit

The plugin explicitly labels unavailable, stale, expired, and provider-error states. It does not infer allowance percentages from local token counts or Hermes activity, and it does not estimate monetary cost from local token counts either.

## Requirements

- macOS with a current Hermes Desktop installation
- Python 3
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) for Claude usage
- [Codex CLI](https://github.com/openai/codex) for Codex usage

The conditional **Reauthenticate** action currently opens the provider-supported login command in macOS Terminal. Usage display and refresh remain usable without that action on other platforms, but the login launcher is currently macOS-only.

## Install on a Mac

No Hermes source checkout, JavaScript build, or plugin rebuild is needed.

```bash
git clone https://github.com/krapwoo/hermes-model-usage-status.git
cd hermes-model-usage-status
./install.sh
```

The installer:

1. Validates the repository with `hermes plugins validate`.
2. Installs the unified package at `~/.hermes/plugins/model-usage-status`.
3. Enables the backend plugin without tool-override permission.
4. Adds Claude's passive status-line observer as a fallback while preserving unrelated Claude settings.
5. Removes only an obsolete standalone Desktop copy of this same plugin, if present.

Running the installer (or uninstaller) from a standard named Hermes profile home still installs/enables (or removes/disables) the plugin in your primary/default `~/.hermes` home, since the Desktop status bar is app-global, not per-profile.

Then restart the Hermes backend and reload Hermes Desktop plugins (`⌘K` → **Reload desktop plugins**) or restart Hermes Desktop.

Right-click the status bar to show or hide **Claude model usage** and **Codex model usage** independently. Selecting either status item opens a menu that stays available while you use **Refresh** or, when required, **Reauthenticate**.

If Claude already has a different `statusLine`, installation stops without changing either the existing plugin or Claude settings. Integrate the commands manually rather than overwriting an existing meter.

### Validate before installing

```bash
./install.sh --dry-run
```

## Authentication behavior

Each provider menu shows **Reauthenticate Claude** or **Reauthenticate Codex** only when that provider's official CLI reports missing or expired authentication, or when Claude's OAuth usage endpoint rejects the saved credentials.

- The button is hidden when authentication is healthy. A Claude OAuth `401`/`403` overrides a false healthy CLI status.
- Stale quota data or provider rate limiting does not by itself trigger the button.
- Clicking it asks the backend to open the official `claude auth login` or `codex login` command in Terminal.
- Credentials, OAuth parameters, codes, and provider output are never returned to the Desktop renderer.
- After login completes, select **Refresh** in the provider menu; the button disappears when the provider reports healthy authentication.

You can always authenticate directly:

```bash
claude auth login
codex login
```

## Data sources

- **Codex:** structured `account/rateLimits/read` data from `codex app-server`; refreshing does not send a model prompt.
- **Claude:** Claude's OAuth usage endpoint through Hermes' credential-safe provider adapter, with documented structured `rate_limits` status-line fields as a fallback. Refreshing does not send a model prompt. One OAuth usage request per refresh supplies both allowance windows and extra-usage credit data.

If neither Claude's OAuth usage endpoint nor a passive structured observation is available, the plugin shows **Unavailable** rather than inventing a percentage.

## Credit usage

Alongside allowance windows, each provider menu always shows a Credits row:

- **Claude** shows extra-usage spend against a monthly cap in Claude's own reported currency, for example `$18.40 of $100 used`.
- **Codex** shows a provider-native credit balance and, when the account has one, a monthly credit limit, always in Codex's own credit units — the plugin never invents a currency for Codex.

Credits always appear in the popover, but the compact status-bar label adds a credit summary only when it is consequential: active, low, exhausted, or stale after a previously consequential observation. Otherwise the compact label stays allowance-only.

Credit states shown in the interface:

- **Current:** the provider returned an up-to-date, truthful balance or spend figure.
- **Off:** the provider confirms credit-backed usage is not enabled for the account.
- **Unlimited:** the provider reports no ceiling on remaining credits.
- **Low:** 20% or less of a monthly cap or spend-controlled limit remains.
- **Exhausted:** the cap, limit, or balance is fully used.
- **stale:** the last known current or off state is still shown, but the observation is aging and no longer guaranteed current.
- **Unavailable:** no truthful credit value can be shown; the plugin never substitutes a guessed or zero amount.

Credit freshness is tracked independently from allowance freshness. If a refresh fails, the plugin keeps showing the last known credit state as visibly stale for at most 15 minutes. After 15 minutes without a successful credit refresh, credits become **Unavailable** even if the allowance windows are still showing usable data from the passive fallback.

## Privacy and local files

The repository and installer do **not** contain or copy:

- Claude or Codex credentials
- `~/.claude` account data beyond the safe `settings.json` status-line merge
- `~/.codex`
- Hermes plugin-data, usage caches, observations, quarantine evidence, or logs
- Claude transcripts or raw session IDs

The Claude OAuth usage request resolves a token through Hermes' existing credential resolver, uses it only for that one request, and discards both the token and the raw provider response as soon as the response is normalized; neither is logged, cached, or returned to the Desktop renderer.

The plugin stores sanitized allowance windows, normalized (schema v2) credit fields, timestamps, and a one-way activity fingerprint derived from Claude's session/activity counters. The fingerprint is used only to avoid rewriting unchanged observations, remains local, and is not sent to the Desktop renderer. These files live under:

```text
~/.hermes/plugin-data/model-usage-status/
```

## Update

Pull the repository and rerun the idempotent installer:

```bash
git pull --ff-only
./install.sh
```

## Uninstall

```bash
./uninstall.sh
```

Uninstalling removes the plugin and its own Claude status-line entry. It intentionally leaves provider credentials and sanitized plugin-data untouched.

## Troubleshooting

- **Chips do not appear:** reload Desktop plugins from the command palette and confirm the package is under `~/.hermes/plugins/model-usage-status/desktop/plugin.js`.
- **Provider menu returns 404:** restart the Hermes backend so `dashboard/plugin_api.py` mounts.
- **Claude says Unavailable after login:** select **Refresh**. If the OAuth usage endpoint is unavailable, use Claude Code normally once to populate the passive fallback.
- **Reauthenticate is missing:** it is intentionally hidden unless the official provider CLI reports authentication is required or Claude's OAuth usage endpoint rejects the saved credentials.
- **Installer reports `status_line_conflict`:** Claude already has a different status-line command; the installer will not overwrite it.
- **Allowance windows show but Credits shows Unavailable:** the provider did not return usable credit data even though allowance data succeeded. For Claude, this happens when the OAuth usage response is missing `extra_usage` or its currency or decimal-places fields are absent or malformed. For Codex, this happens on `codex app-server` versions or accounts that do not return credit fields at all. Select **Refresh**; if the condition persists past 15 minutes, credits remain Unavailable until the provider returns a valid credit block, even though allowance data can keep working from the passive fallback.

## Development

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests -v
node --check desktop/plugin.js
hermes plugins validate . --json
```

The implementation plan and test-first record are in `docs/superpowers/plans/`.

## License

MIT
