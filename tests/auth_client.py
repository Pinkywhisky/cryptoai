"""Real login/CSRF setup for existing API tests; production guards remain enabled."""

import re
from urllib.parse import urlsplit

from fastapi.testclient import TestClient as RawTestClient

TEST_PASSWORD = "test-only-cryptoai-password-2026"


class AuthenticatedClient(RawTestClient):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("base_url", "http://localhost:8000")
        super().__init__(*args, **kwargs)
        self.authenticate()

    def authenticate(self):
        page = super().get("/login", follow_redirects=False)
        assert page.status_code == 200
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        origin = f"{urlsplit(str(self.base_url)).scheme}://{urlsplit(str(self.base_url)).netloc}"
        response = super().post("/login", data={"password": TEST_PASSWORD, "csrf_token": token},
                                headers={"Origin": origin}, follow_redirects=False)
        assert response.status_code == 303
        status = super().get("/api/auth/session")
        assert status.status_code == 200
        page = super().get("/")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        self.headers.update({"X-CSRF-Token": token, "Origin": origin})
