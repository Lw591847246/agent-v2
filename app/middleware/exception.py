# app/middleware/exception.py
"""全局异常处理"""
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.core.exceptions import AppException
from app.core.error_code import ErrorCode
from app.core.logger import logger
from app.core.context import RequestContext


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
        logger.warning(f"参数校验失败: {exc.errors()}")
        return JSONResponse(
            status_code=200,
            content={
                "code": ErrorCode.PARAM_ERROR.code,
                "message": ErrorCode.PARAM_ERROR.message,
                "traceId": RequestContext.get_trace_id(),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.status_code,
                "message": str(exc.detail),
                "traceId": RequestContext.get_trace_id(),
            },
        )

    @app.exception_handler(Exception)
    async def global_handler(request: Request, exc: Exception):
        logger.error(f"[{RequestContext.get_trace_id()}] 未捕获异常: {exc}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=200,
            content={
                "code": ErrorCode.SERVER_ERROR.code,
                "message": ErrorCode.SERVER_ERROR.message,
                "traceId": RequestContext.get_trace_id(),
            },
        )