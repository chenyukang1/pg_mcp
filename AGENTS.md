# Working on pg-mcp

## Verification

- From the repo root, use `uv sync --locked` (Python 3.14). After editing dependencies, run `uv lock` before locked commands.
- Full suite: `uv run --locked python -m unittest discover -s tests -v`. Focused example: `uv run --locked python -m unittest discover -s tests -v -k SqlValidationTests`.
- The suite includes a real stdio subprocess but needs no database by default. `PostgreSQLTests` is opt-in via `PG_MCP_TEST_DSN` for a dedicated test database; it does not use the runtime DSN.

## Runtime and query boundaries

- The `pg-mcp` console script starts FastMCP over stdio via `src/pg_mcp/server.py`; stdout is reserved for protocol messages. The stdio test checks the exact exposed tool set. Trust the tool signatures over README defaults.
- `opencode.jsonc` uses an absolute `uv run --project` path so launch does not depend on OpenCode's working directory. Update it if the checkout moves. The OpenCode service needs `ASSETS_DATABASE_URL` in its environment; the project does not load `.env`. `opencode mcp list` showing `connected` verifies only the MCP handshake, not PostgreSQL access.
- Keep both SQL safeguards: `sql_validation.py` checks the PostgreSQL AST, including nested CTEs and locking clauses; `server.py` executes in a read-only transaction and bounds results. `ASSETS_PG_SCHEMA` scopes metadata and sets the default search path, but database grants control access to explicitly qualified tables. A least-privilege role is still required; a read-only transaction cannot rule out external side effects from callable functions. Do not expose raw driver errors or connection strings.
