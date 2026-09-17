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
    build_codex_messages,
    merge_refresh_result,
    normalize_claude_payload,
    normalize_codex_result,
)
from .storage import atomic_write_json, read_json

CACHE_TTL_SECONDS = 300
CLAUDE_STALE_AFTER_SECONDS = 900
CODEX_TIMEOUT_SECONDS = 20
AUTH_TIMEOUT_SECONDS = 10

_AUTH_PROVIDERS = {
    "claude": {"status": ("auth", "status", "--text"), "login": ("auth", "login")},
    "codex": {"status": ("login", "status"), "login": ("login",)},
}


class AuthenticationNotRequired(RuntimeError):
    pass


class AuthenticationUnavailable(RuntimeError):
    pass


class ClaudeAuthenticationRequired(RuntimeError):
    pass


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


def fetch_claude_rate_limits() -> dict[str, Any]:
    """Read Claude's OAuth usage through Hermes' credential-safe provider adapter."""
    from agent.account_usage import _fetch_anthropic_account_usage
    from agent.anthropic_credentials import (
        is_claude_code_token_valid,
        read_claude_code_credentials,
    )

    try:
        snapshot = _fetch_anthropic_account_usage()
    except Exception as error:
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code in (401, 403):
            raise ClaudeAuthenticationRequired("claude_authentication_required") from error
        raise
    if snapshot is None:
        credentials = read_claude_code_credentials()
        if credentials and not is_claude_code_token_valid(credentials):
            raise ClaudeAuthenticationRequired("claude_authentication_required")
        raise RuntimeError("claude_usage_unavailable")
    if snapshot.unavailable_reason:
        raise RuntimeError("claude_usage_unavailable")

    window_ids = {
        "Current session": "five_hour",
        "Current week": "seven_day",
    }
    rate_limits: dict[str, dict[str, float | int | None]] = {}
    for window in snapshot.windows:
        window_id = window_ids.get(window.label)
        if window_id is None or window.used_percent is None:
            continue
        rate_limits[window_id] = {
            "used_percentage": window.used_percent,
            "resets_at": int(window.reset_at.timestamp()) if window.reset_at else None,
        }
    if not rate_limits:
        raise RuntimeError("claude_usage_unavailable")
    return {"observation_source": "usage", "rate_limits": rate_limits}


class UsageService:
    def __init__(
        self,
        data_dir: Path,
        codex_fetcher: Callable[[], dict[str, Any]] = fetch_codex_rate_limits,
        claude_fetcher: Callable[[], dict[str, Any]] = fetch_claude_rate_limits,
        now: Callable[[], int] = lambda: int(time.time()),
        auth_checker: Callable[[str], dict[str, Any]] = authentication_status,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.codex_fetcher = codex_fetcher
        self.claude_fetcher = claude_fetcher
        self.now = now
        self.auth_checker = auth_checker
        self.cache_path = self.data_dir / "usage-cache.json"
        self.claude_path = self.data_dir / "claude-observation.json"
        self._state_lock = threading.Lock()
        self._refreshing = False
        self._refresh_done = threading.Event()
        self._refresh_done.set()

    def _unavailable(self, provider_id: str, code: str) -> dict[str, Any]:
        return {
            "id": provider_id,
            "status": "unavailable",
            "observed_at": None,
            "source": None,
            "windows": [],
            "model_limits": [],
            "error_code": code,
        }

    def _read_cache(self) -> dict[str, Any] | None:
        return read_json(self.cache_path)

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
        if not isinstance(cached, dict) or not isinstance(generated_at, int) or now - generated_at >= CACHE_TTL_SECONDS:
            return self.refresh()
        return self._with_authentication(cached)

    def refresh(self) -> dict[str, Any]:
        with self._state_lock:
            if self._refreshing:
                done = self._refresh_done
                is_owner = False
            else:
                self._refreshing = True
                self._refresh_done = threading.Event()
                done = self._refresh_done
                is_owner = True

        if not is_owner:
            done.wait(timeout=CODEX_TIMEOUT_SECONDS + 5)
            cached = self._read_cache()
            if cached is not None:
                return self._with_authentication(cached)
            return self._with_authentication({
                "schema_version": 1,
                "generated_at": self.now(),
                "providers": {
                    "codex": self._unavailable("codex", "refresh_unavailable"),
                    "claude": self._unavailable("claude", "refresh_unavailable"),
                },
            })

        try:
            now = self.now()
            previous = self._read_cache() or {}
            previous_providers = previous.get("providers")
            previous_providers = previous_providers if isinstance(previous_providers, dict) else {}

            try:
                codex = normalize_codex_result(self.codex_fetcher(), observed_at=now)
                codex = merge_refresh_result(previous_providers.get("codex"), codex, now)
            except Exception:
                codex = merge_refresh_result(
                    previous_providers.get("codex"), None, now, error_code="provider_unavailable"
                )
                if codex.get("id") == "unknown":
                    codex["id"] = "codex"

            try:
                claude = normalize_claude_payload(self.claude_fetcher(), observed_at=now)
                claude = merge_refresh_result(previous_providers.get("claude"), claude, now)
            except Exception as error:
                observation = read_json(self.claude_path)
                if observation is not None and isinstance(observation.get("observed_at"), int):
                    claude = normalize_claude_payload(observation, observed_at=observation["observed_at"])
                    claude = merge_refresh_result(previous_providers.get("claude"), claude, now)
                    if claude.get("status") == "current" and now - observation["observed_at"] > CLAUDE_STALE_AFTER_SECONDS:
                        claude["status"] = "stale"
                        claude["error_code"] = "observation_stale"
                else:
                    claude = merge_refresh_result(
                        previous_providers.get("claude"), None, now, error_code="observation_unavailable"
                    )
                    if claude.get("id") == "unknown":
                        claude["id"] = "claude"
                if isinstance(error, ClaudeAuthenticationRequired):
                    claude["error_code"] = "authentication_required"

            snapshot = {
                "schema_version": 1,
                "generated_at": now,
                "providers": {"claude": claude, "codex": codex},
            }
            atomic_write_json(self.cache_path, snapshot)
            return self._with_authentication(snapshot)
        finally:
            with self._state_lock:
                self._refreshing = False
                self._refresh_done.set()
