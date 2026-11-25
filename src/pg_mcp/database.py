"""查询和预检使用相同的只读事务与连接超时。"""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
import math
import time

import psycopg

from .limits import current_deadline


@contextmanager
def connect_readonly(dsn: str) -> Iterator[psycopg.Connection]:
    deadline = current_deadline.get()
    statement_ms, connect_seconds = 3000, 5
    if deadline:
        deadline.check()
        remaining = max(1, math.ceil((deadline.end - time.monotonic()) * 1000))
        statement_ms = min(statement_ms, remaining)
        connect_seconds = min(connect_seconds, math.ceil(remaining / 1000))
    with psycopg.connect(
        dsn, connect_timeout=connect_seconds,
        options=f"-c statement_timeout={statement_ms} -c lock_timeout=1000 -c standard_conforming_strings=on",
    ) as conn:
        conn.read_only = True
        with deadline.track(conn) if deadline else nullcontext():
            yield conn
