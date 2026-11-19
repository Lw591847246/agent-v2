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
from app.infrastructure.repositories.task_repo import task_repo
from app.application.conversation_service import shutdown_conversation_background


@asynccontextmanager
async def lifespan(app):
    # ---- 启动 ----
    workers = int(os.getenv("WEB_CONCURRENCY", server_settings.workers or 1))
    if workers > 1:
        logger.warning(
            f"检测到 workers={workers}，GlobalGPULock 是进程级锁，"
            f"多 worker 下 GPU 串行会失效！请设 workers=1，"
            f"或改用 Redis 分布式锁版本。"
        )

    # 0) 外部依赖健康检查（探 Ollama /api/tags，可选 ComfyUI）
    #    丢线程池执行，不阻塞事件循环；health.fail_fast=true 时不通直接抛错阻断启动，
    #    避免"服务起来了但所有请求默默超时"的假活状态。
    await asyncio.to_thread(check_external_services_on_startup)

    # 1) GPU 清场（阻塞操作，丢线程池，不阻塞事件循环）
    await asyncio.to_thread(gpu_lock.purge_all)

    # 2) 回收僵尸任务：进程被 OOM Killer 杀掉重启后，DB 残留的 running 标记为 failed
    #    timeout_minutes 要大于最长正常任务耗时；默认 120min（INDEXTTS2 约 100min）
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


app = create_app(lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=server_settings.host,
        port=server_settings.port,
    )
