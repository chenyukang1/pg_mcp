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

from . import catalog
from .database import connect_readonly, error_hint
from .diagnostics import check_profiles, check_single
from .identity import IDENTITY_SQL, ExpectedIdentity
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
ROW_ENCODER = json.JSONEncoder(default=str, ensure_ascii=True)


class QueryResult(TypedDict):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def _encode_within_budget(row: tuple, remaining: int) -> str | None:
    # 常见超大 text/bytea 字段不再额外分配完整 JSON 字符串。
    if any(isinstance(value, (str, bytes)) and len(value) > remaining for value in row):
        return None
    chunks = []
    for chunk in ROW_ENCODER.iterencode(row):
        remaining -= len(chunk)  # ensure_ascii=True，每个字符占一个字节。
        if remaining < 0:
            return None
        chunks.append(chunk)
    return "".join(chunks)


def _schema() -> str:
    return os.environ.get("ASSETS_PG_SCHEMA") or "public"


@contextmanager
def _read_connection() -> Iterator[psycopg.Connection]:
    dsn = os.environ.get("ASSETS_DATABASE_URL", "").strip()
    if not dsn:
        raise ToolError("未配置 ASSETS_DATABASE_URL，请检查 MCP 进程的环境变量。")

    try:
        with connect_readonly(dsn) as conn:
            if expected := ExpectedIdentity.from_env("PG_MCP"):
                expected.verify(conn.execute(IDENTITY_SQL).fetchone())
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
        raise ToolError("PostgreSQL 查询失败：" + error_hint(exc)) from None


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
def search_tables(keyword: Annotated[str, Field(min_length=1, max_length=128)],
                  limit: LIMIT = 50, offset: OFFSET = 0) -> list[DictRow]:
    """在可见表名和表注释中查找关键词（忽略大小写，百分号和下划线按字面匹配）。"""
    return _query(catalog.SEARCH, (_schema(), keyword, keyword, limit, offset))


@mcp.tool(annotations=READ_ONLY)
def describe_table(table: TABLE_NAME, limit: LIMIT = 50, offset: OFFSET = 0) -> catalog.TableDetails:
    """查看列、默认值、主键、唯一约束和索引；按原样传入表名。各列表独立分页。"""
    with _read_connection() as conn:
        return catalog.describe(conn, _schema(), table, limit, offset)


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
            # 服务端游标逐行取。多看一行以区分刚好读完和截断。
            while (row := cursor.fetchone()) is not None:
                if len(rows) == limit:
                    return {"columns": columns, "rows": rows, "truncated": True}
                encoded = _encode_within_budget(row, bytes_left)
                if encoded is None:
                    if not rows:
                        raise ToolError("单行结果过大，请减少列或在 SQL 中截断大字段。")
                    return {"columns": columns, "rows": rows, "truncated": True}
                bytes_left -= len(encoded)
                rows.append(json.loads(encoded, parse_constant=str))
            return {"columns": columns, "rows": rows, "truncated": False}


def main() -> None:
    """启动已配置的 HTTP 环境，也可显式启动单实例或 stdio。"""
    parser = argparse.ArgumentParser(prog="pg-mcp")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdio", action="store_true", help="通过标准输入和输出通信")
    mode.add_argument("--single", action="store_true", help="仅使用 ASSETS_DATABASE_URL 启动")
    parser.add_argument("--check", action="store_true", help="预检端口与只读数据库连接，然后退出")
    args = parser.parse_args()
    if args.check and args.stdio:
        parser.error("--check 不适用于 --stdio")
    load_dotenv(".env")
    if not os.environ.get("ASSETS_DATABASE_URL"):
        os.environ["ASSETS_DATABASE_URL"] = os.environ.get("PG_TEST_DATABASE_URL", "")
    try:
        profiles = [] if args.single or args.stdio else configured_profiles()
        if not profiles:
            expected = ExpectedIdentity.from_env("PG_MCP")
            if not args.stdio:
                mcp.settings.port = read_port("PG_MCP_PORT", 8765)
    except ValueError as exc:
        parser.error(str(exc))

    if args.check:
        ok = check_profiles(profiles) if profiles else check_single(
            mcp.settings.port, os.environ["ASSETS_DATABASE_URL"]
        )
        raise SystemExit(0 if ok else 1)
    if profiles:
        raise SystemExit(run_profiles(profiles))
    if expected:
        try:
            with _read_connection():
                pass  # 身份通过后才开始监听；工具每次新建连接时仍会再次校验。
        except ToolError as exc:
            parser.exit(1, f"{exc}\n")
    mcp.run(transport="stdio" if args.stdio else "streamable-http")
