import unittest

from .support import ORIGIN, PORT, TOKEN, make_client, requires_web, run_request


@requires_web
class SessionTests(unittest.TestCase):
    def test_token_link_sets_cookie_and_drops_token_from_url(self):
        client = make_client(signed_in=False)
        response = client.get(f"/?token={TOKEN}", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        cookie = response.headers["set-cookie"]
        self.assertIn(f"floability_remote_session_{PORT}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)

    def test_wrong_token_serves_page_without_session(self):
        client = make_client(signed_in=False)
        response = client.get("/?token=wrong", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(client.get("/api/v1/meta").status_code, 401)

    def test_api_requires_session(self):
        client = make_client(signed_in=False)
        response = client.get("/api/v1/meta")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthorized")
        response = client.post("/api/v1/runs/validate", json=run_request())
        self.assertEqual(response.status_code, 401)

    def test_health_is_public(self):
        client = make_client(signed_in=False)
        self.assertEqual(client.get("/api/v1/health").status_code, 200)

    def test_bearer_token_is_accepted(self):
        client = make_client(signed_in=False)
        response = client.get(
            "/api/v1/meta", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        self.assertEqual(response.status_code, 200)


@requires_web
class RequestOriginTests(unittest.TestCase):
    def setUp(self):
        self.client = make_client()

    def test_foreign_host_header_is_rejected(self):
        for host in ("evil.example", f"evil.example:{PORT}", "127.0.0.1:1"):
            with self.subTest(host=host):
                response = self.client.get("/api/v1/health", headers={"Host": host})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_host")

    def test_localhost_host_is_accepted(self):
        response = self.client.get(
            "/api/v1/health", headers={"Host": f"localhost:{PORT}"}
        )
        self.assertEqual(response.status_code, 200)

    def test_cross_origin_request_is_rejected(self):
        response = self.client.post(
            "/api/v1/runs/validate",
            json=run_request(),
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "cross_origin")

    def test_other_local_port_is_cross_origin(self):
        response = self.client.get(
            "/api/v1/meta", headers={"Origin": "http://127.0.0.1:3000"}
        )
        self.assertEqual(response.status_code, 403)

    def test_cross_site_fetch_without_origin_is_rejected(self):
        response = self.client.get(
            "/api/v1/meta", headers={"Sec-Fetch-Site": "same-site"}
        )
        self.assertEqual(response.status_code, 403)

    def test_same_origin_request_is_accepted(self):
        response = self.client.post(
            "/api/v1/runs/validate",
            json=run_request(),
            headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(response.status_code, 200)

    def test_security_headers(self):
        response = self.client.get("/api/v1/meta")
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
