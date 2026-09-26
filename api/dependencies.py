from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import Settings
from api.services.user_service import AuthProviderUnavailable, UserService
from storage.db import Database

bearer_scheme = HTTPBearer(auto_error=False)


def get_db(request: Request) -> Database:
    return request.app.state.database


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_user_service(request: Request) -> UserService:
    return request.app.state.user_service


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> str:
    settings: Settings = request.app.state.settings
    if credentials is not None:
        try:
            return request.app.state.user_service.decode_token(credentials.credentials)
        except AuthProviderUnavailable as exc:
            raise HTTPException(status_code=503, detail={"code": 503, "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail={"code": 401, "message": str(exc)}) from exc
    if settings.allow_development_user_header and x_user_id:
        if len(x_user_id) > 128:
            raise HTTPException(status_code=400, detail={"code": 400, "message": "invalid user id"})
        return x_user_id
    raise HTTPException(status_code=401, detail={"code": 401, "message": "authentication required"})


def get_optional_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> str | None:
    if credentials is None and not x_user_id:
        return None
    return get_current_user(request, credentials, x_user_id)
