import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ticket_app.client import RailwayClient
from ticket_app.configuration import AppError
from ticket_app.crypto import encrypt_password


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def route(responses):
    """Mock session.post dispatching on URL suffix; each value is a payload or list of payloads."""
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs.get("data")))
        for suffix, payload in responses.items():
            if url.endswith(suffix):
                if isinstance(payload, list):
                    return FakeResponse(payload.pop(0))
                return FakeResponse(payload)
        raise AssertionError(f"unexpected POST {url}")

    return post, calls


class LoginTest(unittest.TestCase):
    def make_client(self, temp_dir, persist=True, with_cached_session=True):
        session_file = Path(temp_dir) / "session.cookies"
        if with_cached_session:
            session_file.write_text("# Netscape HTTP Cookie File\n")
        cfg = SimpleNamespace(
            persist_session=persist,
            session_file=session_file,
            qr_code_file=Path(temp_dir) / "login_qr.png",
            request_timeout_seconds=5,
            login_qr_timeout_seconds=30,
            login_qr_poll_seconds=0.001,
        )
        events = []
        return RailwayClient(cfg, event_sink=events.append), events

    def test_expired_otn_session_is_refreshed_via_uamtk_without_qr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client, events = self.make_client(temp_dir)
            self.assertTrue(client.session_loaded)
            client.session.post, calls = route(
                {
                    "/otn/login/checkUser": [{"data": {"flag": False}}, {"data": {"flag": True}}],
                    "/passport/web/auth/uamtk": {"result_code": 0, "newapptk": "tk-1"},
                    "/otn/uamauthclient": {"result_code": 0, "username": "张三"},
                }
            )
            client._create_qr_code = Mock()

            client.ensure_login()

            client._create_qr_code.assert_not_called()
            self.assertEqual(client.username, "张三")
            self.assertEqual(events[-1].data["status"], "logged_in")
            self.assertIn(("https://kyfw.12306.cn/otn/uamauthclient", {"tk": "tk-1"}), calls)

    def test_invalid_uamtk_falls_back_to_qr_login(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client, _events = self.make_client(temp_dir)
            client.session.post, _calls = route(
                {
                    "/otn/login/checkUser": {"data": {"flag": False}},
                    "/passport/web/auth/uamtk": {"result_code": 1, "result_message": "非法的ticker"},
                }
            )
            client._prefetch_login_cookies = Mock()
            client._create_qr_code = Mock(return_value=(b"qr", "uuid"))
            client._check_qr_status = Mock(return_value=("3", "expired"))

            with self.assertRaisesRegex(AppError, "二维码已过期"):
                client.ensure_login()
            client._create_qr_code.assert_called_once()

    def test_no_cached_session_skips_uamtk_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client, _events = self.make_client(temp_dir, with_cached_session=False)
            client.check_session = Mock(return_value=False)
            client._complete_login = Mock()

            self.assertFalse(client.resume_session())
            client._complete_login.assert_not_called()

    def test_complete_login_rejects_session_that_checkuser_reports_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client, _events = self.make_client(temp_dir, with_cached_session=False)
            client.session.post, _calls = route(
                {
                    "/passport/web/auth/uamtk": {"result_code": 0, "newapptk": "tk"},
                    "/otn/uamauthclient": {"result_code": 0, "username": "张三"},
                    "/otn/login/checkUser": {"data": {"flag": False}},
                }
            )

            ok, message = client._complete_login()

            self.assertFalse(ok)
            self.assertIn("checkUser", message)

    def test_password_login_with_sms_encrypts_password_and_saves_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client, _events = self.make_client(temp_dir, with_cached_session=False)
            client._prefetch_login_cookies = Mock()
            client.session.post, calls = route(
                {
                    "/passport/web/checkLoginVerify": {"login_check_code": "3"},
                    "/passport/web/getMessageCode": {"result_code": 0, "result_message": "已发送"},
                    "/passport/web/login": {"result_code": 0},
                    "/passport/web/auth/uamtk": {"result_code": 0, "newapptk": "tk"},
                    "/otn/uamauthclient": {"result_code": 0, "username": "张三"},
                    "/otn/login/checkUser": {"data": {"flag": True}},
                }
            )

            self.assertEqual(client.check_login_verify("user"), "sms")
            self.assertEqual(client.send_sms_code("user", "1234"), "已发送")
            self.assertEqual(client.login_by_password("user", "secret", "654321"), "张三")

            login_data = next(data for url, data in calls if url.endswith("/passport/web/login"))
            self.assertEqual(login_data["password"], encrypt_password("secret"))
            self.assertEqual(login_data["randCode"], "654321")
            self.assertEqual(login_data["checkMode"], "0")
            self.assertTrue(client.cfg.session_file.exists())

    def test_slider_captcha_is_refused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client, _events = self.make_client(temp_dir, with_cached_session=False)
            client._prefetch_login_cookies = Mock()
            client.session.post, _calls = route({"/passport/web/checkLoginVerify": {"login_check_code": "1"}})

            with self.assertRaisesRegex(AppError, "滑块"):
                client.check_login_verify("user")


class CryptoTest(unittest.TestCase):
    def test_sm4_password_format(self):
        encrypted = encrypt_password("test123456")
        self.assertTrue(encrypted.startswith("@"))
        self.assertEqual(len(base64.b64decode(encrypted[1:])), 16)


if __name__ == "__main__":
    unittest.main()
