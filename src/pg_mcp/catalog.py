"""在已授权 schema 中查找对象；通过 information_schema 保留 PostgreSQL 的可见性规则。"""

from typing import TypedDict

import psycopg
from psycopg.rows import DictRow, dict_row

from .errors import Issue, ToolFailure


class TableDetails(TypedDict):
    table_schema: str
    table_name: str
    table_type: str
    comment: str | None
    columns: list[DictRow]
    primary_key: DictRow | None
    unique_constraints: list[DictRow]
    indexes: list[DictRow]
    table_details_visible: bool
    truncated: bool


TABLES = """
    FROM information_schema.tables t
    JOIN pg_catalog.pg_namespace n ON n.nspname = t.table_schema
    JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = t.table_name
    WHERE t.table_schema = %s AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
"""
COMMENT = "pg_catalog.obj_description(c.oid, 'pg_class')"
SEARCH = f"""
    SELECT t.table_schema, t.table_name, t.table_type, {COMMENT} AS comment
    {TABLES}
      AND (strpos(lower(t.table_name), lower(%s)) > 0
           OR strpos(lower({COMMENT}), lower(%s)) > 0)
    ORDER BY t.table_name LIMIT %s OFFSET %s
"""
COLUMNS = """
    SELECT column_name, data_type, udt_schema, udt_name, is_nullable, ordinal_position,
           column_default, is_identity, identity_generation, is_generated, generation_expression,
           pg_catalog.col_description(%s, ordinal_position) AS comment
    FROM information_schema.columns
    WHERE table_schema = %s AND table_name = %s
    ORDER BY ordinal_position LIMIT %s OFFSET %s
"""
KEYS = """
    SELECT con.conname AS name, pg_catalog.pg_get_constraintdef(con.oid) AS definition,
           ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY AS k(num, pos)
                 JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num
                 ORDER BY k.pos) AS columns
    FROM pg_catalog.pg_constraint con
    WHERE con.conrelid = %s AND con.contype = %s
    ORDER BY con.conname LIMIT %s OFFSET %s
"""
INDEXES = """
    SELECT ic.relname AS name, i.indisprimary AS is_primary, i.indisunique AS is_unique,
           pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
           pg_catalog.pg_get_expr(i.indpred, i.indrelid) AS predicate
    FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
    WHERE i.indrelid = %s ORDER BY ic.relname LIMIT %s OFFSET %s
"""


def describe(conn: psycopg.Connection, schema: str, table: str, limit: int, offset: int) -> TableDetails:
    with conn.cursor(row_factory=dict_row) as cur:
        target = cur.execute(
            f"""SELECT c.oid, t.table_type, {COMMENT} AS comment,
                       pg_catalog.has_table_privilege(c.oid, 'SELECT') AS full_access
                {TABLES} AND t.table_name = %s""", (schema, table)
        ).fetchone()
        if target is None:
            raise ToolFailure(Issue.TABLE_NOT_FOUND)
        oid = target["oid"]
        cols = cur.execute(COLUMNS, (oid, schema, table, limit + 1, offset)).fetchall()
        pk, uniques, indexes = None, [], []
        if target["full_access"]:
            pk = cur.execute(KEYS, (oid, "p", 1, 0)).fetchone()
            uniques = cur.execute(KEYS, (oid, "u", limit + 1, offset)).fetchall()
            indexes = cur.execute(INDEXES, (oid, limit + 1, offset)).fetchall()
        return TableDetails(
            table_schema=schema, table_name=table, table_type=target["table_type"],
            comment=target["comment"], columns=cols[:limit], primary_key=pk,
            unique_constraints=uniques[:limit], indexes=indexes[:limit],
            table_details_visible=target["full_access"],
            truncated=any(len(items) > limit for items in (cols, uniques, indexes)),
        )
