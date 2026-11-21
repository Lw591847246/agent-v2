"""GPU 资源池锁：全局串行 + 同资源复用 + 异资源卸载 + OpenTelemetry 链路埋点。

特性：
- threading.Semaphore(1)：硬串行，无重入漏洞
- ContextVar 嵌套识别：sync / async 同一协程树内嵌套自动复用外层租约
- async_lease：给 async 函数用，await 期间不阻塞事件循环
- LeaseTicket：可跨线程释放（流式响应场景）
- 切换资源前强制卸载旧资源，并校验显存实际下降
- purge_all / stats：启动清场 + 观测
- OpenTelemetry：每个租约产出一条 gpu.lease span（wait_ms / hold_ms / resource），
  未安装 opentelemetry 时整体降级为 no-op，绝不拖垮启动。

本版相对上一版的修复：
1) _switch_to 把耗时的卸载（HTTP + _wait_freed 轮询）移出 self._state 临界区，
   避免一个任务切换/卸载时阻塞无关任务的 release() 与 stats()。
2) async_lease 的嵌套标记改为在协程 context 内 set/reset（原先在
   asyncio.to_thread 子线程的复制 context 内 set，不回写协程 context，
   导致同协程嵌套 async_lease 取不到外层标记 -> 重复抢锁 -> 自死锁）。
   同步 lease 仍在调用线程 context 内 set，行为不变、向后兼容裸 acquire()。
3) _waiting 排队列表用唯一 uid（任务名+线程+纳秒）登记，修复同名任务并发时
   stats().waiting 错位。
"""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager, nullcontext
from contextvars import ContextVar
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

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

# ---------------- OpenTelemetry（可选，缺依赖自动 no-op） ----------------
try:
    from opentelemetry import trace as _otel_trace
    _OTEL = True
except Exception:
    _OTEL = False


class _NoopSpan:
    def set_attribute(self, *a, **k):
        pass

    def set_status(self, *a, **k):
        pass


class _NoopTracer:
    def start_as_current_span(self, name, attributes=None, **kw):
        return nullcontext(_NoopSpan())


_TRACER = None


def _tracer():
    """惰性获取 tracer；OTel 未安装则返回 no-op tracer。"""
    global _TRACER
    if _TRACER is None:
        _TRACER = _otel_trace.get_tracer("agent.gpu_lock") if _OTEL else _NoopTracer()
    return _TRACER


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


# 轻量后台任务：如消息摘要(summary)，本身耗时极短，却常被并行的长任务
# (文生图/文生视频/图生视频，单次持锁可达数百秒)在锁队列里饿死。
# 解法：为这类任务单独上调"锁等待超时"，取 max(上游传入值, 配置值)。
LIGHTWEIGHT_BACKGROUND_TASKS = {"summary", "summarize", "摘要", "context_summary"}
DEFAULT_LIGHTWEIGHT_WAIT_TIMEOUT = 900.0   # 秒；覆盖一条完整视频任务的排队时长


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

    # 未知 task_name 不再默认按 COMFYUI 处理，显式抛错，让上层拒绝执行。
    logger.error(f"[GPU锁] 未知 task_name='{task_name}'，拒绝执行以防误卸载在用资源")
    raise ValueError(f"未知 GPU 任务类型: {task_name}")


# 嵌套识别：sync / async 共用，嵌套标记随协程/线程作用域自动传播
_root_ticket: ContextVar[Optional["LeaseTicket"]] = ContextVar(
    "gpu_lock_root_ticket", default=None
)


class LeaseTicket:
    """一次 GPU 租约凭证。根 ticket 持有信号量；子 ticket 仅是嵌套标记。"""

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
        self._ollama_reachable: bool = True

    # ---------- 观测 ----------

    def current(self) -> GPUResource:
        return self._current

    def mark_ollama_unreachable(self):
        self._ollama_reachable = False

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

    # ---------- 参数归一 ----------

    def _norm_args(self, resource, task, timeout,
                   task_name) -> Tuple[GPUResource, str, float, bool]:
        """解析 resource/task/timeout，返回 (resource, task, timeout, is_lightweight)。"""
        if resource is None:
            resource = task_name or task
        if isinstance(resource, str):
            resource = _map_task_name(resource)
        if task_name and task == "default":
            task = task_name
        if timeout is None:
            timeout = DEFAULT_TIMEOUTS.get(resource, 300)

        _tk = (task if task and task != "default" else (task_name or "") or "").strip().lower()
        is_lightweight = _tk in LIGHTWEIGHT_BACKGROUND_TASKS
        if is_lightweight:
            from app.config.loader import config as _cfg
            lw_wait = float(_cfg.get("gpu.lightweight_wait_timeout",
                                      DEFAULT_LIGHTWEIGHT_WAIT_TIMEOUT))
            new_timeout = max(timeout or 0.0, lw_wait)
            if new_timeout != timeout:
                logger.info(
                    f"【GPU调度】轻量后台任务 [{task}] 锁等待超时由 {timeout}s "
                    f"上调至 {new_timeout}s（让位于长任务排队，避免被饿死）"
                )
            timeout = new_timeout
        return resource, task, timeout, is_lightweight

    # ---------- 核心：真正抢信号量 + 切换（不含嵌套判定与 ctx 写入） ----------

    def _acquire_root(self, resource: GPUResource, task: str,
                      timeout: float, is_lightweight: bool) -> LeaseTicket:
        uid = f"{task}:{threading.get_ident()}:{time.time_ns()}"
        with self._waiting_lock:
            self._waiting.append(uid)
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
                self._waiting.remove(uid)
            except ValueError:
                pass

        if not acquired:
            if is_lightweight:
                logger.warning(
                    f"【GPU调度】轻量后台任务 [{task}] 等待超时 {timeout}s（非关键，"
                    f"本轮摘要等增值能力降级，不影响主任务）"
                )
            else:
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

        with self._stats_lock:
            self._stats["acquire_count"] += 1
            self._stats["wait_total_ms"] += wait_ms

        logger.info(f"【GPU调度】任务 [{task}] 开始执行 (resource={resource.value})")
        return ticket

    def acquire(self, resource: Union[GPUResource, str, None] = None,
                task: str = "default",
                timeout: Optional[float] = None,
                task_name: Optional[str] = None) -> LeaseTicket:
        """获取租约（同步、阻塞直到成功或超时）。

        嵌套复用依赖调用线程的 ContextVar；非嵌套根租约的嵌套标记写入调用线程 context，
        因此裸 acquire()/sync lease 的嵌套行为与历史版本一致。异步嵌套请用 async_lease。
        """
        resource, task, timeout, is_lightweight = self._norm_args(
            resource, task, timeout, task_name)

        # 1) 同协程/线程嵌套 -> 复用外层租约
        parent = _root_ticket.get()
        if parent is not None and not parent.released:
            if parent.resource != resource:
                logger.warning(
                    f"【GPU调度】嵌套租约资源不一致 外层={parent.resource.value} "
                    f"内层={resource.value} task={task}，沿用外层"
                )
            return LeaseTicket(self, resource, task, is_root=False)

        # 2) 真正抢锁
        ticket = self._acquire_root(resource, task, timeout, is_lightweight)
        ticket._ctx_token = _root_ticket.set(ticket)
        return ticket

    @contextmanager
    def bind(self, ticket: LeaseTicket):
        """把已存在的租约 ticket 绑到当前线程 context。用于 StreamingResponse 跨线程消费。"""
        if ticket is None or ticket.released or not ticket.is_root:
            yield
            return
        tok = _root_ticket.set(ticket)
        try:
            yield
        finally:
            try:
                _root_ticket.reset(tok)
            except Exception:
                pass

    def _switch_to(self, resource: GPUResource, task: str):
        """切换当前占用资源。

        修复点：耗时的 _unload（HTTP + _wait_freed 轮询）不再放在 self._state 临界区内，
        避免一个任务切换/卸载时长时间持锁，阻塞无关任务的 release()/stats()。
        全局串行已由 _sem(1) 保证，_state 仅用于 _current 读写的原子可见。
        """
        with self._state:
            old = self._current
            if old == resource:
                self._current_task = task
                logger.info(f"【GPU调度】复用资源 [{resource.value}]")
                log_gpu(f"复用 {resource.value}")
                return
            # 占位：先把 current 指向目标 + 本任务，使 stats()/current() 立刻可见新归属；
            # 真正的耗时卸载在临界区外进行。
            self._current = resource
            self._current_task = task
            self._current_since = time.time()

        # —— 耗时卸载：在 _state 临界区之外 ——
        logger.info(f"【GPU调度】切换资源: {old.value} -> {resource.value}")
        log_gpu(f"切换前 {old.value}")
        t0 = time.time()

        try:
            freed = self._unload(old)
        except Exception:
            with self._state:
                self._current = GPUResource.NONE
                self._current_task = ""
            raise

        cost_ms = int((time.time() - t0) * 1000)

        with self._stats_lock:
            self._stats["switch_count"] += 1
            self._stats["unload_total_ms"] += cost_ms

        log_gpu(f"切换后 {resource.value}")
        logger.info(f"【GPU调度】卸载 {old.value} 释放 {freed}MB 耗时 {cost_ms}ms")
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
        """同步租约上下文，附带 gpu.lease span（wait_ms / hold_ms / resource）。"""
        tr = _tracer()
        with tr.start_as_current_span("gpu.lease", attributes={"gpu.task": task}) as span:
            ticket = self.acquire(resource, task=task, timeout=timeout, task_name=task_name)
            span.set_attribute("gpu.resource", ticket.resource.value)
            span.set_attribute("gpu.wait_ms", ticket.wait_ms)
            try:
                yield ticket
            finally:
                t_rel = time.time()
                ticket.release()
                span.set_attribute("gpu.hold_ms", int((time.time() - t_rel) * 1000))

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
        """异步租约：抢锁/切换丢线程池不阻塞事件循环；嵌套标记在协程 context 内读写。"""
        resource, task, timeout, is_lightweight = self._norm_args(
            resource, task, timeout, task_name)

        tr = _tracer()
        with tr.start_as_current_span("gpu.lease", attributes={"gpu.task": task}) as span:
            span.set_attribute("gpu.resource", resource.value)

            # 关键修复：嵌套判定与标记 set/reset 都在协程 context 内完成。
            # asyncio.to_thread 的 copy_context().run() 不会把子线程内的 set 回写到协程，
            # 所以标记绝不能在 _acquire_root 内部 set；这里在协程里 set，子线程 copy 时
            # 才能读到外层标记 -> 同协程嵌套正确复用，不再自死锁。
            parent = _root_ticket.get()
            if parent is not None and not parent.released:
                if parent.resource != resource:
                    logger.warning(
                        f"【GPU调度】嵌套租约资源不一致 外层={parent.resource.value} "
                        f"内层={resource.value} task={task}，沿用外层"
                    )
                yield LeaseTicket(self, resource, task, is_root=False)
                return

            ticket = await asyncio.to_thread(
                self._acquire_root, resource, task, timeout, is_lightweight)
            ticket._ctx_token = _root_ticket.set(ticket)
            span.set_attribute("gpu.wait_ms", ticket.wait_ms)
            try:
                yield ticket
            finally:
                t_rel = time.time()
                ticket.release()
                span.set_attribute("gpu.hold_ms", int((time.time() - t_rel) * 1000))

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
            if r.value.startswith("ollama_") and not self._ollama_reachable:
                logger.debug("[GPU调度] 启动探活已判 Ollama 不通，跳过 Ollama 卸载")
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
