# app/middleware/access_log.py
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from app.core.logger import logger
from app.core.context import RequestContext


class AccessLogMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        cost = (time.time() - start) * 1000
        logger.info(
            f"[{RequestContext.get_trace_id()}] "
            f"{request.method} {request.url.path} "
            f"status={response.status_code} cost={cost:.1f}ms"
        )
        return response