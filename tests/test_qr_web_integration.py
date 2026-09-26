import json
import unittest
from unittest.mock import patch, MagicMock
from xdty_booking.web.server import GymStatusHandler
from xdty_booking.web.template import render_qr_login_page, render_dashboard

class TestQrWebIntegration(unittest.TestCase):
    def test_render_qr_login_page(self):
        html = render_qr_login_page(is_already_logged_in=False)
        self.assertIn("厦门大学统一身份认证", html)
        self.assertIn("企业微信扫码登录", html)
        self.assertIn("/api/qr", html)
        self.assertIn("/api/qr_status", html)
        self.assertIn("qrBox", html)

    def test_render_qr_login_page_already_logged_in(self):
        html = render_qr_login_page(is_already_logged_in=True, phpsessid_masked="abcd1234***")
        self.assertIn("abcd1234***", html)
        self.assertIn("当前系统已存活有效登录态", html)
        self.assertIn("进入预约大厅", html)

    def test_render_dashboard_has_qr_login_button(self):
        data = {
            "stadium_name": "测试健身房",
            "area_name": "二楼力量区",
            "query_time": "2026-09-12 10:00:00",
            "session_valid": False,
            "info": "登录信息失效",
            "groups": []
        }
        html = render_dashboard(data)
        self.assertIn("/login", html)
        self.assertIn("扫码登录", html)

    @patch("xdty_booking.web.server.CasQrLoginClient")
    def test_handle_qr_init(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.init_qr_session.return_value = ("test-uuid-12345", b"fake-png-data")
        mock_client.get_qr_image_base64.return_value = "data:image/png;base64,ZmFrZQ=="
        mock_client_cls.return_value = mock_client

        handler = GymStatusHandler.__new__(GymStatusHandler)
        handler._send_json = MagicMock()

        handler._handle_qr_init()
        handler._send_json.assert_called_once()
        code, body = handler._send_json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["uuid"], "test-uuid-12345")
        self.assertEqual(body["qr_image"], "data:image/png;base64,ZmFrZQ==")

    @patch("xdty_booking.web.server._cas_client")
    def test_handle_qr_status_scanning(self, mock_client):
        import xdty_booking.web.server as srv
        srv._qr_login_result = None
        srv._is_logging_in = False
        srv._has_login_failed = False
        srv._cas_client = MagicMock()
        srv._cas_client.check_status.return_value = ("0", "等待手机企业微信扫码...")

        handler = GymStatusHandler.__new__(GymStatusHandler)
        handler._send_json = MagicMock()

        handler._handle_qr_status()
        handler._send_json.assert_called_once()
        code, body = handler._send_json.call_args[0]
        self.assertEqual(code, 200)
        self.assertEqual(body["code"], "0")
        self.assertFalse(body["logged_in"])

    @patch("xdty_booking.web.server.save_auth_params")
    @patch("xdty_booking.web.server.save_phpsessid")
    def test_handle_qr_status_success_and_save(self, mock_save_php, mock_save_auth):
        import xdty_booking.web.server as srv
        mock_save_php.return_value = True
        mock_save_auth.return_value = True

        srv._qr_login_result = None
        srv._is_logging_in = False
        srv._has_login_failed = False
        srv._cas_client = MagicMock()
        srv._cas_client.check_status.return_value = ("1", "手机端已确认授权！")
        srv._cas_client.exchange_and_login.return_value = {
            "success": True,
            "phpsessid": "32charslongphpsessid123456789012",
            "auth_params": {"token": "my_token", "uid": "12345"}
        }

        handler = GymStatusHandler.__new__(GymStatusHandler)
        handler._send_json = MagicMock()

        handler._handle_qr_status()
        handler._send_json.assert_called_once()
        code, body = handler._send_json.call_args[0]
        self.assertEqual(code, 200)
        self.assertEqual(body["code"], "1")
        self.assertTrue(body["logged_in"])
        self.assertIsNone(body["data"])
        mock_save_php.assert_called_once()
        mock_save_auth.assert_called_once()


class TestPasswordLoginHandler(unittest.TestCase):
    def _handler(self):
        handler = GymStatusHandler.__new__(GymStatusHandler)
        handler._send_json = MagicMock()
        return handler

    @patch("xdty_booking.web.server.save_cas_credentials")
    @patch("xdty_booking.web.server.save_auth_params")
    @patch("xdty_booking.web.server.save_phpsessid")
    @patch("xdty_booking.web.server.CasQrLoginClient")
    def test_pw_login_success_persists_and_remembers(self, mock_cls, save_sess, save_params, save_creds):
        mock_cls.return_value.password_login.return_value = {
            "success": True, "phpsessid": "sess_new", "auth_params": {"token": "t"}, "user_info": {}
        }
        handler = self._handler()
        handler._handle_pw_login({"username": "20230001", "password": "pw", "remember": True})

        mock_cls.return_value.password_login.assert_called_once_with("20230001", "pw")
        save_sess.assert_called_once()
        self.assertEqual(save_sess.call_args[0][1], "sess_new")
        save_params.assert_called_once()
        save_creds.assert_called_once()
        self.assertEqual(save_creds.call_args[0][1:], ("20230001", "pw"))
        code, body = handler._send_json.call_args[0]
        self.assertEqual(code, 200)
        self.assertTrue(body["success"])
        self.assertTrue(body["logged_in"])
        self.assertNotIn("data", body)

    @patch("xdty_booking.web.server.save_cas_credentials")
    @patch("xdty_booking.web.server.save_auth_params")
    @patch("xdty_booking.web.server.save_phpsessid")
    @patch("xdty_booking.web.server.CasQrLoginClient")
    def test_pw_login_without_remember_does_not_store_password(self, mock_cls, save_sess, save_params, save_creds):
        mock_cls.return_value.password_login.return_value = {
            "success": True, "phpsessid": "sess_new", "auth_params": {"token": "t"}
        }
        handler = self._handler()
        handler._handle_pw_login({"username": "20230001", "password": "pw", "remember": False})
        save_creds.assert_not_called()

    @patch("xdty_booking.web.server.CasQrLoginClient")
    def test_pw_login_failure_returns_cas_message(self, mock_cls):
        mock_cls.return_value.password_login.side_effect = RuntimeError("CAS 登录失败: 您提供的用户名或者密码有误")
        handler = self._handler()
        handler._handle_pw_login({"username": "20230001", "password": "bad"})
        code, body = handler._send_json.call_args[0]
        self.assertEqual(code, 200)
        self.assertFalse(body["success"])
        self.assertIn("用户名或者密码有误", body["error"])

    def test_pw_login_rejects_empty_fields(self):
        handler = self._handler()
        handler._handle_pw_login({"username": "", "password": ""})
        code, body = handler._send_json.call_args[0]
        self.assertEqual(code, 400)
        self.assertFalse(body["success"])

    def test_login_page_contains_password_form(self):
        from xdty_booking.web.template import render_qr_login_page
        html = render_qr_login_page(is_already_logged_in=False, phpsessid_masked="")
        self.assertIn("/api/pw_login", html)
        self.assertIn('name="username"', html)
        self.assertIn('type="password"', html)
