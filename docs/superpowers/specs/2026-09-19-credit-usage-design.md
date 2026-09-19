# Claude and Codex Credit Usage Design

- **Date:** 2026-09-19
- **Status:** Approved product design; awaiting written-spec review
- **Project:** Hermes Model Usage Status
- **Scope:** Provider-native monetary or monetary-like credits in the existing Claude and Codex status items

## Summary

Improve the existing Hermes Desktop status-bar plugin so users can see provider-native credit usage alongside allowance windows without confusing credits with local token cost estimates.

The two existing provider popovers always show a Credits section. The compact status-bar label remains allowance-focused until credits become consequential. Once credit spending is active, low, exhausted, or temporarily stale after prior activity, the label shows the controlling exhausted allowance when known plus the provider-native credit summary.

Claude reports monetary extra usage as spend against a monthly cap and includes a currency. Codex reports a provider-native credit balance without a currency. The plugin must preserve those semantics instead of forcing both providers into dollars.

## User outcome

A user can answer these questions without opening either provider CLI:

1. How much included Claude or Codex allowance remains?
2. Is paid or credit-backed continuation active?
3. How much Claude extra usage has been spent against its cap?
4. How many Codex credits remain?
5. Is the displayed monetary or credit value current, stale, off, exhausted, unlimited, or unavailable?

## Existing baseline

The current plugin:

- registers separate Claude and Codex items in the Hermes Desktop status bar;
- shows provider-native allowance windows in each compact label and popover;
- uses one shared versioned query with a five-minute refresh interval;
- serializes provider refreshes and atomically persists a sanitized cache;
- exposes manual refresh and conditional provider reauthentication;
- uses Claude OAuth usage with a passive status-line fallback;
- uses Codex `account/rateLimits/read` through `codex app-server`;
- marks unavailable, stale, and expired allowance data explicitly.

This design extends those mechanisms. It does not create a parallel credits plugin, route, or polling loop.

## Goals

- Add structured credit data to each provider snapshot.
- Always show truthful credit state in each provider popover.
- Promote credits into the compact label only when consequential.
- Keep allowance and credit freshness independent.
- Preserve authentication, refresh, privacy, and keyboard behavior.
- Verify the complete state matrix in automated and rendered checks.

## Non-goals

- Estimating monetary cost from local token activity
- Currency conversion
- Purchasing or topping up credits
- Displaying Codex quota-reset coupons
- Historical spend charts or transaction ledgers
- Account switching or pooled-account totals
- Redesigning the status bar or provider popovers
- Changing provider billing, limits, or authentication policy

## Accepted decisions

### CREDIT-SEMANTICS-001 — Preserve provider-native units

- Claude uses its reported currency and presents extra-usage spend against a monthly cap.
- Codex presents its numeric balance as `credits` because the app-server contract does not report a currency and Codex's own status UI uses credit units.
- Missing currency or amount data must not be repaired with a guessed dollar symbol.

### CREDIT-PLACEMENT-001 — Always in the popover; conditional in the label

- Every provider popover contains a Credits section.
- The status-bar label includes credits only when credit use is active, low, exhausted, or stale after previously confirmed activity.

### CREDIT-LABEL-001 — Show the controlling exhausted allowance

When credits are consequential, the compact label contains at most two facts:

1. the controlling exhausted account allowance, if one is identifiable; and
2. the provider-native credit summary.

Examples:

- `Claude 5h 0% · Credits $18/$100`
- `Claude Week 0% · Credits $18/$100`
- `Codex Week 0% · 9.5 credits left`
- `Claude Credits $18/$100` when no controlling account allowance is identifiable

If the weekly allowance is exhausted, healthy 5-hour headroom is omitted because it cannot restore included access. If both account windows are exhausted, the weekly exhaustion is the controlling constraint. Model-specific windows remain available in the popover and do not expand the compact label beyond two facts.

### CREDIT-FRESHNESS-001 — Bound monetary staleness

Credit data has its own observation time and freshness state. A failed refresh may preserve the last successful consequential credit value as `stale` for at most 15 minutes. After that, the popover shows `Unavailable` and the monetary or credit amount leaves the compact label.

## Architecture

### Existing surfaces retained

- `desktop/plugin.js` continues to register exactly two provider status items.
- `GET /usage` remains the single read surface.
- `POST /refresh` remains the single manual-refresh surface.
- Provider reauthentication routes remain unchanged.
- `UsageService` remains the owner of refresh serialization, cache persistence, and provider merging.

### Normalized credit contract

Each provider object gains a nullable `credits` object. Increment both the persisted snapshot schema and the renderer query-contract version. A schema-v1 allowance-only cache must trigger a refresh rather than appear to contain complete credit data.

```json
{
  "credits": {
    "status": "current | stale | off | unavailable",
    "unit": "currency | credits",
    "currency": "USD or null",
    "balance": {
      "amount_credits": "9.5",
      "unlimited": false
    },
    "spend": {
      "used_minor": 1840,
      "limit_minor": 10000,
      "used_credits": null,
      "limit_credits": null,
      "remaining_percent": 81.6,
      "resets_at": 1792800000
    },
    "active": true,
    "low": false,
    "exhausted": false,
    "observed_at": 1790200000,
    "source": "provider-owned source label or null",
    "error_code": null
  }
}
```

Contract rules:

- `currency` is required only when `unit` is `currency`.
- Claude currency values use `spend.used_minor` and `spend.limit_minor`; formatting happens in the renderer with `Intl.NumberFormat`.
- Codex balances use `balance.amount_credits`. Optional monthly-limit amounts use `spend.used_credits` and `spend.limit_credits`. These remain validated decimal strings because the provider contract supplies credit amounts, not a currency.
- A provider may supply both `balance` and `spend`; this is required for Codex accounts that expose a remaining balance and a monthly spend-control limit in the same snapshot.
- `spend.remaining_percent` exists only when the provider supplies a valid denominator or authoritative remaining percentage.
- `low` means 20% or less remains and is false when no denominator exists.
- `exhausted` requires an explicit provider zero or reached-spend-control signal.
- `active` must come from reported spend or an explicit provider condition proving credit-backed continuation. Local balance history and token activity are not evidence of active spending.
- Invalid, negative, non-finite, boolean, or ambiguous values invalidate only the affected credit block.

### Claude acquisition and normalization

One credential-safe OAuth usage read captures both allowance windows and `extra_usage`.

Normalize:

- `is_enabled: false` to `status: off`;
- valid enabled spend and cap to a currency `spend` object;
- provider currency plus minor-unit spend and cap without guessing scale;
- spend greater than zero to `active: true`;
- zero cap remaining to `exhausted: true`;
- a valid remaining ratio of 20% or less to `low: true`.

The passive Claude status-line observation can recover allowance windows only. It does not contain `extra_usage`; therefore it cannot make credits current. A prior current credit value may remain stale within the 15-minute bound, otherwise credits become unavailable.

### Codex acquisition and normalization

The existing `account/rateLimits/read` response already carries allowance snapshots and may carry:

- `rateLimits.credits.hasCredits`;
- `rateLimits.credits.unlimited`;
- `rateLimits.credits.balance`;
- `rateLimits.individualLimit` with provider-owned monthly credit-limit values;
- `ordinaryUsageAllowed` and `spendControlReached` signals.

Normalize:

- `unlimited: true` to `balance.unlimited: true`;
- `hasCredits: true` plus a valid balance to `balance.amount_credits`;
- explicit zero balance or reached spend control to `exhausted: true`;
- the optional individual monthly limit to a `spend` object using provider-native credit units;
- provider-reported spend or explicit credit-backed continuation to `active: true`.

`rateLimitResetCredits` is unrelated to monetary credit balance and remains out of scope.

## Interface behavior

### Provider popover

Place the Credits section after allowance and model-limit rows and before the provider source.

#### Claude

| State | Primary text | Supporting text |
|---|---|---|
| Enabled, zero spend | `$0 of $100 used` | `$100 remains this month` |
| Active | `$18.40 of $100 used` | `$81.60 remains this month` |
| Low | `$85 of $100 used · Low` | `$15 remains this month` |
| Exhausted | `$100 of $100 used · Exhausted` | `$0 remains this month` |
| Off | `Off` | `Paid extra usage is not enabled.` |
| Stale | Last known amount plus `stale` | Existing observation age remains visible |
| Unavailable | `Unavailable` | No zero value or inferred balance |

A valid spend-and-cap state includes a progress indicator based on amount used.

#### Codex

| State | Primary text | Supporting text |
|---|---|---|
| Balance | `9.5 credits left` | None required |
| Unlimited | `Unlimited` | None required |
| Exhausted | `0 credits left · Exhausted` | None required |
| Available, hidden balance | `Available` | The provider did not return a balance |
| Off/no credit entitlement | `Off` | Credit-backed usage is not available |
| Stale | Last known amount plus `stale` | Existing observation age remains visible |
| Unavailable | `Unavailable` | No zero value or inferred balance |

When `individualLimit` is present, show a second `Monthly credit limit` row with the provider's used amount, limit, remaining percentage, and reset time.

### Compact label algorithm

1. If credits are not active, low, exhausted, or bounded-stale after prior activity, use the current allowance-only label unchanged.
2. Find the controlling exhausted account allowance:
   - weekly if the weekly account window is exhausted;
   - otherwise the exhausted short account window;
   - otherwise none.
3. Format the provider-native credit summary:
   - Claude: localized spent/cap currency, compacted without false precision;
   - Codex: validated decimal balance followed by `credits left`, or `Unlimited`/`Exhausted`.
4. Render provider name, optional controlling allowance, and credit summary.
5. Append `stale` when the consequential credit value is bounded-stale.
6. Never include unavailable credit data in the compact label.

### Accessibility and responsive behavior

- Reuse the host's existing keyboard-operable menu items and focus behavior.
- Give each progress indicator a programmatic label and numeric value/limit semantics.
- Include textual `Low`, `Exhausted`, `Off`, `Unavailable`, and `stale` labels; color is supplementary only.
- Preserve the existing popover width and token-based spacing, typography, borders, and colors.
- Keep the compact label to at most two facts so it remains usable at constrained status-bar widths.
- Verify browser zoom and constrained width do not clip critical state text or make menu actions unreachable.

## Data flow

1. The renderer requests the existing combined usage snapshot.
2. `UsageService.get()` returns a cache younger than five minutes or calls `refresh()`.
3. `refresh()` allows one owner; concurrent callers wait for that owner rather than launching duplicate provider reads.
4. Claude and Codex refresh independently.
5. Each provider result normalizes allowance and credit data at its provider boundary.
6. The service merges successes and bounded stale data without letting one provider failure erase the other provider.
7. One sanitized snapshot is atomically persisted.
8. Labels and popovers update from the same versioned query result.

## Error and recovery behavior

| Condition | Required behavior |
|---|---|
| Authentication rejected | Preserve provider-specific reauthentication; credits are unavailable until a successful read |
| Timeout, 429, or transient provider failure | Preserve a last-known consequential credit value as visibly stale for at most 15 minutes |
| Stale bound expires | Replace the amount with `Unavailable` in the popover and remove it from the compact label |
| Malformed credit block | Reject only that block; retain valid allowance data |
| Partial provider response | Render valid fields and mark the missing credit portion unavailable |
| Manual refresh fails | Keep the menu open, restore the Refresh control, and retain safe prior state |
| Passive Claude fallback succeeds | Refresh allowance windows only; credit freshness remains independent |
| Later provider recovery | Replace stale/unavailable credit state atomically without a plugin reload |
| Concurrent refresh request | Wait for the existing owner; do not create live overlap |

Refresh is short, read-only, and serialized. It does not need a user-facing cancellation control.

## Flow-State Brief

### Scope and mode

- **Outcome:** Show trustworthy allowance and credit state through initial load, refresh, partial provider recovery, bounded staleness, and authentication interruption.
- **Non-goals:** Payment actions, historical spend, account switching, reset-credit redemption, or new refresh controls.
- **Mode:** `single_flow`

### Journey and surfaces

- **Primary journey:** Glance at a provider label, open its popover for complete allowance and credit state, optionally refresh, and recover after authentication or provider failure.
- **Affected surfaces:** Claude status item, Codex status item, both provider popovers, shared usage API response, sanitized usage cache.

### States

| State ID | Meaning | Maintained by | Entry conditions | Preserved work | Requirement IDs |
|---|---|---|---|---|---|
| S1 | Allowance-only label with current inactive/off credits | Renderer from cached service state | Credits are not consequential | Current windows and normalized credit state | REQ-UI-1, REQ-LABEL-1 |
| S2 | Current consequential credits | Service and renderer | Active credit use is provider-confirmed | Current windows and credit values | REQ-LABEL-2, REQ-DATA-1 |
| S3 | Current low credits | Service and renderer | Valid denominator reports 20% or less remaining | Current values and observation time | REQ-LABEL-2, REQ-STATE-1 |
| S4 | Current exhausted credits | Service and renderer | Explicit zero or reached-spend-control signal | Current values and observation time | REQ-LABEL-2, REQ-STATE-2 |
| S5 | Refreshing | `UsageService` owner and renderer control state | Automatic expiry or manual Refresh | Last safe sanitized snapshot | REQ-FLOW-1 |
| S6 | Bounded-stale consequential credits | Service and renderer | Refresh fails and prior consequential data is at most 15 minutes old | Last successful amount and observation time | REQ-FRESH-1 |
| S7 | Credits unavailable | Service and renderer | No valid data, malformed data, or stale bound expired | Valid allowance windows | REQ-FRESH-2, REQ-ERROR-1 |
| S8 | Authentication required | Provider authentication state | Provider rejects saved credentials | Safe allowance/cache data where valid | REQ-AUTH-1 |

### Transitions

| Transition ID | From | To | Initiated by | Completed by | Guards | Effects | Requirement IDs |
|---|---|---|---|---|---|---|---|
| T1 | S1/S2/S3/S4/S6/S7 | S5 | Timer or user | `UsageService` | No refresh owner exists; otherwise caller waits | Disable Refresh and begin one provider read | REQ-FLOW-1 |
| T2 | S5 | S1/S2/S3/S4 | Provider response | Provider normalizer and service | Response fields are valid and sanitized | Atomically replace provider state and clear stale error | REQ-DATA-1, REQ-RECOVERY-1 |
| T3 | S5 | S6 | Provider failure | Service | Prior consequential credits exist and are no older than 15 minutes | Preserve amount with explicit stale state | REQ-FRESH-1 |
| T4 | S5/S6 | S7 | Provider failure or clock expiry | Service | No valid prior amount or stale age exceeds 15 minutes | Remove amount from compact label and expose Unavailable | REQ-FRESH-2 |
| T5 | S5 | S8 | Provider authentication rejection | Service | Provider explicitly reports rejected credentials | Expose existing reauthentication action without leaking credentials | REQ-AUTH-1 |
| T6 | S8 | S5 | User selects Refresh after provider login | User and service | Provider login has completed outside the plugin | Revalidate authentication and usage state | REQ-AUTH-2 |

### Lifecycle behavior

| Transition ID | Cancellation | Interruption | Retry/recovery | Supersession/stale result | Resume/revalidation |
|---|---|---|---|---|---|
| T1 | No cancellation control; read is short and non-destructive | Menu may close without cancelling the service refresh | Automatic or later manual refresh | Concurrent request waits for the active owner | Result updates shared query data independent of menu visibility |
| T2 | n/a | Atomic write prevents partial cache visibility | Successful response is the recovery path | Serialized owner prevents an older concurrent completion | Validate and normalize at completion before persistence |
| T3 | n/a | Renderer may unmount; stale snapshot remains service-owned | Next timer or manual Refresh retries | Observation time prevents stale data appearing current | Recheck age on every service read/render |
| T4 | n/a | Unavailable state survives menu close/reopen | Next successful refresh recovers | No stale amount may re-enter after expiry without a new success | Full provider read is required |
| T5 | User may ignore the reauthentication action | Terminal login occurs outside the popover | Existing Reauthenticate action starts provider login | Authentication result is not assumed from launcher success | User selects Refresh; provider state is revalidated |
| T6 | User can stop the external login without data mutation | Terminal or provider interruption leaves S8 intact | Repeat existing reauthentication flow or use provider CLI | Only a successful provider read clears S8 | Refresh rechecks current provider authentication and usage |

### Specialist applicability

- **Content:** applicable
- **Motion:** not applicable; no new motion is introduced
- **Accessibility:** applicable
- **Responsive:** applicable
- **Implementation:** applicable

### Verification requirements

- **REQ-UI-1:** Every popover visibly contains a truthful Credits section in every supported state.
- **REQ-LABEL-1:** Non-consequential credits leave the existing compact allowance label unchanged.
- **REQ-LABEL-2:** Consequential credits show no more than the controlling exhausted allowance and native credit summary.
- **REQ-DATA-1:** A successful refresh updates windows and credits from one provider observation without exposing raw payloads or credentials.
- **REQ-STATE-1:** Low appears only with a valid denominator at 20% or less remaining.
- **REQ-STATE-2:** Exhausted appears only from explicit provider zero or reached state.
- **REQ-FLOW-1:** Concurrent refresh requests produce one provider read and restore the Refresh control on every exit.
- **REQ-FRESH-1:** A failed refresh preserves last-known consequential credits as visibly stale for no more than 15 minutes.
- **REQ-FRESH-2:** After 15 minutes, stale monetary data becomes unavailable and leaves the compact label.
- **REQ-ERROR-1:** Malformed credit data cannot erase valid allowance windows or appear as zero/off.
- **REQ-RECOVERY-1:** A later successful refresh clears stale/error state without reloading the plugin.
- **REQ-AUTH-1:** Authentication rejection exposes the existing provider-specific reauthentication path without leaking credential material.
- **REQ-AUTH-2:** Reauthentication launcher success alone does not mark usage current; Refresh must revalidate provider state.

### Unresolved decisions and evidence gaps

No product decision remains unresolved.

Evidence gaps to close during implementation:

- Observe current live Claude and Codex credit payloads without recording private account values in repository artifacts.
- Confirm the exact Codex `individualLimit` display unit against the installed Codex version; retain the provider's `credits` wording unless a currency is explicitly supplied.
- Verify all designed visual states in the actual Hermes host; states unavailable from live accounts require deterministic mocked renderer data.

**Flow-state status:** `ready`. Coverage, authority discipline, implementation usefulness, and proportionality pass. The listed evidence gaps are implementation verification obligations, not unresolved product behavior.

## Testing strategy

### Core normalization tests

Cover:

- Claude enabled, off, zero spend, active, low, exhausted, missing cap, invalid currency, and malformed values;
- Codex finite balance, zero, unlimited, hidden balance, individual monthly limit, spend-control reached, and absent data;
- provider-specific unit conversion and exact decimal preservation;
- rejection of booleans, NaN, infinity, negatives, and ambiguous strings;
- active, low, exhausted, and unavailable derivation without local inference.

### Service tests

Cover:

- current allowances with unavailable credits;
- current credits with provider-specific source and observation time;
- transient failure to bounded stale state;
- stale expiry after 15 minutes;
- later success clearing stale state;
- independent provider failure and recovery;
- concurrent refreshes sharing one provider read;
- authentication-required behavior;
- cache and API payloads excluding credentials and raw provider data.

### Desktop contract tests

Cover:

- a Credits section for both providers;
- approved copy for every credit state;
- consequential-label selection and controlling-window priority;
- at-most-two-facts compact label rule;
- provider-native Claude currency and Codex credit-unit formatting;
- accessible progress semantics;
- existing keyboard-operable Refresh and Reauthenticate controls;
- incremented query contract version.

### Rendered verification

Exercise the installed plugin at normal desktop width and a constrained status-bar width. Verify:

- allowance-only;
- enabled with zero spend;
- active;
- low;
- exhausted;
- off;
- unavailable;
- stale;
- authentication required;
- refreshing and refresh recovery.

Inspect label geometry, truncation, menu spacing, focus order, hit targets, overflow, zoom, progress semantics, and console errors. Use deterministic mocked data for states the live account cannot produce and separately exercise the installed plugin with current live provider state.

### Repository checks

```text
python -m unittest discover -s tests -v
node --check desktop/plugin.js
hermes plugins validate . --json
```

## Documentation updates

Update `README.md` to explain:

- Claude monetary extra-usage spend versus Codex provider-native credit units;
- when credits appear in compact labels;
- current, stale, off, exhausted, unlimited, and unavailable states;
- provider sources and the 15-minute monetary stale bound;
- sanitized local persistence and privacy guarantees;
- troubleshooting when allowance data exists but credit data is unavailable.

## Acceptance criteria

The feature is technically complete only when:

1. Both provider popovers always render a truthful Credits section.
2. Claude displays provider-reported spend/cap currency and Codex displays provider-native credit units without invented currency.
3. Compact labels follow the accepted consequential and controlling-window rules.
4. Credit freshness is independent from allowance freshness and stale monetary data expires after 15 minutes.
5. Existing refresh, authentication, visibility-menu, and keyboard behavior regressions are absent.
6. Automated tests, JavaScript syntax validation, and plugin validation pass.
7. Required states are verified in rendered Hermes Desktop behavior at normal and constrained widths.
8. The renderer console is free of new errors in the exercised flows.
9. README behavior, source, privacy, and troubleshooting documentation matches the implementation.

## Implementation boundary

Expected touched areas:

- `model_usage_status/core.py`
- `model_usage_status/service.py`
- `desktop/plugin.js`
- focused tests under `tests/`
- `README.md`
- package version metadata only if the implementation plan includes a release-ready version bump

No commit, push, pull request, release, deployment, or production activation is authorized by this design document.