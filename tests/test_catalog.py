import os
import unittest
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from pg_mcp import server


@unittest.skipUnless(os.getenv("PG_MCP_CATALOG_FIXTURE") == "1", "需要临时元数据样本")
class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"ASSETS_DATABASE_URL": os.environ["PG_MCP_TEST_DSN"],
                                                  "ASSETS_PG_SCHEMA": "catalog_demo"}))

    def test_keyword_and_literal_comment_match_only_visible_tables(self):
        for keyword in ("waLLET", "结算", "%_"):
            with self.subTest(keyword=keyword):
                self.assertEqual([t["table_name"] for t in server.search_tables(keyword)], ["Wallet_Ledger"])

    def test_constraints_defaults_and_partial_index(self):
        details = server.describe_table("Wallet_Ledger")
        columns = {col["column_name"]: col for col in details["columns"]}
        self.assertEqual(columns["id"]["identity_generation"], "ALWAYS")
        self.assertEqual(columns["currency"]["comment"], "币种")
        self.assertIn("open", columns["status"]["column_default"])
        self.assertEqual(details["primary_key"]["columns"], ["id"])
        self.assertEqual(details["unique_constraints"][0]["columns"], ["currency", "status"])
        partial = next(i for i in details["indexes"] if i["name"] == "open_currency")
        self.assertTrue(partial["is_unique"])
        self.assertIn("open", partial["predicate"])
        self.assertTrue(server.describe_table("Wallet_Ledger", limit=1)["truncated"])

    def test_column_only_grant_does_not_reveal_private_index_or_hidden_table(self):
        restricted = server.describe_table("partial_access")
        self.assertEqual([c["column_name"] for c in restricted["columns"]], ["visible"])
        self.assertFalse(restricted["table_details_visible"])
        self.assertEqual(restricted["indexes"], [])
        with self.assertRaisesRegex(ToolError, "不可见"):
            server.describe_table("hidden_wallet")
