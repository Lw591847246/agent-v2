# app/skills/code/workspace/queue.py
from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app.core.logger import logger


class JobQueue(ABC):
    @abstractmethod
    def submit(self, fn: Callable, *args, **kwargs) -> str:
        ...


class InProcessJobQueue(JobQueue):
    """本地实现：进程内线程池。集群可换 Redis Stream / MQ，不改调用方。"""

    def __init__(self, max_workers: int = 1):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="code-scan")
        self._lock = threading.Lock()

    def submit(self, fn, *args, **kwargs) -> str:
        job_id = uuid.uuid4().hex
        def _run():
            try:
                fn(job_id, *args, **kwargs)
            except Exception as e:
                logger.error(f"[CodeQueue] job={job_id} 失败: {e}", exc_info=True)
        self._executor.submit(_run)
        return job_id


job_queue: JobQueue = InProcessJobQueue(max_workers=1)