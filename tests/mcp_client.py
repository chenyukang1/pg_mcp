"""隔离数据库的端到端测试共用的本地 MCP 客户端。"""

import asyncio
import os
import socket
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def port_pair():
    with socket.socket() as first, socket.socket() as second:
        for sock in (first, second):
            sock.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def clean_env(**settings):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("PG_MCP_", "PG_TEST_", "PG_PROD_RO_", "ASSETS_"))}
    return {**base, "PYTHON_DOTENV_DISABLED": "1", **settings}


@asynccontextmanager
async def serving(env, *ports, stderr=None):
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "pg_mcp", env=env, stderr=stderr)
    try:
        async with asyncio.timeout(15):
            for port in ports:
                while process.returncode is None:
                    try:
                        _, writer = await asyncio.open_connection("127.0.0.1", port)
                    except ConnectionRefusedError:
                        await asyncio.sleep(0.05)
                        continue
                    writer.close()
                    await writer.wait_closed()
                    break
                else:
                    raise RuntimeError(f"MCP 监听前退出，状态码 {process.returncode}")
        yield
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), timeout=15)


@asynccontextmanager
async def session_at(port):
    async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            yield session
