"""查询和预检使用相同的只读事务与连接超时。"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg


@contextmanager
def connect_readonly(dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        dsn, connect_timeout=5,
        options="-c statement_timeout=3000 -c lock_timeout=1000 -c standard_conforming_strings=on",
    ) as conn:
        conn.read_only = True
        yield conn
