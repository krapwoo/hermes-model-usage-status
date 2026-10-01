# Provider-Native Credit Usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing Claude and Codex status items so their popovers always show truthful provider-native credit state and their compact labels add credits only when consequential.

**Architecture:** Keep the current two-item Desktop plugin, `/usage`, `/refresh`, provider authentication routes, five-minute cache, and passive Claude fallback. Normalize allowance and credit fields together at each provider boundary, persist only schema-v2 sanitized snapshots, age credit observations independently, and render all labels and popover rows from one query result. Claude makes one backend-only OAuth usage request through Hermes's credential resolver; Codex keeps one `account/rateLimits/read` request.

**Tech Stack:** Python 3.11 (`unittest`, `decimal`, `threading`, `httpx` supplied by Hermes), FastAPI plugin routes, JavaScript/React through `@hermes/plugin-sdk`, Node 22 VM modules for pure renderer-helper tests, Hermes plugin validator.

## Global Constraints

- Improve the existing plugin; do not add a status item, API route, polling loop, progress component, local token-cost estimate, currency conversion, purchase flow, or reset-credit display.
- Preserve exactly two registered status items, `GET /usage`, `POST /refresh`, provider-specific reauthentication, one five-minute shared query, passive Claude allowance fallback, keyboard-operable menu items, and current host tokens/layout.
- Persisted snapshot schema becomes `2`; Desktop `DATA_CONTRACT_VERSION` becomes `4`. Reject legacy cache schema rather than presenting allowance-only data as a complete credit snapshot.
- Claude uses integer provider-reported minor units plus a three-letter currency and explicit `minor_unit_scale` from 0 through 6. Never infer currency or scale.
- Codex uses validated provider-native decimal strings. Never add a currency symbol unless the provider contract later supplies currency.
- Every popover has a text-only Credits section. Credits enter a compact label only for active, low, exhausted, or bounded-stale-after-activity states.
- Compact labels contain at most one selected account allowance plus one credit summary. Weekly exhaustion controls; otherwise short-window exhaustion controls; otherwise lowest remaining percentage controls, with weekly winning ties.
- Credit freshness is independent from allowance freshness. Transient failure retains a previous current/off credit observation as visibly stale for at most `900` seconds; authentication rejection, malformed/unsupported data, negative age, or age over `900` becomes unavailable immediately.
- Automatic refresh callers share one owner. A manual refresh newer than the active owner queues exactly one coalesced follow-up read for that owner.
- Tokens and raw provider payloads exist only inside the Claude request function. Never log, persist, cache, return, or interpolate them into exceptions.
- Keep `IDEA.md` untouched. Do not bump package/plugin versions, push, open a PR, release, or deploy in this implementation slice.
- Run Python tests with `/Users/woohopark/.hermes/hermes-agent/venv/bin/python`; the system Python lacks Hermes's `agent` and `fastapi` modules.

## File Map

- `model_usage_status/core.py` — provider normalization, closed credit contract, independent credit aging/merge, existing allowance normalization.
- `model_usage_status/service.py` — one Claude OAuth read, provider error classification, schema-v2 cache, refresh serialization, passive fallback merge.
- `dashboard/plugin_api.py` — identify `POST /refresh` as a manual refresh without changing the route.
- `desktop/plugin.js` — versioned query, pure credit/allowance formatting helpers, compact-label selection, text-only Credits section.
- `tests/test_core.py` — provider credit normalization and independent freshness tests.
- `tests/test_service.py` — credential boundary, sanitized persistence, cache migration, partial recovery, and refresh ownership tests.
- `tests/test_api.py` — route-level proof that `/refresh` requests manual supersession.
- `tests/test_desktop_contract.py` — structural plugin invariants and query-contract version.
- `tests/test_desktop_logic.py` — executable Node VM tests for exact renderer helper output.
- `tests/support/run_desktop_helpers.mjs` — loads the no-build plugin with mocked imports and calls named pure helpers.
- `tests/fixtures/credit_usage_states.json` — nine deterministic sanitized snapshots reused by renderer tests and host verification.
- `tests/support/write_usage_fixture.py` — writes one selected fixture atomically to an explicitly supplied cache path.
- `README.md` — credit semantics, visibility, freshness, privacy, and troubleshooting.
- `docs/verification/2026-09-19-credit-usage/README.md` plus screenshots — durable rendered verification record.

## Required Execution Preflight

Before Task 1 writes fixtures, exercise the installed implementation's existing credential-safe Claude usage request without recording values. Confirm only that enabled `extra_usage` reports `is_enabled: bool`, integral non-negative `used_credits` and `monthly_limit`, a three-letter `currency`, and integer `decimal_places`. Confirm the installed Codex schema still names `credits`, `individualLimit`, and `spendControlReached` and uses native credit strings. If these names/types differ or no live account is available to confirm them, stop before implementation: return to design review for a contract decision rather than treating the plan's fixtures as authoritative.

---

### Task 1: Normalize Provider Credit Contracts

**Files:**
- Modify: `model_usage_status/core.py:15-297`
- Modify: `tests/test_core.py:10-247`

**Interfaces:**
- Consumes: Raw Claude OAuth response dictionaries and Codex `account/rateLimits/read` result dictionaries.
- Produces: `normalize_claude_oauth_result(payload: dict[str, Any], observed_at: int) -> dict[str, Any]`, `normalize_codex_result(result: dict[str, Any], observed_at: int) -> dict[str, Any]`, `normalize_claude_payload(payload: dict[str, Any], observed_at: int) -> dict[str, Any]`, `unavailable_credits(unit: str, error_code: str) -> dict[str, Any]`, `age_credit_state(credits: dict[str, Any], now: int) -> dict[str, Any]`, and `merge_credit_refresh_result(previous: dict[str, Any] | None, fresh: dict[str, Any] | None, now: int, error_code: str | None) -> dict[str, Any]`.

- [ ] **Step 1: Add failing Claude credit-contract tests**

Extend the import list and add table-driven tests that assert the whole normalized credit block, not selected fields:

```python
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
```

Add these concrete validation methods:

```python
    def test_scale_and_minor_amount_validation(self) -> None:
        for scale in (0, 2, 3):
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
```

- [ ] **Step 2: Run the Claude normalization tests and confirm RED**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_core.ClaudeCreditNormalizationTests -v
```

Expected: import failure for `normalize_claude_oauth_result` and `merge_credit_refresh_result`.

- [ ] **Step 3: Add failing Codex credit-contract tests**

Add tests using the official app-server keys and exact decimal strings:

```python
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
            "hidden": ({"hasCredits": True, "unlimited": False, "balance": None}, "current", False),
            "off": ({"hasCredits": False, "unlimited": False, "balance": None}, "off", False),
            "unlimited": ({"hasCredits": True, "unlimited": True, "balance": None}, "current", False),
            "zero": ({"hasCredits": True, "unlimited": False, "balance": "0"}, "current", True),
        }
        for name, (raw, status, exhausted) in cases.items():
            with self.subTest(name=name):
                credits = normalize_codex_result(self.result(raw), observed_at=100)["credits"]
                self.assertEqual(credits["status"], status)
                self.assertEqual(credits["exhausted"], exhausted)

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
```

Add the remaining boundary method:

```python
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
        self.assertEqual(absent, unavailable_credits("credits", "unsupported"))

        for invalid in (True, "-1", "1e2", "Infinity"):
            with self.subTest(invalid=invalid):
                credits = normalize_codex_result(self.result({
                    "hasCredits": True, "unlimited": False, "balance": invalid,
                }), observed_at=100)["credits"]
                self.assertEqual(credits["status"], "unavailable")
                self.assertEqual(credits["error_code"], "malformed")
```

- [ ] **Step 4: Run the Codex normalization tests and confirm RED**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_core.CodexCreditNormalizationTests -v
```

Expected: FAIL because `normalize_codex_result()` does not emit `credits`.

- [ ] **Step 5: Implement the closed credit helpers and provider normalizers**

Add `math`, `re`, `datetime`, `Decimal`, and `InvalidOperation` imports. Use these concrete helper contracts:

```python
_CREDIT_ERROR_CODES = {
    "auth_rejected", "timeout", "rate_limited", "provider_unavailable",
    "malformed", "unsupported", None,
}
_DECIMAL_CREDITS = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


def unavailable_credits(unit: str, error_code: str) -> dict[str, Any]:
    if unit not in {"currency", "credits"}:
        raise ValueError("invalid_credit_unit")
    if error_code not in _CREDIT_ERROR_CODES or error_code is None:
        raise ValueError("invalid_credit_error_code")
    return {
        "status": "unavailable", "freshness": None, "unit": unit,
        "currency": None, "minor_unit_scale": None, "balance": None,
        "spend": None, "active": False, "low": False, "exhausted": False,
        "observed_at": None, "source": None, "error_code": error_code,
    }


def _non_negative_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or value < 0 or int(value) != value:
        return None
    return int(value)


def _credit_decimal(value: Any) -> str | None:
    if not isinstance(value, str) or not _DECIMAL_CREDITS.fullmatch(value):
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return value if parsed.is_finite() and parsed >= 0 else None


def _remaining(used: Decimal, limit: Decimal) -> float | None:
    if limit <= 0:
        return None
    return float(max(Decimal(0), (limit - used) * Decimal(100) / limit))
```

Implement `_normalize_claude_credits(extra_usage, observed_at)`, `_normalize_codex_credits(result, account, observed_at)`, and `normalize_claude_oauth_result()`. Preserve the existing fraction behavior for Claude OAuth `utilization` (`0 <= value <= 1` is multiplied by 100), parse ISO reset strings to UTC epoch seconds, and do not copy unknown provider keys. `normalize_claude_payload()` must continue normalizing passive `rate_limits` while attaching `unavailable_credits("currency", "unsupported")`. `normalize_codex_result()` must attach the normalized credit block while leaving account and model windows unchanged.

For Codex `individualLimit`, a finite non-boolean provider `remainingPercent` in the inclusive range `0..100` is authoritative and is preserved as supplied. When it is `null`/absent and valid positive `limit` plus non-negative `used` decimals exist, derive it with `_remaining()`. Any other supplied percentage is malformed. In either source path, `used >= limit > 0` overrides the percentage to `0.0` and sets `exhausted: true`; the existing `79.1666666667`, `20`, and reached `0.0` test oracles follow this precedence.

- [ ] **Step 6: Add and run independent freshness tests**

Add:

```python
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
```

Implement `merge_credit_refresh_result()` with the exact `0 <= now - observed_at <= 900` bound for transient errors only. A retained stale block stores the safe originating transient code (`timeout`, `rate_limited`, or `provider_unavailable`) in `error_code`; current successful blocks keep `error_code: null`. `age_credit_state()` is the cache-read path: negative age or age over `900` becomes `unavailable_credits(credits["unit"], credits["error_code"] or "provider_unavailable")` for either current or stale blocks; current and stale blocks within the inclusive bound retain their respective freshness. Extend `merge_refresh_result()` with keyword-only `credit_unit: str | None = None` and `credit_error_code: str | None = None`; derive the compatibility default from the fresh/previous provider id (`claude` → `currency`, `codex` → `credits`) so existing allowance tests remain valid, while every service call in Task 2 passes the unit explicitly. Merge allowances with the existing logic and credits with the new helper.

- [ ] **Step 7: Run Task 1 tests and commit**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest tests.test_core -v
```

Expected: all core tests pass.

Commit:

```bash
git add model_usage_status/core.py tests/test_core.py
git commit -m "feat: normalize provider credit usage"
```

---

### Task 2: Acquire Claude Credits Safely and Persist Schema v2

**Files:**
- Modify: `model_usage_status/service.py:1-382`
- Modify: `tests/test_service.py:18-411`

**Interfaces:**
- Consumes: `normalize_claude_oauth_result(payload: dict[str, Any], observed_at: int)`, `normalize_codex_result(result: dict[str, Any], observed_at: int)`, and `merge_refresh_result(previous, fresh, now, error_code=None, *, credit_unit=None, credit_error_code=None)` from Task 1.
- Produces: `fetch_claude_rate_limits(observed_at: int, *, token_resolver: Callable[[], str | None] | None = None, oauth_checker: Callable[[str], bool] | None = None, request_json: Callable[[str, dict[str, str], float], dict[str, Any]] | None = None) -> dict[str, Any]`; schema-v2 sanitized snapshots; independent credit stale/expiry behavior.

- [ ] **Step 1: Write failing one-request and privacy-boundary tests**

Replace tests that patch `agent.account_usage._fetch_anthropic_account_usage` with injected credential/request functions:

```python
from model_usage_status.core import normalize_claude_oauth_result, normalize_codex_result
from model_usage_status.service import ProviderUsageUnavailable


class ClaudeOAuthUsageTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "five_hour": {"utilization": 0.1, "resets_at": "2026-09-20T01:00:00Z"},
            "seven_day": {"utilization": 0.2, "resets_at": "2026-09-25T01:00:00Z"},
            "extra_usage": {
                "is_enabled": True, "used_credits": 1840, "monthly_limit": 10000,
                "currency": "USD", "decimal_places": 2,
            },
        }

    def test_one_oauth_request_returns_only_normalized_provider_state(self) -> None:
        calls = []
        def request_json(url: str, headers: dict[str, str], timeout: float) -> dict:
            calls.append((url, headers, timeout))
            return self.payload()

        provider = fetch_claude_rate_limits(
            100,
            token_resolver=lambda: "oauth-secret-token",
            oauth_checker=lambda token: token == "oauth-secret-token",
            request_json=request_json,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "https://api.anthropic.com/api/oauth/usage")
        self.assertEqual(calls[0][2], 15.0)
        self.assertEqual(calls[0][1]["Authorization"], "Bearer oauth-secret-token")
        serialized = json.dumps(provider)
        self.assertNotIn("oauth-secret-token", serialized)
        self.assertNotIn("extra_usage", serialized)
        self.assertEqual(provider["credits"]["spend"]["used_minor"], 1840)

    def test_default_path_imports_the_installed_hermes_credential_resolver(self) -> None:
        with (
            patch("agent.anthropic_credentials.resolve_anthropic_token",
                  return_value="oauth-secret-token"),
            patch("agent.anthropic_credentials._is_oauth_token", return_value=True),
            patch("model_usage_status.service._claude_request_json",
                  return_value=self.payload()),
        ):
            provider = fetch_claude_rate_limits(100)
        self.assertEqual(provider["credits"]["status"], "current")

    def test_401_403_429_timeout_and_invalid_body_use_safe_errors(self) -> None:
        class ResponseError(RuntimeError):
            def __init__(self, status_code: int) -> None:
                super().__init__("provider body containing secret-value")
                self.response = SimpleNamespace(status_code=status_code)

        for error, expected in (
            (ResponseError(401), ClaudeAuthenticationRequired),
            (ResponseError(403), ClaudeAuthenticationRequired),
            (ResponseError(429), ProviderUsageUnavailable),
            (TimeoutError("secret-value"), ProviderUsageUnavailable),
        ):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(expected) as raised:
                    fetch_claude_rate_limits(
                        100,
                        token_resolver=lambda: "oauth-secret-token",
                        oauth_checker=lambda _token: True,
                        request_json=lambda *_args: (_ for _ in ()).throw(error),
                    )
                self.assertNotIn("secret-value", str(raised.exception))
```

Use `assertLogs()` only around service code that intentionally logs; otherwise assert no logger call is introduced. Add missing-token and non-OAuth-token cases with safe `unsupported` classification.

- [ ] **Step 2: Run the OAuth tests and confirm RED**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_service.ClaudeOAuthUsageTests -v
```

Expected: FAIL because the existing function takes no timestamp/dependency arguments and returns the aggregate adapter's allowance-only shape.

- [ ] **Step 3: Implement the backend-only OAuth request**

Add constants and safe exceptions:

```python
SCHEMA_VERSION = 2
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_TIMEOUT_SECONDS = 15.0
CREDIT_STALE_AFTER_SECONDS = 900

class ProviderUsageUnavailable(RuntimeError):
    def __init__(self, credit_error_code: str) -> None:
        self.credit_error_code = credit_error_code
        super().__init__(credit_error_code)
```

Implement the default request and normalized fetch boundary:

```python
def _claude_request_json(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    import httpx
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ProviderUsageUnavailable("malformed")
    return payload


def fetch_claude_rate_limits(
    observed_at: int,
    *,
    token_resolver: Callable[[], str | None] | None = None,
    oauth_checker: Callable[[str], bool] | None = None,
    request_json: Callable[[str, dict[str, str], float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if token_resolver is None or oauth_checker is None:
        from agent.anthropic_credentials import _is_oauth_token, resolve_anthropic_token
        token_resolver = token_resolver or resolve_anthropic_token
        oauth_checker = oauth_checker or _is_oauth_token
    requester = request_json or _claude_request_json
    token = str(token_resolver() or "").strip()
    if not token or not oauth_checker(token):
        raise ProviderUsageUnavailable("unsupported")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.1.0",
    }
    try:
        payload = requester(CLAUDE_USAGE_URL, headers, CLAUDE_TIMEOUT_SECONDS)
    except Exception as error:
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code in (401, 403):
            raise ClaudeAuthenticationRequired("claude_authentication_required") from None
        if status_code == 429:
            raise ProviderUsageUnavailable("rate_limited") from None
        if isinstance(error, TimeoutError) or error.__class__.__name__ in {"TimeoutException", "ReadTimeout"}:
            raise ProviderUsageUnavailable("timeout") from None
        if isinstance(error, ProviderUsageUnavailable):
            raise
        raise ProviderUsageUnavailable("provider_unavailable") from None
    if not isinstance(payload, dict):
        raise ProviderUsageUnavailable("malformed")
    return normalize_claude_oauth_result(payload, observed_at)
```

Do not log `headers`, `token`, `payload`, or the caught exception string. Delete the formatted-details adapter path entirely.

- [ ] **Step 4: Write failing cache, partial-recovery, and freshness tests**

Add tests that assert:

```python
class CreditServiceTests(unittest.TestCase):
    def test_legacy_cache_forces_refresh_and_schema_two_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "usage-cache.json").write_text(json.dumps({
                "schema_version": 1, "generated_at": 999,
                "providers": {},
            }), encoding="utf-8")
            calls = []
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: calls.append("codex") or codex_result(),
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: 1000,
                auth_checker=authenticated,
            )
            result = service.get()
            self.assertEqual(calls, ["codex"])
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(json.loads((data_dir / "usage-cache.json").read_text())["schema_version"], 2)

    def test_passive_allowance_fallback_cannot_refresh_credits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            now = [100]
            service = UsageService(
                data_dir,
                codex_fetcher=lambda: codex_result(),
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: now[0],
                auth_checker=authenticated,
            )
            first = service.refresh()
            now[0] = 200
            service.claude_fetcher = lambda _observed_at: (_ for _ in ()).throw(
                ProviderUsageUnavailable("timeout")
            )
            write_passive_observation(data_dir, observed_at=200)
            second = service.refresh()
            self.assertEqual(second["providers"]["claude"]["windows"][0]["remaining_percent"], 90.0)
            self.assertEqual(second["providers"]["claude"]["credits"]["freshness"], "stale")
            self.assertEqual(second["providers"]["claude"]["credits"]["observed_at"], 100)

    def test_stale_credit_expires_on_cached_read_without_waiting_for_cache_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_schema_two_cache(data_dir, generated_at=995, credit_observed_at=99,
                                   credit_freshness="stale")
            service = UsageService(data_dir, now=lambda: 1000, auth_checker=authenticated)
            credits = service.get()["providers"]["claude"]["credits"]
            self.assertEqual(credits["status"], "unavailable")
            self.assertIsNone(credits["observed_at"])
            self.assertIsNone(credits["source"])
```

Define these concrete test helpers in `tests/test_service.py`:

```python
def normalized_claude(observed_at: int) -> dict:
    return normalize_claude_oauth_result({
        "five_hour": {"utilization": 0.1, "resets_at": "2026-09-20T01:00:00Z"},
        "seven_day": {"utilization": 0.2, "resets_at": "2026-09-25T01:00:00Z"},
        "extra_usage": {
            "is_enabled": True, "used_credits": 1840, "monthly_limit": 10000,
            "currency": "USD", "decimal_places": 2,
        },
    }, observed_at)


def write_passive_observation(data_dir: Path, observed_at: int) -> None:
    (data_dir / "claude-observation.json").write_text(json.dumps({
        "schema_version": 1,
        "provider": "claude",
        "observed_at": observed_at,
        "activity_marker": "fixture",
        "observation_source": "status_line",
        "rate_limits": {
            "five_hour": {"used_percentage": 10, "resets_at": 2000},
            "seven_day": {"used_percentage": 20, "resets_at": 3000},
        },
    }), encoding="utf-8")


def write_schema_two_cache(data_dir: Path, *, generated_at: int,
                           credit_observed_at: int,
                           credit_freshness: str) -> None:
    claude = normalized_claude(credit_observed_at)
    claude["credits"]["freshness"] = credit_freshness
    codex = normalize_codex_result(codex_result(), observed_at=credit_observed_at)
    (data_dir / "usage-cache.json").write_text(json.dumps({
        "schema_version": 2,
        "generated_at": generated_at,
        "providers": {"claude": claude, "codex": codex},
    }), encoding="utf-8")
```

- [ ] **Step 5: Implement schema-v2 cache and provider-specific merging**

Change the injected Claude fetcher contract to `Callable[[int], dict[str, Any]]`. Make `_read_cache()` return `None` unless `schema_version == SCHEMA_VERSION`. Include unit-correct unavailable credits in `_unavailable()`. Extract `_refresh_once()` from the current owner body and use these calls:

```python
codex_fresh = normalize_codex_result(self.codex_fetcher(), observed_at=now)
codex = merge_refresh_result(
    previous_providers.get("codex"), codex_fresh, now,
    credit_unit="credits",
)

claude_fresh = self.claude_fetcher(now)
claude = merge_refresh_result(
    previous_providers.get("claude"), claude_fresh, now,
    credit_unit="currency",
)
```

Map caught errors without copying their messages:

```python
def _credit_error_code(error: Exception) -> str:
    if isinstance(error, ClaudeAuthenticationRequired):
        return "auth_rejected"
    if isinstance(error, ProviderUsageUnavailable):
        return error.credit_error_code
    if str(error) == "codex_timeout":
        return "timeout"
    return "provider_unavailable"
```

On Claude live-read failure, normalize the passive observation for allowance only, then call `merge_refresh_result(..., credit_error_code=_credit_error_code(error))`. If no passive observation exists, merge a whole-provider failure. Authentication rejection sets the existing top-level `authentication_required` error and credit `auth_rejected` unavailable state. Write exactly one schema-v2 snapshot per refresh cycle.

Before returning a fresh cache in `get()`, reject a negative `generated_at` age and call `age_credit_state(provider["credits"], now)` for both providers. If aging changes a block, acquire `_state_lock`, require `not self._refreshing`, re-read the cache, and persist only when its `generated_at` still equals the snapshot being aged; keep the lock through `atomic_write_json()`. If a refresh is active or a newer `generated_at` exists, skip the aging write and return the in-memory aged view. Add a test with an owner blocked in `_refresh_once()` while `get()` ages an older cache, then assert the owner's newer `generated_at` survives. This prevents a cache-read aging write from clobbering a newer provider refresh.

- [ ] **Step 6: Run Task 2 tests and commit**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_core tests.test_service -v
```

Expected: all core and service tests pass; serialized snapshots contain neither `Authorization`, `oauth-secret-token`, `extra_usage`, nor raw provider error text.

Commit:

```bash
git add model_usage_status/core.py model_usage_status/service.py tests/test_core.py tests/test_service.py
git commit -m "feat: refresh and cache sanitized credit usage"
```

---

### Task 3: Coalesce Newer Manual Refreshes

**Files:**
- Modify: `model_usage_status/service.py:231-382`
- Modify: `dashboard/plugin_api.py:25-32`
- Modify: `tests/test_service.py:377-411`
- Modify: `tests/test_api.py:1-112`

**Interfaces:**
- Consumes: Task 2 `_refresh_once() -> dict[str, Any]` and schema-v2 cache.
- Produces: `UsageService.refresh(*, manual: bool = False) -> dict[str, Any]`; `/refresh` calls `manual=True`; automatic `get()` calls the default.

- [ ] **Step 1: Write failing refresh-ownership tests**

Keep the existing automatic-sharing test and add a deterministic manual supersession test with two gates:

```python
class RefreshOwnershipTests(unittest.TestCase):
    def test_newer_manual_requests_coalesce_into_one_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0
            first_started = threading.Event()
            release_first = threading.Event()
            followup_started = threading.Event()
            release_followup = threading.Event()
            ticks = iter([10.0, 20.0, 21.0, 30.0])

            def codex_fetcher() -> dict:
                nonlocal calls
                calls += 1
                if calls == 1:
                    first_started.set()
                    release_first.wait(timeout=2)
                elif calls == 2:
                    followup_started.set()
                    release_followup.wait(timeout=2)
                return codex_result(used=50 - calls)

            service = UsageService(
                Path(directory), codex_fetcher=codex_fetcher,
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: 1000, monotonic=lambda: next(ticks),
                auth_checker=authenticated,
            )
            results = []
            owner = threading.Thread(target=lambda: results.append(service.refresh()))
            owner.start()
            self.assertTrue(first_started.wait(timeout=1))
            waiter_count = 0
            waiter_count_lock = threading.Lock()
            both_waiting = threading.Event()
            original_wait = service._refresh_done.wait
            def observed_wait(timeout=None):
                nonlocal waiter_count
                with waiter_count_lock:
                    waiter_count += 1
                    if waiter_count == 2:
                        both_waiting.set()
                return original_wait(timeout)
            manual_a = threading.Thread(target=lambda: results.append(service.refresh(manual=True)))
            manual_b = threading.Thread(target=lambda: results.append(service.refresh(manual=True)))
            with patch.object(service._refresh_done, "wait", side_effect=observed_wait):
                manual_a.start()
                manual_b.start()
                self.assertTrue(both_waiting.wait(timeout=1))
            release_first.set()
            self.assertTrue(followup_started.wait(timeout=1))
            release_followup.set()
            for thread in (owner, manual_a, manual_b):
                thread.join(timeout=2)

            self.assertEqual(calls, 2)
            self.assertEqual(len(results), 3)
            self.assertTrue(all(result["generated_at"] == 1000 for result in results))
```

Keep the existing automatic-sharing assertion (`calls == 1`) and add these bounded cases:

```python
    def test_manual_request_with_equal_timestamp_accepts_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gate = threading.Event()
            started = threading.Event()
            calls = 0
            def fetcher() -> dict:
                nonlocal calls
                calls += 1
                started.set()
                gate.wait(timeout=2)
                return codex_result()
            service = UsageService(
                Path(directory), codex_fetcher=fetcher,
                claude_fetcher=lambda observed_at: normalized_claude(observed_at),
                now=lambda: 1000, monotonic=lambda: 10.0,
                auth_checker=authenticated,
            )
            owner = threading.Thread(target=service.refresh)
            manual = threading.Thread(target=lambda: service.refresh(manual=True))
            owner.start()
            self.assertTrue(started.wait(timeout=1))
            waiting = threading.Event()
            original_wait = service._refresh_done.wait
            def observed_wait(timeout=None):
                waiting.set()
                return original_wait(timeout)
            with patch.object(service._refresh_done, "wait", side_effect=observed_wait):
                manual.start()
                self.assertTrue(waiting.wait(timeout=1))
            gate.set()
            owner.join(timeout=2)
            manual.join(timeout=2)
            self.assertEqual(calls, 1)

    def test_unexpected_owner_failure_releases_waiters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = UsageService(
                Path(directory), now=lambda: 1000, monotonic=lambda: 10.0,
                auth_checker=authenticated,
            )
            started = threading.Event()
            release = threading.Event()
            def fail_once() -> dict:
                started.set()
                release.wait(timeout=2)
                raise RuntimeError("synthetic failure")
            service._refresh_once = fail_once
            owner_errors = []
            owner = threading.Thread(target=lambda: _capture_error(service.refresh, owner_errors))
            waiter = threading.Thread(target=service.refresh)
            owner.start()
            self.assertTrue(started.wait(timeout=1))
            waiter.start()
            release.set()
            owner.join(timeout=2)
            waiter.join(timeout=2)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(len(owner_errors), 1)

    def test_stale_owner_token_cannot_deregister_a_new_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = UsageService(Path(directory), auth_checker=authenticated)
            stale_done = threading.Event()
            newer_done = threading.Event()
            with service._state_lock:
                service._refreshing = True
                service._owner_started_at = 20.0
                service._refresh_done = newer_done
                released = service._finish_owner_locked(stale_done)
            self.assertFalse(released)
            self.assertTrue(service._refreshing)
            self.assertEqual(service._owner_started_at, 20.0)
            self.assertIs(service._refresh_done, newer_done)
```

Define this helper once above the test class; no exception text is serialized into the fallback snapshot:

```python
def _capture_error(callable_, errors: list[Exception]) -> None:
    try:
        callable_()
    except Exception as error:
        errors.append(error)
```

- [ ] **Step 2: Run refresh-ownership tests and confirm RED**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_service.RefreshOwnershipTests -v
```

Expected: FAIL because `refresh()` has no `manual` or `monotonic` contract.

- [ ] **Step 3: Implement the monotonic owner loop**

Add `monotonic: Callable[[], float] = time.monotonic` to `UsageService.__init__()`. Replace owner state with:

```python
self.monotonic = monotonic
self._refreshing = False
self._owner_started_at: float | None = None
self._followup_requested = False
self._refresh_done = threading.Event()
self._refresh_done.set()
```

Use this owner loop:

```python
def _finish_owner_locked(self, done: threading.Event) -> bool:
    if self._refresh_done is not done:
        return False
    self._refreshing = False
    self._owner_started_at = None
    self._followup_requested = False
    done.set()
    return True


def refresh(self, *, manual: bool = False) -> dict[str, Any]:
    requested_at = self.monotonic()
    with self._state_lock:
        if self._refreshing:
            if manual and self._owner_started_at is not None and self._owner_started_at < requested_at:
                self._followup_requested = True
            done = self._refresh_done
            is_owner = False
        else:
            self._refreshing = True
            self._owner_started_at = requested_at
            self._followup_requested = False
            self._refresh_done = threading.Event()
            done = self._refresh_done
            is_owner = True

    if not is_owner:
        done.wait(timeout=90)
        cached = self._read_cache()
        return self._with_authentication(cached or self._empty_snapshot("refresh_unavailable"))

    try:
        result = self._refresh_once()
        while True:
            with self._state_lock:
                if not self._followup_requested:
                    if not self._finish_owner_locked(done):
                        cached = self._read_cache()
                        return self._with_authentication(
                            cached or self._empty_snapshot("refresh_unavailable")
                        )
                    return self._with_authentication(result)
                self._followup_requested = False
                self._owner_started_at = self.monotonic()
            result = self._refresh_once()
    except BaseException:
        with self._state_lock:
            self._finish_owner_locked(done)
        raise
```

`_refresh_once()` returns a sanitized snapshot without adding authentication; `refresh()` adds authentication exactly once to its returned copy. `_empty_snapshot()` emits schema 2 and unit-correct unavailable credits. Keep all provider reads serialized under one logical owner, but do not hold `_state_lock` during I/O. The normal return path performs its only teardown while still holding `_state_lock`; the exception path clears state only when `self._refresh_done is done`, so a departing invocation can never deregister a newer owner.

- [ ] **Step 4: Mark only the existing POST route as manual**

Change `dashboard/plugin_api.py`:

```python
@router.post("/refresh")
async def refresh() -> dict:
    return await run_in_threadpool(service.refresh, manual=True)
```

Update `tests/test_api.py` so the mocked service records `refresh(manual=True)`, while `GET /usage` still calls `get()` and remains cache-aware.

- [ ] **Step 5: Run Task 3 tests and commit**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_service tests.test_api -v
```

Expected: one read for concurrent automatic calls, exactly two reads for any number of newer manual callers during one owner, and all waiters released on every path.

Commit:

```bash
git add model_usage_status/service.py dashboard/plugin_api.py tests/test_service.py tests/test_api.py
git commit -m "fix: revalidate newer manual usage refreshes"
```

---

### Task 4: Render Exact Credit States in the Existing Menus

**Files:**
- Modify: `desktop/plugin.js:12-319`
- Modify: `tests/test_desktop_contract.py:10-93`
- Create: `tests/test_desktop_logic.py`
- Create: `tests/support/run_desktop_helpers.mjs`
- Create: `tests/fixtures/credit_usage_states.json`
- Create: `tests/support/write_usage_fixture.py`

**Interfaces:**
- Consumes: schema-v2 provider snapshots with nullable `credits` from Tasks 1–3.
- Produces: named pure exports `formatCurrencyMinor`, `creditPresentation`, `selectCompactAllowance`, and `compactLabel`; the default plugin registration remains unchanged.

- [ ] **Step 1: Add failing structural contract tests**

Update `tests/test_desktop_contract.py` with exact invariants:

```python
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
```

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_desktop_contract -v
```

Expected: FAIL on version 3 and missing Credits section.

- [ ] **Step 2: Create executable pure-helper tests**

Create `tests/support/run_desktop_helpers.mjs` with this complete loader. It evaluates no host registration or network action:

```javascript
import fs from 'node:fs'
import vm from 'node:vm'

const pluginPath = process.argv[2]
const context = vm.createContext({
  console, Date, Intl, JSON, Math, Number, Promise,
  setTimeout, clearTimeout
})
const source = fs.readFileSync(pluginPath, 'utf8')
const plugin = new vm.SourceTextModule(source, { context, identifier: pluginPath })
const values = {
  '@hermes/plugin-sdk': {
    cn: (...parts) => parts.filter(Boolean).join(' '),
    Codicon: () => null,
    DropdownMenuItem: () => null,
    queryClient: { invalidateQueries() {}, setQueryData() {} },
    STATUSBAR_AREAS: { right: 'right' },
    useQuery: () => ({ data: null, isLoading: false })
  },
  react: { useState: value => [value, () => {}] },
  'react/jsx-runtime': {
    jsx: (type, props, key) => ({ type, props, key }),
    jsxs: (type, props, key) => ({ type, props, key })
  }
}

await plugin.link(async specifier => {
  const exports = values[specifier]
  if (!exports) throw new Error(`Unexpected import: ${specifier}`)
  let dependency
  dependency = new vm.SyntheticModule(Object.keys(exports), () => {
    for (const [name, value] of Object.entries(exports)) dependency.setExport(name, value)
  }, { context, identifier: specifier })
  return dependency
})
await plugin.evaluate()

let serialized = ''
for await (const chunk of process.stdin) serialized += chunk
const request = JSON.parse(serialized)
const helper = plugin.namespace[request.function]
if (typeof helper !== 'function') throw new Error(`Unknown helper: ${request.function}`)
process.stdout.write(JSON.stringify(helper(...request.args)))
```

Create `tests/test_desktop_logic.py` with this runner:

```python
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "support" / "run_desktop_helpers.mjs"
PLUGIN = ROOT / "desktop" / "plugin.js"


def call_helper(name: str, *args):
    result = subprocess.run(
        ["node", "--experimental-vm-modules", str(RUNNER), str(PLUGIN)],
        input=json.dumps({"function": name, "args": args}),
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


class DesktopCreditLogicTests(unittest.TestCase):
    def test_currency_scale_is_provider_reported_and_exact(self) -> None:
        self.assertEqual(call_helper("formatCurrencyMinor", 1840, "USD", 2, "en-US"), "$18.40")
        self.assertEqual(call_helper("formatCurrencyMinor", 10000, "USD", 2, "en-US"), "$100")
        self.assertEqual(call_helper("formatCurrencyMinor", 18400, "USD", 3, "en-US"), "$18.400")
        self.assertNotEqual(call_helper("formatCurrencyMinor", 1840, "JPY", 2, "en-US"), "¥1,840")

    def test_controlling_allowance_selection(self) -> None:
        windows = [
            {"label": "5h", "duration_minutes": 300, "remaining_percent": 80},
            {"label": "Week", "duration_minutes": 10080, "remaining_percent": 0},
        ]
        self.assertEqual(call_helper("selectCompactAllowance", windows)["label"], "Week")
        tie = [dict(windows[0], remaining_percent=42), dict(windows[1], remaining_percent=42)]
        self.assertEqual(call_helper("selectCompactAllowance", tie)["label"], "Week")

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
```

Add exact `creditPresentation()` assertions for Claude zero/active/low/exhausted/over-cap/off/stale/unavailable and Codex finite/unlimited/zero/hidden/off/stale/unavailable/monthly-low/monthly-reached-with-balance states. Assert `Exhausted` suppresses `Low`, unavailable credits never enter a compact label, and allowance-only output remains byte-for-byte identical when credits are non-consequential.

- [ ] **Step 3: Run Desktop logic tests and confirm RED**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_desktop_logic -v
```

Expected: FAIL because the named pure helper exports do not exist.

- [ ] **Step 4: Implement exact formatters and compact selection**

Set `DATA_CONTRACT_VERSION = 4`. Add pure functions before the React components:

```javascript
function formatCurrencyMinor(amountMinor, currency, scale, locale) {
  if (!Number.isSafeInteger(amountMinor) || amountMinor < 0 ||
      !/^[A-Z]{3}$/.test(currency || '') || !Number.isInteger(scale) || scale < 0 || scale > 6) {
    return null
  }
  const divisor = 10 ** scale
  const fractionDigits = amountMinor % divisor === 0 ? 0 : scale
  return new Intl.NumberFormat(locale ? [locale] : [], {
    style: 'currency', currency,
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits
  }).format(amountMinor / divisor)
}

function selectCompactAllowance(windows) {
  const valid = (Array.isArray(windows) ? windows : []).filter(
    window => Number.isFinite(window?.remaining_percent) &&
      (window.duration_minutes === 300 || window.duration_minutes === 10080)
  )
  const weekly = valid.find(window => window.duration_minutes === 10080)
  const short = valid.find(window => window.duration_minutes === 300)
  if (weekly?.remaining_percent === 0) return weekly
  if (short?.remaining_percent === 0) return short
  if (!short) return weekly || null
  if (!weekly) return short
  return weekly.remaining_percent <= short.remaining_percent ? weekly : short
}

function creditIsConsequential(credits) {
  return credits?.status === 'current' &&
    Boolean(credits.active || credits.low || credits.exhausted)
}
```

Implement `creditPresentation(providerId, credits)` as a total function returning:

```javascript
{
  primary: 'Unavailable',
  supporting: null,
  monthly: null,
  compact: null,
  consequential: false
}
```

for absent/malformed data, and the exact approved strings for every valid state. `creditPresentation(providerId, credits, locale)` and `compactLabel(providerId, provider, locale)` pass the optional locale through to `formatCurrencyMinor`; production omits it to respect the host locale, while exact tests pass `"en-US"`. Compute Claude remaining amount as `Math.max(limit_minor - used_minor, 0)`. Keep Codex decimal strings unchanged. If a Codex balance is displayed while spend controls `low` or `exhausted`, qualify with `Monthly limit low` or `Monthly limit reached`. Use text qualifiers even when styling also changes.

Task 1 preserves the current structural contract: `provider.windows` contains account windows labeled `5h`/`Week`, and `provider.model_limits[*].windows` contains model-specific windows. `selectCompactAllowance()` therefore receives account-window collection membership as its scope and uses duration only; model windows are never flattened into it. Rewrite `compactLabel()` so non-consequential credits use the existing allowance-only branch byte-for-byte, including its one existing trailing provider-level `stale` marker. Consequential credits call `selectCompactAllowance(provider.windows)` and join at most the selected allowance and credit summary; when provider and credits are stale, one trailing ` · stale` qualifies the combined snapshot rather than duplicating the marker per fact. The test provider's 1%-remaining `model_limits` entry above must not change the expected `Claude 5h 42% · Credits $18.40/$100` label. Export the four pure helpers:

```javascript
export { compactLabel, creditPresentation, formatCurrencyMinor, selectCompactAllowance }
```

- [ ] **Step 5: Add the text-only Credits section**

Add `CreditsSection({ providerId, credits })` using existing classes only: section border/top padding, `font-medium text-(--ui-text-secondary)` heading, primary `text-foreground`, supporting/reset copy in existing tertiary text, and no progress bar. Insert it after allowance/model-limit rows and before provider source.

The section must always render. Use `age(credits.observed_at)` only when freshness is stale; do not fall back to `generatedAt` for credit age. Preserve `DropdownMenuItem`, `event.preventDefault()`, focus order, refresh disabling, reauthentication behavior, and the existing `w-[20rem]` menu width.

- [ ] **Step 6: Add deterministic fixture support**

Create `tests/fixtures/credit_usage_states.json` with complete schema-v2 snapshots at fixture epoch `1000`. Use this exact state matrix; every unspecified normalized field uses the contract default from Task 1, both providers include current account windows, and every provider includes an authentication object:

| Fixture | Claude credit state | Codex credit state | Expected compact behavior |
|---|---|---|---|
| `allowance_only` | current, `$0/$100`, inactive | current, `9.5` balance, inactive | existing allowance-only labels |
| `active` | current, `$18.40/$100`, active, plus a 1%-remaining Opus model window in `model_limits` | current, `9.5` balance, active | lowest account allowance plus credits; model window ignored |
| `low` | current, `$85/$100`, low | balance `9.5`, monthly `8/10`, low | textual `Low` / `Monthly limit low` |
| `exhausted` | current, `$100/$100`, exhausted | balance `9.5`, monthly `10/10`, spend control reached | controlling exhausted allowance plus `Exhausted` / `Monthly limit reached` |
| `off` | current observation with `status: off` | current observation with `status: off` | Credits rows say `Off`; labels remain allowance-only |
| `unlimited` | current zero spend, inactive | current unlimited balance, inactive | Codex row says `Unlimited`; labels remain allowance-only |
| `hidden` | current zero spend, inactive | current available balance with `amount_credits: null` | Codex row says `Available` with hidden-balance support copy |
| `unavailable` | currency-unit unavailable, `provider_unavailable` | credit-unit unavailable, `unsupported` | both rows say `Unavailable`; no credit label fact |
| `stale_auth` | active current-state value retained with `freshness: stale`, `observed_at: 400` | credit-unit unavailable `auth_rejected`, provider top-level `authentication_required` | Claude credit summary says `stale`; Codex shows Reauthenticate |

Create `tests/support/write_usage_fixture.py` with this complete implementation. It has no default cache path:

```python
from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from model_usage_status.storage import atomic_write_json  # noqa: E402

TIME_KEYS = {"generated_at", "observed_at", "resets_at"}


def shifted(value, delta: int):
    if isinstance(value, list):
        return [shifted(item, delta) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in TIME_KEYS and isinstance(item, int):
            result[key] = item + delta
        else:
            result[key] = shifted(item, delta)
    return result


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: write_usage_fixture.py STATE CACHE_PATH")
    state, cache_path = sys.argv[1], Path(sys.argv[2]).expanduser()
    fixtures = json.loads((ROOT / "tests/fixtures/credit_usage_states.json").read_text())
    if state not in fixtures:
        raise SystemExit(f"unknown fixture: {state}")
    snapshot = deepcopy(fixtures[state])
    delta = int(time.time()) - 1000
    atomic_write_json(cache_path, shifted(snapshot, delta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add a `DesktopFixtureTests` class that loads every matrix key, asserts `schema_version == 2`, recursively rejects the exact forbidden keys `Authorization`, `extra_usage`, `accessToken`, and `refreshToken`, and passes each provider object to `compactLabel` plus each credit block to `creditPresentation` through the Node runner. Assert the result is a string/object rather than relying on source-text matching.

- [ ] **Step 7: Run Task 4 tests and commit**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_desktop_contract tests.test_desktop_logic -v
node --check desktop/plugin.js
```

Expected: all contract/logic tests pass; Node reports no syntax error.

Commit:

```bash
git add desktop/plugin.js tests/test_desktop_contract.py tests/test_desktop_logic.py \
  tests/support/run_desktop_helpers.mjs tests/support/write_usage_fixture.py \
  tests/fixtures/credit_usage_states.json
git commit -m "feat: show credit usage in provider status items"
```

---

### Task 5: Document, Validate, and Verify the Installed Experience

**Files:**
- Modify: `README.md:1-122`
- Create: `docs/verification/2026-09-19-credit-usage/README.md`
- Create: `docs/verification/2026-09-19-credit-usage/*.png`

**Interfaces:**
- Consumes: Complete implementation and deterministic fixtures from Tasks 1–4.
- Produces: User-facing behavior/privacy/troubleshooting documentation and durable rendered evidence with hashes.

- [ ] **Step 1: Update README behavior and privacy documentation**

Change the opening provider list to include Claude monthly extra-usage spend/cap and Codex native credits. Document:

- Credits always appear in each popover but enter the compact label only when active, low, exhausted, or bounded-stale after activity.
- Claude uses provider-reported currency/minor scale; Codex uses `credits` and never receives an invented dollar sign.
- Current, Off, Unlimited, Low, Exhausted, stale, and Unavailable meanings.
- Credit observations become unavailable after 15 minutes of transient failure while allowance fallback can remain usable.
- The backend makes one Claude OAuth usage request with Hermes's credential resolver; token and raw response are discarded before return, and only normalized schema-v2 fields are cached.
- Troubleshooting for allowance-present/credit-unavailable, including missing Claude `currency`/`decimal_places` and Codex versions without the credit fields.

Keep installation, update, uninstall, and authentication commands unchanged. Replace the development test command with the Hermes venv command used by this repository environment.

- [ ] **Step 2: Run the full automated gate**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests -v
node --check desktop/plugin.js
hermes plugins validate . --json
./install.sh --dry-run
```

Expected: all tests pass; JavaScript syntax exits 0; validator returns `"ok": true` with no warnings; dry run exits 0 without modifying the installed plugin or Claude settings.

- [ ] **Step 3: Inspect the live provider contracts without recording account values**

Run the installed-version probes through the implementation's normal fetch path. Record only field presence and type, never values:

```text
Claude: extra_usage object present; is_enabled boolean present; used_credits integral number present; monthly_limit integral number present; currency string present; decimal_places integer present
Codex: credits object present/absent; individualLimit object present/absent; units remain provider-native credits
```

If an enabled Claude response lacks `used_credits`, `monthly_limit`, `currency`, or `decimal_places`, if either amount is not a non-negative integral provider value, or installed Codex reports a currency contract instead of native credits, stop and return to design review. Do not infer or repair either contract.

- [ ] **Step 4: Install locally and verify live state**

Run `./install.sh`, restart the Hermes backend, and reload Desktop plugins through `⌘K` → **Reload desktop plugins**. Verify both existing status items remain independently visible in the status-bar visibility menu, each opens the host menu, Refresh and conditional Reauthenticate remain keyboard reachable, and the current live account state matches the sanitized `/usage` response. Check the Desktop console for new exceptions or failed requests.

- [ ] **Step 5: Verify deterministic states at normal and constrained widths**

Back up `/Users/woohopark/.hermes/profiles/app-design/plugin-data/model-usage-status/usage-cache.json` to a timestamped file outside the repository. For each fixture state, run `/Users/woohopark/.hermes/hermes-agent/venv/bin/python tests/support/write_usage_fixture.py STATE /Users/woohopark/.hermes/profiles/app-design/plugin-data/model-usage-status/usage-cache.json`, reload Desktop plugins, and capture both provider menus at normal width and the compact labels at constrained width. Exercise browser/app zoom, keyboard focus order, refresh hit testing, overflow, truncation, text-row hierarchy, menu spacing, and non-color-only Low/Exhausted/stale copy.

Capture at least:

1. `allowance-only-normal.png`
2. `active-constrained.png`
3. `low-normal.png`
4. `exhausted-constrained.png`
5. `off-normal.png`
6. `unlimited-normal.png`
7. `hidden-balance-normal.png`
8. `unavailable-normal.png`
9. `stale-auth-focus.png`

Restore the backed-up cache in a `finally` path, reload plugins, and verify the live state returns. Do not retain private live values in screenshots; crop or mask them before placing an artifact in the repository.

- [ ] **Step 6: Create the durable verification record**

Write `docs/verification/2026-09-19-credit-usage/README.md` with a table containing fixture name, viewport/status-bar condition, exercised state, keyboard/zoom result, console result, screenshot filename, and SHA-256. Compute hashes with:

```bash
shasum -a 256 docs/verification/2026-09-19-credit-usage/*.png
```

State separately which observations came from live provider data and which came from deterministic fixtures. Do not claim a product outcome for states not directly observed.

- [ ] **Step 7: Run final regression and inspect repository scope**

Run:

```bash
/Users/woohopark/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests -v
node --check desktop/plugin.js
hermes plugins validate . --json
git diff --check
git status --short
git diff --stat HEAD~4..HEAD
```

Expected: all gates pass; no production file outside the implementation boundary changed; `IDEA.md` remains the pre-existing untracked file; no credential/cache/runtime file appears in Git status.

- [ ] **Step 8: Commit documentation and durable evidence**

```bash
git add README.md docs/verification/2026-09-19-credit-usage
git commit -m "docs: verify provider credit usage experience"
```

Do not push, open a PR, release, deploy, or bump the plugin version.
