# Claude and Codex Credit Usage Design

- **Date:** 2026-09-19
- **Status:** Approved product design; awaiting written-spec review
- **Project:** Hermes Model Usage Status
- **Scope:** Provider-native monetary or monetary-like credits in the existing Claude and Codex status items

## Summary

Improve the existing Hermes Desktop status-bar plugin so users can see provider-native credit usage alongside allowance windows without confusing credits with local token cost estimates.

The two existing provider popovers always show a Credits section. The compact status-bar label remains allowance-only until credits become consequential. Once credit spending is active, low, exhausted, or temporarily stale after prior activity, the label shows one account allowance plus the provider-native credit summary: the controlling exhausted allowance when one exists, otherwise the current account allowance with the lowest remaining percentage.

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
- A balance-only Codex snapshot cannot truthfully report `low` because it has no denominator. The UI must not infer a threshold from local history; it moves directly from available balance to explicit exhaustion unless a provider spend limit supplies a denominator.

### CREDIT-PLACEMENT-001 — Always in the popover; conditional in the label

- Every provider popover contains a Credits section.
- The status-bar label includes credits only when credit use is active, low, exhausted, or stale after previously confirmed activity.

### CREDIT-LABEL-001 — Preserve one account allowance beside credits

When credits are consequential, the compact label contains at most two facts:

1. one account allowance: the controlling exhausted allowance when one exists, otherwise the current short or weekly account window with the lowest remaining percentage; and
2. the provider-native credit summary.

Examples:

- `Claude 5h 0% · Credits $18.40/$100`
- `Claude Week 0% · Credits $18.40/$100`
- `Codex Week 0% · 9.5 credits left`
- `Claude 5h 42% · Credits $18.40/$100` when no account allowance is exhausted and the 5-hour window has the lowest remaining percentage
- `Claude Credits $18.40/$100` only when no current account allowance is identifiable

If the weekly allowance is exhausted, healthy 5-hour headroom is omitted because it cannot restore included access. If both account windows are exhausted, the weekly exhaustion is the controlling constraint. If neither is exhausted, compare valid current short and weekly percentages and choose the lower one; a tie chooses weekly because it is the longer-lived constraint. Model-specific windows remain available in the popover and do not expand the compact label beyond two facts. `Low`, `Exhausted`, and `stale` qualify the credit fact rather than count as additional facts.

### CREDIT-FRESHNESS-001 — Bound credit staleness

Credit data has its own observation time and freshness state. A failed refresh must preserve any last successful credit state, including `Off` and enabled zero-spend states, as visibly `stale` in the popover for at most 15 minutes. Non-consequential states remain absent from the compact label. After 15 minutes, the popover shows `Unavailable` and any credit amount leaves the compact label.

### CREDIT-ACQUISITION-001 — Keep Claude OAuth material server-side

- The plugin backend makes one OAuth usage request using Hermes's existing Claude credential resolver so the same response supplies allowance windows and structured `extra_usage` fields.
- The resolved token exists only in memory for that request. It is never logged, persisted, cached, or returned to the Desktop renderer.
- The raw provider response is normalized immediately and discarded; only the sanitized provider snapshot is persisted.
- Do not parse the shared account-usage adapter's formatted `details` text. That representation omits the `Off` state, can default an absent currency, and does not preserve the structured minor-unit scale required by this design.

## Architecture

### Existing surfaces retained

- `desktop/plugin.js` continues to register exactly two provider status items.
- `GET /usage` remains the single read surface.
- `POST /refresh` remains the single manual-refresh surface.
- Provider reauthentication routes remain unchanged.
- `UsageService` remains the owner of refresh serialization, cache persistence, and provider merging.

### Normalized credit contract

Each provider object gains a nullable `credits` object. Increment both the persisted snapshot schema and the renderer query-contract version. A schema-v1 allowance-only cache must trigger a refresh rather than appear to contain complete credit data.

Claude example:

```json
{
  "credits": {
    "status": "current",
    "freshness": "current",
    "unit": "currency",
    "currency": "USD",
    "minor_unit_scale": 2,
    "balance": null,
    "spend": {
      "used_minor": 1840,
      "limit_minor": 10000,
      "remaining_percent": 81.6,
      "resets_at": 1792800000
    },
    "active": true,
    "low": false,
    "exhausted": false,
    "observed_at": 1790200000,
    "source": "claude-oauth-usage",
    "error_code": null
  }
}
```

Codex example with both a balance and a monthly spend-control limit:

```json
{
  "credits": {
    "status": "current",
    "freshness": "current",
    "unit": "credits",
    "currency": null,
    "minor_unit_scale": null,
    "balance": {
      "available": true,
      "amount_credits": "9.5",
      "unlimited": false
    },
    "spend": {
      "used_credits": "2.5",
      "limit_credits": "12.0",
      "remaining_percent": 79.1666666667,
      "resets_at": 1792800000
    },
    "active": true,
    "low": false,
    "exhausted": false,
    "observed_at": 1790200000,
    "source": "codex-app-server",
    "error_code": null
  }
}
```

Contract rules:

- `status` is `current`, `off`, or `unavailable`. It describes credit entitlement and data presence, not observation age. `freshness` independently describes observation age as `current` or `stale` for a retained `current`/`off` observation and is `null` when status is `unavailable`.
- `unit` governs the entire credit block. `currency` and `minor_unit_scale` are required only when an enabled `currency` block contains monetary amounts. Currency is exactly three uppercase ASCII letters. The scale is a provider-reported integer from 0 through 6; never infer it from the currency code.
- Claude currency values use `spend.used_minor` and `spend.limit_minor`. Accept non-negative integers or integral finite floats only, preserve them as integers, and format them in the renderer with `Intl.NumberFormat` configured to the explicit `minor_unit_scale`.
- Codex balances use `balance.amount_credits`. Optional monthly-limit amounts use `spend.used_credits` and `spend.limit_credits`. These remain validated decimal strings because the provider contract supplies credit amounts, not a currency.
- `balance.available: true` with `amount_credits: null` means the provider confirmed credit availability but withheld the amount; it is current, non-consequential, and popover-only.
- `*_minor` fields are invalid when `unit` is `credits`; `*_credits` fields are invalid when `unit` is `currency`. A violation invalidates only the credit block.
- A provider may supply both `balance` and `spend`; this is required for Codex accounts that expose a remaining balance and a monthly spend-control limit in the same snapshot.
- `spend.remaining_percent` always describes the `spend` subobject, never `balance`, and exists only when the provider supplies a valid denominator or authoritative remaining percentage.
- `low` means 20% or less remains and is false when no denominator exists.
- `exhausted` requires an explicit provider zero or reached-spend-control signal.
- When `exhausted` and `low` are both true, `Exhausted` suppresses `Low` in all rendered copy; the normalized `low` flag may remain true.
- `active` must come from reported spend or an explicit provider condition proving credit-backed continuation. Local balance history and token activity are not evidence of active spending.
- Invalid, negative, non-finite, boolean, or ambiguous values invalidate only the affected credit block.
- `observed_at` is stamped locally in UTC epoch seconds when the provider response is received. Age is computed against the same wall clock; a negative age or an age over the applicable bound is expired, never current.
- Permitted `source` values are `claude-oauth-usage`, `codex-app-server`, or `null` before any successful credit observation. Permitted `error_code` values are `auth_rejected`, `timeout`, `rate_limited`, `malformed`, `unsupported`, or `null`. Neither field is rendered verbatim to the user.

### Claude acquisition and normalization

One plugin-backend OAuth usage read captures both allowance windows and `extra_usage`. Resolve the OAuth token through Hermes's existing credential resolver, use it only in the request authorization header, normalize the response at the provider boundary, and discard both token and raw response before returning. The renderer and persisted cache receive only sanitized fields from the normalized contract.

Normalize:

- `is_enabled: false` to `status: off`;
- valid enabled spend and cap to a currency `spend` object;
- provider currency plus minor-unit spend and cap without guessing scale;
- provider `decimal_places` to `minor_unit_scale`, rejecting absent, boolean, fractional, negative, or greater-than-six scales for enabled monetary amounts;
- spend greater than zero to `active: true`;
- zero cap remaining to `exhausted: true`;
- a valid remaining ratio of 20% or less to `low: true`.

The passive Claude status-line observation can recover allowance windows only. It does not contain `extra_usage`; therefore it cannot make credits current. A prior current or off credit state must remain visibly stale within the 15-minute bound, otherwise credits become unavailable.

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
- `hasCredits: false` with no balance to `status: off`, never `exhausted`;
- `hasCredits: true` with no returned balance to `status: current`, `balance.available: true`, and `balance.amount_credits: null`, with `active`, `low`, and `exhausted` all false;
- explicit zero balance or reached spend control to `exhausted: true`;
- the optional individual monthly limit to a `spend` object using provider-native credit units;
- provider-reported spend or explicit credit-backed continuation to `active: true`.

Resolve overlapping Codex signals deterministically. `spendControlReached` controls first and may coexist with a remaining balance. Otherwise, a valid numeric zero balance is exhausted, a valid unlimited balance is unlimited, `hasCredits: false` with no balance is off, and a finite or hidden available balance is current. Contradictory balance signals—such as `hasCredits: false` with a positive balance, or `unlimited: true` with a finite balance—make the credit block malformed rather than inviting a guess. Popover copy follows the same precedence.

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
| Stale current or off state | Last known current-state copy plus `stale`, or `Off · stale` | Existing observation age remains visible |
| Unavailable | `Unavailable` | No zero value or inferred balance |

Credit amounts use text rows only, matching the existing popover. This feature does not introduce a progress component.

#### Codex

| State | Primary text | Supporting text |
|---|---|---|
| Balance | `9.5 credits left` | None required |
| Unlimited | `Unlimited` | None required |
| Balance exhausted | `0 credits left · Exhausted` | None required |
| Available, hidden balance | `Available` | The provider did not return a balance |
| Off/no credit entitlement | `Off` | Credit-backed usage is not available |
| Stale current or off state | Last known current-state copy plus `stale`, or `Off · stale` | Existing observation age remains visible |
| Unavailable | `Unavailable` | No zero value or inferred balance |

When `individualLimit` is present, show a second text-only `Monthly credit limit` row with the provider's used amount, limit, remaining percentage, and reset time. Reuse the existing allowance percentage formatting in this row; retain the normalized full-precision percentage only for threshold evaluation. If spend control is reached while a balance remains, keep the truthful balance row and mark the monthly-limit row `Reached`; do not rewrite the balance as zero.

### Compact label algorithm

1. If credits are not active, low, exhausted, or bounded-stale after prior activity, use the current allowance-only label unchanged.
2. Select one account allowance:
   - weekly if the weekly account window is exhausted;
   - otherwise the exhausted short account window;
   - otherwise the current short or weekly account window with the lowest valid remaining percentage;
   - if the percentages tie, weekly;
   - otherwise none.
3. Format the provider-native credit summary:
   - Claude: `Credits <used>/<limit>`; convert integer minor units with the reported currency's standard minor-unit scale, render the exact amount, omit fractional digits for whole values, and otherwise preserve the full native fractional value; never approximate, truncate, or round beyond that exact conversion;
   - Codex: `<amount> credits left` or `Unlimited`, using the validated decimal balance when present; if no balance summary exists and spend control is reached, use `Monthly limit reached`.
4. When `low` or `exhausted` is true, append `Low` or `Exhausted` to the credit summary for either provider; `Exhausted` takes precedence. When a Codex qualifier derives from `spend` while the summary shows `balance`, use `Monthly limit low` or `Monthly limit reached` so the qualifier names the controlling value. Do not repeat a qualifier already present in the base summary.
5. Render provider name, optional selected allowance, and credit summary.
6. Append `stale` when the consequential credit value is bounded-stale.
7. Never include unavailable credit data in the compact label.

### Accessibility and responsive behavior

- Reuse the host's existing keyboard-operable menu items and focus behavior.
- Include textual `Low`, `Exhausted`, `Off`, `Unavailable`, and `stale` labels; color is supplementary only.
- Preserve the existing popover width and token-based spacing, typography, borders, and colors.
- Keep the compact label to at most two facts so it remains usable at constrained status-bar widths.
- Verify browser zoom and constrained width do not clip critical state text or make menu actions unreachable.

## Data flow

1. The renderer requests the existing combined usage snapshot.
2. `UsageService.get()` returns a cache younger than five minutes or calls `refresh()`.
3. `refresh()` allows one service-level owner covering both provider reads. Concurrent automatic callers share that owner. A manual request records `requested_at`: it accepts the owner's result only if the owner started at or after that time; otherwise manual requests coalesce into exactly one follow-up read after the owner completes. Both `requested_at` and `owner.started_at` use one in-process monotonic clock, so wall-clock adjustment cannot let an older owner satisfy a later manual request.
4. Claude and Codex refresh independently.
5. Each provider result normalizes allowance and credit data at its provider boundary.
6. The service merges successes and bounded stale data without letting one provider failure erase the other provider.
7. One sanitized snapshot is atomically persisted.
8. Labels and popovers update from the same versioned query result.

## Error and recovery behavior

| Condition | Required behavior |
|---|---|
| Authentication rejected | Preserve provider-specific reauthentication; credits are unavailable until a successful read |
| Timeout, 429, or transient provider failure | Preserve any last-known credit state as visibly stale in the popover for at most 15 minutes; only previously consequential values remain eligible for the compact label |
| Stale bound expires | Replace the amount with `Unavailable` in the popover and remove it from the compact label |
| Malformed credit block | Reject only that block; retain valid allowance data |
| Partial provider response | Render valid fields and mark the missing credit portion unavailable |
| Manual refresh fails | Keep the menu open, restore the Refresh control, and retain safe prior state |
| Passive Claude fallback succeeds | Refresh allowance windows only; credit freshness remains independent |
| Later provider recovery | Replace stale/unavailable credit state atomically without a plugin reload |
| Concurrent automatic refresh request | Wait for the existing owner; do not create live overlap |
| Manual refresh starts during an older in-flight read | Queue exactly one follow-up read after the owner completes; coalesce duplicate manual requests |

Refresh is short, read-only, and serialized. It does not need a user-facing cancellation control.

## Flow-State Brief

### Scope and mode

- **Outcome:** Show trustworthy allowance and credit state through initial load, refresh, partial provider recovery, bounded staleness, and authentication interruption.
- **Non-goals:** Payment actions, historical spend, account switching, reset-credit redemption, or new refresh controls.
- **Mode:** `single_flow`
- **State-machine scope:** S1–S4 and S6–S8 are instantiated independently per provider status item. S5 is one service-level refresh owner covering both provider reads. Mixed states are valid—for example, Claude may be S8 while Codex remains S2—and one provider's failure never changes the other provider's state.

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
| S6 | Bounded-stale credit state | Service and renderer | Refresh fails and any prior credit observation is at most 15 minutes old | Last successful current/off state and observation time | REQ-FRESH-1 |
| S7 | Credits unavailable | Service and renderer | No valid data, malformed data, stale bound expired, cold start, or legacy allowance-only cache | Valid allowance windows where present | REQ-FRESH-2, REQ-ERROR-1 |
| S8 | Authentication required | Provider authentication state | Provider rejects saved credentials | Safe allowance/cache data where valid | REQ-AUTH-1 |

### Transitions

| Transition ID | From | To | Initiated by | Completed by | Guards | Effects | Requirement IDs |
|---|---|---|---|---|---|---|---|
| T1 | S1/S2/S3/S4/S6/S7 | S5 | Timer or user | `UsageService` | Cold/legacy cache enters immediately; automatic callers share an owner; a manual caller accepts that owner only if `owner.started_at >= requested_at`, otherwise it queues one coalesced follow-up | Disable Refresh and begin one combined refresh with independent provider reads | REQ-FLOW-1 |
| T2 | S5 | S1/S2/S3/S4 | Provider response | Provider normalizer and service | Response fields are valid and sanitized | Atomically replace provider state and clear stale error | REQ-DATA-1, REQ-RECOVERY-1 |
| T3 | S5 | S6 | Provider failure | Service | Any prior credit observation exists and is no older than 15 minutes | Preserve the prior current/off state with explicit stale freshness; only consequential data remains label-eligible | REQ-FRESH-1 |
| T4 | S5/S6 | S7 | Provider failure or clock expiry | Service | No valid prior credit observation, age is negative, or stale age exceeds 15 minutes | Remove credit data from the compact label and expose Unavailable | REQ-FRESH-2 |
| T5 | S5 | S8 | Provider authentication rejection | Service | Provider explicitly reports rejected credentials | Expose existing reauthentication action without leaking credentials | REQ-AUTH-1 |
| T6 | S8 | S5 | User selects Refresh after provider login | User and service | Provider login has completed outside the plugin; if an older owner is running, queue exactly one follow-up read after it | Revalidate authentication and usage state from a read that starts no earlier than the user's request | REQ-AUTH-2 |

### Lifecycle behavior

| Transition ID | Cancellation | Interruption | Retry/recovery | Supersession/stale result | Resume/revalidation |
|---|---|---|---|---|---|
| T1 | No cancellation control; read is short and non-destructive | Menu may close without cancelling the service refresh | Automatic callers share the active owner; manual callers requested after that owner started coalesce into one follow-up | An owner's result cannot satisfy a later manual request; one follow-up supersedes it for that request | Result updates shared query data independent of menu visibility |
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
- **REQ-LABEL-2:** Consequential credits show no more than one selected account allowance and the native credit summary; an exhausted controlling allowance wins, otherwise the valid short/weekly window with the lowest remaining percentage wins.
- **REQ-DATA-1:** A successful refresh updates windows and credits from one provider observation without exposing raw payloads or credentials.
- **REQ-STATE-1:** Low appears only with a valid denominator at 20% or less remaining.
- **REQ-STATE-2:** Exhausted appears only from explicit provider zero or reached state.
- **REQ-FLOW-1:** Concurrent automatic refreshes share one service-level owner, while manual refreshes issued after that owner started coalesce into exactly one follow-up read; the Refresh control restores on every exit.
- **REQ-FRESH-1:** A failed refresh preserves any last-known current/off credit state as visibly stale in the popover for no more than 15 minutes; only consequential states remain eligible for the compact label.
- **REQ-FRESH-2:** After 15 minutes, stale credit data becomes unavailable and leaves the compact label.
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

- Claude enabled, off, zero spend, active, low, exhausted, exhausted-at-0%-with-Low-suppressed, missing cap, invalid currency, and malformed values;
- Claude minor-unit scales 0, 2, and 3; integral-float minor amounts; and missing, boolean, fractional, negative, or oversized scales;
- Codex finite balance, zero, unlimited, `hasCredits: false`, `hasCredits: true` with hidden balance, individual monthly limit, spend-control reached with and without a remaining balance, overlapping-signal precedence, contradictory-signal rejection, and absent data;
- provider-specific unit conversion and exact decimal preservation;
- invalid cross-unit fields and spend-only `remaining_percent` scoping;
- rejection of booleans, NaN, infinity, negatives, and ambiguous strings;
- active, low, exhausted, and unavailable derivation without local inference.

### Service tests

Cover:

- current allowances with unavailable credits;
- a single Claude OAuth response supplying both windows and credits without persisting or returning token/raw-response material;
- current credits with provider-specific source and observation time;
- transient failure to bounded stale state;
- failure and expiry from non-consequential `Off` and enabled-zero states;
- stale expiry after 15 minutes;
- negative freshness age expiring immediately;
- later success clearing stale state;
- independent provider failure and recovery;
- concurrent automatic refreshes sharing one provider read;
- manual refresh issued during an older in-flight read triggering exactly one follow-up provider read;
- authentication-required behavior;
- closed `source`/`error_code` vocabularies and cache/API payloads excluding credentials and raw provider data.

### Desktop contract tests

Cover:

- a Credits section for both providers;
- approved copy for every credit state;
- consequential-label selection and controlling-window priority;
- lowest-remaining allowance selection when no account allowance is exhausted;
- at-most-two-facts compact label rule;
- provider-native Claude currency and Codex credit-unit formatting;
- Codex `Monthly limit low`/`Monthly limit reached` qualifier provenance and existing allowance percentage formatting;
- textual Low/Exhausted precedence without color dependence;
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

Inspect label geometry, truncation, menu spacing, focus order, hit targets, overflow, zoom, text-row hierarchy, and console errors. Use deterministic mocked data for states the live account cannot produce and separately exercise the installed plugin with current live provider state.

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
- provider sources and the 15-minute credit stale bound;
- sanitized local persistence and privacy guarantees;
- troubleshooting when allowance data exists but credit data is unavailable.

## Acceptance criteria

The feature is technically complete only when:

1. Both provider popovers always render a truthful Credits section.
2. Claude displays provider-reported spend/cap currency and Codex displays provider-native credit units without invented currency.
3. Compact labels follow the accepted consequential and account-allowance selection rules.
4. Credit freshness is independent from allowance freshness and stale credit data expires after 15 minutes.
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