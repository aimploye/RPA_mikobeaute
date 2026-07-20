from __future__ import annotations

import base64
from email.message import EmailMessage
from html import unescape
import mimetypes
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel

from pos_report_bot.config.models import EmailSettings
from pos_report_bot.google.oauth import GoogleOAuthService


class GmailSendResult(BaseModel):
    ok: bool
    message: str
    gmail_message_id: str | None = None
    error_code: str | None = None


class GmailOAuthSender:
    def __init__(self, oauth: GoogleOAuthService, *, build_func: Any | None = None) -> None:
        self.oauth = oauth
        self.build_func = build_func

    def send(
        self,
        settings: EmailSettings,
        *,
        subject: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> GmailSendResult:
        if not settings.recipients:
            return GmailSendResult(ok=False, error_code="GMAIL_RECIPIENTS_MISSING", message="尚未設定 Gmail 收件人。")

        message = EmailMessage()
        message["To"] = ", ".join(settings.recipients)
        if settings.cc:
            message["Cc"] = ", ".join(settings.cc)
        if settings.username:
            message["From"] = settings.username
        message["Subject"] = subject
        if _contains_html_table(body):
            message.set_content(_html_to_plain_text(body))
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)
        for attachment in attachments or []:
            if not attachment.exists() or not attachment.is_file():
                return GmailSendResult(
                    ok=False,
                    error_code="GMAIL_ATTACHMENT_MISSING",
                    message=f"Gmail API 附件不存在：{attachment}",
                )
            guessed_type, _encoding = mimetypes.guess_type(str(attachment))
            maintype, subtype = (guessed_type or "application/octet-stream").split("/", 1)
            message.add_attachment(
                attachment.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=attachment.name,
            )
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        try:
            service = self._build_func()("gmail", "v1", credentials=self.oauth.credentials())
            result = service.users().messages().send(userId="me", body={"raw": encoded}).execute()
        except Exception as exc:
            return GmailSendResult(ok=False, error_code="GMAIL_SEND_FAILED", message=f"Gmail API 寄送失敗：{exc}")

        message_id = result.get("id") if isinstance(result, dict) else None
        return GmailSendResult(ok=True, gmail_message_id=message_id, message="Gmail API message sent.")

    def _build_func(self) -> Any:
        if self.build_func is not None:
            return self.build_func
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        return build


def _contains_html_table(body: str) -> bool:
    return bool(re.search(r"<\s*table\b", body, flags=re.IGNORECASE))


def _html_to_plain_text(body: str) -> str:
    text = re.sub(r"<\s*/\s*(p|tr|table|thead|tbody)\s*>", "\n", body, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/\s*(td|th)\s*>", "\t", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
