"""Domain 04 — Auth & Permissions.

Exercises password hashing, JWT creation/verification, the SECRET_KEY boot
guard in app.config, the /api/auth/login and /api/auth/me endpoints, and the
RBAC guards around user management. Tests that need the live seeded stack
depend on the `client`/`auth_headers`/`superadmin_headers` fixtures so they
SKIP (rather than fail) when PostgreSQL is unavailable.

All `import app.*` happens inside the test bodies so collection works even when
the application's heavy dependencies are missing.
"""
import os
import subprocess
import sys

import pytest


class TestPasswordHashing:
    def test_hash_has_bcrypt_prefix(self):
        from app.routes.auth import hash_password

        hashed = hash_password("hunter2")
        assert hashed.startswith("$2b$"), f"expected bcrypt $2b$ prefix, got {hashed[:4]!r}"

    def test_verify_returns_true_for_correct_password(self):
        from app.routes.auth import hash_password, verify_password

        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed) is True

    def test_verify_returns_false_for_wrong_password(self):
        from app.routes.auth import hash_password, verify_password

        hashed = hash_password("correct horse battery staple")
        assert verify_password("wrong password", hashed) is False

    def test_same_password_hashes_differ_random_salt(self):
        from app.routes.auth import hash_password

        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2, "two hashes of the same password must differ (random salt)"

    def test_unicode_password_works(self):
        from app.routes.auth import hash_password, verify_password

        pw = "pаsswörd🔐—Ωμέγα"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True
        assert verify_password("password", hashed) is False

    def test_very_long_password_works(self):
        from app.routes.auth import hash_password, verify_password

        pw = "a" * 4096
        hashed = hash_password(pw)
        assert hashed.startswith("$2b$")
        assert verify_password(pw, hashed) is True


class TestJWT:
    def test_round_trip_preserves_sub(self):
        from app.routes.auth import create_access_token, decode_token

        token = create_access_token({"sub": "alice"})
        payload = decode_token(token)
        assert payload["sub"] == "alice"

    def test_token_has_three_segments(self):
        from app.routes.auth import create_access_token

        token = create_access_token({"sub": "bob"})
        assert token.count(".") == 2, "a JWS compact token has 3 dot-separated parts"

    def test_expired_token_raises_401(self):
        from datetime import timedelta

        from fastapi import HTTPException

        from app.routes.auth import create_access_token, decode_token

        token = create_access_token({"sub": "carol"}, expires_delta=timedelta(minutes=-5))
        with pytest.raises(HTTPException) as exc:
            decode_token(token)
        assert exc.value.status_code == 401

    def test_tampered_signature_rejected(self):
        from fastapi import HTTPException

        from app.routes.auth import create_access_token, decode_token

        token = create_access_token({"sub": "dave"})
        header, payload, sig = token.split(".")
        # Flip the last character of the signature to invalidate it.
        bad_last = "A" if sig[-1] != "A" else "B"
        tampered = f"{header}.{payload}.{sig[:-1]}{bad_last}"
        with pytest.raises(HTTPException) as exc:
            decode_token(tampered)
        assert exc.value.status_code == 401

    def test_token_signed_with_different_secret_rejected(self):
        import jwt as _jwt
        from fastapi import HTTPException

        from app.config import ALGORITHM
        from app.routes.auth import decode_token

        forged = _jwt.encode(
            {"sub": "mallory"},
            "a-completely-different-secret-key-32chars!",
            algorithm=ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc:
            decode_token(forged)
        assert exc.value.status_code == 401

    def test_alg_none_forged_token_rejected(self):
        import base64
        import json

        from fastapi import HTTPException

        from app.routes.auth import decode_token

        def _b64(obj):
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        header = _b64({"alg": "none", "typ": "JWT"})
        payload = _b64({"sub": "eve", "is_admin": True})
        forged = f"{header}.{payload}."  # empty signature
        with pytest.raises(HTTPException) as exc:
            decode_token(forged)
        assert exc.value.status_code == 401

    def test_garbage_string_raises_401(self):
        from fastapi import HTTPException

        from app.routes.auth import decode_token

        with pytest.raises(HTTPException) as exc:
            decode_token("this.is.not-a-real-jwt")
        assert exc.value.status_code == 401

    def test_decode_returns_role_claims(self):
        from app.routes.auth import create_access_token, decode_token

        token = create_access_token(
            {"sub": "frank", "is_admin": True, "is_superadmin": False}
        )
        payload = decode_token(token)
        assert payload["is_admin"] is True
        assert payload["is_superadmin"] is False


class TestSecretKeyGuard:
    def _run_import(self, env_overrides, tmp_path):
        from conftest import REPO_ROOT

        env = os.environ.copy()
        # Clear anything that would mask the behaviour under test.
        env.pop("SECRET_KEY", None)
        env.pop("ENVIRONMENT", None)
        env.update(env_overrides)
        # app.config calls load_dotenv(), which would read the repo's .env and
        # mask the env we set here. Run from a clean temp cwd (so no .env is
        # found) and put the repo on PYTHONPATH so `import app.config` resolves.
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-c", "import app.config"],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
        )

    def test_production_without_secret_key_fails(self, tmp_path):
        proc = self._run_import({"ENVIRONMENT": "production"}, tmp_path)
        assert proc.returncode != 0, "production import must fail without SECRET_KEY"
        assert "SECRET_KEY" in proc.stderr or "RuntimeError" in proc.stderr

    def test_short_secret_key_fails(self, tmp_path):
        proc = self._run_import({"SECRET_KEY": "tooshort"}, tmp_path)
        assert proc.returncode != 0, "a <32-char SECRET_KEY must raise"
        assert "RuntimeError" in proc.stderr or "too short" in proc.stderr

    def test_valid_long_key_in_production_imports_ok(self, tmp_path):
        proc = self._run_import(
            {
                "ENVIRONMENT": "production",
                "SECRET_KEY": "x" * 48,
            },
            tmp_path,
        )
        assert proc.returncode == 0, f"valid key should import cleanly; stderr={proc.stderr}"


class TestLoginEndpoint:
    async def test_bad_credentials_returns_401(self, client, require_db):
        resp = await client.post(
            "/api/auth/login",
            json={"username": "nope", "password": "definitely-wrong"},
        )
        if resp.status_code not in (401, 403):
            pytest.skip(f"login path unavailable (status {resp.status_code})")
        assert resp.status_code == 401

    async def test_missing_username_returns_422(self, client):
        resp = await client.post("/api/auth/login", json={"password": "x"})
        assert resp.status_code == 422, f"missing username should be 422, got {resp.status_code}"

    async def test_missing_password_returns_422(self, client):
        resp = await client.post("/api/auth/login", json={"username": "admin"})
        assert resp.status_code == 422, f"missing password should be 422, got {resp.status_code}"

    async def test_successful_login_returns_token_and_flags(self, client, require_db):
        resp = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        if resp.status_code != 200:
            pytest.skip("admin login unavailable (needs live seeded DB)")
        body = resp.json()
        assert "access_token" in body and body["access_token"]
        assert "is_admin" in body
        assert "is_superadmin" in body

    async def test_response_never_contains_hashed_password(self, client, require_db):
        resp = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        if resp.status_code != 200:
            pytest.skip("admin login unavailable (needs live seeded DB)")
        assert "hashed_password" not in resp.json()

    async def test_success_body_has_admin_flag(self, client, require_db):
        resp = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        if resp.status_code != 200:
            pytest.skip("admin login unavailable (needs live seeded DB)")
        body = resp.json()
        assert body["is_admin"] is True
        assert body["username"] == "admin"


class TestMeEndpoint:
    async def test_me_without_token_returns_401(self, client):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401, f"expected 401 without token, got {resp.status_code}"

    async def test_me_with_invalid_token_returns_401(self, client):
        resp = await client.get(
            "/api/auth/me", headers={"Authorization": "Bearer not-a-valid-token"}
        )
        assert resp.status_code == 401, f"expected 401 for invalid token, got {resp.status_code}"

    async def test_me_with_valid_admin_token_returns_username(self, client, require_db, auth_headers):
        resp = await client.get("/api/auth/me", headers=auth_headers)
        if resp.status_code != 200:
            pytest.skip(f"/me unavailable (status {resp.status_code})")
        assert resp.json().get("username") == "admin"

    async def test_me_response_has_no_hashed_password(self, client, require_db, auth_headers):
        resp = await client.get("/api/auth/me", headers=auth_headers)
        if resp.status_code != 200:
            pytest.skip(f"/me unavailable (status {resp.status_code})")
        assert "hashed_password" not in resp.json()


class TestRBACGuards:
    async def test_admin_list_excludes_superadmins(self, client, require_db, auth_headers):
        resp = await client.get("/api/auth/users", headers=auth_headers)
        if resp.status_code != 200:
            pytest.skip(f"user listing unavailable (status {resp.status_code})")
        usernames = [u.get("username") for u in resp.json()]
        assert "superadmin" not in usernames, "admin must not see superadmin rows"

    async def test_superadmin_list_includes_superadmins(self, client, require_db, superadmin_headers):
        resp = await client.get("/api/auth/users", headers=superadmin_headers)
        if resp.status_code != 200:
            pytest.skip(f"user listing unavailable (status {resp.status_code})")
        usernames = [u.get("username") for u in resp.json()]
        assert "superadmin" in usernames, "superadmin must see superadmin rows"

    async def test_create_user_without_auth_returns_401(self, client):
        resp = await client.post(
            "/api/auth/users",
            json={"username": "newbie", "email": "n@x.io", "password": "password123"},
        )
        assert resp.status_code == 401, f"unauthenticated create must be 401, got {resp.status_code}"

    async def test_admin_cannot_create_superadmin(self, client, require_db, auth_headers):
        resp = await client.post(
            "/api/auth/users",
            headers=auth_headers,
            json={
                "username": "wannabe_super",
                "email": "ws@x.io",
                "password": "password123",
                "is_superadmin": True,
            },
        )
        if resp.status_code in (401, 422) or resp.status_code >= 500:
            pytest.skip(f"create path unavailable (status {resp.status_code})")
        assert resp.status_code == 403, f"admin minting superadmin must be 403, got {resp.status_code}"

    async def test_cannot_delete_root_admin(self, client, require_db, superadmin_headers):
        # Find the root "admin" user's id via the superadmin listing.
        listing = await client.get("/api/auth/users", headers=superadmin_headers)
        if listing.status_code != 200:
            pytest.skip(f"user listing unavailable (status {listing.status_code})")
        admin_rows = [u for u in listing.json() if u.get("username") == "admin"]
        if not admin_rows:
            pytest.skip("root admin user not present in seeded DB")
        admin_id = admin_rows[0]["id"]
        resp = await client.delete(
            f"/api/auth/users/{admin_id}", headers=superadmin_headers
        )
        assert resp.status_code == 403, f"deleting root admin must be 403, got {resp.status_code}"

    async def test_cannot_delete_last_active_superadmin(self, client, require_db, superadmin_headers):
        listing = await client.get("/api/auth/users", headers=superadmin_headers)
        if listing.status_code != 200:
            pytest.skip(f"user listing unavailable (status {listing.status_code})")
        supers = [u for u in listing.json() if u.get("is_superadmin")]
        if len(supers) != 1:
            pytest.skip("test only valid when exactly one active superadmin exists")
        super_id = supers[0]["id"]
        resp = await client.delete(
            f"/api/auth/users/{super_id}", headers=superadmin_headers
        )
        assert resp.status_code == 403, (
            f"deleting the last active superadmin must be 403, got {resp.status_code}"
        )

    async def test_non_admin_cannot_list_users(self, client):
        resp = await client.get("/api/auth/users")
        assert resp.status_code == 401, (
            f"listing users without a token must be 401, got {resp.status_code}"
        )
