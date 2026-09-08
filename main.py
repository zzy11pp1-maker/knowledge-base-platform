"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.api.chat import router as chat_router
from app.api.identity import router as identity_router
from app.api.knowledge import router as knowledge_router
from app.api.operations import router as operations_router
from app.core.auth import current_user, enforce
from app.core.business_dependencies import get_knowledge_service
from app.core.config import get_settings
from app.core.dependencies import (
    close_dependencies,
    get_bge_client,
    get_qdrant_repository,
    get_reranker_client,
)
from app.core.exceptions import PlatformError
from app.core.logging import configure_logging
from app.db.session import close_database, initialize_database, session_factory
from app.integrations.llm_client import CompatibleLLMClient
from app.services.metrics import record_metric

settings = get_settings()
configure_logging(settings.log_level)
LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    LOGGER.info("应用启动 env=%s", settings.app_env)
    initialize_database()
    try:
        yield
    finally:
        get_knowledge_service.cache_clear()
        close_dependencies()
        close_database()
        LOGGER.info("应用已停止")


app = FastAPI(title=settings.app_name, version="0.3.0", lifespan=lifespan)
app.include_router(identity_router)
app.include_router(knowledge_router)
app.include_router(chat_router)
app.include_router(operations_router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """为每次请求提供可追踪 ID。"""

    request_id = str(uuid4())
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception as exc:
        # 避免服务器默认回溯将 SQL 参数或上游响应正文写入日志。
        response = await unexpected_error_handler(request, exc)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(PlatformError)
async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
    LOGGER.warning("可预期异常 code=%s request_id=%s", exc.code, getattr(request.state, "request_id", None))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.get("/health")
def health(check_dependencies: bool = Query(default=False)) -> dict:
    """默认只检查应用；显式请求时再探测三项外部依赖。"""

    result: dict = {
        "status": "ok",
        "project": "knowledge-platform",
        "environment": settings.app_env,
        "configured": {
            "bge": bool(settings.bge_base_url),
            "reranker": bool(settings.reranker_base_url),
            "qdrant": bool(settings.qdrant_url or settings.qdrant_location),
        },
    }
    if not check_dependencies:
        return result

    # 深度探测迁移到认证接口，防止匿名请求消耗外部服务或枚举部署信息。
    return {"status": "ok", "detail": "请登录后使用 /health/dependencies"}


@app.get("/health/dependencies")
async def dependency_health(user=Depends(current_user)):
    enforce(user, "user:manage")
    services = {}
    for name, getter in (("bge", get_bge_client), ("reranker", get_reranker_client)):
        try:
            services[name] = await run_in_threadpool(lambda factory=getter: factory().health())
        except Exception:
            services[name] = {"ok": False}
    try:
        await run_in_threadpool(lambda: get_qdrant_repository().client.get_collections())
        services["qdrant"] = {"ok": True}
    except Exception:
        services["qdrant"] = {"ok": False}
    try:
        with session_factory()() as db:
            db.execute(text("SELECT 1"))
        services["database"] = {"ok": True}
    except Exception:
        services["database"] = {"ok": False}
    try:
        services["llm"] = await CompatibleLLMClient().health()
    except Exception:
        services["llm"] = {"ok": False}
    return {"status": "ok" if all(x["ok"] for x in services.values()) else "degraded", "services": services}


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, exc):
    # 不回显数据库 SQL 参数，参数中可能包含密码哈希或内部资料。
    return JSONResponse(
        status_code=409, content={"code": "data_conflict", "message": "记录冲突或仍被其他数据引用"}
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    principal = getattr(request.state, "principal", None)
    if exc.status_code == 403 and principal is not None:
        record_metric("permission_denied", tenant_id=principal.tenant_id, user_id=principal.id,
                      metadata={"route": request.url.path, "status": 403})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": list(error["loc"]), "type": error["type"], "msg": "请求字段格式不正确"}
                for error in exc.errors()
            ]
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request, exc):
    LOGGER.error(
        "未预期错误 type=%s request_id=%s", type(exc).__name__, getattr(request.state, "request_id", None)
    )
    return JSONResponse(status_code=500, content={"code": "internal_error", "message": "服务内部错误"})
