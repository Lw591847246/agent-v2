"""GPU 资源池锁：同资源复用、异资源卸载、全局串行。"""
from __future__ import annotations

import subprocess
import threading
import time
from contextlib import contextmanager
from enum import Enum

import requests

from app.core.logger import logger


class GPUResource(str, Enum):
    NONE = "none"
    OLLAMA_CHAT = "ollama_chat"
    OLLAMA_CODE = "ollama_code"
    OLLAMA_VISION = "ollama_vision"
    OLLAMA_EMBEDDING = "ollama_embedding"
    COMFYUI = "comfyui"
    INDEXTTS2 = "indextts2"
    MUSETALK = "musetalk"
    LIVEPORTRAIT = "liveportrait"


def _map_task_name(task_name: str) -> GPUResource:
    name = (task_name or "").lower()

    if "llm-intent" in name or name in ("intent", "ollama_intent", "router"):
        return GPUResource.OLLAMA_CHAT
    if "llm-chat" in name or name in ("chat", "ollama_chat", "model_manager"):
        return GPUResource.OLLAMA_CHAT
    if "llm-code" in name or name in ("code", "ollama_code", "coder"):
        return GPUResource.OLLAMA_CODE
    if "llm-vision" in name or name in ("vision", "ollama_vision", "vl"):
        return GPUResource.OLLAMA_VISION
    if "llm-embedding" in name or name in ("embedding", "bge"):
        return GPUResource.OLLAMA_EMBEDDING

    if any(kw in name for kw in (
        "comfyui", "comfy", "i2v", "t2v", "faceswap", "face_swap",
        "image_gen", "image_gen_client", "qwen_image",
    )):
        return GPUResource.COMFYUI

    if "tts" in name or "indextts" in name:
        return GPUResource.INDEXTTS2

    if "musetalk" in name or "lipsync" in name or "lip_sync" in name:
        return GPUResource.MUSETALK

    if "liveportrait" in name or "anchor" in name or "portrait" in name:
        return GPUResource.LIVEPORTRAIT

    logger.warning(f"[GPU锁] 未知 task_name='{task_name}'，默认按 ComfyUI 处理")
    return GPUResource.COMFYUI


def _get_gpu_used_mb() -> int:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return int(r.stdout.strip().split()[0])
    except Exception:
        pass
    return -1


def _get_gpu_total_mb() -> int:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return int(r.stdout.strip().split()[0])
    except Exception:
        pass
    return 0


def _log_gpu_status(label: str):
    used = _get_gpu_used_mb()
    if used >= 0:
        total = _get_gpu_total_mb()
        if total > 0:
            pct = int(used / total * 100)
            logger.info(f"🔍 [显存] {label}: {used}MB / {total}MB ({pct}%)")
        else:
            logger.info(f"🔍 [显存] {label}: {used}MB")


def _unload_ollama():
    from app.config.loader import config
    logger.info("[GPU锁] 卸载 Ollama 模型...")
    try:
        models = config.get("ollama.models", {})
        unique_models = set(models.values())
        for name in unique_models:
            try:
                requests.post(
                    "http://localhost:11434/api/generate",
                    json={"model": name, "keep_alive": 0, "prompt": "", "stream": False},
                    timeout=15,
                )
            except Exception:
                pass
        time.sleep(3)
        _log_gpu_status("Ollama 卸载后")
    except Exception as e:
        logger.warning(f"[GPU锁] Ollama 卸载异常: {e}")


def _unload_comfyui():
    try:
        r = requests.post(
            "http://127.0.0.1:8188/free",
            json={"unload_models": True, "free_memory": True},
            timeout=15,
        )
        if r.status_code == 200:
            logger.info("[GPU锁] ✅ ComfyUI 已释放")
        time.sleep(2)
        _log_gpu_status("ComfyUI 释放后")
    except Exception as e:
        logger.warning(f"[GPU锁] ComfyUI 释放失败: {e}")


def _unload_tts():
    from app.config.loader import config
    logger.info("[GPU锁] 卸载 TTS 引擎...")
    engine = (config.get("tts.engine", "indextts2") or "indextts2").lower()
    if engine == "cosyvoice2":
        base = config.get("tts.cosyvoice2.base_url", "http://127.0.0.1:9011")
    else:
        base = config.get("tts.indextts2.base_url", "http://127.0.0.1:9010")
    base = base.rstrip("/")
    for url in (f"{base}/unload", f"{base}/release", f"{base}/free"):
        try:
            r = requests.post(url, timeout=5)
            if r.status_code == 200:
                logger.info(f"[GPU锁] ✅ TTS 已释放: {url}")
                time.sleep(2)
                _log_gpu_status("TTS 卸载后")
                return
        except Exception:
            pass
    logger.warning(f"[GPU锁] TTS 卸载全部失败: {base}")


def _unload_resource(resource: GPUResource):
    if resource == GPUResource.NONE:
        return
    if resource.value.startswith("ollama_"):
        _unload_ollama()
    elif resource == GPUResource.COMFYUI:
        _unload_comfyui()
    elif resource == GPUResource.INDEXTTS2:
        _unload_tts()
    elif resource in (GPUResource.MUSETALK, GPUResource.LIVEPORTRAIT):
        time.sleep(1)


class GlobalGPULock:
    """全局 GPU 资源锁：同资源复用，异资源卸载。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._current: GPUResource = GPUResource.NONE
        self._current_since: float = 0.0
        self._current_task: str = ""

    def current(self) -> GPUResource:
        return self._current

    @contextmanager
    def _acquire_internal(self, resource: GPUResource, task_name: str, timeout: int):
        _log_gpu_status(f"请求前 (task={task_name})")
        logger.info(f"【GPU调度】任务 [{task_name}] 等待占用 GPU...")

        acquire_start = time.time()
        acquired = self._lock.acquire(timeout=timeout)
        wait_ms = int((time.time() - acquire_start) * 1000)

        if not acquired:
            logger.error(f"【GPU调度】任务 [{task_name}] 等待超时 ({timeout}s)")
            raise TimeoutError(f"GPU 资源繁忙，任务 [{task_name}] 获取执行权超时。")

        if wait_ms > 100:
            logger.info(f"【GPU调度】任务 [{task_name}] 等待 {wait_ms}ms 后获得锁")

        try:
            if self._current == resource:
                logger.info(
                    f"【GPU调度】任务 [{task_name}] 复用资源 [{resource.value}]（无需切换）"
                )
                _log_gpu_status(f"复用 {resource.value}")
            else:
                logger.info(
                    f"【GPU调度】任务 [{task_name}] 切换资源: "
                    f"{self._current.value} → {resource.value}"
                )
                _log_gpu_status(f"切换前 {self._current.value}")
                _unload_resource(self._current)
                self._current = resource
                self._current_since = time.time()
                _log_gpu_status(f"切换后 {resource.value}")

            self._current_task = task_name
            logger.info(f"【GPU调度】任务 [{task_name}] 开始执行。")
            yield
        finally:
            logger.info(f"【GPU调度】任务 [{task_name}] 执行完毕，释放 GPU。")
            self._current_task = ""
            self._lock.release()
            _log_gpu_status(f"释放后 (task={task_name})")

    @contextmanager
    def acquire(self, task_name: str = "default", timeout: int = 300):
        resource = _map_task_name(task_name)
        with self._acquire_internal(resource, task_name, timeout):
            yield

    @contextmanager
    def use(self, resource: GPUResource, timeout: int = 300):
        task_name = f"resource:{resource.value}"
        with self._acquire_internal(resource, task_name, timeout):
            yield


gpu_lock = GlobalGPULock()