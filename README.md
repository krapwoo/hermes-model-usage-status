# Hermes Model Usage Status

A no-build Hermes Desktop status-bar plugin that shows provider-native remaining usage for:

- **Claude:** 5-hour and weekly windows
- **Codex:** main weekly window and any model-specific windows returned by Codex

The plugin explicitly labels unavailable, stale, expired, and provider-error states. It does not infer allowance percentages from local token counts or Hermes activity.

## Requirements

- macOS with a current Hermes Desktop installation
- Python 3
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) for Claude usage
- [Codex CLI](https://github.com/openai/codex) for Codex usage

The conditional **Reauthenticate** action currently opens the provider-supported login command in macOS Terminal. Usage display and refresh remain usable without that action on other platforms, but the login launcher is macOS-only in v0.2.0.

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

Then restart the Hermes backend and reload Hermes Desktop plugins (`⌘K` → **Reload desktop plugins**) or restart Hermes Desktop.

If Claude already has a different `statusLine`, installation stops without changing either the existing plugin or Claude settings. Integrate the commands manually rather than overwriting an existing meter.

### Validate before installing

```bash
./install.sh --dry-run
```

## Authentication behavior

Each provider popover shows **Reauthenticate Claude** or **Reauthenticate Codex** only when that provider's official CLI reports missing or expired authentication, or when Claude's OAuth usage endpoint rejects the saved credentials.

- The button is hidden when authentication is healthy. A Claude OAuth `401`/`403` overrides a false healthy CLI status.
- Stale quota data or provider rate limiting does not by itself trigger the button.
- Clicking it asks the backend to open the official `claude auth login` or `codex login` command in Terminal.
- Credentials, OAuth parameters, codes, and provider output are never returned to the Desktop renderer.
- After login completes, select **Refresh** in the popover; the button disappears when the provider reports healthy authentication.

You can always authenticate directly:

```bash
claude auth login
codex login
```

## Data sources

- **Codex:** structured `account/rateLimits/read` data from `codex app-server`; refreshing does not send a model prompt.
- **Claude:** Claude's OAuth usage endpoint through Hermes' credential-safe provider adapter, with documented structured `rate_limits` status-line fields as a fallback. Refreshing does not send a model prompt.

If neither Claude's OAuth usage endpoint nor a passive structured observation is available, the plugin shows **Unavailable** rather than inventing a percentage.

## Privacy and local files

The repository and installer do **not** contain or copy:

- Claude or Codex credentials
- `~/.claude` account data beyond the safe `settings.json` status-line merge
- `~/.codex`
- Hermes plugin-data, usage caches, observations, quarantine evidence, or logs
- Claude transcripts or raw session IDs

The plugin stores sanitized allowance windows, timestamps, and a one-way activity fingerprint derived from Claude's session/activity counters. The fingerprint is used only to avoid rewriting unchanged observations, remains local, and is not sent to the Desktop renderer. These files live under:

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
- **Popover returns 404:** restart the Hermes backend so `dashboard/plugin_api.py` mounts.
- **Claude says Unavailable after login:** select **Refresh**. If the OAuth usage endpoint is unavailable, use Claude Code normally once to populate the passive fallback.
- **Reauthenticate is missing:** it is intentionally hidden unless the official provider CLI reports authentication is required or Claude's OAuth usage endpoint rejects the saved credentials.
- **Installer reports `status_line_conflict`:** Claude already has a different status-line command; the installer will not overwrite it.

## Development

```bash
python -m unittest discover -s tests -v
node --check desktop/plugin.js
hermes plugins validate . --json
```

The implementation plan and test-first record are in `docs/superpowers/plans/`.

## License

MIT
