import io
import socket
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import psycopg

from pg_mcp.diagnostics import check_single


class DiagnosticsTests(unittest.TestCase):
    @patch("pg_mcp.diagnostics.connect_readonly")
    def test_distinct_errors_do_not_echo_driver_details(self, connect):
        for reason, expected in [
            ("no password supplied", "未提供密码"),
            ("password authentication failed", "认证失败"),
            ("connection refused", "不可达"),
            ("unexpected detail", "检查连接设置"),
        ]:
            with self.subTest(reason=reason), redirect_stdout(io.StringIO()) as output:
                connect.side_effect = psycopg.OperationalError("SENSITIVE " + reason)
                self.assertFalse(check_single(0, "dbname=test"))
                self.assertIn(expected, output.getvalue())
                self.assertNotIn("SENSITIVE", output.getvalue())

    @patch("pg_mcp.diagnostics.connect_readonly")
    def test_occupied_port_still_reports_database_identity(self, connect):
        conn = connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = ("test_db", "reader", "on")
        with socket.socket() as taken, redirect_stdout(io.StringIO()) as output:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            self.assertFalse(check_single(taken.getsockname()[1], "dbname=test"))
            self.assertIn("无法监听", output.getvalue())
            self.assertIn('"database": "test_db"', output.getvalue())
            self.assertIn('"transaction_read_only": "on"', output.getvalue())
