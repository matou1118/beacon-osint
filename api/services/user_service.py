from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import jwt

from api.config import Settings


class AuthProviderUnavailable(RuntimeError):
    pass


class SmsProvider(Protocol):
    def send_code(self, phone: str, code: str) -> None:
        ...


class DisabledSmsProvider:
    def send_code(self, phone: str, code: str) -> None:
        raise AuthProviderUnavailable("SMS provider is not configured")


@dataclass
class _Otp:
    digest: str
    expires_at: float
    created_at: float
    attempts: int = 0


class UserService:
    def __init__(self, settings: Settings, sms_provider: SmsProvider | None = None) -> None:
        self.settings = settings
        self.sms_provider = sms_provider or DisabledSmsProvider()
        self._codes: dict[str, _Otp] = {}
        self._lock = threading.Lock()

    @staticmethod
    def normalize_phone(phone: str) -> str:
        value = re.sub(r"\s+", "", str(phone or ""))
        if not re.fullmatch(r"1[3-9]\d{9}", value):
            raise ValueError("invalid phone number")
        return value

    def _digest(self, phone: str, code: str) -> str:
        secret = self.settings.jwt_secret or "development-only"
        return hmac.new(secret.encode("utf-8"), f"{phone}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()

    def request_code(self, phone: str) -> None:
        normalized = self.normalize_phone(phone)
        now = time.time()
        with self._lock:
            existing = self._codes.get(normalized)
            if existing is not None and existing.expires_at >= now and now - existing.created_at < 30:
                raise ValueError("please wait before requesting another code")
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._codes[normalized] = _Otp(self._digest(normalized, code), now + self.settings.otp_ttl_seconds, now)
        try:
            self.sms_provider.send_code(normalized, code)
        except Exception:
            with self._lock:
                self._codes.pop(normalized, None)
            raise

    def verify_code(self, phone: str, code: str) -> str:
        normalized = self.normalize_phone(phone)
        now = time.time()
        with self._lock:
            otp = self._codes.get(normalized)
            if otp is None or otp.expires_at < now:
                self._codes.pop(normalized, None)
                raise ValueError("verification code expired")
            otp.attempts += 1
            if otp.attempts > self.settings.otp_max_attempts:
                self._codes.pop(normalized, None)
                raise ValueError("too many verification attempts")
            if not hmac.compare_digest(otp.digest, self._digest(normalized, code)):
                raise ValueError("invalid verification code")
            self._codes.pop(normalized, None)
        return self.user_id_for_phone(normalized)

    def user_id_for_phone(self, phone: str) -> str:
        secret = self.settings.jwt_secret or "development-only"
        digest = hashlib.sha256(f"{secret}:{phone}".encode("utf-8")).hexdigest()
        return f"u_{digest[:16]}"

    def create_token(self, user_id: str) -> str:
        if not self.settings.jwt_secret:
            raise AuthProviderUnavailable("JWT secret is not configured")
        now = int(time.time())
        payload = {"sub": user_id, "iat": now, "exp": now + self.settings.token_expire_seconds}
        return jwt.encode(payload, self.settings.jwt_secret, algorithm=self.settings.jwt_algorithm)

    def decode_token(self, token: str) -> str:
        if not self.settings.jwt_secret:
            raise AuthProviderUnavailable("JWT secret is not configured")
        try:
            payload = jwt.decode(token, self.settings.jwt_secret, algorithms=[self.settings.jwt_algorithm])
        except jwt.PyJWTError as exc:
            raise ValueError("invalid token") from exc
        subject = payload.get("sub")
        if not subject:
            raise ValueError("invalid token")
        return str(subject)
