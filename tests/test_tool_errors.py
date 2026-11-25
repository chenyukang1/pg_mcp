import json
import os
import unittest

from mcp_client import clean_env, port_pair, serving, session_at


@unittest.skipUnless(os.getenv("PG_MCP_CATALOG_FIXTURE") == "1", "需要临时元数据样本")
class AgentErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_wire_errors_are_actionable_without_echoing_inputs(self):
        port, _ = port_pair()
        env = clean_env(ASSETS_DATABASE_URL=os.environ["PG_MCP_TEST_DSN"], PG_MCP_PORT=str(port))
        async with serving(env, port), session_at(port) as session:
            for args, code in [
                ({"sql": "SELECT private_marker FROM pg_catalog.pg_type"}, "COLUMN_NOT_FOUND"),
                ({"sql": "SELECT * FROM private_marker"}, "TABLE_NOT_FOUND"),
                ({"sql": "SELECT * FROM catalog_demo.hidden_wallet /*private_marker*/"}, "PERMISSION_DENIED"),
                ({"sql": "SELECT 1", "limit": "private_marker"}, "INVALID_ARGUMENT"),
                ({"sql": "DELETE FROM private_marker"}, "READ_ONLY_REQUIRED"),
            ]:
                with self.subTest(code=code):
                    result = await session.call_tool("query", args)
                    self.assertTrue(result.isError)
                    self.assertEqual(json.loads(result.content[0].text), result.structuredContent)
                    error = result.structuredContent["error"]
                    self.assertEqual(error["code"], code)
                    self.assertTrue(error["next_action"])
                    self.assertFalse(error["retryable"])
                    self.assertNotIn("private_marker", result.content[0].text)
