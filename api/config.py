from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str
    environment: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    token_expire_seconds: int = 604800
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    sms_provider: str = "disabled"
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5

    @property
    def allow_development_user_header(self) -> bool:
        return self.environment != "production"


def load_settings() -> Settings:
    environment = os.getenv("BEACON_ENV", "development")
    secret = os.getenv("BEACON_JWT_SECRET", "")
    if environment == "production" and len(secret) < 32:
        raise RuntimeError("BEACON_JWT_SECRET must be at least 32 characters in production")
    origins = tuple(item.strip() for item in os.getenv("BEACON_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if item.strip())
    return Settings(
        database_path=os.getenv("BEACON_DB_PATH", "data/beacon.db"),
        environment=environment,
        jwt_secret=secret,
        jwt_algorithm=os.getenv("BEACON_JWT_ALGORITHM", "HS256"),
        token_expire_seconds=int(os.getenv("BEACON_TOKEN_EXPIRE_SECONDS", "604800")),
        cors_origins=origins,
        sms_provider=os.getenv("BEACON_SMS_PROVIDER", "disabled"),
        otp_ttl_seconds=int(os.getenv("BEACON_OTP_TTL_SECONDS", "300")),
        otp_max_attempts=int(os.getenv("BEACON_OTP_MAX_ATTEMPTS", "5")),
    )
