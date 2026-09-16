# Conditional Reauthentication and Public Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release v0.2.0 of the Hermes model-usage status plugin with provider-specific reauthentication shown only when needed and a public, no-build Mac installer.

**Architecture:** Provider authentication is checked server-side with official CLI status commands and returned only as sanitized state. A backend action launches the provider-supported login command in macOS Terminal; the renderer never receives credentials or OAuth data. A Python installer, wrapped by `install.sh`, copies the unified plugin package, safely merges Claude’s passive status-line command, validates and enables the plugin, and never copies runtime or credential state.

**Tech Stack:** Python 3 standard library, FastAPI APIRouter, Hermes Desktop disk/unified plugin ESM, `unittest`, POSIX shell, GitHub CLI.

## Global Constraints

- Show Reauthenticate only for a provider whose authentication state is `required`.
- Hide it for authenticated, stale-but-authenticated, rate-limited, unavailable-CLI, and healthy states.
- Use only `claude auth login` and `codex login`; do not pass secrets through the renderer.
- Do not issue model prompts to refresh quota.
- Do not copy `~/.claude` credentials, `~/.codex`, plugin-data, caches, quarantined data, or logs.
- Support macOS installation without rebuilding Hermes Desktop or this plugin.
- Preserve unrelated Claude settings and refuse to overwrite a conflicting third-party status line.
- Keep executable/runtime plugin files outside WooVault.

---

### Task 1: Sanitized provider authentication contract

**Files:**
- Modify: `model_usage_status/service.py`
- Modify: `dashboard/plugin_api.py`
- Modify: `desktop/plugin.js`
- Test: `tests/test_service.py`
- Test: `tests/test_api.py`
- Test: `tests/test_desktop_contract.py`

**Interfaces:**
- Produces: `authentication_status(provider, runner=subprocess.run) -> dict`
- Produces: `launch_reauthentication(provider, runner=subprocess.run, platform=sys.platform) -> dict`
- Produces: `POST /authentication/{provider}` returning `{provider, state: "login_started"}`.

- [ ] **Step 1: Write failing service tests**

Add tests asserting exit `0` becomes `{state: "authenticated", action_available: false}`, an explicit unauthenticated CLI result becomes `{state: "required", action_available: true}`, missing CLIs become `{state: "unavailable", action_available: false}`, and login launch creates a secret-free `.command` file containing only the resolved provider executable and official login arguments.

- [ ] **Step 2: Run service tests and verify RED**

Run: `python3 -m unittest tests.test_service -v`
Expected: failures because the authentication functions do not exist.

- [ ] **Step 3: Implement minimal service contract**

Implement fixed provider metadata:

```python
_AUTH_PROVIDERS = {
    "claude": {"status": ("auth", "status", "--text"), "login": ("auth", "login")},
    "codex": {"status": ("login", "status"), "login": ("login",)},
}
```

Resolve executables with `shutil.which`, execute status commands with captured output and a short timeout, return only state/action fields, and launch a generated mode-`0700` `.command` file via `open -a Terminal` on macOS. Reject launches unless the provider’s current state is `required`.

- [ ] **Step 4: Run service tests and verify GREEN**

Run: `python3 -m unittest tests.test_service -v`
Expected: all service tests pass.

- [ ] **Step 5: Add API and UI RED tests**

Test that the API validates provider names, returns `409` when authentication is healthy, and returns `202` after a login launch. Add static UI contract tests requiring `authentication.state === 'required'`, `Reauthenticate Claude`, `Reauthenticate Codex`, and a POST to `/authentication/${provider}`.

- [ ] **Step 6: Run API/UI tests and verify RED**

Run: `python3 -m unittest tests.test_api tests.test_desktop_contract -v`
Expected: failures because the route and conditional control do not exist.

- [ ] **Step 7: Implement API and conditional popover action**

Add the POST route using `run_in_threadpool`. Merge sanitized authentication into every provider payload at read/refresh time. Render a provider-specific button only when `state === 'required'`; disable it while starting login, toast sanitized success/error text, and invalidate the usage query after action completion.

- [ ] **Step 8: Run focused and complete tests**

Run: `python3 -m unittest discover -s tests -v && node --check desktop/plugin.js`
Expected: all tests pass and JavaScript syntax exits `0`.

### Task 2: Idempotent no-build installer

**Files:**
- Create: `scripts/install.py`
- Create: `install.sh`
- Create: `scripts/uninstall.py`
- Create: `uninstall.sh`
- Create: `tests/test_installer.py`
- Create: `README.md`
- Create: `.gitignore`
- Create: `LICENSE`
- Modify: `plugin.yaml`
- Modify: `dashboard/manifest.json`

**Interfaces:**
- Produces: `python3 scripts/install.py [--source PATH] [--dry-run]`
- Produces: `./install.sh`
- Produces: `./uninstall.sh`

- [ ] **Step 1: Write failing installer tests**

Use temporary HOME/HERMES_HOME directories and a fake `hermes` executable. Assert the installer copies only distributable files, writes the unified plugin path, adds the exact Claude status-line observer while preserving unrelated JSON, updates its own prior command idempotently, refuses a conflicting status line without mutation, calls `hermes plugins validate` then `hermes plugins enable`, and never copies credential/runtime directories.

- [ ] **Step 2: Run installer tests and verify RED**

Run: `python3 -m unittest tests.test_installer -v`
Expected: import/file-not-found failure because installer code does not exist.

- [ ] **Step 3: Implement installer and wrappers**

Implement installation with standard-library file operations, a temporary sibling directory, atomic replacement with rollback, JSON merge, explicit excluded path names, validation before activation, and clear restart instructions. Add shell wrappers that resolve their repository directory and `exec python3` the scripts.

- [ ] **Step 4: Implement narrow uninstaller**

Remove only the plugin directory, standalone Desktop compatibility copy if present, and the Claude status line only when its command points to this plugin. Do not remove provider credentials or plugin-data.

- [ ] **Step 5: Run installer tests twice**

Run: `python3 -m unittest tests.test_installer -v`
Expected: all tests pass, including second-run idempotency.

- [ ] **Step 6: Write public documentation and bump version**

Document requirements, clone/install commands, authentication behavior, privacy boundaries, manual reload/restart, uninstall, troubleshooting, and provider-native data sources. Set both manifests to `0.2.0`.

### Task 3: Local qualification and public release

**Files:**
- Create: `dist/hermes-model-usage-status-v0.2.0.tar.gz` (release artifact only)
- Update: `Resources/Workflows/Hermes Model Usage Status Bar.md` in WooVault after external verification.

**Interfaces:**
- Produces: public `https://github.com/krapwoo/hermes-model-usage-status`
- Produces: GitHub release `v0.2.0` with SHA-256 checksum.

- [ ] **Step 1: Audit repository contents and secrets**

Run syntax/tests, `hermes plugins validate . --json`, `git diff --check`, list tracked paths, scan tracked text for tokens, email addresses, absolute home paths, cache data, quarantine/debug files, and credential filenames.

- [ ] **Step 2: Install from the repository artifact locally**

Run `./install.sh`, restart/reload the Hermes backend/Desktop as required, verify the API route is mounted, confirm healthy providers omit reauthentication, and use automated contract tests for the required-state button without intentionally logging out healthy providers.

- [ ] **Step 3: Commit the reviewed repository**

Create one clean `v0.2.0` commit and verify `git status`, commit contents, and tag target.

- [ ] **Step 4: Create and verify public GitHub repository**

Create `krapwoo/hermes-model-usage-status` as public, push `main`, tag `v0.2.0`, create the release with archive and checksum, then verify the remote branch SHA, tag SHA, release assets, and public visibility through GitHub.

- [ ] **Step 5: Verify install from the published source**

Clone the public repository into a fresh temporary directory and run the full tests plus installer dry-run in an isolated temporary HOME. Confirm no build step is required.

- [ ] **Step 6: Record WooVault receipt**

Append a verified receipt through `Resources/Tools/log_append.py` and update the workflow status with repository URL, release URL, commit SHA, checks, and known macOS-only login-launch limitation. Do not commit or push WooVault without a separate checkpoint approval.
