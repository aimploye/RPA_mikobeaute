from dataclasses import dataclass
from email.message import EmailMessage
import importlib
import smtplib
from typing import Any

from pos_report_bot.config.models import EmailSettings


@dataclass(frozen=True)
class EmailDeliveryResult:
    ok: bool
    message: str


def send_failure_notification(settings: EmailSettings, *, subject: str, body: str) -> EmailDeliveryResult:
    if not settings.enabled or not settings.notify_on_failure:
        return EmailDeliveryResult(ok=True, message="Email failure notification is disabled.")
    if not settings.smtp_host or not settings.recipients:
        return EmailDeliveryResult(ok=False, message="Email 通知已啟用，但尚未設定 SMTP host 或收件人。")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.username or "POSReportBot"
    message["To"] = ", ".join(settings.recipients)
    if settings.cc:
        message["Cc"] = ", ".join(settings.cc)
    message.set_content(body)

    recipients = [*settings.recipients, *settings.cc]
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.use_tls:
                smtp.starttls()
            if settings.username:
                password = _smtp_password(settings.username)
                if not password:
                    return EmailDeliveryResult(ok=False, message="Email 通知已啟用，但找不到 SMTP 密碼。")
                smtp.login(settings.username, password)
            smtp.send_message(message, to_addrs=recipients)
    except Exception as exc:
        return EmailDeliveryResult(ok=False, message=f"Email 通知寄送失敗：{exc}")
    return EmailDeliveryResult(ok=True, message="Email failure notification sent.")


def _smtp_password(username: str) -> str:
    try:
        keyring: Any = importlib.import_module("keyring")
    except ImportError:
        return ""
    get_password = getattr(keyring, "get_password", None)
    if get_password is None:
        return ""
    for service_name in ("POSReportBot SMTP", "POSReportBot"):
        try:
            password = get_password(service_name, username)
        except Exception:
            password = None
        if password:
            return str(password)
    return ""
