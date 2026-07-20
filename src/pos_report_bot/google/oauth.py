from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
from typing import Any

from pydantic import BaseModel

from pos_report_bot.config.models import ProjectConfig


GOOGLE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
)
GOOGLE_DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.file",)
GOOGLE_GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.send",)
GOOGLE_SHEETS_SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)
GOOGLE_COMBINED_PROFILE = "combined"
GOOGLE_DRIVE_PROFILE = "drive"
GOOGLE_GMAIL_PROFILE = "gmail"
GOOGLE_SHEETS_PROFILE = "sheets"


class GoogleOAuthReauthRequired(RuntimeError):
    error_code = "GOOGLE_OAUTH_REAUTH_REQUIRED"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GoogleOAuthResult(BaseModel):
    ok: bool
    message: str
    account_email: str | None = None
    token_path: str | None = None
    error_code: str | None = None


class GoogleOAuthService:
    keyring_service = "POSReportBot Google OAuth"
    legacy_keyring_user = "google_user_token"

    def __init__(
        self,
        config: ProjectConfig,
        *,
        flow_factory: Any | None = None,
        credentials_cls: Any | None = None,
        request_factory: Any | None = None,
        build_func: Any | None = None,
        scopes: tuple[str, ...] | list[str] | None = None,
        profile: str = GOOGLE_COMBINED_PROFILE,
    ) -> None:
        self.config = config
        self.flow_factory = flow_factory
        self.credentials_cls = credentials_cls
        self.request_factory = request_factory
        self.build_func = build_func
        self.scopes = tuple(scopes or GOOGLE_OAUTH_SCOPES)
        self.profile = _normalize_google_oauth_profile(profile)

    @property
    def credentials_path(self) -> Path:
        return Path(self.config.google_drive.client_secret_path)

    @property
    def token_path(self) -> Path:
        return Path(self.config.app.state_dir) / self._token_filename()

    def connect(self) -> GoogleOAuthResult:
        credentials_path = self.credentials_path
        if not credentials_path.exists():
            return GoogleOAuthResult(
                ok=False,
                error_code="GOOGLE_CREDENTIALS_JSON_NOT_FOUND",
                message=f"找不到 Google credentials JSON：{credentials_path}",
            )
        try:
            flow_factory = self._flow_factory()
            flow = flow_factory.from_client_secrets_file(str(credentials_path), scopes=list(self.scopes))
            credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
            self._write_token(credentials)
            email = self.account_email(credentials)
        except Exception as exc:
            return GoogleOAuthResult(
                ok=False,
                error_code="GOOGLE_OAUTH_FAILED",
                message=f"Google OAuth 授權失敗：{exc}",
            )
        return GoogleOAuthResult(
            ok=True,
            message=f"Google OAuth 已連線：{email or '帳號已授權'}",
            account_email=email,
            token_path=self._token_location_label(),
        )

    def status(self) -> GoogleOAuthResult:
        try:
            credentials = self.credentials()
            email = self.account_email(credentials)
        except FileNotFoundError:
            return GoogleOAuthResult(ok=False, error_code="GOOGLE_TOKEN_MISSING", message="尚未完成 Google OAuth 授權。")
        except GoogleOAuthReauthRequired as exc:
            return GoogleOAuthResult(ok=False, error_code=exc.error_code, message=exc.message)
        except Exception as exc:
            return GoogleOAuthResult(ok=False, error_code="GOOGLE_TOKEN_INVALID", message=f"Google token 無法使用：{exc}")
        return GoogleOAuthResult(
            ok=True,
            message=f"Google OAuth 可用：{email or '已連線'}",
            account_email=email,
            token_path=self._token_location_label(),
        )

    def credentials(self) -> Any:
        token_text = self._read_token_text()
        credentials_cls = self._credentials_cls()
        if hasattr(credentials_cls, "from_authorized_user_info"):
            credentials = credentials_cls.from_authorized_user_info(
                json.loads(token_text),
                scopes=list(self.scopes),
            )
        else:
            # Test doubles may only implement from_authorized_user_file.
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(token_text, encoding="utf-8")
            credentials = credentials_cls.from_authorized_user_file(str(self.token_path), scopes=list(self.scopes))
        if getattr(credentials, "expired", False) and getattr(credentials, "refresh_token", None):
            try:
                credentials.refresh(self._request_factory()())
            except Exception as exc:
                if _is_invalid_scope_error(exc):
                    raise GoogleOAuthReauthRequired(_google_oauth_reauth_message(self._token_location_label())) from exc
                raise
            self._write_token(credentials)
        return credentials

    def account_email(self, credentials: Any | None = None) -> str | None:
        credentials = credentials or self.credentials()
        try:
            service = self._build_func()("oauth2", "v2", credentials=credentials)
            payload = service.userinfo().get().execute()
        except Exception:
            return None
        email = payload.get("email") if isinstance(payload, dict) else None
        return str(email) if email else None

    def _write_token(self, credentials: Any) -> None:
        self._write_token_text(credentials.to_json())

    def _write_token_text(self, token_text: str) -> None:
        if self.config.google_drive.token_storage == "plaintext_test":
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(token_text, encoding="utf-8")
            return
        if self._write_token_to_keyring(token_text):
            return
        if sys.platform.startswith("win"):
            self._write_token_to_windows_dpapi(token_text)
            return
        raise RuntimeError("目前環境沒有可用的 Google token 安全儲存方式；請安裝 keyring 或在 Windows 執行。")

    def _read_token_text(self) -> str:
        if self.config.google_drive.token_storage == "plaintext_test":
            token_path = self._first_existing_token_path()
            if token_path is None:
                raise FileNotFoundError(self._token_location_label())
            return token_path.read_text(encoding="utf-8")
        token = self._read_token_from_keyring()
        if token:
            return token
        if sys.platform.startswith("win"):
            token_path = self._first_existing_token_path()
            if token_path is not None:
                return self._read_token_from_windows_dpapi(token_path)
        raise FileNotFoundError(self._token_location_label())

    def _write_token_to_keyring(self, token_text: str) -> bool:
        try:
            import keyring  # type: ignore[import-not-found]

            keyring.set_password(self.keyring_service, self._keyring_user(), token_text)
        except Exception:
            return False
        return True

    def _read_token_from_keyring(self) -> str | None:
        try:
            import keyring

            token = keyring.get_password(self.keyring_service, self._keyring_user())
            if not token and self.profile != GOOGLE_COMBINED_PROFILE:
                token = keyring.get_password(self.keyring_service, self.legacy_keyring_user)
        except Exception:
            return None
        return str(token) if token else None

    def _write_token_to_windows_dpapi(self, token_text: str) -> None:
        try:
            import win32crypt  # type: ignore[import-untyped]
        except Exception as exc:
            raise RuntimeError("Windows DPAPI 不可用，無法安全儲存 Google token。") from exc
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = win32crypt.CryptProtectData(token_text.encode("utf-8"), None, None, None, None, 0)
        self.token_path.write_text(base64.b64encode(encrypted).decode("ascii"), encoding="ascii")

    def _read_token_from_windows_dpapi(self, token_path: Path | None = None) -> str:
        try:
            import win32crypt
        except Exception as exc:
            raise RuntimeError("Windows DPAPI 不可用，無法讀取 Google token。") from exc
        encrypted = base64.b64decode((token_path or self.token_path).read_text(encoding="ascii"))
        _description, decrypted = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
        return str(decrypted.decode("utf-8"))

    def _token_location_label(self) -> str:
        if self.config.google_drive.token_storage == "plaintext_test":
            return self._token_paths_label()
        if sys.platform.startswith("win"):
            return f"keyring:{self.keyring_service}/{self._keyring_user()} 或 DPAPI:{self._token_paths_label()}"
        return f"keyring:{self.keyring_service}/{self._keyring_user()}"

    def _keyring_user(self) -> str:
        if self.profile == GOOGLE_COMBINED_PROFILE:
            return self.legacy_keyring_user
        return f"google_{self.profile}_user_token"

    def _token_filename(self) -> str:
        if self.profile == GOOGLE_COMBINED_PROFILE:
            return "google_user_token.bin"
        return f"google_{self.profile}_user_token.bin"

    def _legacy_token_path(self) -> Path:
        return Path(self.config.app.state_dir) / "google_user_token.bin"

    def _candidate_token_paths(self) -> list[Path]:
        paths = [self.token_path]
        legacy_path = self._legacy_token_path()
        if legacy_path != self.token_path:
            paths.append(legacy_path)
        return paths

    def _first_existing_token_path(self) -> Path | None:
        return next((path for path in self._candidate_token_paths() if path.exists()), None)

    def _token_paths_label(self) -> str:
        return " 或 ".join(str(path) for path in self._candidate_token_paths())

    def _flow_factory(self) -> Any:
        if self.flow_factory is not None:
            return self.flow_factory
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]

        return InstalledAppFlow

    def _credentials_cls(self) -> Any:
        if self.credentials_cls is not None:
            return self.credentials_cls
        from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]

        return Credentials

    def _request_factory(self) -> Any:
        if self.request_factory is not None:
            return self.request_factory
        from google.auth.transport.requests import Request  # type: ignore[import-not-found]

        return Request

    def _build_func(self) -> Any:
        if self.build_func is not None:
            return self.build_func
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        return build


def _is_invalid_scope_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "invalid_scope" in text


def _google_oauth_reauth_message(token_location: str) -> str:
    return (
        "Google OAuth 授權範圍已失效或與目前程式需要的權限不一致，請到「Google Drive 設定」重新連接 Google Drive。"
        f" 若重新連接後仍失敗，請移除舊 token 後再授權；目前 token 位置：{token_location}。"
    )


def _normalize_google_oauth_profile(profile: str) -> str:
    normalized = profile.strip().lower().replace("_", "-")
    aliases = {
        "": GOOGLE_COMBINED_PROFILE,
        "default": GOOGLE_COMBINED_PROFILE,
        "legacy": GOOGLE_COMBINED_PROFILE,
        "combined": GOOGLE_COMBINED_PROFILE,
        "drive": GOOGLE_DRIVE_PROFILE,
        "google-drive": GOOGLE_DRIVE_PROFILE,
        "gmail": GOOGLE_GMAIL_PROFILE,
        "sheets": GOOGLE_SHEETS_PROFILE,
        "google-sheets": GOOGLE_SHEETS_PROFILE,
    }
    if normalized not in aliases:
        raise ValueError(f"不支援的 Google OAuth token profile：{profile}")
    return aliases[normalized]
