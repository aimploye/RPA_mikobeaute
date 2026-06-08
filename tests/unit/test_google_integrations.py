from pathlib import Path

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.drive.uploader import GoogleDriveUploader
from pos_report_bot.google.gmail import GmailOAuthSender
from pos_report_bot.google.oauth import GoogleOAuthService


ROOT = Path(__file__).resolve().parents[2]


class FakeCredentials:
    expired = False
    refresh_token = "refresh"

    def __init__(self, token: str = "token") -> None:
        self.token = token
        self.refreshed = False

    @classmethod
    def from_authorized_user_file(cls, _path: str, *, scopes: list[str]):  # type: ignore[no-untyped-def]
        return cls()

    @classmethod
    def from_authorized_user_info(cls, _info, *, scopes: list[str]):  # type: ignore[no-untyped-def]
        return cls()

    def refresh(self, _request):  # type: ignore[no-untyped-def]
        self.refreshed = True

    def to_json(self) -> str:
        return '{"token":"fake"}'


class FakeFlow:
    @classmethod
    def from_client_secrets_file(cls, _path: str, *, scopes: list[str]):  # type: ignore[no-untyped-def]
        return cls()

    def run_local_server(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeCredentials()


class FakeExecute:
    def __init__(self, payload):  # type: ignore[no-untyped-def]
        self.payload = payload

    def execute(self):  # type: ignore[no-untyped-def]
        return self.payload


class FakeOAuth2Service:
    def userinfo(self):  # type: ignore[no-untyped-def]
        return self

    def get(self):  # type: ignore[no-untyped-def]
        return FakeExecute({"email": "user@example.com"})


class FakeDriveService:
    def files(self):  # type: ignore[no-untyped-def]
        return self

    def create(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute({"id": "drive-file-1", "name": "report.xls", "size": "12", "mimeType": "application/vnd.ms-excel"})


class FakeDriveServiceWithoutFileId:
    def files(self):  # type: ignore[no-untyped-def]
        return self

    def create(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute({"name": "report.xls", "size": "12", "mimeType": "application/vnd.ms-excel"})


class FakeGmailService:
    def users(self):  # type: ignore[no-untyped-def]
        return self

    def messages(self):  # type: ignore[no-untyped-def]
        return self

    def send(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute({"id": "gmail-message-1"})


class FakeMediaFileUpload:
    def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        pass


def fake_build(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "oauth2":
        return FakeOAuth2Service()
    if api_name == "drive":
        return FakeDriveService()
    if api_name == "gmail":
        return FakeGmailService()
    raise AssertionError(api_name)


def fake_build_without_drive_file_id(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "oauth2":
        return FakeOAuth2Service()
    if api_name == "drive":
        return FakeDriveServiceWithoutFileId()
    raise AssertionError(api_name)


def test_google_oauth_connect_writes_token_and_reads_account(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    config.google_drive.client_secret_path = str(credentials_path)
    config.google_drive.token_storage = "plaintext_test"
    config.app.state_dir = str(tmp_path)

    result = GoogleOAuthService(
        config,
        flow_factory=FakeFlow,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
    ).connect()

    assert result.ok is True
    assert result.account_email == "user@example.com"
    assert (tmp_path / "google_user_token.bin").exists()


def test_google_drive_uploader_uploads_with_oauth_credentials(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    file_path = tmp_path / "report.xls"
    file_path.write_bytes(b"excel")
    oauth = GoogleOAuthService(config, credentials_cls=FakeCredentials, request_factory=lambda: object, build_func=fake_build)

    result = GoogleDriveUploader(oauth, media_file_upload_cls=FakeMediaFileUpload, build_func=fake_build).upload(
        file_path,
        "folder123",
        "report.xls",
    )

    assert result.success is True
    assert result.drive_file_id == "drive-file-1"


def test_google_drive_uploader_requires_drive_file_id(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    file_path = tmp_path / "report.xls"
    file_path.write_bytes(b"excel")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build_without_drive_file_id,
    )

    result = GoogleDriveUploader(
        oauth,
        media_file_upload_cls=FakeMediaFileUpload,
        build_func=fake_build_without_drive_file_id,
    ).upload(file_path, "folder123", "report.xls")

    assert result.success is False
    assert result.error_code == "DRIVE_FILE_ID_MISSING"


def test_gmail_oauth_sender_sends_message(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    config.email.recipients = ["ops@example.com"]
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthService(config, credentials_cls=FakeCredentials, request_factory=lambda: object, build_func=fake_build)

    result = GmailOAuthSender(oauth, build_func=fake_build).send(config.email, subject="Test", body="Body")

    assert result.ok is True
    assert result.gmail_message_id == "gmail-message-1"
