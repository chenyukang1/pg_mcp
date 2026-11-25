"""通过 MCP 提供 PostgreSQL 只读查询。"""

import argparse
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, TypedDict

import psycopg
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from psycopg.rows import DictRow, dict_row, tuple_row
from pydantic import Field

from .launcher import configured_profiles, read_port, run_profiles
from .sql_validation import validate_select

logger = logging.getLogger(__name__)
mcp = FastMCP(
    "pg-mcp",
    instructions="可查询表结构和数据；query 只接受单条只读 SELECT。请只查询必要的列和行。",
    host="127.0.0.1",
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)

LIMIT = Annotated[int, Field(ge=1, le=100)]
OFFSET = Annotated[int, Field(ge=0, le=2_147_483_647)]
TABLE_NAME = Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[^\x00]+$")]
SQL_TEXT = Annotated[str, Field(min_length=1, max_length=16_000)]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
MAX_RESULT_BYTES = 128_000


class QueryResult(TypedDict):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def _schema() -> str:
    return os.environ.get("ASSETS_PG_SCHEMA") or "public"


@contextmanager
def _read_connection() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("ASSETS_DATABASE_URL", "").strip()
    if not dsn:
        raise ToolError("未配置 ASSETS_DATABASE_URL，请检查 MCP 进程的环境变量。")

    try:
        with psycopg.connect(
            dsn,
            connect_timeout=5,
            options=("-c statement_timeout=3000 -c lock_timeout=1000 -c standard_conforming_strings=on"),
        ) as conn:
            conn.read_only = True
            yield conn
    except psycopg.errors.QueryCanceled:
        raise ToolError("PostgreSQL 查询超时。") from None
    except psycopg.Error as exc:
        # 不记录驱动异常正文，其中可能包含连接信息。
        logger.warning(
            "PostgreSQL 查询失败：%s，SQLSTATE=%s",
            type(exc).__name__,
            exc.sqlstate or "-",
        )
        raise ToolError("PostgreSQL 查询失败，请检查连接设置与账号权限。") from None


def _query(sql: str, params: tuple[str | int, ...]) -> list[DictRow]:
    with _read_connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(sql, params).fetchall()


@mcp.tool(annotations=READ_ONLY)
def list_tables(limit: LIMIT = 50, offset: OFFSET = 0) -> list[DictRow]:
    """按名称分页列出配置 schema 中当前账号可见的表和视图。"""
    return _query(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
        LIMIT %s OFFSET %s
        """,
        (_schema(), limit, offset),
    )


@mcp.tool(annotations=READ_ONLY)
def describe_table(table: TABLE_NAME, limit: LIMIT = 50, offset: OFFSET = 0) -> list[DictRow]:
    """按字段顺序分页查看表或视图的名称、类型和可空性；表名区分大小写。"""
    return _query(
        """
        SELECT column_name, data_type, udt_schema, udt_name, is_nullable,
               ordinal_position
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        LIMIT %s OFFSET %s
        """,
        (_schema(), table, limit, offset),
    )


@mcp.tool(annotations=READ_ONLY)
def query(sql: SQL_TEXT, limit: LIMIT = 100) -> QueryResult:
    """运行单条只读 SELECT，默认最多返回 100 行。需要稳定顺序时请写 ORDER BY。"""
    validate_select(sql)
    with _read_connection() as conn:
        conn.execute(
            "SELECT pg_catalog.set_config('search_path', pg_catalog.quote_ident(%s), true)",
            (_schema(),),
        )
        with conn.cursor(name="pg_mcp_query", row_factory=tuple_row) as cursor:
            cursor.execute(sql)
            columns = [column.name for column in cursor.description or ()]
            rows = []
            bytes_left = MAX_RESULT_BYTES
            for row in cursor.fetchmany(limit + 1):
                if len(rows) == limit:
                    return {"columns": columns, "rows": rows, "truncated": True}
                # 保留列顺序和重复列名；Decimal 等非 JSON 类型用字符串表示。
                encoded = json.dumps(row, default=str)
                bytes_left -= len(encoded)  # 默认 ensure_ascii=True，每字符一字节。
                if bytes_left < 0:
                    if not rows:
                        raise ToolError("单行结果过大，请减少列或在 SQL 中截断大字段。")
                    return {"columns": columns, "rows": rows, "truncated": True}
                rows.append(json.loads(encoded, parse_constant=str))
            return {"columns": columns, "rows": rows, "truncated": False}


def main() -> None:
    """启动已配置的 HTTP 环境，也可显式启动单实例或 stdio。"""
    parser = argparse.ArgumentParser(prog="pg-mcp")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdio", action="store_true", help="通过标准输入和输出通信")
    mode.add_argument("--single", action="store_true", help="仅使用 ASSETS_DATABASE_URL 启动")
    args = parser.parse_args()
    load_dotenv(".env")
    if not os.environ.get("ASSETS_DATABASE_URL"):
        os.environ["ASSETS_DATABASE_URL"] = os.environ.get("PG_TEST_DATABASE_URL", "")
    if args.stdio:
        mcp.run(transport="stdio")
        return

    try:
        profiles = [] if args.single else configured_profiles()
        if not profiles:
            mcp.settings.port = read_port("PG_MCP_PORT", 8765)
    except ValueError as exc:
        parser.error(str(exc))

    if profiles:
        raise SystemExit(run_profiles(profiles))
    mcp.run(transport="streamable-http")
