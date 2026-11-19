# app/core/healthcheck.py
"""外部依赖启动健康检查

解决"服务起来了但所有请求默默超时"的假活状态：Ollama 被定位为必须在线的核心
AI 组件，若启动时它就不可达，应在 lifespan 阶段暴露，而不是等到第一条用户请求
才在连接超时后静默失败。

fail_fast=true(默认)：探活失败直接抛错阻断启动，让进程管理/容器明确重启或告警；
fail_fast=false：仅 WARNING 不阻断(适合依赖可能晚于本服务就绪的开发环境)。

接入(main.py 的 lifespan 启动段调用一次即可，同步阻塞可接受，仅启动时跑)：
    from app.core.healthcheck import check_external_services_on_startup
    @asynccontextmanager
    async def lifespan(app):
        check_external_services_on_startup()   # <- 启动探活
        yield
        # ...原关闭逻辑(shutdown_conversation_background 等)
"""
import requests
from app.config.loader import config
from app.core.logger import logger


def _probe(name: str, url: str, timeout: float):
    """探一个 URL，返回 None 表示健康，否则返回失败描述。"""
    connect = min(3.0, timeout)   # 建连短超时，端口不通秒级失败
    try:
        resp = requests.get(url, timeout=(connect, timeout))
        if resp.status_code != 200:
            return f"{name} 返回 HTTP {resp.status_code} @ {url}"
        return None
    except Exception as e:
        return f"{name} 不可达 @ {url}: {type(e).__name__}: {e}"


def check_external_services_on_startup():
    fail_fast = config.get("health.fail_fast", True)
    probe_timeout = config.get("health.probe_timeout", 5)
    failures = []

    # ---- Ollama：核心，默认必检 ----
    if config.get("health.check_ollama", True):
        base = (config.get("ollama.base_url") or "http://localhost:11434").rstrip("/")
        ollama_err = _probe("Ollama", f"{base}/api/tags", probe_timeout)
        if ollama_err:
            # 把探活结论透传给全局 GPU 锁：fail_fast=false 降级继续启动时，
            # purge_all 据此跳过对已知不通的 Ollama 逐个卸载，省掉启动空等。
            # 延迟 import 避免与 main/healthcheck 的 import 顺序耦合。
            try:
                from app.core.gpu_lock import gpu_lock
                gpu_lock.mark_ollama_unreachable()
            except Exception as _e:
                logger.warning(f"标记 Ollama 不可达失败(不影响探活判定): {_e}")
        failures.append(ollama_err)

    # ---- ComfyUI(可选)：图/视频链路依赖，按需开启 ----
    if config.get("health.check_comfyui", False):
        base = (config.get("services.qwen_image.base_url") or "").rstrip("/")
        if base:
            failures.append(_probe("ComfyUI", f"{base}/system_stats", probe_timeout))
        else:
            logger.warning("health.check_comfyui=true 但未配置 services.qwen_image.base_url，跳过 ComfyUI 探活")

    failures = [f for f in failures if f]
    if failures:
        msg = "外部依赖健康检查失败: " + "; ".join(failures)
        if fail_fast:
            raise RuntimeError(msg)   # 阻断启动
        logger.warning(msg)           # 仅告警，不阻断
    else:
        logger.info("外部依赖健康检查通过")
