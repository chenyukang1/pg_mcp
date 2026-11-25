"""在私有 Unix socket 上创建两个一次性数据库，再运行 unittest（不读取项目 DSN）。"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo


def main() -> int:
    bindir = os.getenv("PG_MCP_TEST_PGBIN") or os.path.dirname(shutil.which("initdb") or "")
    if not bindir:
        sys.exit("请先安装 PostgreSQL，或设置 PG_MCP_TEST_PGBIN 指向包含 initdb 的 bin 目录")
    bins = Path(bindir).resolve()
    with tempfile.TemporaryDirectory(prefix="pgmcp-") as tmp:
        root = Path(tmp)
        data = root / "data"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "ASSETS_"))}
        env.update(LC_ALL="C", PYTHON_DOTENV_DISABLED="1")
        subprocess.run([bins / "initdb", "-D", data, "-U", "fixture_admin", "-A", "trust",
                        "--no-sync", "--no-locale", "-E", "UTF8"], check=True, env=env,
                       stdout=subprocess.DEVNULL)
        ctl = [bins / "pg_ctl", "-D", data, "-w"]
        options = f"-c listen_addresses='' -k {shlex.quote(tmp)} -p 5432"
        subprocess.run([*ctl, "start", "-l", root / "postgres.log", "-o", options], check=True, env=env)
        try:
            default = dict(host=tmp, port=5432, user="fixture_admin", dbname="postgres")
            with psycopg.connect(**default, autocommit=True) as admin:
                for database, role, key in (
                    ("first_db", "test_reader", "PG_MCP_TEST_DSN"),
                    ("second_db", "standin_reader", "PG_MCP_TEST_SECOND_DSN"),
                ):
                    admin.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN").format(psycopg.sql.Identifier(role)))
                    admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(database)))
                    env[key] = make_conninfo(host=tmp, port=5432, dbname=database, user=role)
            with psycopg.connect(**{**default, "dbname": "first_db"}) as admin:
                admin.execute((Path(__file__).with_name("catalog_fixture.sql")).read_text())
            env["PG_MCP_CATALOG_FIXTURE"] = "1"
            return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                                   "-v", *sys.argv[1:]], env=env).returncode
        finally:
            subprocess.run([*ctl, "stop", "-m", "fast"], check=True, env=env)


if __name__ == "__main__":
    sys.exit(main())
