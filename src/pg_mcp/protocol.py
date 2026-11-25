"""在 MCP 边界输出结构化工具错误；成功结果沿用 FastMCP 自身的输出 schema。"""

import json
from functools import wraps

from mcp import types
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from .errors import Issue, ToolFailure
from .limits import Governor, Limits


def safe_failure(error: Exception) -> ToolFailure:
    # FastMCP 用 ToolError 包装工具和 Pydantic 异常；包装异常的文本可能含原始参数。
    cause = error
    while cause is not None:
        if isinstance(cause, ToolFailure):
            return cause
        if isinstance(cause, ValidationError):
            fields = {str(loc) for err in cause.errors(include_input=False, include_context=False)
                      for loc in err["loc"] if loc in ("sql", "limit", "offset", "table", "keyword")}
            return ToolFailure(Issue.INVALID_ARGUMENT, "参数无效：" + ", ".join(sorted(fields))
                               if fields else None)
        cause = cause.__cause__
    return ToolFailure(Issue.INTERNAL_ERROR)


class ReadOnlyMCP(FastMCP):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.governor = Governor(Limits())

    def database_tool(self, fn):
        @wraps(fn)
        async def dispatch(*args, **kwargs):
            return await self.governor.run(fn, *args, **kwargs)

        self.add_tool(dispatch, annotations=types.ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True))
        return fn

    async def call_tool(self, name, arguments):
        try:
            return await super().call_tool(name, arguments)
        except Exception as exc:
            data = {"error": safe_failure(exc).fields()}
            return types.CallToolResult(isError=True, structuredContent=data, content=[
                types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))
            ])
