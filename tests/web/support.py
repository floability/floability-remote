"""Shared helpers for web tests. FastAPI's TestClient requires httpx."""

import importlib.util
import unittest

HAS_WEB_TEST_DEPENDENCIES = all(
    importlib.util.find_spec(name) for name in ("fastapi", "httpx")
)
requires_web = unittest.skipUnless(
    HAS_WEB_TEST_DEPENDENCIES, "install the 'test' extra to run web tests"
)

PORT = 48123
TOKEN = "test-session-token"
ORIGIN = f"http://127.0.0.1:{PORT}"


def make_client(signed_in=True, services=None):
    from fastapi.testclient import TestClient

    from floability_remote.web.app import WebSettings, create_app

    client = TestClient(
        create_app(WebSettings(port=PORT, session_token=TOKEN), services),
        base_url=ORIGIN,
    )
    if signed_in:
        response = client.get(f"/?token={TOKEN}", follow_redirects=False)
        assert response.status_code == 303, response.status_code
    return client


def run_request(**overrides):
    body = {
        "mode": "execute",
        "connection": {"target": "user@login.example.org"},
        "backpack": {"repository": "https://github.com/example/backpack.git"},
        "batch_type": "slurm",
    }
    body.update(overrides)
    return body
