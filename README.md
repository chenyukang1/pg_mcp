# PostgreSQL MCP

本地 stdio MCP 服务，可查看 PostgreSQL 表结构，也可用单条只读 `SELECT` 查询实际数据。

## 启动

需要 `uv` 和 Python 3.14（也可以用 `uv python install 3.14` 安装）。在项目根目录执行：

```bash
uv sync --locked
opencode mcp list
```

项目自带 `opencode.jsonc`，OpenCode V2 会自动加载。启动命令通过 `--project` 指向本项目的绝对路径，避免从其他目录启动时找错 `uv.lock`；如果移动项目，请更新该路径。确保运行 OpenCode 后台服务的进程能读取以下变量：

| 变量 | 用途 |
| --- | --- |
| `ASSETS_DATABASE_URL` | 必填，PostgreSQL URI 或 libpq 连接字符串。 |
| `ASSETS_PG_SCHEMA` | 可选，元数据查询范围，以及 SQL 中未限定表名时的默认业务 schema；默认 `public`。 |

连接串示意（不含密码）：`postgresql://mcp_reader@db.example.test:5432/assets_test?sslmode=verify-full`。可以通过受保护的 `~/.pgpass` 提供数据库密码，文件权限设为 `0600`。不要将真实密码写进配置、命令参数或聊天。项目不会自动读取 `.env`。如果 OpenCode 后台服务已在运行，仅在另一个终端执行 `export` 不会改变后台进程的环境。若后台进程的 `PATH` 找不到 `uv`，在配置中将 `uv` 换成 `command -v uv` 查到的绝对路径。

在 OpenCode 中用 `/mcps` 检查状态，然后请求「用 `pg-mcp` 列出前 5 张表」。`connected` 只表示 MCP 服务握手成功，工具成功返回才表示数据库连接可用；空列表表示当前账号没有看到符合条件的对象，也可能是 schema 配错或分页已到末尾。

## 在其他项目使用

若要在 `qbit-assets` 使用，把本项目 `opencode.jsonc` 中 `mcp.servers.pg-mcp` 的配置条目合并进 `qbit-assets` 的项目配置。保留启动命令中的绝对路径和 `environment` 设置。无需改全局配置，也无需复制 Python 源码。

## 工具

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `list_tables` | `limit`、`offset` | 表名、对象类型。 |
| `describe_table` | `table`、`limit`、`offset` | 列名、类型、可空性和列顺序。表名按原样匹配，不加 schema 前缀。 |
| `query` | `sql`、`limit` | `columns`、按列顺序排列的 `rows`，以及是否截断的 `truncated`。 |

各工具默认返回 50 条，最多 100 条。元数据工具的 `offset` 从 0 开始；元数据来自 `information_schema`，不覆盖 PostgreSQL 特有的所有对象类型（例如物化视图）。

例如，在 OpenCode 中请求「用 `pg-mcp` 执行 `SELECT id, status FROM orders ORDER BY id DESC LIMIT 10`」，将表名和列名换成实际名称。`query` 支持 `JOIN`、聚合和只读 `WITH`，拒绝多语句、写入型 CTE、`SELECT INTO` 和 `FOR UPDATE/SHARE` 等锁行语句。查询工具还限制返回的行数据大小，超出时截断；如果首行就超限，需要减少列或截短大字段。`numeric` 等不能直接用 JSON 表示的类型会转为字符串。

## 权限和数据安全

建议先连接测试库或只读副本。为 MCP 使用独立账号，仅授予目标数据库的 `CONNECT`、指定 schema 的 `USAGE`，以及所需表或脱敏视图的 `SELECT`。不要使用应用写库账号。默认 schema 不是授权边界：显式写 `schema.table` 时，能否查询由数据库角色权限决定。

SQL 先经过 PostgreSQL 语法树检查，再在只读事务中执行。服务设置了 5 秒连接超时、3 秒语句超时和 1 秒锁等待超时。只读事务不能阻止所有 `SELECT` 函数的外部副作用；不要让账号通过角色继承或 `PUBLIC` 调用有副作用的自定义函数或扩展。查询结果会进入模型上下文，不要授权敏感原始数据。

## 测试

```bash
uv run --locked python -m unittest discover -s tests -v
```

测试覆盖 SQL 校验、查询限制、错误脱敏，以及 MCP stdio 握手和工具调用。设置独立测试库连接串 `PG_MCP_TEST_DSN` 后，测试还会真实读取 PostgreSQL 系统表，并确认事务处于只读模式；未设置时该项跳过。
