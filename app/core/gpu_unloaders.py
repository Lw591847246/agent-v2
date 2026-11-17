"""GPU 资源卸载器：每类资源一个 unloader，返回实际释放的 MB 数。"""
from __future__ import annotations

import subprocess
import time

import requests

from app.core.logger import logger


def gpu_used_mb() -> int:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return int(r.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return -1


def gpu_total_mb() -> int:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return int(r.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return 0


def log_gpu(label: str):
    used = gpu_used_mb()
    if used < 0:
        logger.info(f"🔍 [显存] {label}: (nvidia-smi 不可用)")
        return
    total = gpu_total_mb()
    if total > 0:
        logger.info(f"🔍 [显存] {label}: {used}MB / {total}MB ({used * 100 // total}%)")
    else:
        logger.info(f"🔍 [显存] {label}: {used}MB")


def _wait_freed(before: int, target_drop: int = 100, max_wait_s: float = 5.0) -> int:
    if before < 0:
        return 0
    deadline = time.time() + max_wait_s
    after = before
    while time.time() < deadline:
        time.sleep(0.5)
        after = gpu_used_mb()
        if after < 0 or (before - after) >= target_drop:
            break
    if after < 0:
        return 0
    return max(0, before - after)


def _strip_api_path(base: str) -> str:
    """把 http://host:port/api/chat 之类裁剪成 http://host:port。"""
    base = (base or "").strip().rstrip("/")
    if not base:
        return base
    if "/api/" in base:
        base = base.split("/api/")[0]
    # 兜底：去掉可能残留的尾部已知路径
    for suffix in ("/api", "/v1", "/generate", "/chat"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base.rstrip("/")


def unload_ollama() -> int:
    from app.config.loader import config

    before = gpu_used_mb()
    base = _strip_api_path(config.get("ollama.base_url", "http://127.0.0.1:11434"))

    # ⭐ 1. 查当前真加载的
    loaded = None
    try:
        r = requests.get(f"{base}/api/ps", timeout=5)
        if r.status_code == 200:
            loaded = {
                m.get("name") or m.get("model")
                for m in (r.json().get("models") or [])
                if (m.get("name") or m.get("model"))
            }
    except Exception as e:
        logger.debug(f"[GPU卸载] /api/ps 失败，回退全量: {e}")

    # ⭐ 2. 两条兜底
    if loaded is None:                          # 接口挂了
        models_cfg = config.get("ollama.models", {}) or {}
        loaded = {m for m in models_cfg.values() if isinstance(m, str) and m}
    elif not loaded and before > 2000:          # 返回空但显存占用
        logger.warning(f"[GPU卸载] /api/ps 返回空但显存 {before}MB，回退全量")
        models_cfg = config.get("ollama.models", {}) or {}
        loaded = {m for m in models_cfg.values() if isinstance(m, str) and m}

    if not loaded:
        logger.info("[GPU卸载] ollama 无加载模型，跳过")
        return 0

    logger.info(f"[GPU卸载] ollama 当前加载: {sorted(loaded)}")
    for name in loaded:
        try:
            requests.post(
                f"{base}/api/generate",
                json={"model": name, "keep_alive": 0, "prompt": "", "stream": False},
                timeout=5,
            )
            logger.info(f"[GPU卸载] ollama 卸载 {name}")
        except Exception as e:
            logger.debug(f"[GPU卸载] ollama {name} 失败: {e}")

    return _wait_freed(before)


def unload_comfyui() -> int:
    from app.config.loader import config

    before = gpu_used_mb()
    # comfyui 的 base_url 在 yml 里可能出现在 services.qwen_image / services.text_to_video / services.image_to_video 下
    candidates = set()
    for path in (
        "comfyui.base_url",
        "services.qwen_image.base_url",
        "services.face_swap.base_url",
        "services.text_to_video.base_url",
        "services.image_to_video.base_url",
    ):
        v = config.get(path)
        if v:
            candidates.add(_strip_api_path(v))
    if not candidates:
        candidates = {"http://127.0.0.1:8188"}

    for base in candidates:
        for path in ("/free", "/api/free"):
            try:
                r = requests.post(
                    f"{base}{path}",
                    json={"unload_models": True, "free_memory": True},
                    timeout=15,
                )
                if r.status_code < 400:
                    logger.info(f"[GPU卸载] comfyui OK ({base}{path})")
                    return _wait_freed(before)
            except Exception as e:
                logger.debug(f"[GPU卸载] comfyui {base}{path} 失败: {e}")

    logger.warning(f"[GPU卸载] comfyui 所有候选地址失败: {candidates}")
    return _wait_freed(before)


def unload_tts() -> int:
    from app.config.loader import config

    before = gpu_used_mb()

    # 1) 优先：用户显式配置的 unload_urls
    urls = config.get("services.tts.unload_urls", []) or []
    if urls:
        for url in urls:
            try:
                r = requests.post(url, timeout=10)
                if r.status_code < 400:
                    logger.info(f"[GPU卸载] tts OK ({url})")
                    return _wait_freed(before)
                logger.debug(f"[GPU卸载] tts {url} HTTP {r.status_code}")
            except Exception as e:
                logger.debug(f"[GPU卸载] tts {url} 失败: {e}")
        logger.warning(f"[GPU卸载] ⚠️ services.tts.unload_urls 全部失败: {urls}")

    # 2) 回退：按 engine 自动探测
    engine = (config.get("tts.engine", "indextts2") or "indextts2").lower()
    if engine == "cosyvoice2":
        base = config.get("tts.cosyvoice2.base_url", "http://127.0.0.1:9011")
    else:
        base = config.get("tts.indextts2.base_url", "http://127.0.0.1:9010")
    base = _strip_api_path(base)

    for path in ("/unload", "/api/unload", "/v1/unload", "/release", "/free"):
        try:
            r = requests.post(f"{base}{path}", timeout=10)
            if r.status_code < 400:
                logger.info(f"[GPU卸载] tts OK ({engine} {base}{path})")
                return _wait_freed(before)
        except Exception:
            pass

    # 3) 兜底：taskkill 只在显式开启时做，且我们只打印告警不做真实 kill
    if config.get("services.tts.taskkill_enabled", False):
        logger.warning(
            "[GPU卸载] ⚠️ taskkill_enabled=true 但本模块不主动 kill 进程。"
            "请手动在 services.tts.unload_urls 配置上游卸载端点。"
        )

    logger.warning(f"[GPU卸载] ⚠️ tts 自动探测全部失败 (engine={engine}, base={base})")
    return _wait_freed(before)


def _http_unload_or_docker_stop(base_url: str, container: str, label: str) -> int:
    before = gpu_used_mb()

    if base_url:
        base = _strip_api_path(base_url)
        for path in ("/v1/unload", "/v1/free", "/unload", "/free"):
            try:
                r = requests.post(f"{base}{path}", timeout=10)
                if r.status_code < 400:
                    logger.info(f"[GPU卸载] {label} OK ({base}{path})")
                    return _wait_freed(before)
            except Exception:
                pass

    if container:
        try:
            subprocess.run(["docker", "stop", container], check=False, timeout=60)
            logger.info(f"[GPU卸载] {label} docker stop {container}")
            return _wait_freed(before, max_wait_s=20)
        except Exception as e:
            logger.warning(f"[GPU卸载] {label} docker stop 失败: {e}")

    return _wait_freed(before, max_wait_s=2)


def unload_liveportrait() -> int:
    from app.config.loader import config
    base = config.get("liveportrait.base_url", "http://127.0.0.1:8001")
    container = config.get("liveportrait.container_name", "")
    return _http_unload_or_docker_stop(base, container, "liveportrait")


def unload_musetalk() -> int:
    from app.config.loader import config
    base = config.get("musetalk.base_url", "http://127.0.0.1:8002")
    container = config.get("musetalk.container_name", "")
    return _http_unload_or_docker_stop(base, container, "musetalk")