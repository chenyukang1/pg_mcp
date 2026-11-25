import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pg_mcp.launcher import configured_profiles


class EnvironmentTests(unittest.TestCase):
    def test_worker_cannot_reload_sibling_profile_from_dotenv(self):
        profiles = {"PG_TEST_DATABASE_URL": "dbname=test", "PG_PROD_RO_DATABASE_URL": "dbname=other"}
        with patch.dict(os.environ, {**profiles, "PGPASSWORD": "unused"}, clear=True):
            test, prod = configured_profiles()
            env = test.worker_env()
            self.assertEqual(env["ASSETS_DATABASE_URL"], test.dsn)
            self.assertNotIn(prod.dsn, env.values())
            self.assertNotIn("PGPASSWORD", env)
        with tempfile.TemporaryDirectory() as root:
            Path(root, ".env").write_text("PG_PROD_RO_DATABASE_URL=unexpected\n")
            # 使用真实入口，但用探针替代 HTTP 循环，检查启动后的环境。
            subprocess.run([sys.executable, "-c", """
import os
from pg_mcp import server
def probe(**_):
    assert 'PG_PROD_RO_DATABASE_URL' not in os.environ
    assert os.environ['ASSETS_DATABASE_URL'] == 'dbname=test'
server.mcp.run = probe
server.main()
""", "--single"], cwd=root, env=env, check=True)
