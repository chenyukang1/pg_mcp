"""使用 PostgreSQL 语法树验证查询，而不是按 SQL 关键字匹配。"""

from pglast import ast, parse_sql
from pglast.parser import ParseError
from pglast.visitors import Visitor

from .errors import Issue, ToolFailure


class _ReadOnlySelect(Visitor):
    def visit_SelectStmt(self, _ancestors, node: ast.SelectStmt) -> None:
        if node.intoClause or node.lockingClause:
            raise ToolFailure(Issue.READ_ONLY_REQUIRED, "不允许 SELECT INTO 或锁行查询。")

    def visit_CommonTableExpr(self, _ancestors, node: ast.CommonTableExpr) -> None:
        if not isinstance(node.ctequery, ast.SelectStmt):
            raise ToolFailure(Issue.READ_ONLY_REQUIRED, "WITH 中只允许只读 SELECT。")


def validate_select(sql: str) -> None:
    """拒绝非单条 SELECT、写入型 CTE 和任何层级的锁行查询。"""
    if "\x00" in sql:
        raise ToolFailure(Issue.INVALID_SQL, "SQL 不允许包含空字符。")
    try:
        statements = parse_sql(sql)
    except ParseError:
        raise ToolFailure(Issue.INVALID_SQL) from None
    if len(statements) != 1 or not isinstance(statements[0].stmt, ast.SelectStmt):
        raise ToolFailure(Issue.READ_ONLY_REQUIRED, "只允许单条 SELECT，可包含只读 WITH。")
    _ReadOnlySelect()(statements[0].stmt)
