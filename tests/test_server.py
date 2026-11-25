import asyncio
import os
import socket
import sys
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp.exceptions import ToolError

from pg_mcp import server
from pg_mcp.sql_validation import validate_select


class SqlValidationTests(unittest.TestCase):
    def test_accepts_read_only_selects(self):
        validate_select("WITH items AS (SELECT 1 AS id) SELECT id FROM items;")
        validate_select("SELECT '; DELETE' AS text /* FOR UPDATE */;")

    def test_rejects_writes_and_non_select_statements(self):
        invalid_queries = (
            "DELETE FROM items",
            "CREATE TABLE items (id int)",
            "SELECT 1; SELECT 2",
            "SELECT 1 INTO new_table",
            "SELECT * FROM items FOR UPDATE",
            "SELECT * FROM (SELECT * FROM items FOR KEY SHARE) AS locked",
            "WITH removed AS (DELETE FROM items RETURNING *) SELECT * FROM removed",
            "SELECT * FROM (WITH added AS (INSERT INTO items VALUES (1) RETURNING *) "
            "SELECT * FROM added) AS nested",
            "EXPLAIN ANALYZE SELECT 1",
            "SET TRANSACTION READ WRITE",
            "SELECT 1\x00",
            "SELEC 1",
        )
        for sql in invalid_queries:
            with self.subTest(sql=sql), self.assertRaises(ToolError):
                validate_select(sql)


class QueryTests(unittest.TestCase):
    @patch.dict("os.environ", {"ASSETS_PG_SCHEMA": "reporting"})
    @patch("pg_mcp.server._query")
    def test_queries_use_configured_schema_and_bind_values(self, query):
        server.list_tables(limit=5, offset=10)
        sql, params = query.call_args.args
        self.assertIn("FROM information_schema.tables", sql)
        self.assertEqual(params, ("reporting", 5, 10))

        table = "Customer's Name"
        server.describe_table(table)
        sql, params = query.call_args.args
        self.assertIn("FROM information_schema.columns", sql)
        self.assertNotIn(table, sql)
        self.assertEqual(params, ("reporting", table, 50, 0))

    @patch.dict("os.environ", {"ASSETS_DATABASE_URL": "dbname=test"})
    @patch("pg_mcp.server.psycopg.connect")
    def test_query_uses_read_only_transaction(self, connect):
        conn = connect.return_value.__enter__.return_value
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.execute.return_value.fetchall.return_value = [{"table_name": "example"}]

        self.assertEqual(server.list_tables(), [{"table_name": "example"}])
        self.assertTrue(conn.read_only)
        self.assertIn("statement_timeout=3000", connect.call_args.kwargs["options"])

    @patch.dict("os.environ", {"ASSETS_DATABASE_URL": "dbname=test"})
    @patch("pg_mcp.server.psycopg.connect")
    def test_driver_details_are_not_exposed(self, connect):
        connect.side_effect = psycopg.OperationalError("sensitive driver details")

        with self.assertLogs("pg_mcp.server", level="WARNING") as logs:
            with self.assertRaisesRegex(ToolError, "PostgreSQL 查询失败") as error:
                server.list_tables()

        self.assertNotIn("sensitive", str(error.exception))
        self.assertNotIn("sensitive", "\n".join(logs.output))

    @patch.dict("os.environ", {"ASSETS_DATABASE_URL": "dbname=test"})
    @patch("pg_mcp.server.psycopg.connect")
    def test_query_timeout_has_a_distinct_error(self, connect):
        conn = connect.return_value.__enter__.return_value
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = (
            psycopg.errors.QueryCanceled()
        )
        with self.assertRaisesRegex(ToolError, "查询超时"):
            server.list_tables()

    @patch.dict("os.environ", {"ASSETS_DATABASE_URL": "dbname=test"})
    @patch("pg_mcp.server.psycopg.connect")
    def test_data_query_limits_rows_and_preserves_duplicate_columns(self, connect):
        conn = connect.return_value.__enter__.return_value
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = [SimpleNamespace(name="id"), SimpleNamespace(name="id")]
        cursor.fetchmany.return_value = [(Decimal("1.20"), 2), (3, 4)]

        result = server.query("SELECT 1.20 AS id, 2 AS id", limit=1)

        self.assertEqual(
            result, {"columns": ["id", "id"], "rows": [["1.20", 2]], "truncated": True}
        )
        self.assertTrue(conn.read_only)
        cursor.fetchmany.assert_called_once_with(2)

    @patch.dict("os.environ", {"ASSETS_DATABASE_URL": "dbname=test"})
    @patch("pg_mcp.server.MAX_RESULT_BYTES", 10)
    @patch("pg_mcp.server.psycopg.connect")
    def test_data_query_caps_output_size(self, connect):
        conn = connect.return_value.__enter__.return_value
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchmany.return_value = [(1,), ("long result",)]

        self.assertTrue(server.query("SELECT 1")["truncated"])
        cursor.fetchmany.return_value = [("long result",)]
        with self.assertRaisesRegex(ToolError, "单行结果过大"):
            server.query("SELECT 1")


class PostgreSQLTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("PG_MCP_TEST_DSN"), "未配置独立测试库 PG_MCP_TEST_DSN")
    def test_data_query_reads_real_postgresql_in_read_only_transaction(self):
        with patch.dict("os.environ", {"ASSETS_DATABASE_URL": os.environ["PG_MCP_TEST_DSN"]}):
            result = server.query(
                "SELECT typname, current_setting('transaction_read_only') AS read_only "
                "FROM pg_catalog.pg_type ORDER BY oid",
                limit=1,
            )
        self.assertEqual(result["columns"], ["typname", "read_only"])
        self.assertEqual(result["rows"][0][1], "on")
        self.assertTrue(result["truncated"])


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_discover_and_call_tools_without_database(self):
        process = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from pg_mcp import main; main()", "--stdio"],
            env={"ASSETS_DATABASE_URL": ""},
        )
        async with stdio_client(process) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                self.assertEqual(
                    {tool.name for tool in tools}, {"list_tables", "describe_table", "query"}
                )
                self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools))

                invalid = await session.call_tool("list_tables", {"limit": 101})
                self.assertTrue(invalid.isError)
                self.assertIn("limit", invalid.content[0].text)
                self.assertNotIn("ASSETS_DATABASE_URL", invalid.content[0].text)

                missing_dsn = await session.call_tool(
                    "describe_table", {"table": "example"}
                )
                self.assertTrue(missing_dsn.isError)
                self.assertIn("ASSETS_DATABASE_URL", missing_dsn.content[0].text)

                rejected = await session.call_tool("query", {"sql": "SELECT 1; DELETE FROM items"})
                self.assertTrue(rejected.isError)
                self.assertIn("只允许单条 SELECT", rejected.content[0].text)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_transport_serves_mcp_over_http(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from pg_mcp import main; main()",
            env={**os.environ, "PG_MCP_PORT": str(port), "ASSETS_DATABASE_URL": ""},
        )
        try:
            async with asyncio.timeout(10):
                while True:
                    if process.returncode is not None:
                        self.fail(f"HTTP 服务提前退出：{process.returncode}")
                    try:
                        _, writer = await asyncio.open_connection("127.0.0.1", port)
                    except ConnectionRefusedError:
                        await asyncio.sleep(0.05)
                    else:
                        writer.close()
                        await writer.wait_closed()
                        break

                async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (
                    reader,
                    writer,
                    _,
                ):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        result = await session.call_tool("query", {"sql": "SELECT 1"})
                        self.assertTrue(result.isError)
                        self.assertIn("ASSETS_DATABASE_URL", result.content[0].text)
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
