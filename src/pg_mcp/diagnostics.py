"""启动前检查本地监听端口和数据库身份，不运行 MCP 服务。"""

import json
import socket
import subprocess
import sys

import psycopg

from .database import connect_readonly, error_hint
from .launcher import Profile


def check_single(port: int, dsn: str) -> bool:
    available = True
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
            sock.listen()
    except OSError:
        available = False
    print(f"127.0.0.1:{port}：" + ("可监听" if available else "无法监听（可能已占用）"))
    if not dsn.strip():
        print("数据库：未配置连接串")
        return False
    try:
        with connect_readonly(dsn) as conn:
            db, user, readonly = conn.execute(
                "SELECT current_database(), current_user, current_setting('transaction_read_only')"
            ).fetchone()
    except psycopg.Error as exc:
        print("数据库：" + error_hint(exc))
        return False
    print(json.dumps({"database": db, "user": user, "transaction_read_only": readonly}))
    return available and readonly == "on"


def check_profiles(profiles: list[Profile]) -> bool:
    ok = True
    for profile in profiles:
        print(f"[{profile.name}]", flush=True)
        # 用与实际 worker 相同的环境执行，不继承父进程的其他 libpq 凭据。
        result = subprocess.run(
            [sys.executable, "-m", "pg_mcp", "--single", "--check"], env=profile.worker_env()
        )
        if result.returncode:
            ok = False
    return ok
