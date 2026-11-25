"""使用 PostgreSQL 语法树验证查询，而不是按 SQL 关键字匹配。"""

from mcp.server.fastmcp.exceptions import ToolError
from pglast import ast, parse_sql
from pglast.parser import ParseError
from pglast.visitors import Visitor


class _ReadOnlySelect(Visitor):
    def visit_SelectStmt(self, _ancestors, node: ast.SelectStmt) -> None:
        if node.intoClause or node.lockingClause:
            raise ToolError("不允许 SELECT INTO 或 SELECT FOR UPDATE/SHARE 等锁行查询。")

    def visit_CommonTableExpr(self, _ancestors, node: ast.CommonTableExpr) -> None:
        if not isinstance(node.ctequery, ast.SelectStmt):
            raise ToolError("WITH 中只允许只读 SELECT。")


def validate_select(sql: str) -> None:
    """拒绝非单条 SELECT、写入型 CTE 和任何层级的锁行查询。"""
    if "\x00" in sql:
        raise ToolError("SQL 不允许包含空字符。")
    try:
        statements = parse_sql(sql)
    except ParseError:
        raise ToolError("SQL 语法无效；只支持单条 PostgreSQL SELECT。") from None
    if len(statements) != 1 or not isinstance(statements[0].stmt, ast.SelectStmt):
        raise ToolError("只允许单条 SELECT，可包含只读 WITH。")
    _ReadOnlySelect()(statements[0].stmt)
