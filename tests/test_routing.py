import asyncio
import os
import subprocess
import sys
import unittest

from mcp_client import clean_env, port_pair, serving, session_at
from pg_mcp.database import connect_readonly
from pg_mcp.identity import IDENTITY_SQL


@unittest.skipUnless(os.getenv("PG_MCP_TEST_DSN") and os.getenv("PG_MCP_TEST_SECOND_DSN"),
                     "需要两个不同的独立测试库")
class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoints_query_expected_databases_and_refuse_swapped_dsns(self):
        ports = port_pair()
        dsns = [os.environ["PG_MCP_TEST_DSN"], os.environ["PG_MCP_TEST_SECOND_DSN"]]
        env = clean_env()
        identities = []
        for prefix, port, dsn in zip(("PG_TEST", "PG_PROD_RO"), ports, dsns):
            with connect_readonly(dsn) as conn:
                db, user, readonly = conn.execute(IDENTITY_SQL).fetchone()
            identities.append([db, user, readonly])
            env.update({f"{prefix}_MCP_PORT": str(port), f"{prefix}_DATABASE_URL": dsn,
                        f"{prefix}_EXPECTED_DATABASE": db, f"{prefix}_EXPECTED_USER": user})
        self.assertNotEqual(identities[0][0], identities[1][0], "两个测试目标必须是不同库名")
        async with serving(env, *ports):
            for port, identity in zip(ports, identities):
                async with session_at(port) as session:
                    result = await session.call_tool("query", {"sql": IDENTITY_SQL})
                    self.assertFalse(result.isError)
                    self.assertEqual(result.structuredContent["rows"], [identity])

        env["PG_TEST_DATABASE_URL"], env["PG_PROD_RO_DATABASE_URL"] = reversed(dsns)
        for args in (["--check"], []):
            run = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "pg_mcp", *args],
                                          env=env, capture_output=True, timeout=15)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn(b"IDENTITY_MISMATCH", run.stdout + run.stderr)
        for port in ports:
            with self.assertRaises(ConnectionRefusedError):
                await asyncio.open_connection("127.0.0.1", port)
