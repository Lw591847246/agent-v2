"""GPU 资源池锁：全局串行 + 同资源复用 + 异资源卸载。

特性：
- threading.Semaphore(1)：硬串行，无重入漏洞
- ContextVar 嵌套识别：sync / async 同一协程树内嵌套自动复用外层租约
- async_lease：给 async 函数用，`await` 期间不阻塞事件循环
- LeaseTicket：可跨线程释放（流式响应场景）
- 切换资源前强制卸载旧资源，并校验显存实际下降
- purge_all / stats：启动清场 + 观测

修复：_map_task_name 未知任务不再默认按 COMFYUI 处理（原逻辑会误卸载
正在被 Ollama 占用的显存去给没用的 ComfyUI 腾地方），改为显式抛错，
让上层明确拒绝，避免无谓的显存/内存抖动与重新加载延迟。
"""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Dict, List, Optional, Union

from app.core.gpu_unloaders import (
    gpu_total_mb,
    gpu_used_mb,
    log_gpu,
    unload_comfyui,
    unload_liveportrait,
    unload_musetalk,
    unload_ollama,
    unload_tts,
)
from app.core.logger import logger


class GPUResource(str, Enum):
    NONE = "none"
    OLLAMA_CHAT = "ollama_chat"
    OLLAMA_CODE = "ollama_code"
    OLLAMA_VISION = "ollama_vision"
    OLLAMA_EMBEDDING = "ollama_embedding"
    COMFYUI = "comfyui"
    INDEXTTS2 = "indextts2"
    COSYVOICE2 = "cosyvoice2"
    MUSETALK = "musetalk"
    LIVEPORTRAIT = "liveportrait"


DEFAULT_TIMEOUTS: Dict[GPUResource, float] = {
    GPUResource.OLLAMA_CHAT: 60,
    GPUResource.OLLAMA_CODE: 180,
    GPUResource.OLLAMA_VISION: 180,
    GPUResource.OLLAMA_EMBEDDING: 30,
    GPUResource.COMFYUI: 3600,          # 对齐 services.text_to_video.poll_timeout
    GPUResource.INDEXTTS2: 6000,        # 对齐 yml tts.indextts2.timeout
    GPUResource.COSYVOICE2: 6000,       # 对齐 yml tts.cosyvoice2.timeout
    GPUResource.MUSETALK: 1800,
    GPUResource.LIVEPORTRAIT: 1800,
}


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

    if "cosyvoice" in name:
        return GPUResource.COSYVOICE2
    if "tts" in name or "indextts" in name:
        return GPUResource.INDEXTTS2

    if any(kw in name for kw in (
        "comfyui", "comfy", "i2v", "t2v", "faceswap", "face_swap",
        "image_gen", "image_gen_client", "qwen_image",
    )):
        return GPUResource.COMFYUI
    if "musetalk" in name or "lipsync" in name or "lip_sync" in name:
        return GPUResource.MUSETALK
    if "liveportrait" in name or "anchor" in name or "portrait" in name:
        return GPUResource.LIVEPORTRAIT

    # 修复：未知 task_name 不再默认按 COMFYUI 处理。
    # 原兜底会让 Ollama 正在用的显存被错误卸载去给没用的 ComfyUI 腾地方，
    # 造成无谓的显存/内存抖动与模型重新加载延迟。改为显式抛错，让上层拒绝执行。
    logger.error(f"[GPU锁] 未知 task_name='{task_name}'，拒绝执行以防误卸载在用资源")
    raise ValueError(f"未知 GPU 任务类型: {task_name}")


# 嵌套识别：sync / async 共用，嵌套标记随协程/线程作用域自动传播
_root_ticket: ContextVar[Optional["LeaseTicket"]] = ContextVar(
    "gpu_lock_root_ticket", default=None
)


class LeaseTicket:
    """一次 GPU 租约凭证。根 ticket 持有信号量；子 ticket 仅是嵌套标记。

    - release() 可在任意线程/协程调用（流式响应场景）
    - 同一 ticket 只能 release 一次
    """

    __slots__ = ("lock", "resource", "task", "is_root", "acquired_at",
                 "wait_ms", "released", "_mu", "_ctx_token")

    def __init__(self, lock: "GlobalGPULock", resource: GPUResource,
                 task: str, is_root: bool, wait_ms: int = 0):
        self.lock = lock
        self.resource = resource
        self.task = task
        self.is_root = is_root
        self.acquired_at = time.time()
        self.wait_ms = wait_ms
        self.released = False
        self._mu = threading.Lock()
        self._ctx_token = None

    def release(self):
        with self._mu:
            if self.released:
                return
            self.released = True
        self.lock._on_release(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class GlobalGPULock:

    def __init__(self):
        self._sem = threading.Semaphore(1)
        self._state = threading.Lock()
        self._current: GPUResource = GPUResource.NONE
        self._current_task = ""
        self._current_since = 0.0
        self._waiting: List[str] = []
        self._waiting_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._stats = {
            "acquire_count": 0,
            "wait_total_ms": 0,
            "switch_count": 0,
            "unload_total_ms": 0,
        }

    # ---------- 观测 ----------

    def current(self) -> GPUResource:
        return self._current

    def stats(self) -> Dict:
        with self._waiting_lock:
            waiting = list(self._waiting)
        with self._state:
            cur = self._current.value
            cur_task = self._current_task
            held_ms = int((time.time() - self._current_since) * 1000) if self._current_since else 0
        with self._stats_lock:
            s = dict(self._stats)
        return {
            "current": cur,
            "current_task": cur_task,
            "held_ms": held_ms,
            "waiting": waiting,
            "queue_depth": len(waiting),
            "gpu_used_mb": gpu_used_mb(),
            "gpu_total_mb": gpu_total_mb(),
            **s,
        }

    # ---------- 核心 ----------

    def acquire(self, resource: Union[GPUResource, str, None] = None,
                task: str = "default",
                timeout: Optional[float] = None,
                task_name: Optional[str] = None) -> LeaseTicket:
        """获取租约（同步、阻塞直到成功或超时）。

        兼容写法：
          gpu_lock.acquire("LLM-intent")             # 旧式
          gpu_lock.acquire(task_name="LLM-intent")   # 旧式 keyword
          gpu_lock.acquire(GPUResource.COMFYUI, task="i2v", timeout=1800)
        """
        if resource is None:
            resource = task_name or task
        if isinstance(resource, str):
            resource = _map_task_name(resource)
        if task_name and task == "default":
            task = task_name
        if timeout is None:
            timeout = DEFAULT_TIMEOUTS.get(resource, 300)

        # 1) 同协程/线程嵌套 -> 复用外层租约
        parent = _root_ticket.get()
        if parent is not None and not parent.released:
            if parent.resource != resource:
                logger.warning(
                    f"【GPU调度】嵌套租约资源不一致 外层={parent.resource.value} "
                    f"内层={resource.value} task={task}，沿用外层"
                )
            return LeaseTicket(self, resource, task, is_root=False)

        # 2) 排队
        with self._waiting_lock:
            self._waiting.append(task)
            pos = len(self._waiting) - 1

        log_gpu(f"请求前 (task={task})")
        if pos > 0:
            logger.info(f"【GPU调度】任务 [{task}] 排队，前方 {pos} 个")
        else:
            logger.info(f"【GPU调度】任务 [{task}] 等待占用 GPU...")

        t0 = time.time()
        acquired = self._sem.acquire(timeout=timeout)
        wait_ms = int((time.time() - t0) * 1000)

        with self._waiting_lock:
            try:
                self._waiting.remove(task)
            except ValueError:
                pass

        if not acquired:
            logger.error(f"【GPU调度】任务 [{task}] 等待超时 {timeout}s")
            raise TimeoutError(
                f"GPU 资源繁忙，任务 [{task}] 获取执行权超时 {timeout}s"
            )

        if wait_ms > 100:
            logger.info(f"【GPU调度】任务 [{task}] 等待 {wait_ms}ms 后获得锁")

        # 3) 资源切换
        try:
            self._switch_to(resource, task)
        except Exception:
            self._sem.release()
            raise

        ticket = LeaseTicket(self, resource, task, is_root=True, wait_ms=wait_ms)
        ticket._ctx_token = _root_ticket.set(ticket)

        with self._stats_lock:
            self._stats["acquire_count"] += 1
            self._stats["wait_total_ms"] += wait_ms

        logger.info(f"【GPU调度】任务 [{task}] 开始执行 (resource={resource.value})")
        return ticket

    def _switch_to(self, resource: GPUResource, task: str):
        with self._state:
            old = self._current
            if old == resource:
                self._current_task = task
                logger.info(f"【GPU调度】复用资源 [{resource.value}]")
                log_gpu(f"复用 {resource.value}")
                return

            logger.info(f"【GPU调度】切换资源: {old.value} -> {resource.value}")
            log_gpu(f"切换前 {old.value}")
            t0 = time.time()
            freed = self._unload(old)
            cost_ms = int((time.time() - t0) * 1000)

            self._current = resource
            self._current_task = task
            self._current_since = time.time()

            with self._stats_lock:
                self._stats["switch_count"] += 1
                self._stats["unload_total_ms"] += cost_ms

            log_gpu(f"切换后 {resource.value}")
            logger.info(
                f"【GPU调度】卸载 {old.value} 释放 {freed}MB 耗时 {cost_ms}ms"
            )
            if old != GPUResource.NONE and freed < 100:
                logger.warning(
                    f"【GPU调度】卸载 {old.value} 仅释放 {freed}MB，"
                    f"可能未真正卸载（检查 unloader 配置）"
                )

    def _unload(self, resource: GPUResource) -> int:
        if resource == GPUResource.NONE:
            return 0
        if resource.value.startswith("ollama_"):
            return unload_ollama()
        if resource == GPUResource.COMFYUI:
            return unload_comfyui()
        if resource in (GPUResource.INDEXTTS2, GPUResource.COSYVOICE2):
            return unload_tts()
        if resource == GPUResource.LIVEPORTRAIT:
            return unload_liveportrait()
        if resource == GPUResource.MUSETALK:
            return unload_musetalk()
        return 0

    def _on_release(self, ticket: LeaseTicket):
        if not ticket.is_root:
            return
        try:
            if ticket._ctx_token is not None:
                _root_ticket.reset(ticket._ctx_token)
        except Exception:
            pass
        with self._state:
            if self._current_task == ticket.task:
                self._current_task = ""
        self._sem.release()
        held_ms = int((time.time() - ticket.acquired_at) * 1000)
        logger.info(
            f"【GPU调度】任务 [{ticket.task}] 执行完毕，释放 GPU (持锁 {held_ms}ms)"
        )
        log_gpu(f"释放后 (task={ticket.task})")

    # ---------- 同步便捷 API ----------

    @contextmanager
    def lease(self, resource: Union[GPUResource, str, None] = None,
              task: str = "default",
              timeout: Optional[float] = None,
              task_name: Optional[str] = None):
        ticket = self.acquire(resource, task=task, timeout=timeout, task_name=task_name)
        try:
            yield ticket
        finally:
            ticket.release()

    @contextmanager
    def use(self, resource: GPUResource, timeout: int = 300):
        with self.lease(resource, task=f"resource:{resource.value}", timeout=timeout):
            yield

    # ---------- 异步便捷 API（给 async def 用，不阻塞事件循环） ----------

    @asynccontextmanager
    async def async_lease(self, resource: Union[GPUResource, str, None] = None,
                          task: str = "default",
                          timeout: Optional[float] = None,
                          task_name: Optional[str] = None):
        ticket = await asyncio.to_thread(
            self.acquire, resource, task, timeout, task_name,
        )
        try:
            yield ticket
        finally:
            ticket.release()

    # ---------- 启动/关闭清场 ----------

    def purge_all(self, exclude: Optional[List[GPUResource]] = None):
        from app.config.loader import config

        if not config.get("gpu.purge_on_start", True):
            logger.info("【GPU调度】purge_on_start=false，跳过启动清场")
            return

        exclude = set(exclude or [])
        logger.info("【GPU调度】启动清场开始...")
        log_gpu("清场前")
        for r in (GPUResource.OLLAMA_CHAT, GPUResource.COMFYUI,
                  GPUResource.INDEXTTS2, GPUResource.COSYVOICE2,
                  GPUResource.LIVEPORTRAIT, GPUResource.MUSETALK):
            if r in exclude:
                continue
            try:
                freed = self._unload(r)
                if freed > 0:
                    logger.info(f"【GPU调度】清场 {r.value} 释放 {freed}MB")
            except Exception as e:
                logger.warning(f"【GPU调度】清场 {r.value} 异常: {e}")
        with self._state:
            self._current = GPUResource.NONE
        log_gpu("清场后")


gpu_lock = GlobalGPULock()
