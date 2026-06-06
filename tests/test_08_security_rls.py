"""
Domain 08 — Security & RLS.

Covers response safety (no traceback / no secret leakage), SQL-injection
resistance on the public login endpoint, JWT hardening (alg:none, tampering,
wrong secret, malformed/expired tokens), RBAC + row-level visibility for the
user-management endpoints, CORS configuration, and the security-relevant
Terraform infrastructure (forced SSL on RDS, secrets generation, IMDSv2, and
RDS not being publicly accessible).

HTTP tests use the in-process `client` fixture. Tests that need seeded users
depend on `auth_headers` / `superadmin_headers` + `require_db` so they SKIP
(not fail) when the live stack is absent. Infra tests read terraform via the
`terraform_text()` helper.
"""
import time

import pytest

from conftest import terraform_text


_TRACEBACK_MARKER = "Traceback (most recent call last)"


class TestResponseSafety:
    """Error and identity responses must never leak tracebacks or secrets."""

    async def test_404_body_has_no_traceback(self, client):
        resp = await client.get("/api/this-route-does-not-exist-12345")
        assert resp.status_code == 404, f"expected 404, got {resp.status_code}"
        assert _TRACEBACK_MARKER not in resp.text, "404 body leaked a Python traceback"

    async def test_422_body_has_no_traceback(self, client):
        # Missing required login fields -> 422 validation error.
        resp = await client.post("/api/auth/login", json={})
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}"
        assert _TRACEBACK_MARKER not in resp.text, "422 body leaked a Python traceback"

    async def test_login_response_never_exposes_password(self, client, require_db, auth_headers):
        resp = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert resp.status_code == 200, f"admin login failed: {resp.status_code}"
        body = resp.text
        assert "hashed_password" not in body, "login response exposed hashed_password"
        assert "$2b$" not in body, "login response exposed a bcrypt hash"

    async def test_user_list_never_exposes_hashed_password(self, client, require_db, auth_headers):
        resp = await client.get("/api/auth/users", headers=auth_headers)
        assert resp.status_code == 200, f"user list failed: {resp.status_code}"
        assert "hashed_password" not in resp.text, "user list exposed hashed_password"

    async def test_me_never_exposes_hashed_password(self, client, require_db, auth_headers):
        resp = await client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200, f"/me failed: {resp.status_code}"
        assert "hashed_password" not in resp.text, "/me exposed hashed_password"


class TestSQLInjection:
    """The public login endpoint must treat injection payloads as plain data.

    `/api/auth/login` is public but issues a parameterised DB lookup, so these
    depend on `require_db` to SKIP (rather than hang/fail) when PostgreSQL is
    unreachable. They assert the status is in the allowed set and never 500.
    """

    async def test_classic_or_true_payload(self, client, require_db):
        resp = await client.post(
            "/api/auth/login", json={"username": "' OR '1'='1", "password": "x"}
        )
        assert resp.status_code in (401, 422), f"unexpected status {resp.status_code}"
        assert resp.status_code != 500, "injection payload caused a 500"

    async def test_union_select_payload(self, client, require_db):
        resp = await client.post(
            "/api/auth/login",
            json={"username": "x' UNION SELECT username, hashed_password FROM users--", "password": "x"},
        )
        assert resp.status_code in (401, 422), f"unexpected status {resp.status_code}"
        assert resp.status_code != 500, "UNION SELECT payload caused a 500"

    async def test_null_byte_payload(self, client, require_db):
        resp = await client.post(
            "/api/auth/login", json={"username": "admin\x00", "password": "x"}
        )
        assert resp.status_code != 500, "null-byte payload caused a 500"
        assert resp.status_code in (401, 422), f"unexpected status {resp.status_code}"

    async def test_overlong_username_payload(self, client, require_db):
        resp = await client.post(
            "/api/auth/login", json={"username": "a" * 5000, "password": "x"}
        )
        assert resp.status_code in (401, 422), f"unexpected status {resp.status_code}"
        assert resp.status_code != 500, "5000-char username caused a 500"

    async def test_drop_table_payload(self, client, require_db):
        resp = await client.post(
            "/api/auth/login", json={"username": "'; DROP TABLE users;--", "password": "x"}
        )
        assert resp.status_code != 500, "DROP TABLE payload caused a 500"
        assert resp.status_code in (401, 422), f"unexpected status {resp.status_code}"

    async def test_quote_in_password_payload(self, client, require_db):
        resp = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "' OR '1'='1"}
        )
        assert resp.status_code != 500, "quote-in-password caused a 500"
        # Valid username + bad password -> 401 (never authenticated by injection).
        assert resp.status_code == 401, f"expected 401, got {resp.status_code}"


class TestJWTSecurity:
    """A protected endpoint must reject every form of forged/invalid token."""

    PROTECTED = "/api/auth/me"

    async def test_alg_none_forged_token_rejected(self, client):
        jwt = pytest.importorskip("jwt")
        token = jwt.encode({"sub": "admin", "is_admin": True}, "", algorithm="none")
        resp = await client.get(
            self.PROTECTED, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code in (401, 403), f"alg:none accepted ({resp.status_code})"

    async def test_tampered_signature_rejected(self, client):
        jwt = pytest.importorskip("jwt")
        token = jwt.encode({"sub": "admin"}, "some-signing-secret-aaaaaaaaaaaa", algorithm="HS256")
        # Flip the last char of the signature segment.
        head, payload, sig = token.split(".")
        bad_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")
        tampered = f"{head}.{payload}.{bad_sig}"
        resp = await client.get(
            self.PROTECTED, headers={"Authorization": f"Bearer {tampered}"}
        )
        assert resp.status_code in (401, 403), f"tampered token accepted ({resp.status_code})"

    async def test_wrong_secret_token_rejected(self, client):
        jwt = pytest.importorskip("jwt")
        token = jwt.encode(
            {"sub": "admin", "is_admin": True},
            "definitely-not-the-real-server-secret-key-xxxx",
            algorithm="HS256",
        )
        resp = await client.get(
            self.PROTECTED, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code in (401, 403), f"wrong-secret token accepted ({resp.status_code})"

    async def test_missing_bearer_prefix_rejected(self, client):
        resp = await client.get(
            self.PROTECTED, headers={"Authorization": "some-raw-token-without-prefix"}
        )
        assert resp.status_code in (401, 403), f"missing Bearer prefix accepted ({resp.status_code})"

    async def test_basic_auth_rejected(self, client):
        # "Basic dXNlcjpwYXNz" == user:pass — wrong scheme entirely.
        resp = await client.get(
            self.PROTECTED, headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert resp.status_code in (401, 403), f"Basic auth accepted ({resp.status_code})"

    async def test_empty_or_garbage_token_rejected(self, client):
        resp = await client.get(
            self.PROTECTED, headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert resp.status_code in (401, 403), f"garbage token accepted ({resp.status_code})"

    async def test_expired_token_rejected(self, client):
        jwt = pytest.importorskip("jwt")
        # Valid HS256 shape but already expired. Even with the wrong secret this
        # must be rejected; PyJWT checks exp and the gate rejects bad sigs too.
        token = jwt.encode(
            {"sub": "admin", "exp": int(time.time()) - 3600},
            "any-secret-key-for-shape-only-xxxxxxxxx",
            algorithm="HS256",
        )
        resp = await client.get(
            self.PROTECTED, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code in (401, 403), f"expired token accepted ({resp.status_code})"


class TestRBACAndRLS:
    """Role gates and row-visibility rules on the user-management endpoints."""

    async def test_admin_cannot_create_superadmin(self, client, require_db, auth_headers):
        resp = await client.post(
            "/api/auth/users",
            headers=auth_headers,
            json={
                "username": "rbac_super_attempt",
                "email": "x@example.com",
                "password": "password123",
                "is_superadmin": True,
            },
        )
        assert resp.status_code == 403, f"admin created a superadmin ({resp.status_code})"

    async def test_unauthenticated_cannot_list_users(self, client):
        resp = await client.get("/api/auth/users")
        assert resp.status_code in (401, 403), f"no-token listed users ({resp.status_code})"

    async def test_unauthenticated_cannot_create_project(self, client):
        resp = await client.post(
            "/api/projects", json={"name": "Hacker Project", "slug": "hacker-project"}
        )
        assert resp.status_code == 401, f"no-token created a project ({resp.status_code})"

    async def test_admin_cannot_delete_superadmin(self, client, require_db, auth_headers, superadmin_headers):
        # Find the seeded superadmin's id via a superadmin list, then try to
        # delete it as a plain admin -> 403.
        listing = await client.get("/api/auth/users", headers=superadmin_headers)
        assert listing.status_code == 200, f"superadmin list failed: {listing.status_code}"
        supers = [u for u in listing.json() if u.get("is_superadmin")]
        if not supers:
            pytest.skip("no superadmin row available to target")
        target_id = supers[0]["id"]
        resp = await client.delete(f"/api/auth/users/{target_id}", headers=auth_headers)
        assert resp.status_code == 403, f"admin deleted a superadmin ({resp.status_code})"

    async def test_admin_list_hides_superadmins(self, client, require_db, auth_headers):
        resp = await client.get("/api/auth/users", headers=auth_headers)
        assert resp.status_code == 200, f"admin user list failed: {resp.status_code}"
        assert all(
            not u.get("is_superadmin") for u in resp.json()
        ), "admin user list exposed a superadmin account"

    async def test_superadmin_list_shows_superadmins(self, client, require_db, superadmin_headers):
        resp = await client.get("/api/auth/users", headers=superadmin_headers)
        assert resp.status_code == 200, f"superadmin user list failed: {resp.status_code}"
        assert any(
            u.get("is_superadmin") for u in resp.json()
        ), "superadmin user list hid all superadmin accounts"


class TestCORS:
    """CORS must be configured with a credentialed, non-wildcard allowlist."""

    def _cors_origins(self):
        import app.main as main

        return main._ALLOWED_ORIGINS

    async def test_cors_middleware_present(self, client):
        origin = self._cors_origins()[0]
        resp = await client.get("/api/health", headers={"Origin": origin})
        acao = resp.headers.get("access-control-allow-origin")
        acac = resp.headers.get("access-control-allow-credentials")
        assert acao == origin or acac is not None, (
            "CORS middleware did not reflect the allowed origin / credentials header"
        )

    async def test_allow_credentials_true_without_wildcard(self, client):
        origins = self._cors_origins()
        assert "*" not in origins, "credentialed CORS must not use a wildcard origin"
        # Verify the live middleware reflects a configured origin with creds.
        origin = origins[0]
        resp = await client.get("/api/health", headers={"Origin": origin})
        if resp.headers.get("access-control-allow-credentials"):
            assert resp.headers["access-control-allow-credentials"] == "true"
        assert resp.headers.get("access-control-allow-origin") != "*", (
            "wildcard origin returned alongside credentials"
        )

    async def test_allowed_methods_include_post_and_delete(self, client):
        import app.main as main
        from starlette.middleware.cors import CORSMiddleware

        methods = None
        for mw in main.app.user_middleware:
            if mw.cls is CORSMiddleware:
                # Starlette stores config in `.options` (or `.kwargs` on older
                # releases). Support both.
                opts = getattr(mw, "options", None) or getattr(mw, "kwargs", {})
                methods = opts.get("allow_methods")
                break
        assert methods is not None, "CORSMiddleware not registered on the app"
        assert "POST" in methods and "DELETE" in methods, (
            f"allowed methods missing POST/DELETE: {methods}"
        )


class TestInfrastructureSecurity:
    """Security-relevant Terraform settings."""

    def test_rds_force_ssl_enabled(self):
        tf = terraform_text()
        # Locate the actual parameter declaration (not the comment mention in
        # the file header) and confirm its value is "1".
        marker = 'name  = "rds.force_ssl"'
        if marker not in tf:
            marker = 'name = "rds.force_ssl"'
        assert marker in tf, "rds.force_ssl parameter declaration not found"
        idx = tf.find(marker)
        window = tf[idx: idx + 120]
        assert 'value = "1"' in window or 'value  = "1"' in window, (
            "rds.force_ssl is not set to '1'"
        )

    def test_db_master_password_uses_random_password(self):
        tf = terraform_text()
        assert 'resource "random_password" "db_master"' in tf, (
            "db master password is not a random_password resource"
        )
        assert "password = random_password.db_master.result" in tf, (
            "RDS instance does not use the generated random_password"
        )

    def test_ec2_enforces_imdsv2(self):
        tf = terraform_text()
        assert 'http_tokens   = "required"' in tf or 'http_tokens = "required"' in tf, (
            "EC2 does not enforce IMDSv2 (http_tokens=required)"
        )

    def test_rds_not_publicly_accessible(self):
        tf = terraform_text()
        assert "publicly_accessible    = false" in tf or "publicly_accessible = false" in tf, (
            "RDS is publicly accessible"
        )
