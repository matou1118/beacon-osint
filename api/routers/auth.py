from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_user_service
from api.schemas import PhoneLoginRequest, PhoneRequest, WechatLoginRequest
from api.services.user_service import AuthProviderUnavailable, UserService

router = APIRouter()


@router.post("/send-code")
def send_code(payload: PhoneRequest, service: UserService = Depends(get_user_service)):
    try:
        service.request_code(payload.phone)
    except AuthProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": 503, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": 400, "message": str(exc)}) from exc
    return {"code": 0, "data": {"sent": True}}


@router.post("/login")
def login(payload: PhoneLoginRequest, service: UserService = Depends(get_user_service)):
    try:
        user_id = service.verify_code(payload.phone, payload.code)
        token = service.create_token(user_id)
    except AuthProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": 503, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": 400, "message": str(exc)}) from exc
    return {"code": 0, "data": {"token": token, "user_id": user_id, "expires_in": service.settings.token_expire_seconds}}


@router.post("/wechat")
def wechat_login(payload: WechatLoginRequest):
    raise HTTPException(status_code=501, detail={"code": 501, "message": "WeChat provider is not configured"})
