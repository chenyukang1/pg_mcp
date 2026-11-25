import asyncio
import os
import threading
import unittest
from contextlib import suppress
from unittest.mock import patch

import psycopg
from psycopg.conninfo import make_conninfo

from mcp_client import clean_env, port_pair, serving, session_at
from pg_mcp.errors import Issue, ToolFailure
from pg_mcp.limits import Governor, Limits


class LimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_keeps_slot_until_blocking_work_exits(self):
        governor = Governor(Limits(1, 5000))
        entered, finish = threading.Event(), threading.Event()

        def blocking():
            entered.set()
            finish.wait(5)

        task = asyncio.create_task(governor.run(blocking))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            with self.assertRaises(ToolFailure) as failure:
                await governor.run(lambda: 1)
            self.assertIs(failure.exception.issue, Issue.BUSY)
        finally:
            finish.set()

    def test_limits_are_validated_per_profile(self):
        with patch.dict(os.environ, {"PG_TEST_QUERY_TIMEOUT_MS": "80", "PG_PROD_RO_MAX_CONCURRENT": "0"}):
            self.assertEqual(Limits.from_env("PG_TEST").timeout_ms, 80)
            with self.assertRaisesRegex(ValueError, "PG_PROD_RO_MAX_CONCURRENT"):
                Limits.from_env("PG_PROD_RO")


@unittest.skipUnless(os.getenv("PG_MCP_TEST_DSN"), "需要独立测试库")
class HttpLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_and_total_deadline_across_short_fetches(self):
        dsn = os.environ["PG_MCP_TEST_DSN"]
        port, _ = port_pair()
        marker = f"mcp_budget_{port}"
        env = clean_env(ASSETS_DATABASE_URL=make_conninfo(dsn, application_name=marker),
                        PG_MCP_PORT=str(port), PG_MCP_MAX_CONCURRENT="1", PG_MCP_QUERY_TIMEOUT_MS="900")
        async with serving(env, port), session_at(port) as a, session_at(port) as b:
            # 每次 FETCH 都低于单语句超时，但取完整个游标会明显超过总预算。
            slow = asyncio.create_task(a.call_tool("query", {
                "sql": "SELECT i, pg_catalog.pg_sleep(0.1) FROM generate_series(1,60) i"
            }))
            async with asyncio.timeout(4):
                async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as observer:
                    while True:
                        cur = await observer.execute(
                            "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                            "WHERE application_name=%s AND wait_event='PgSleep'", (marker,))
                        if (await cur.fetchone())[0]:
                            break
                        await asyncio.sleep(0.02)
                busy = await b.call_tool("list_tables", {"limit": 1})
                self.assertFalse(slow.done())
                self.assertEqual(busy.structuredContent["error"]["code"], "BUSY")
                self.assertTrue(busy.structuredContent["error"]["retryable"])
                self.assertEqual((await slow).structuredContent["error"]["code"], "QUERY_TIMEOUT")
                while True:
                    ready = await b.call_tool("query", {"sql": "SELECT 1"})
                    if not ready.isError:
                        break
                    self.assertEqual(ready.structuredContent["error"]["code"], "BUSY")
                    await asyncio.sleep(0.02)
                self.assertEqual(ready.structuredContent["rows"], [[1]])
