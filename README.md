# PostgreSQL MCP

本地 PostgreSQL MCP 服务，默认通过 Streamable HTTP 提供表结构和单条只读 `SELECT` 查询，不依赖 OpenCode 启动。

## 启动

需要 `uv` 和 Python 3.14（也可以用 `uv python install 3.14` 安装）。在项目根目录执行：

```bash
uv sync --locked
uv run pg-mcp
```

服务默认监听 `http://127.0.0.1:8765/mcp`。在本地 `.env` 中设置：

| 变量 | 用途 |
| --- | --- |
| `ASSETS_DATABASE_URL` | 单实例使用的 PostgreSQL URI 或 libpq 连接字符串。多环境配置见下文。 |
| `ASSETS_PG_SCHEMA` | 可选，元数据查询范围，以及 SQL 中未限定表名时的默认业务 schema；默认 `public`。 |
| `PG_MCP_PORT` | 可选，HTTP 端口；默认 `8765`。 |

连接串示意（不含密码）：`postgresql://mcp_reader@db.example.test:5432/assets_test?sslmode=verify-full`。可以通过受保护的 `~/.pgpass` 提供数据库密码，文件权限设为 `0600`。不要将真实密码写进配置、命令参数或聊天。显式使用 `--env-file` 可以固定读取哪个文件；如果已通过进程环境设置变量，也可以省略该参数直接运行。

## 客户端接入

启动前运行 `uv run pg-mcp --check`，逐个检查已配置环境的本机端口能否监听，并查询实际数据库名、当前用户和事务只读状态。已运行实例占用端口会导致预检失败；这个命令不会启动或停止服务。单实例用 `--single --check`。所有检查通过返回 0，否则返回非零。`transaction_read_only=on` 表示本次事务只读，不代表账号本身没有写权限。

任何支持 Streamable HTTP 的 MCP 客户端都可以连接 `http://127.0.0.1:8765/mcp`。OpenCode V2 用户可将 `opencode.jsonc.example` 中的 `mcp.servers` 条目合并到使用 MCP 的项目配置；已有同名 `local` 条目应整个替换为 `remote` 条目。

连接后执行 `SELECT current_database(), current_user;` 验证数据库和账号。`connected` 只表示 MCP 握手成功，不代表数据库可用。只支持 stdio 的客户端，可在原有 `pg-mcp` 启动命令末尾加 `--stdio`，由客户端启动。

### 同时启动两个环境

在项目根目录 `.env` 中分别填写两个只读连接串，例如：

```dotenv
PG_TEST_DATABASE_URL=postgresql://test_reader@test.example.test:5432/assets_test
PG_PROD_RO_DATABASE_URL=postgresql://prod_reader@replica.example.test:5432/assets
```

运行 **`uv run pg-mcp`** 即可。程序读取 `.env`，为每个非空连接串启动独立进程：测试库默认 `8765`，生产只读库默认 `8766`，路径都是 `/mcp`。端口可在 `.env` 用 `PG_TEST_MCP_PORT`、`PG_PROD_RO_MCP_PORT` 修改。按 `Ctrl+C` 一起停止；任一进程异常退出时，另一个也会停止。

仅配置一个环境就只启动该环境；两个变量都为空时回退到 `ASSETS_DATABASE_URL` 和 `PG_MCP_PORT`。如需忽略多环境配置并只启动这个单实例，运行 `uv run pg-mcp --single`。模板中的 `disabled` 仅控制 OpenCode 是否连接，不控制服务是否监听。**HTTP 服务没有应用层认证，只监听本机，不要代理或转发到其他机器。**

`--single` 和 `--stdio` 优先使用显式的 `ASSETS_DATABASE_URL`，否则只回退到测试库。使用旧模板时，请将其中指向生产库的 `ASSETS_DATABASE_URL` 改成测试连接串或删除。多环境工作进程不加载 `.env`，只接收分配给自己的连接串。密码和 TLS 参数可以按环境在 libpq 连接配置中指定；工作进程不会继承通用 `PGPASSWORD`。

## 工具

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `list_tables` | `limit`、`offset` | 表名、对象类型。 |
| `describe_table` | `table`、`limit`、`offset` | 列名、类型、可空性和列顺序。表名按原样匹配，不加 schema 前缀。 |
| `query` | `sql`、`limit` | `columns`、按列顺序排列的 `rows`，以及是否截断的 `truncated`。 |

`query` 默认返回 100 行，元数据工具默认返回 50 条，各工具最多返回 100 条。元数据工具的 `offset` 从 0 开始；元数据来自 `information_schema`，不覆盖 PostgreSQL 特有的所有对象类型（例如物化视图）。

例如，在 OpenCode 中请求「用 `pg-mcp` 执行 `SELECT id, status FROM orders ORDER BY id DESC LIMIT 10`」，将表名和列名换成实际名称。`query` 支持 `JOIN`、聚合和只读 `WITH`，拒绝多语句、写入型 CTE、`SELECT INTO` 和 `FOR UPDATE/SHARE` 等锁行语句。查询工具还限制返回的行数据大小，超出时截断；如果首行就超限，需要减少列或截短大字段。`numeric` 等不能直接用 JSON 表示的类型会转为字符串。

## 权限和数据安全

`query` 使用服务端游标逐行读取，累计行数据的 JSON 达到 128,000 字节且仍有数据时截断。这个预算不是数据库单行大小限制：驱动仍需接收当前行。查询很大的 `text`、`bytea` 或 JSON 字段时，请在 SQL 中只投影需要的部分。

建议先连接测试库或只读副本。为 MCP 使用独立账号，仅授予目标数据库的 `CONNECT`、指定 schema 的 `USAGE`，以及所需表或脱敏视图的 `SELECT`。不要使用应用写库账号。默认 schema 不是授权边界：显式写 `schema.table` 时，能否查询由数据库角色权限决定。

SQL 先经过 PostgreSQL 语法树检查，再在只读事务中执行。服务设置了 5 秒连接超时、3 秒语句超时和 1 秒锁等待超时。只读事务不能阻止所有 `SELECT` 函数的外部副作用；不要让账号通过角色继承或 `PUBLIC` 调用有副作用的自定义函数或扩展。查询结果会进入模型上下文，不要授权敏感原始数据。

## 测试

```bash
uv run --locked python -m unittest discover -s tests -v
```

测试覆盖 SQL 校验、查询限制、错误脱敏，以及 HTTP 和 stdio 的 MCP 握手和工具调用。设置独立测试库连接串 `PG_MCP_TEST_DSN` 后，测试还会真实读取 PostgreSQL 系统表，并确认事务处于只读模式；未设置时该项跳过。
