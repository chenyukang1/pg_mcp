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
        for key in ("PG_TEST", "PG_PROD_RO"):
            profiles.update({f"{key}_EXPECTED_DATABASE": key, f"{key}_EXPECTED_USER": "reader"})
        with patch.dict(os.environ, {**profiles, "PGPASSWORD": "unused"}, clear=True):
            test, prod = configured_profiles()
            env = test.worker_env()
            self.assertEqual(env["ASSETS_DATABASE_URL"], test.dsn)
            self.assertNotIn(prod.dsn, env.values())
            self.assertNotIn("PGPASSWORD", env)
        with tempfile.TemporaryDirectory() as root:
            Path(root, ".env").write_text("PG_PROD_RO_DATABASE_URL=unexpected\n")
            # 只检查 dotenv 边界，不让探针的启动校验触发数据库连接。
            subprocess.run([sys.executable, "-c", """
import os
from pg_mcp import server
from unittest.mock import patch
def probe(**_):
    assert 'PG_PROD_RO_DATABASE_URL' not in os.environ
    assert os.environ['ASSETS_DATABASE_URL'] == 'dbname=test'
server.mcp.run = probe
with patch.object(server, '_read_connection'):
    server.main()
""", "--single"], cwd=root, env=env, check=True)
