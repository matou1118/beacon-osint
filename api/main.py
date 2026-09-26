from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import Settings, load_settings
from api.routers import announcements, auth, categories, feedback, graph, heat, subscriptions
from api.services.match_service import MatchService
from api.services.user_service import UserService
from storage.db import Database

ROOT = Path(__file__).resolve().parents[1]


def create_app(db_path: str | None = None, settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    database = Database(db_path or app_settings.database_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        database.close()

    app = FastAPI(title="灯塔 OSINT API", description="公开信息筛选与预警工具后端服务", version="1.0.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.database = database
    app.state.user_service = UserService(app_settings)
    app.state.match_service = MatchService(str(ROOT / "config" / "rules.yaml"))
    app.state.categories_path = ROOT / "config" / "categories.yaml"
    if app_settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(app_settings.cors_origins), allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "X-User-Id"])

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": exc.status_code, "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"code": detail.get("code", exc.status_code), "message": detail.get("message", "request failed"), "data": None}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"code": 422, "message": "request validation failed", "data": None, "errors": jsonable_encoder(exc.errors())})

    app.include_router(announcements.router, prefix="/api/announcements", tags=["信息"])
    app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["订阅"])
    app.include_router(feedback.router, prefix="/api/feedback", tags=["反馈"])
    app.include_router(graph.router, prefix="/api/graph", tags=["知识图谱"])
    app.include_router(heat.router, prefix="/api/heat", tags=["热频话题"])
    app.include_router(categories.router, prefix="/api", tags=["基础数据"])
    app.include_router(auth.router, prefix="/api/auth", tags=["认证"])

    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "lighthouse-osint-api"}

    return app


app = create_app()
