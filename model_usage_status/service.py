from __future__ import annotations

import json
import os
import selectors
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .core import (
    age_credit_state,
    build_codex_messages,
    merge_refresh_result,
    normalize_claude_oauth_result,
    normalize_claude_payload,
    normalize_codex_result,
    unavailable_credits,
)
from .storage import atomic_write_json, read_json

SCHEMA_VERSION = 2
CACHE_TTL_SECONDS = 300
CLAUDE_STALE_AFTER_SECONDS = 900
CODEX_TIMEOUT_SECONDS = 20
AUTH_TIMEOUT_SECONDS = 10
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_TIMEOUT_SECONDS = 15.0

_AUTH_PROVIDERS = {
    "claude": {"status": ("auth", "status", "--text"), "login": ("auth", "login")},
    "codex": {"status": ("login", "status"), "login": ("login",)},
}
_PROVIDER_CREDIT_UNIT = {"claude": "currency", "codex": "credits"}
_CREDIT_KEYS_REQUIRED = {
    "status", "freshness", "unit", "currency", "minor_unit_scale", "balance",
    "spend", "active", "low", "exhausted", "observed_at", "source", "error_code",
}


class AuthenticationNotRequired(RuntimeError):
    pass


class AuthenticationUnavailable(RuntimeError):
    pass


class ClaudeAuthenticationRequired(RuntimeError):
    pass


class ProviderUsageUnavailable(RuntimeError):
    def __init__(self, credit_error_code: str) -> None:
        self.credit_error_code = credit_error_code
        super().__init__(credit_error_code)


def _is_valid_schema_v2_cache(data: Any) -> bool:
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return False
    generated_at = data.get("generated_at")
    if not isinstance(generated_at, int) or isinstance(generated_at, bool):
        return False
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return False
    for provider_id, unit in _PROVIDER_CREDIT_UNIT.items():
        provider = providers.get(provider_id)
        if not isinstance(provider, dict) or provider.get("id") != provider_id:
            return False
        credits = provider.get("credits")
        if not isinstance(credits, dict) or credits.get("unit") != unit:
            return False
        if not _CREDIT_KEYS_REQUIRED.issubset(credits.keys()):
            return False
    return True


def _credit_error_code(error: Exception) -> str:
    if isinstance(error, ClaudeAuthenticationRequired):
        return "auth_rejected"
    if isinstance(error, ProviderUsageUnavailable):
        return error.credit_error_code
    if str(error) == "codex_timeout":
        return "timeout"
    return "provider_unavailable"


def authentication_status(
    provider: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    config = _AUTH_PROVIDERS.get(provider)
    if config is None:
        raise ValueError("unsupported_provider")
    executable = which(provider)
    if executable is None:
        return {"state": "unavailable", "action_available": False}
    try:
        result = runner(
            [executable, *config["status"]],
            capture_output=True,
            check=False,
            text=True,
            timeout=AUTH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return {"state": "unavailable", "action_available": False}
    if result.returncode == 0:
        return {"state": "authenticated", "action_available": False}
    combined = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}".lower()
    required_markers = ("not logged in", "not authenticated", "login required", "authentication required", "expired")
    if any(marker in combined for marker in required_markers):
        return {"state": "required", "action_available": True}
    return {"state": "unavailable", "action_available": False}


def launch_reauthentication(
    provider: str,
    *,
    data_dir: Path,
    status_checker: Callable[[str], dict[str, Any]] = authentication_status,
    runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    platform: str = sys.platform,
) -> dict[str, str]:
    config = _AUTH_PROVIDERS.get(provider)
    if config is None:
        raise ValueError("unsupported_provider")
    status = status_checker(provider)
    if status.get("state") != "required":
        raise AuthenticationNotRequired("authentication_not_required")
    if platform != "darwin":
        raise AuthenticationUnavailable("authentication_launcher_unavailable")
    executable = which(provider)
    if executable is None:
        raise AuthenticationUnavailable("provider_cli_unavailable")

    auth_dir = Path(data_dir) / "authentication"
    auth_dir.mkdir(parents=True, exist_ok=True)
    command_path = auth_dir / f"reauthenticate-{provider}.command"
    command = " ".join(shlex.quote(part) for part in (executable, *config["login"]))
    command_path.write_text(
        "#!/bin/zsh\n"
        "set -u\n"
        f"printf '\\nStarting {provider.title()} authentication…\\n\\n'\n"
        f"{command}\n"
        "status=$?\n"
        "printf '\\nPress Return to close this window.\\n'\n"
        "read -r _\n"
        "exit \"$status\"\n",
        encoding="utf-8",
    )
    os.chmod(command_path, 0o700)
    try:
        launched = runner(
            ["open", "-a", "Terminal", str(command_path)],
            capture_output=True,
            check=False,
            text=True,
            timeout=AUTH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AuthenticationUnavailable("authentication_launcher_failed") from error
    if launched.returncode != 0:
        raise AuthenticationUnavailable("authentication_launcher_failed")
    return {"provider": provider, "state": "login_started"}


def parse_codex_response_lines(lines: Iterable[str]) -> dict[str, Any]:
    for line in lines:
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("id") != 1:
            continue
        if "error" in payload:
            raise RuntimeError("codex_provider_error")
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        raise RuntimeError("codex_response_invalid")
    raise RuntimeError("codex_response_missing")


def fetch_codex_rate_limits(timeout: int = CODEX_TIMEOUT_SECONDS) -> dict[str, Any]:
    process = subprocess.Popen(
        ["codex", "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("codex_process_unavailable")
        for message in build_codex_messages():
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while time.monotonic() < deadline:
            wait = max(0.0, min(0.5, deadline - time.monotonic()))
            for key, _ in selector.select(timeout=wait):
                line = key.fileobj.readline()
                if not line:
                    raise RuntimeError("codex_process_closed")
                lines.append(line)
                try:
                    return parse_codex_response_lines(lines)
                except RuntimeError as error:
                    if str(error) != "codex_response_missing":
                        raise
        raise RuntimeError("codex_timeout")
    except FileNotFoundError as error:
        raise RuntimeError("codex_not_installed") from error
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


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
    """Read Claude's OAuth usage directly and return a normalized provider state."""
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


class UsageService:
    def __init__(
        self,
        data_dir: Path,
        codex_fetcher: Callable[[], dict[str, Any]] = fetch_codex_rate_limits,
        claude_fetcher: Callable[[int], dict[str, Any]] = fetch_claude_rate_limits,
        now: Callable[[], int] = lambda: int(time.time()),
        monotonic: Callable[[], float] = time.monotonic,
        auth_checker: Callable[[str], dict[str, Any]] = authentication_status,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.codex_fetcher = codex_fetcher
        self.claude_fetcher = claude_fetcher
        self.now = now
        self.monotonic = monotonic
        self.auth_checker = auth_checker
        self.cache_path = self.data_dir / "usage-cache.json"
        self.claude_path = self.data_dir / "claude-observation.json"
        self._state_lock = threading.Lock()
        self._refreshing = False
        self._owner_started_at: float | None = None
        self._followup_requested = False
        self._refresh_done = threading.Event()
        self._refresh_done.set()

    def _unavailable(self, provider_id: str, code: str) -> dict[str, Any]:
        unit = _PROVIDER_CREDIT_UNIT.get(provider_id, "credits")
        return {
            "id": provider_id,
            "status": "unavailable",
            "observed_at": None,
            "source": None,
            "windows": [],
            "model_limits": [],
            "error_code": code,
            "credits": unavailable_credits(unit, "provider_unavailable"),
        }

    def _read_cache(self) -> dict[str, Any] | None:
        data = read_json(self.cache_path)
        return data if _is_valid_schema_v2_cache(data) else None

    def _with_authentication(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        providers = snapshot.get("providers")
        if not isinstance(providers, dict):
            return snapshot
        for provider_id in _AUTH_PROVIDERS:
            provider = providers.get(provider_id)
            if not isinstance(provider, dict):
                continue
            try:
                provider["authentication"] = self._authentication_status(provider_id, provider)
            except Exception:
                provider["authentication"] = {"state": "unavailable", "action_available": False}
        return snapshot

    def _authentication_status(
        self,
        provider_id: str,
        provider: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if provider_id == "claude":
            if provider is None:
                cached = self._read_cache() or {}
                providers = cached.get("providers")
                providers = providers if isinstance(providers, dict) else {}
                candidate = providers.get(provider_id)
                provider = candidate if isinstance(candidate, dict) else None
            if provider is not None and provider.get("error_code") == "authentication_required":
                return {"state": "required", "action_available": True}
        return self.auth_checker(provider_id)

    def launch_reauthentication(self, provider: str) -> dict[str, str]:
        return launch_reauthentication(
            provider,
            data_dir=self.data_dir,
            status_checker=self._authentication_status,
        )

    def get(self) -> dict[str, Any]:
        cached = self._read_cache()
        now = self.now()
        generated_at = cached.get("generated_at") if isinstance(cached, dict) else None
        age = now - generated_at if isinstance(generated_at, int) else None
        if not isinstance(cached, dict) or age is None or age < 0 or age >= CACHE_TTL_SECONDS:
            return self.refresh()
        return self._with_authentication(self._aged_snapshot(cached, now))

    def _aged_snapshot(self, cached: dict[str, Any], now: int) -> dict[str, Any]:
        providers = cached.get("providers")
        if not isinstance(providers, dict):
            return cached

        aged_providers: dict[str, Any] = {}
        changed = False
        for provider_id, provider in providers.items():
            if not isinstance(provider, dict) or not isinstance(provider.get("credits"), dict):
                aged_providers[provider_id] = provider
                continue
            aged_credits = age_credit_state(provider["credits"], now)
            if aged_credits != provider["credits"]:
                changed = True
                provider = dict(provider)
                provider["credits"] = aged_credits
            aged_providers[provider_id] = provider
        if not changed:
            return cached

        aged_snapshot = dict(cached)
        aged_snapshot["providers"] = aged_providers

        with self._state_lock:
            if self._refreshing:
                return aged_snapshot
            current_cached = self._read_cache()
            if not isinstance(current_cached, dict) or current_cached.get("generated_at") != cached.get("generated_at"):
                return aged_snapshot
            persisted = dict(current_cached)
            persisted["providers"] = aged_providers
            atomic_write_json(self.cache_path, persisted)
            return persisted

    def _empty_snapshot(self, code: str) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.now(),
            "providers": {
                "codex": self._unavailable("codex", code),
                "claude": self._unavailable("claude", code),
            },
        }

    def _read_cache_or_empty_aged(self) -> dict[str, Any]:
        now = self.now()
        cached = self._read_cache()
        if cached is not None:
            return self._aged_snapshot(cached, now)
        return self._empty_snapshot("refresh_unavailable")

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
            done.wait(timeout=CODEX_TIMEOUT_SECONDS + CLAUDE_TIMEOUT_SECONDS + 5)
            return self._with_authentication(self._read_cache_or_empty_aged())

        try:
            result = self._refresh_once()
            while True:
                with self._state_lock:
                    if not self._followup_requested:
                        if self._finish_owner_locked(done):
                            return self._with_authentication(result)
                        break
                    self._followup_requested = False
                    self._owner_started_at = self.monotonic()
                result = self._refresh_once()
        except BaseException:
            with self._state_lock:
                self._finish_owner_locked(done)
            raise

        return self._with_authentication(self._read_cache_or_empty_aged())

    def _refresh_once(self) -> dict[str, Any]:
        now = self.now()
        previous = self._read_cache() or {}
        previous_providers = previous.get("providers")
        previous_providers = previous_providers if isinstance(previous_providers, dict) else {}

        try:
            codex_fresh = normalize_codex_result(self.codex_fetcher(), observed_at=now)
            codex = merge_refresh_result(previous_providers.get("codex"), codex_fresh, now, credit_unit="credits")
        except Exception as error:
            codex = merge_refresh_result(
                previous_providers.get("codex"), None, now,
                error_code="provider_unavailable", credit_unit="credits",
                credit_error_code=_credit_error_code(error),
            )
            codex["id"] = "codex"

        try:
            claude_fresh = self.claude_fetcher(now)
            claude = merge_refresh_result(previous_providers.get("claude"), claude_fresh, now, credit_unit="currency")
        except Exception as error:
            observation = read_json(self.claude_path)
            if observation is not None and isinstance(observation.get("observed_at"), int):
                passive = normalize_claude_payload(observation, observed_at=observation["observed_at"])
                # A passive status-line observation carries allowance only, so omitting
                # credits here lets merge_refresh_result preserve/age the prior live
                # credit observation instead of collapsing it to "unsupported".
                passive.pop("credits", None)
                claude = merge_refresh_result(
                    previous_providers.get("claude"), passive, now,
                    credit_unit="currency", credit_error_code=_credit_error_code(error),
                )
                if claude.get("status") == "current" and now - observation["observed_at"] > CLAUDE_STALE_AFTER_SECONDS:
                    claude["status"] = "stale"
                    claude["error_code"] = "observation_stale"
            else:
                claude = merge_refresh_result(
                    previous_providers.get("claude"), None, now,
                    error_code="observation_unavailable", credit_unit="currency",
                    credit_error_code=_credit_error_code(error),
                )
            claude["id"] = "claude"
            if isinstance(error, ClaudeAuthenticationRequired):
                claude["error_code"] = "authentication_required"

        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now,
            "providers": {"claude": claude, "codex": codex},
        }
        atomic_write_json(self.cache_path, snapshot)
        return snapshot
