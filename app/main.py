# app/main.py
import os
from pathlib import Path

# 必须保留：启动时切到项目根，所有相对路径才能正确解析
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)

import asyncio
from contextlib import asynccontextmanager
from app.bootstrap import create_app
from app.config.settings import server_settings
from app.core.gpu_lock import gpu_lock
from app.core.logger import logger
from app.core.healthcheck import check_external_services_on_startup
from app.core.tracing import init_tracing          # ← OpenTelemetry 链路追踪初始化
from app.infrastructure.repositories.task_repo import task_repo
from app.application.conversation_service import shutdown_conversation_background


@asynccontextmanager
async def lifespan(app):
    # ---- 启动 ----
    # -1) 初始化链路追踪（TracerProvider / 导出器）。必须在任何埋点之前；
    #     放在 lifespan 启动段足够早（早于任何请求）。OTel 未安装或开关关闭时
    #     init_tracing 内部 no-op 返回 False，绝不阻断启动。
    try:
        ok = init_tracing()
        logger.info(f"[boot] tracing enabled = {ok}")
    except Exception as e:
        logger.warning(f"[Startup] tracing 初始化失败（已忽略，不影响主流程）: {e}")

    workers = int(os.getenv("WEB_CONCURRENCY", server_settings.workers or 1))
    if workers > 1:
        logger.warning(
            f"检测到 workers={workers}，GlobalGPULock 是进程级锁，"
            f"多 worker 下 GPU 串行会失效！请设 workers=1，"
            f"或改用 Redis 分布式锁版本。"
        )

    # 0) 外部依赖健康检查（探 Ollama /api/tags，可选 ComfyUI）
    await asyncio.to_thread(check_external_services_on_startup)

    # 1) GPU 清场（阻塞操作，丢线程池，不阻塞事件循环）
    await asyncio.to_thread(gpu_lock.purge_all)

    # 2) 回收僵尸任务：进程被 OOM Killer 杀掉重启后，DB 残留的 running 标记为 failed
    timeout_minutes = int(os.getenv("TASK_ZOMBIE_TIMEOUT_MINUTES", "120"))
    try:
        await asyncio.to_thread(task_repo.mark_timeout_running_as_failed, timeout_minutes)
    except Exception as e:
        logger.error(f"[Startup] 回收僵尸任务失败: {e}", exc_info=True)

    yield

    # ---- 关闭 ----
    # 先停后台执行器（标题/摘要后台任务），让在跑的 worker 收尾、释放摘要锁，
    # 再清 GPU——顺序不能反，否则 purge 时后台任务可能仍占用显存。
    try:
        shutdown_conversation_background()
    except Exception as e:
        logger.warning(f"[Shutdown] 关闭后台执行器失败: {e}")

    try:
        await asyncio.to_thread(gpu_lock.purge_all)
    except Exception as e:
        logger.warning(f"[Shutdown] purge_all 失败: {e}")

    # flush 并关闭链路追踪导出器（OTel 未安装时安全跳过，不依赖 tracing 的额外符号）。
    try:
        from opentelemetry import trace as _otel_trace
        _otel_trace.get_tracer_provider().shutdown()
    except Exception as e:
        logger.debug(f"[Shutdown] tracing 关闭: {e}")


app = create_app(lifespan=lifespan)

# ---- OpenTelemetry：FastAPI 自动埋点（每个 HTTP 请求自动生成 server span）----
# 装上依赖后生效；未安装 / 未启用时安全跳过。配合 RequestsInstrumentor，
# 三个视频/图片 client 的 requests 调用会自动生成 span，client 代码无需改动。
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app)
except Exception as e:  # 未安装 fastapi-instrumentor 时忽略
    import logging as _logging
    _logging.getLogger(__name__).debug(f"FastAPIInstrumentor 未启用: {e}")

# ---- OpenTelemetry：requests 自动埋点（覆盖所有 client 的 requests.post/get）----
# 关键接线：没有这一步，三个视频/图片 client（t2v/i2v/face_swap/qwen_image）里的
# submit / 轮询 / 下载 不会生成 span，链路会在"外部调用"这一环断掉。装上依赖即生效，
# client 代码无需改动；未装依赖时安全跳过。
try:
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    RequestsInstrumentor().instrument()
except Exception as e:  # 未安装 requests-instrumentor 时忽略
    import logging as _logging
    _logging.getLogger(__name__).debug(f"RequestsInstrumentor 未启用: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=server_settings.host,
        port=server_settings.port,
    )
