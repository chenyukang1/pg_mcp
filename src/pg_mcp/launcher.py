"""为各数据库环境启动独立的 HTTP 工作进程。"""

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field

from .identity import ExpectedIdentity

PROFILES = (("pg-test", "PG_TEST", 8765), ("pg-prod-ro", "PG_PROD_RO", 8766))


@dataclass(frozen=True)
class Profile:
    name: str
    port: int
    dsn: str = field(repr=False)
    identity: ExpectedIdentity

    def worker_env(self) -> dict[str, str]:
        # 不继承其他环境的 DSN、PGPASSWORD 或任意父进程凭据。
        shared = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
                  "SYSTEMROOT", "USERPROFILE", "APPDATA", "ASSETS_PG_SCHEMA",
                  "PGPASSFILE", "PGSSLROOTCERT", "PGSERVICEFILE")
        env = {key: os.environ[key] for key in shared if key in os.environ}
        return {**env, "ASSETS_DATABASE_URL": self.dsn, "PG_MCP_PORT": str(self.port),
                "PG_MCP_EXPECTED_DATABASE": self.identity.database,
                "PG_MCP_EXPECTED_USER": self.identity.user,
                "PYTHON_DOTENV_DISABLED": "1"}


def read_port(name: str, default: int) -> int:
    try:
        port = int(os.environ.get(name, default))
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} 必须是 1 到 65535 之间的整数")
    return port


def configured_profiles() -> list[Profile]:
    profiles = []
    for name, prefix, default in PROFILES:
        dsn = os.environ.get(f"{prefix}_DATABASE_URL", "").strip()
        if dsn:
            identity = ExpectedIdentity.from_env(prefix, required=True)
            profiles.append(Profile(name, read_port(f"{prefix}_MCP_PORT", default), dsn, identity))
    if len({profile.port for profile in profiles}) != len(profiles):
        raise ValueError("PG_TEST_MCP_PORT 和 PG_PROD_RO_MCP_PORT 不能相同")
    return profiles


def _stop_on_term(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def run_profiles(profiles: list[Profile]) -> int:
    """任一工作进程退出时停止其他进程，避免留下部分服务。"""
    children = []
    previous_handler = signal.signal(signal.SIGTERM, _stop_on_term)
    try:
        for profile in profiles:
            children.append(
                subprocess.Popen(
                    [sys.executable, "-m", "pg_mcp", "--single"],
                    env=profile.worker_env(),
                    start_new_session=True,
                )
            )
        while True:
            for child in children:
                if (code := child.poll()) is not None:
                    return code if code >= 0 else 128 - code
            time.sleep(0.1)
    except KeyboardInterrupt:
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
