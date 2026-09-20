"""Local development sessions and Cognito bearer-token validation."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from hmac import compare_digest

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient

from .config import Settings, get_settings
from .models import AuthResponse, LoginRequest, PasswordRecoveryRequest, RegisterRequest, User
from .security import hash_password, hash_session_token, new_session_token, verify_password
from .store import SQLiteStore, utc_now


class AuthService:
    _login_failures: defaultdict[str, deque[float]] = defaultdict(deque)
    _login_lock = threading.Lock()
    _login_window_seconds = 15 * 60
    _login_failure_limit = 8

    def __init__(self, store: SQLiteStore, settings: Settings):
        self.store = store
        self.settings = settings
        self._jwk_client: PyJWKClient | None = None

    def recover_password(self, request: PasswordRecoveryRequest) -> AuthResponse:
        self._ensure_local()
        configured_code = self.settings.local_recovery_code
        if not configured_code:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Local pilot password recovery is not configured. "
                    "Ask an administrator for help."
                ),
            )

        key = f"recovery:{request.email.casefold().strip()}"
        now = time.monotonic()
        with self._login_lock:
            failures = self._login_failures[key]
            while failures and now - failures[0] > self._login_window_seconds:
                failures.popleft()
            if len(failures) >= self._login_failure_limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many recovery attempts. Try again in a few minutes.",
                    headers={"Retry-After": "900"},
                )

        record = self.store.get_user_by_email(request.email)
        valid = bool(record and compare_digest(request.recovery_code, configured_code))
        if not valid:
            with self._login_lock:
                self._login_failures[key].append(now)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Recovery details were not accepted.",
            )

        user = record[0]
        self.store.update_password(user.id, hash_password(request.password))
        self.store.revoke_user_sessions(user.id)
        with self._login_lock:
            self._login_failures.pop(key, None)
        return self._session_response(user)

    def register(self, request: RegisterRequest) -> AuthResponse:
        self._ensure_local()
        user = self.store.create_user(request.name, request.email, hash_password(request.password))
        return self._session_response(user)

    def login(self, request: LoginRequest) -> AuthResponse:
        self._ensure_local()
        key = request.email.casefold().strip()
        now = time.monotonic()
        with self._login_lock:
            failures = self._login_failures[key]
            while failures and now - failures[0] > self._login_window_seconds:
                failures.popleft()
            if len(failures) >= self._login_failure_limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many sign-in attempts. Try again in a few minutes.",
                    headers={"Retry-After": "900"},
                )
        record = self.store.get_user_by_email(request.email)
        if record is None or not verify_password(request.password, record[1]):
            with self._login_lock:
                self._login_failures[key].append(now)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        with self._login_lock:
            self._login_failures.pop(key, None)
        return self._session_response(record[0])

    def logout(self, token: str | None) -> None:
        if token and self.settings.auth_mode == "local":
            self.store.revoke_session(hash_session_token(token))

    def user_from_token(self, token: str | None) -> User:
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
        if self.settings.auth_mode == "local":
            user = self.store.get_user_by_session(hash_session_token(token))
        else:
            user = self._cognito_user(token)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session is invalid or expired",
            )
        return user

    def _session_response(self, user: User) -> AuthResponse:
        token = new_session_token()
        self.store.create_session(
            hash_session_token(token),
            user.id,
            utc_now().replace(microsecond=0)
            + timedelta(hours=self.settings.session_ttl_hours),
        )
        return AuthResponse(user=user, access_token=token)

    def _ensure_local(self) -> None:
        if self.settings.auth_mode != "local":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Local auth is disabled",
            )

    def _cognito_user(self, token: str) -> User | None:
        issuer = self.settings.cognito_issuer
        client_id = self.settings.cognito_client_id
        if not issuer or not client_id:
            return None
        try:
            if self._jwk_client is None:
                self._jwk_client = PyJWKClient(
                    f"{issuer}/.well-known/jwks.json", cache_jwk_set=True
                )
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=issuer,
                options={
                    "require": ["exp", "iat", "sub", "token_use"],
                    # Cognito ID tokens include `aud`. Validate it explicitly
                    # below after checking token_use; PyJWT rejects the token
                    # when audience=None and verify_aud remains enabled.
                    "verify_aud": False,
                },
                audience=None,
            )
            if claims.get("token_use") not in {"access", "id"}:
                return None
            if claims.get("token_use") == "access" and claims.get("client_id") != client_id:
                return None
            if claims.get("token_use") == "id" and claims.get("aud") != client_id:
                return None
            user_id = str(claims["sub"])
            email = str(claims.get("email") or f"{user_id}@cognito.local")
            name = str(claims.get("name") or claims.get("preferred_username") or email)
            return User(id=user_id, name=name, email=email)
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, KeyError):
            return None


def bearer_token(authorization: str | None = Header(default=None)) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Use a Bearer token")
    return value.strip()


def get_auth_service() -> AuthService:
    from .main import get_store

    return AuthService(get_store(), get_settings())


def current_user(
    token: str | None = Depends(bearer_token), auth: AuthService = Depends(get_auth_service)
) -> User:
    return auth.user_from_token(token)
