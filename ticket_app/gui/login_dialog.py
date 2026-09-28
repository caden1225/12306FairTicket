"""Password + SMS login dialog that signs the GUI's shared session in without a QR code."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, Callable, Optional

import requests
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ticket_app.client import RailwayClient

SMS_COOLDOWN_SECONDS = 60


class _CallRelay(QObject):
    """Deliver a background call's (result, error) back onto the GUI thread."""

    finished = Signal(object, object, object)


class SmsLoginDialog(QDialog):
    """Logs `session` in via 12306 password + SMS code. Cookies stay in memory only."""

    def __init__(self, session: requests.Session, timeout_seconds: float = 10.0, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("短信验证码登录")
        self.setMinimumWidth(380)
        cfg = SimpleNamespace(persist_session=False, request_timeout_seconds=timeout_seconds)
        self.client = RailwayClient(cfg, session=session)
        self.username = ""
        self._verify_mode: Optional[str] = None
        self._busy = False
        self._cooldown = 0

        self._relay = _CallRelay(self)
        self._relay.finished.connect(self._on_call_finished)
        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.setInterval(1000)
        self._cooldown_timer.timeout.connect(self._tick_cooldown)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("用户名 / 手机号 / 邮箱")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.id_last4_edit = QLineEdit()
        self.id_last4_edit.setMaxLength(4)
        self.id_last4_edit.setPlaceholderText("证件号后 4 位")
        self.sms_button = QPushButton("获取验证码")
        self.sms_button.clicked.connect(self._request_sms)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(6)
        self.code_edit.setPlaceholderText("6 位短信验证码")

        id_row = QHBoxLayout()
        id_row.addWidget(self.id_last4_edit, 1)
        id_row.addWidget(self.sms_button)

        form = QFormLayout()
        form.addRow("账号", self.username_edit)
        form.addRow("密码", self.password_edit)
        form.addRow("证件号", id_row)
        form.addRow("验证码", self.code_edit)

        self.message = QLabel("填写账号密码和证件号后 4 位，获取短信验证码后登录。")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)

        self.login_button = QPushButton("登录")
        self.login_button.setObjectName("primaryButton")
        self.login_button.clicked.connect(self._login)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.login_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.message)
        layout.addLayout(buttons)

    # ----- background calls ------------------------------------------------
    def _run(self, action: str, fn: Callable[[], Any]) -> None:
        self._set_busy(True)

        relay = self._relay

        def target() -> None:
            try:
                result, error = fn(), None
            except Exception as exc:  # surfaced to the user, never raised in the worker thread
                result, error = None, exc
            try:
                relay.finished.emit(action, result, error)
            except RuntimeError:
                pass  # dialog was closed before the request finished

        threading.Thread(target=target, name=f"sms-login-{action}", daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.login_button.setEnabled(not busy)
        self.sms_button.setEnabled(not busy and self._cooldown == 0)

    def _show(self, text: str, error: bool = False) -> None:
        self.message.setText(text)
        self.message.setStyleSheet("color: #e53935;" if error else "")

    # ----- actions ---------------------------------------------------------
    def _credentials(self) -> Optional[tuple[str, str]]:
        username, password = self.username_edit.text().strip(), self.password_edit.text()
        if not username or not password:
            self._show("请输入账号和密码", error=True)
            return None
        return username, password

    def _request_sms(self) -> None:
        creds = self._credentials()
        if not creds:
            return
        id_last4 = self.id_last4_edit.text().strip()
        if len(id_last4) != 4:
            self._show("请输入证件号后 4 位", error=True)
            return
        username = creds[0]

        def call() -> tuple[str, str]:
            mode = self.client.check_login_verify(username)
            if mode == "none":
                return mode, "该账号当前无需短信验证，可直接点击登录"
            return mode, self.client.send_sms_code(username, id_last4)

        self._show("正在发送验证码…")
        self._run("sms", call)

    def _login(self) -> None:
        creds = self._credentials()
        if not creds:
            return
        username, password = creds
        code = self.code_edit.text().strip()
        verify_mode = self._verify_mode

        def call() -> str:
            mode = verify_mode or self.client.check_login_verify(username)
            if mode == "sms" and not code:
                raise ValueError("该账号需要短信验证，请先获取并填写验证码")
            return self.client.login_by_password(username, password, code if mode == "sms" else "")

        self._show("正在登录…")
        self._run("login", call)

    def _on_call_finished(self, action: str, result: Any, error: Optional[BaseException]) -> None:
        self._set_busy(False)
        if error is not None:
            self._show(str(error), error=True)
            return
        if action == "sms":
            self._verify_mode, message = result
            self._show(message)
            if self._verify_mode == "sms":
                self._cooldown = SMS_COOLDOWN_SECONDS
                self._tick_cooldown()
                self._cooldown_timer.start()
                self.code_edit.setFocus()
        elif action == "login":
            self.username = str(result or "")
            self.password_edit.clear()
            self.accept()

    def _tick_cooldown(self) -> None:
        if self._cooldown <= 0:
            self._cooldown_timer.stop()
            self.sms_button.setText("获取验证码")
            self.sms_button.setEnabled(not self._busy)
            return
        self.sms_button.setText(f"{self._cooldown}s")
        self.sms_button.setEnabled(False)
        self._cooldown -= 1
