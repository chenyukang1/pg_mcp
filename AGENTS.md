# Working on pg-mcp

## Verification

- From the repo root, use `uv sync --locked` (Python 3.14). After editing dependencies, run `uv lock` before locked commands.
- Full suite: `uv run --locked python -m unittest discover -s tests -v`. Focused example: `uv run --locked python -m unittest discover -s tests -v -k SqlValidationTests`.
- The transport tests start real subprocesses but need no database; the HTTP test needs a loopback port. `PostgreSQLTests` is opt-in via `PG_MCP_TEST_DSN` for a dedicated test database; it does not use the runtime DSN.

## Runtime and query boundaries

- `pg-mcp` starts unauthenticated Streamable HTTP on loopback; keep it off external interfaces. Explicit `pg-mcp --stdio` keeps stdout reserved for MCP protocol messages.
- Set `ASSETS_DATABASE_URL` in the MCP process, not the HTTP client. For separate databases, launch separate processes on distinct `PG_MCP_PORT` values. `opencode.jsonc.example` is client-only and does not start HTTP servers; `connected` verifies MCP, not PostgreSQL access.
- Keep both SQL safeguards: `sql_validation.py` checks the PostgreSQL AST, including nested CTEs and locking clauses; `server.py` executes in a read-only transaction and bounds results. `ASSETS_PG_SCHEMA` scopes metadata and sets the default search path, but database grants control access to explicitly qualified tables. A least-privilege role is still required; a read-only transaction cannot rule out external side effects from callable functions. Do not expose raw driver errors or connection strings.
