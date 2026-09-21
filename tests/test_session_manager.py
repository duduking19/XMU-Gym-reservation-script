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
