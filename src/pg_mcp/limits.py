"""每个 MCP 进程独立限流；超时响应后，数据库工作真正结束才归还名额。"""

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import psycopg

from .errors import Issue, ToolFailure


@dataclass(frozen=True)
class Limits:
    concurrency: int = 8
    timeout_ms: int = 10_000

    @classmethod
    def from_env(cls, prefix: str) -> Limits:
        prod = prefix == "PG_PROD_RO"
        return cls(_integer(f"{prefix}_MAX_CONCURRENT", 2 if prod else 8, 1, 32),
                   _integer(f"{prefix}_QUERY_TIMEOUT_MS", 5000 if prod else 10000, 1, 120000))


def _integer(key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(key, str(default)))
        if low <= value <= high:
            return value
    except ValueError:
        pass
    raise ValueError(f"{key} 必须是 {low} 到 {high} 之间的整数")


class Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._conn: psycopg.Connection | None = None

    def check(self):
        if self._cancelled.is_set() or time.monotonic() >= self.end:
            raise ToolFailure(Issue.QUERY_TIMEOUT)

    @contextmanager
    def track(self, conn: psycopg.Connection) -> Iterator[None]:
        with self._lock:
            self.check()
            self._conn = conn
        try:
            yield
            self.check()
        finally:
            with self._lock:
                self._conn = None

    def cancel(self):
        with self._lock:
            self._cancelled.set()
            conn = self._conn
        if conn is not None:
            # 专用连接不会归池；取消动作不可阻塞事件循环或排在饱和的查询线程池后面。
            threading.Thread(target=_cancel_connection, args=(conn,), daemon=True).start()


def _cancel_connection(conn: psycopg.Connection):
    try:
        conn.cancel_safe(timeout=1)
    except psycopg.Error:
        logging.getLogger(__name__).debug("未确认取消；连接仍受数据库语句超时限制")


current_deadline: ContextVar[Deadline | None] = ContextVar("pg_mcp_deadline", default=None)


class Governor:
    def __init__(self, limits: Limits):
        self.limits = limits
        self._slots = threading.BoundedSemaphore(limits.concurrency)

    async def run(self, fn: Callable, *args, **kwargs):
        if not self._slots.acquire(blocking=False):
            raise ToolFailure(Issue.BUSY)
        budget = Deadline(self.limits.timeout_ms / 1000)

        def work():
            token = current_deadline.set(budget)
            try:
                budget.check()
                result = fn(*args, **kwargs)
                budget.check()
                return result
            finally:
                current_deadline.reset(token)
                self._slots.release()

        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(None, work)
        except RuntimeError:
            self._slots.release()
            raise
        # 客户端断开或先收到超时结果时，后台任务最终的异常也会被回收。
        future.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        try:
            done, _ = await asyncio.wait({future}, timeout=max(0.0, budget.end - time.monotonic()))
            if not done:
                budget.cancel()
                raise ToolFailure(Issue.QUERY_TIMEOUT)
            return future.result()
        except asyncio.CancelledError:
            budget.cancel()
            raise
