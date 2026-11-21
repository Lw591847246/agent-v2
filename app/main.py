# app/main.py
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)

import asyncio
from contextlib import asynccontextmanager
from app.bootstrap import create_app
from app.config.settings import server_settings
from app.core.gpu_lock import gpu_lock
from app.core.logger import logger
from app.core.healthcheck import check_external_services_on_startup
from app.core.tracing import init_tracing, shutdown_tracing
from app.infrastructure.repositories.task_repo import task_repo
from app.application.conversation_service import shutdown_conversation_background


@asynccontextmanager
async def lifespan(app):
    # ---- 启动 ----
    try:
        # 传入 app：init_tracing 内部在 set_tracer_provider 成功后自动
        # 安装 FastAPI / Requests instrument，避免"instrument 先于 provider"时序问题。
        ok = init_tracing(service_name="agent-v2", app=app)
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

    await asyncio.to_thread(check_external_services_on_startup)

    await asyncio.to_thread(gpu_lock.purge_all)

    timeout_minutes = int(os.getenv("TASK_ZOMBIE_TIMEOUT_MINUTES", "120"))
    try:
        await asyncio.to_thread(task_repo.mark_timeout_running_as_failed, timeout_minutes)
    except Exception as e:
        logger.error(f"[Startup] 回收僵尸任务失败: {e}", exc_info=True)

    yield

    # ---- 关闭 ----
    try:
        shutdown_conversation_background()
    except Exception as e:
        logger.warning(f"[Shutdown] 关闭后台执行器失败: {e}")

    try:
        await asyncio.to_thread(gpu_lock.purge_all)
    except Exception as e:
        logger.warning(f"[Shutdown] purge_all 失败: {e}")

    try:
        shutdown_tracing()
    except Exception as e:
        logger.debug(f"[Shutdown] tracing 关闭: {e}")


app = create_app(lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=server_settings.host,
        port=server_settings.port,
    )