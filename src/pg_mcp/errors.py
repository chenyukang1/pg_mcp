"""工具错误白名单。对客户端只返回固定的错误码、说明与下一步。"""

from enum import Enum

import psycopg
from mcp.server.fastmcp.exceptions import ToolError


class Issue(Enum):
    TABLE_NOT_FOUND = ("表不存在或不可见", "用 search_tables 在当前 schema 查找表。", False)
    COLUMN_NOT_FOUND = ("列不存在", "用 describe_table 核对列名和大小写。", False)
    PERMISSION_DENIED = ("当前角色没有所需权限", "不要重试；改查已授权视图，或联系管理员。", False)
    INVALID_SQL = ("SQL 语法无效", "修正 PostgreSQL SELECT 语法后再提交。", False)
    READ_ONLY_REQUIRED = ("只允许单条只读 SELECT", "移除写操作、多语句和锁行子句。", False)
    INVALID_ARGUMENT = ("工具参数无效", "按工具输入 schema 修正参数。", False)
    IDENTITY_MISMATCH = ("数据库身份与预期不符", "停止查询，核对该环境的库名、角色和只读状态。", False)
    MISSING_DSN = ("未配置 ASSETS_DATABASE_URL", "检查 MCP 服务进程的数据库配置。", False)
    MISSING_PASSWORD = ("未提供密码", "检查 .pgpass 匹配项和权限。", False)
    AUTH_FAILED = ("密码认证失败", "核对当前数据库角色的凭据。", False)
    UNREACHABLE = ("数据库主机或端口不可达", "检查地址、网络和 VPN。", True)
    QUERY_TIMEOUT = ("PostgreSQL 查询超时", "缩小查询范围或减少返回列数。", False)
    BUSY = ("当前环境并发请求已满", "稍后重试，避免并行重复请求。", True)
    LOCK_TIMEOUT = ("等待数据库锁超时", "稍后再试。", True)
    RESULT_TOO_LARGE = ("单行结果过大", "减少列或在 SQL 中截断大字段。", False)
    DATABASE_ERROR = ("PostgreSQL 查询失败", "请检查连接设置与账号权限。", False)
    INTERNAL_ERROR = ("工具执行失败", "联系 MCP 服务维护者。", False)


class ToolFailure(ToolError):
    def __init__(self, issue: Issue, message: str | None = None):
        self.issue = issue
        self.message = message or issue.value[0]
        super().__init__(f"{issue.name}：{self.message}")

    def fields(self) -> dict:
        return {"code": self.issue.name, "message": self.message, "next_action": self.issue.value[1],
                "retryable": self.issue.value[2]}


def database_failure(error: psycopg.Error) -> ToolFailure:
    by_state = {"42P01": Issue.TABLE_NOT_FOUND, "42703": Issue.COLUMN_NOT_FOUND,
                "42501": Issue.PERMISSION_DENIED, "42601": Issue.INVALID_SQL, "28P01": Issue.AUTH_FAILED,
                "57014": Issue.QUERY_TIMEOUT, "55P03": Issue.LOCK_TIMEOUT}
    if issue := by_state.get(error.sqlstate):
        return ToolFailure(issue)
    # libpq 建连阶段经常没有 SQLSTATE；只匹配类别，不向外传递原文。
    text = str(error).lower()
    if "no password supplied" in text:
        return ToolFailure(Issue.MISSING_PASSWORD)
    if "password authentication failed" in text:
        return ToolFailure(Issue.AUTH_FAILED)
    if any(term in text for term in (
        "could not translate host", "failed to resolve host", "name or service not known",
        "nodename nor servname", "no route to host", "network is unreachable",
        "connection refused", "timeout expired", "connection timeout",
    )):
        return ToolFailure(Issue.UNREACHABLE)
    return ToolFailure(Issue.DATABASE_ERROR)
