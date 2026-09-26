import subprocess
import sys
import os

def test_cli_help():
    env = os.environ.copy()
    env["PATH"] = "D:\\Anaconda3;D:\\Anaconda3\\Scripts;D:\\Anaconda3\\Library\\bin;" + env.get("PATH", "")
    env["PYTHONPATH"] = "."
    res = subprocess.run([sys.executable, "main.py", "--help"], capture_output=True, text=True, env=env)
    assert res.returncode == 0
    assert "厦大体育馆自动预约" in res.stdout
    assert "book" in res.stdout
    assert "heartbeat" in res.stdout
    assert "check" in res.stdout
    assert "harvest" in res.stdout
    assert "query" in res.stdout


def test_web_server_can_bind_only_to_loopback():
    from unittest.mock import MagicMock, patch
    from main import build_cli_parser
    from xdty_booking.web.server import run_server
    args = build_cli_parser().parse_args(['web', '--host', '127.0.0.1'])
    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt
    with patch('xdty_booking.web.server.ThreadingHTTPServer', return_value=server) as factory:
        run_server(host=args.host)
    assert factory.call_args.args[0] == ('127.0.0.1', 8080)
