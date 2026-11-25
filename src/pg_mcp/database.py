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


def error_hint(error: psycopg.Error) -> str:
    """libpq 在连接阶段常不提供 SQLSTATE。只返回固定提示，不回显匹配文本。"""
    text = str(error).lower()
    if "no password supplied" in text:
        return "未提供密码，请检查 .pgpass 的匹配项和权限"
    if error.sqlstate == "28P01" or "password authentication failed" in text:
        return "密码认证失败"
    if any(term in text for term in (
        "could not translate host", "failed to resolve host", "name or service not known",
        "nodename nor servname", "no route to host", "network is unreachable",
        "connection refused", "timeout expired", "connection timeout",
    )):
        return "数据库主机或端口不可达，请检查地址和网络"
    return "请检查连接设置与账号权限"
