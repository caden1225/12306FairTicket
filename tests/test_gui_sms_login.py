from __future__ import annotations

import requests
from PySide6.QtWidgets import QDialog

from ticket_app.client import RailwayClient
from ticket_app.gui import app as gui_app
from ticket_app.gui.login_dialog import SmsLoginDialog


def fill(dialog: SmsLoginDialog, code: str = "") -> None:
    dialog.username_edit.setText("user")
    dialog.password_edit.setText("secret")
    dialog.id_last4_edit.setText("1234")
    dialog.code_edit.setText(code)


def test_sms_flow_logs_in_shared_session(qtbot, monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(RailwayClient, "check_login_verify", lambda self, u: calls.append(("verify", u)) or "sms")
    monkeypatch.setattr(RailwayClient, "send_sms_code", lambda self, u, n: calls.append(("sms", u, n)) or "已发送")

    def login(self, username, password, code=""):
        calls.append(("login", username, password, code))
        self.session.cookies.set("tk", "valid")
        return "张三"

    monkeypatch.setattr(RailwayClient, "login_by_password", login)
    session = requests.Session()
    dialog = SmsLoginDialog(session)
    qtbot.addWidget(dialog)
    fill(dialog)

    dialog.sms_button.click()
    qtbot.waitUntil(lambda: dialog.message.text() == "已发送")
    assert not dialog.sms_button.isEnabled()
    assert dialog.sms_button.text().endswith("s")

    dialog.code_edit.setText("654321")
    with qtbot.waitSignal(dialog.accepted):
        dialog.login_button.click()

    assert dialog.username == "张三"
    assert dialog.password_edit.text() == ""
    assert session.cookies.get("tk") == "valid"
    assert calls == [("verify", "user"), ("sms", "user", "1234"), ("login", "user", "secret", "654321")]


def test_login_without_code_is_refused_when_sms_required(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(RailwayClient, "check_login_verify", lambda self, u: "sms")
    login_calls: list[str] = []
    monkeypatch.setattr(RailwayClient, "login_by_password", lambda *a, **k: login_calls.append("x"))
    dialog = SmsLoginDialog(requests.Session())
    qtbot.addWidget(dialog)
    fill(dialog)

    dialog.login_button.click()

    qtbot.waitUntil(lambda: "请先获取并填写验证码" in dialog.message.text())
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.login_button.isEnabled()
    assert login_calls == []


def test_server_error_is_shown_in_dialog(qtbot, monkeypatch) -> None:
    def fail(self, u):
        raise gui_app.AppError("该账号当前需要滑块验证")

    monkeypatch.setattr(RailwayClient, "check_login_verify", fail)
    dialog = SmsLoginDialog(requests.Session())
    qtbot.addWidget(dialog)
    fill(dialog)

    dialog.sms_button.click()

    qtbot.waitUntil(lambda: "滑块" in dialog.message.text())
    assert dialog.sms_button.isEnabled()
