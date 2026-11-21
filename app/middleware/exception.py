# app/middleware/exception.py
"""全局异常处理（对外脱敏 + 运维端点 404 归一）。

【本轮修复】
1. 低危：StarletteHTTPException 原来原样透出 str(exc.detail)。反代/上游组件（网关、
   负载均衡、框架内部）抛出的 detail 可能带内部路径、后端主机名等内部信息。
   现在：5xx 一律返回通用文案（真实原因只进日志）；4xx 只在**明确白名单**内才透出
   原文，其余归一为通用提示；校验异常同样只把明细写日志、不回显。
2. 新增 OpsNotFoundException（运维端点未授权，见 middleware/auth.py）映射：
   统一输出 404 + 通用文案，不暴露端点存在。
3. 兜底 Exception handler：以前未处理异常会走 Starlette 默认 500（无 traceId）；
   现在统一出参并带 traceId，与本轮 trace_id 打通的修复配套。
"""
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.error_code import ErrorCode
from app.core.exceptions import AppException
from app.core.logger import logger
from app.core.context import RequestContext
from app.middleware.auth import OpsNotFoundException

# 4xx 允许原样透给前端的 detail（本服务自己抛出的、面向用户的业务提示）
_SAFE_DETAIL_KEYWORDS = (
    "文件不存在", "任务不存在", "音频文件不存在", "thumb not found", "not found",
    "invalid filename", "invalid path", "invalid url", "unsupported url",
    "empty file", "empty path", "path not allowed", "文件过大", "参数",
    "缺少访问令牌", "访问令牌", "用户信息", "未登录", "非法用户标识",
)


def _safe_detail(exc: StarletteHTTPException) -> str:
    if exc.status_code >= 500:
        return "服务内部错误"
    raw = str(getattr(exc, "detail", "") or "").strip()
    if not raw or len(raw) > 128:
        return "请求失败"
    if any(k in raw for k in _SAFE_DETAIL_KEYWORDS):
        return raw
    return "请求失败"


def register_exception_handlers(app: FastAPI):

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        logger.warning(f"[{RequestContext.get_trace_id()}] 业务异常: {exc.message}")
        return JSONResponse(
            status_code=200,
            content={
                "code": exc.code,
                "message": exc.message,
                "traceId": RequestContext.get_trace_id(),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        logger.warning(
            f"[{RequestContext.get_trace_id()}] 参数校验失败: {exc.errors()}"
        )
        return JSONResponse(
            status_code=200,
            content={
                "code": ErrorCode.PARAM_ERROR.code,
                "message": ErrorCode.PARAM_ERROR.message,
                "traceId": RequestContext.get_trace_id(),
            },
        )

    @app.exception_handler(OpsNotFoundException)
    async def ops_not_found_handler(request: Request, exc: OpsNotFoundException):
        return JSONResponse(
            status_code=404,
            content={
                "code": 404,
                "message": "not found",
                "traceId": RequestContext.get_trace_id(),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        log = logger.error if exc.status_code >= 500 else logger.info
        log(
            f"[{RequestContext.get_trace_id()}] HTTP {exc.status_code} "
            f"{request.method} {request.url.path} detail={getattr(exc, 'detail', None)!r}"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.status_code,
                "message": _safe_detail(exc),
                "traceId": RequestContext.get_trace_id(),
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.error(
            f"[{RequestContext.get_trace_id()}] 未处理异常 "
            f"{request.method} {request.url.path}: {exc}\n{traceback.format_exc()}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "message": "服务内部错误",
                "traceId": RequestContext.get_trace_id(),
            },
        )
