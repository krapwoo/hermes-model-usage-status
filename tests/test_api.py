from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN_API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"


def load_plugin_api():
    spec = importlib.util.spec_from_file_location("model_usage_status_plugin_api_test", PLUGIN_API)
    if spec is None or spec.loader is None:
        raise RuntimeError("plugin API could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeService:
    def __init__(self) -> None:
        self.get_calls = 0
        self.refresh_calls = 0
        self.auth_calls: list[str] = []
        self.auth_error = None

    def get(self) -> dict:
        self.get_calls += 1
        return {"mode": "cached"}

    def refresh(self) -> dict:
        self.refresh_calls += 1
        return {"mode": "refreshed"}

    def launch_reauthentication(self, provider: str) -> dict:
        self.auth_calls.append(provider)
        if self.auth_error is not None:
            raise self.auth_error
        return {"provider": provider, "state": "login_started"}


class PluginApiTests(unittest.TestCase):
    def test_get_is_cache_aware_and_post_forces_refresh(self) -> None:
        module = load_plugin_api()
        fake = FakeService()
        module.service = fake
        app = FastAPI()
        app.include_router(module.router, prefix="/api/plugins/model-usage-status")
        client = TestClient(app)

        cached = client.get("/api/plugins/model-usage-status/usage")
        refreshed = client.post("/api/plugins/model-usage-status/refresh")

        self.assertEqual(cached.status_code, 200)
        self.assertEqual(cached.json(), {"mode": "cached"})
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.json(), {"mode": "refreshed"})
        self.assertEqual(fake.get_calls, 1)
        self.assertEqual(fake.refresh_calls, 1)

    def test_reauthentication_starts_only_for_supported_provider(self) -> None:
        module = load_plugin_api()
        fake = FakeService()
        module.service = fake
        app = FastAPI()
        app.include_router(module.router, prefix="/api/plugins/model-usage-status")
        client = TestClient(app)

        started = client.post("/api/plugins/model-usage-status/authentication/claude")
        invalid = client.post("/api/plugins/model-usage-status/authentication/other")

        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json(), {"provider": "claude", "state": "login_started"})
        self.assertEqual(fake.auth_calls, ["claude"])
        self.assertEqual(invalid.status_code, 422)

    def test_reauthentication_returns_conflict_when_auth_is_healthy(self) -> None:
        module = load_plugin_api()
        fake = FakeService()
        fake.auth_error = module.AuthenticationNotRequired("authentication_not_required")
        module.service = fake
        app = FastAPI()
        app.include_router(module.router, prefix="/api/plugins/model-usage-status")
        client = TestClient(app)

        response = client.post("/api/plugins/model-usage-status/authentication/codex")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": "authentication_not_required"})


if __name__ == "__main__":
    unittest.main()
