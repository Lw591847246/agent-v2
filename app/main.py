# app/main.py
import os
from pathlib import Path

# ⭐ 必须保留：启动时切到项目根，所有相对路径才能正确解析
#    （./data/voices/...、app/config/workflows/*.json、./data/uploads 等）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)

import asyncio
from contextlib import asynccontextmanager

from app.bootstrap import create_app
from app.config.settings import server_settings
from app.core.gpu_lock import gpu_lock
from app.core.logger import logger


@asynccontextmanager
async def lifespan(app):
    # ---- 启动 ----
    workers = int(os.getenv("WEB_CONCURRENCY", server_settings.workers or 1))
    if workers > 1:
        logger.warning(
            f"⚠️ 检测到 workers={workers}，GlobalGPULock 是进程级锁，"
            f"多 worker 下 GPU 串行会失效！请设 workers=1，"
            f"或改用 Redis 分布式锁版本。"
        )

    # GPU 清场是阻塞操作，丢线程池执行，不阻塞事件循环
    await asyncio.to_thread(gpu_lock.purge_all)

    yield

    # ---- 关闭 ----
    try:
        await asyncio.to_thread(gpu_lock.purge_all)
    except Exception as e:
        logger.warning(f"[Shutdown] purge_all 失败: {e}")


# ⭐ 把 lifespan 交给 create_app
app = create_app(lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=server_settings.host,
        port=server_settings.port,
    )