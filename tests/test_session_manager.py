import pytest
from unittest.mock import Mock
from xdty_booking.auth.session_manager import SessionManager

def test_session_check_alive_success():
    api = Mock()
    api.my_subscribe.return_value = {"status": 1, "info": "查询成功", "data": []}
    mgr = SessionManager(api, phpsessid="valid_token")
    assert mgr.check_alive() is True

def test_session_check_alive_failure_status():
    api = Mock()
    api.my_subscribe.return_value = {"status": -1, "info": "登录过期"}
    mgr = SessionManager(api, phpsessid="expired_token")
    assert mgr.check_alive() is False

def test_session_check_alive_exception():
    api = Mock()
    api.my_subscribe.side_effect = Exception("Network error")
    mgr = SessionManager(api, phpsessid="token")
    assert mgr.check_alive() is False

def test_session_heartbeat_daemon():
    api = Mock()
    api.my_subscribe.return_value = {"status": 1, "info": "查询成功"}
    mgr = SessionManager(api, phpsessid="token")
    mgr.start_heartbeat_daemon(interval_seconds=1)
    assert mgr._running is True
    mgr.stop()
    assert mgr._running is False


def _cfg_with_creds(tmp_path, username="20230001", password="pw"):
    p = tmp_path / "config.yaml"
    p.write_text(
        f'auth:\n  phpsessid: "old"\n  cas_username: "{username}"\n  cas_password: "{password}"\n',
        encoding="utf-8",
    )
    return str(p)


def test_refresh_session_via_password_logs_in_and_persists(tmp_path):
    from unittest.mock import patch
    from xdty_booking.config import load_config
    api = Mock()
    api.my_subscribe.return_value = {"status": 1}
    mgr = SessionManager(api, phpsessid="old", config_path=_cfg_with_creds(tmp_path))

    with patch("xdty_booking.auth.session_manager.CasQrLoginClient") as cls:
        cls.return_value.password_login.return_value = {
            "success": True, "phpsessid": "new_sess", "auth_params": {"token": "t1", "uid": "9"}
        }
        assert mgr.refresh_session_via_password() == "new_sess"
        cls.return_value.password_login.assert_called_once_with("20230001", "pw")

    assert mgr.phpsessid == "new_sess"
    assert mgr.auth_params == {"token": "t1", "uid": "9"}
    api.client.set_session_token.assert_called_with("new_sess")
    cfg = load_config(mgr.config_path)
    assert cfg.auth.phpsessid == "new_sess"
    assert cfg.auth.auth_params["token"] == "t1"


def test_refresh_session_via_password_skips_without_credentials(tmp_path):
    from unittest.mock import patch
    mgr = SessionManager(Mock(), config_path=_cfg_with_creds(tmp_path, "", ""))
    with patch("xdty_booking.auth.session_manager.CasQrLoginClient") as cls:
        assert mgr.refresh_session_via_password() is None
        cls.assert_not_called()


def test_renew_or_fallback_uses_password_before_on_expired(tmp_path):
    from unittest.mock import patch
    api = Mock()
    api.check_login.return_value = (False, None, {"info": "token 过期"})
    on_expired = Mock(return_value="harvested")
    mgr = SessionManager(api, phpsessid="old", auth_params={"token": "dead"},
                         config_path=_cfg_with_creds(tmp_path), on_expired=on_expired)

    with patch.object(mgr, "refresh_session_via_password", return_value="pw_sess") as pw:
        assert mgr.renew_or_fallback() == "pw_sess"
        pw.assert_called_once()
    on_expired.assert_not_called()


# ---------- 登录状态监控（heartbeat_loop + notifier） ----------

def _run_ticks(mgr, alive_sequence, heal_result=None, interval=1):
    """按 alive_sequence 驱动若干轮心跳，序列耗尽后停止循环"""
    seq = list(alive_sequence)

    def alive():
        if not seq:
            mgr.stop()
            return True
        v = seq.pop(0)
        if not seq:
            mgr._running = False  # 本轮探测后即退出，避免额外休眠
        return v

    mgr.check_alive = alive
    mgr.renew_or_fallback = Mock(return_value=heal_result)
    mgr.heartbeat_loop(interval_seconds=interval, notifier=mgr._test_notifier)


def _monitor(tmp_path, phpsessid="sess"):
    p = tmp_path / "config.yaml"
    p.write_text(f'auth:\n  phpsessid: "{phpsessid}"\n', encoding="utf-8")
    api = Mock()
    mgr = SessionManager(api, phpsessid=phpsessid, config_path=str(p))
    mgr._test_notifier = Mock()
    return mgr, api, p


def test_monitor_notifies_once_while_expired_and_once_on_recovery(tmp_path):
    mgr, _, _ = _monitor(tmp_path)
    _run_ticks(mgr, [True, False, False, False, True], heal_result=None)

    titles = [c.kwargs["title"] for c in mgr._test_notifier.send.call_args_list]
    assert len(titles) == 2
    assert "登录失效" in titles[0] and "失败" in titles[0]
    assert "恢复" in titles[1]
    assert mgr.renew_or_fallback.call_count == 3  # 每轮失效都尝试自愈，但只通知一次


def test_monitor_reports_successful_auto_relogin(tmp_path):
    mgr, _, _ = _monitor(tmp_path)
    _run_ticks(mgr, [False], heal_result="new_sess")

    kwargs = mgr._test_notifier.send.call_args.kwargs
    assert "登录失效" in kwargs["title"] and "已自动重新登录" in kwargs["title"]
    assert "new_sess"[:8] in kwargs["content"]


def test_monitor_adopts_credentials_from_config_before_each_probe(tmp_path):
    mgr, api, p = _monitor(tmp_path, phpsessid="old")
    p.write_text('auth:\n  phpsessid: "web_login_sess"\n  auth_params:\n    token: "t9"\n', encoding="utf-8")
    _run_ticks(mgr, [True])

    assert mgr.phpsessid == "web_login_sess"
    assert mgr.auth_params == {"token": "t9"}
    api.client.set_session_token.assert_called_with("web_login_sess")
    mgr._test_notifier.send.assert_not_called()


def test_monitor_survives_notifier_exception(tmp_path):
    mgr, _, _ = _monitor(tmp_path)
    mgr._test_notifier.send.side_effect = RuntimeError("feishu down")
    _run_ticks(mgr, [False, True])  # 不抛异常即可
