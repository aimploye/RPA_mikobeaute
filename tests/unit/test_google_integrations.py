import base64
from datetime import date
from email import message_from_bytes
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pos_report_bot.config.loader import load_project_config
from pos_report_bot.drive.uploader import GoogleDriveUploader
from pos_report_bot.google.gmail import GmailOAuthSender
from pos_report_bot.google.oauth import (
    GOOGLE_OAUTH_SCOPES,
    GOOGLE_DRIVE_PROFILE,
    GOOGLE_DRIVE_SCOPES,
    GOOGLE_GMAIL_PROFILE,
    GOOGLE_GMAIL_SCOPES,
    GOOGLE_SHEETS_PROFILE,
    GOOGLE_SHEETS_SCOPES,
    GoogleOAuthService,
)
from pos_report_bot.google.sheets import GoogleSheetsInventoryClient, R14InventorySheetError


ROOT = Path(__file__).resolve().parents[2]


class FakeCredentials:
    expired = False
    refresh_token = "refresh"
    last_scopes: list[str] = []

    def __init__(self, token: str = "token", scopes: list[str] | None = None) -> None:
        self.token = token
        self.refreshed = False
        self.scopes = scopes or []

    @classmethod
    def from_authorized_user_file(cls, _path: str, *, scopes: list[str]):  # type: ignore[no-untyped-def]
        cls.last_scopes = list(scopes)
        return cls(scopes=scopes)

    @classmethod
    def from_authorized_user_info(cls, _info, *, scopes: list[str]):  # type: ignore[no-untyped-def]
        cls.last_scopes = list(scopes)
        return cls(scopes=scopes)

    def refresh(self, _request):  # type: ignore[no-untyped-def]
        self.refreshed = True

    def to_json(self) -> str:
        return '{"token":"fake"}'


class FakeInvalidScopeCredentials(FakeCredentials):
    expired = True

    def refresh(self, _request):  # type: ignore[no-untyped-def]
        raise RuntimeError("('invalid_scope: Bad Request', {'error': 'invalid_scope', 'error_description': 'Bad Request'})")


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
    create_calls = 0
    update_calls = 0
    list_calls = []

    def files(self):  # type: ignore[no-untyped-def]
        return self

    def list(self, **kwargs):  # type: ignore[no-untyped-def]
        self.__class__.list_calls.append(kwargs)
        return FakeExecute({"files": []})

    def create(self, **_kwargs):  # type: ignore[no-untyped-def]
        self.__class__.create_calls += 1
        return FakeExecute({"id": "drive-file-1", "name": "report.xls", "size": "12", "mimeType": "application/vnd.ms-excel"})

    def update(self, **_kwargs):  # type: ignore[no-untyped-def]
        self.__class__.update_calls += 1
        return FakeExecute({"id": "updated-drive-file", "name": "report.xls", "size": "12", "mimeType": "application/vnd.ms-excel"})


class FakeDriveServiceWithoutFileId:
    def files(self):  # type: ignore[no-untyped-def]
        return self

    def list(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute({"files": []})

    def create(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute({"name": "report.xls", "size": "12", "mimeType": "application/vnd.ms-excel"})


class FakeDriveServiceWithExistingFile:
    create_calls = 0
    update_calls = 0
    update_kwargs = None

    def files(self):  # type: ignore[no-untyped-def]
        return self

    def list(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute(
            {
                "files": [
                    {"id": "older-file", "name": "report.xls", "modifiedTime": "2026-06-16T01:00:00Z"},
                    {"id": "existing-file", "name": "report.xls", "modifiedTime": "2026-06-17T01:00:00Z"},
                ]
            }
        )

    def create(self, **_kwargs):  # type: ignore[no-untyped-def]
        self.__class__.create_calls += 1
        return FakeExecute({"id": "new-drive-file", "name": "report.xls"})

    def update(self, **kwargs):  # type: ignore[no-untyped-def]
        self.__class__.update_calls += 1
        self.__class__.update_kwargs = kwargs
        return FakeExecute({"id": "existing-file", "name": "report.xls", "size": "12", "mimeType": "application/vnd.ms-excel"})


class FakeSheetsValuesService:
    last_get_kwargs = None

    def spreadsheets(self):  # type: ignore[no-untyped-def]
        return self

    def values(self):  # type: ignore[no-untyped-def]
        return self

    def get(self, **kwargs):  # type: ignore[no-untyped-def]
        self.__class__.last_get_kwargs = kwargs
        return FakeExecute(
            {
                "values": [
                    ["*自2025/1起調整為最小單位(發/點/支)"],
                    ["料件編號 新", "採購分類", "凱惠料號", "品名", "盒入數", "庫存\n 單位", "隸屬部門", "2026/6/25"],
                    [None, None, None, None, None, None, None, "站前4樓", "忠孝7樓", "忠孝健康7樓", "站前11樓", "忠孝國際醫學3樓", "忠孝預防醫學3樓"],
                    ["MP001", "針劑", "6050010", "商品A", "1", "PCS", "護理部", "12", "4", "6", "3", "5", "7"],
                    ["MP002", "針劑", "6050011", "商品B", "1", "PCS", "護理部", "0", "8", "10", "", "9", "11"],
                ]
            }
        )


class FakeSheetsValuesServiceWithInvalidNumber:
    def spreadsheets(self):  # type: ignore[no-untyped-def]
        return self

    def values(self):  # type: ignore[no-untyped-def]
        return self

    def get(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute(
            {
                "values": [
                    ["料件編號 新", "採購分類", "凱惠料號", "品名", "盒入數", "庫存\n 單位", "隸屬部門", "2026/6/25"],
                    [None, None, None, None, None, None, None, "站前4樓", "站前11樓", "忠孝7樓", "忠孝國際醫學3樓", "忠孝健康7樓", "忠孝預防醫學3樓"],
                    ["MP001", "針劑", "6050010", "商品A", "1", "PCS", "護理部", "不是數字"],
                ]
            }
        )


class FakeSheetsDepartmentService:
    last_get_kwargs = None

    def spreadsheets(self):  # type: ignore[no-untyped-def]
        return self

    def values(self):  # type: ignore[no-untyped-def]
        return self

    def get(self, **kwargs):  # type: ignore[no-untyped-def]
        self.__class__.last_get_kwargs = kwargs
        return FakeExecute(
            {
                "values": [
                    ["料件編號 新", "採購分類", "凱惠料號", "品名", "盒入數", "庫存\n 單位", "隸屬部門"],
                    ["MP001", "針劑", "6150001", "商品A", "1", "PCS", "護理部"],
                    ["MP002", "針劑", "6150002", "商品B", "1", "PCS", "美容部"],
                    ["MP003", "針劑", "6150003", "商品C", "1", "PCS", ""],
                ]
            }
        )


class FakeSheetsDepartmentConflictService:
    def spreadsheets(self):  # type: ignore[no-untyped-def]
        return self

    def values(self):  # type: ignore[no-untyped-def]
        return self

    def get(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute(
            {
                "values": [
                    ["料件編號 新", "採購分類", "凱惠料號", "品名", "盒入數", "庫存\n 單位", "隸屬部門"],
                    ["MP001", "針劑", "6150001", "商品A", "1", "PCS", "護理部"],
                    ["MP001", "針劑", "6150001", "商品A", "1", "PCS", "美容部"],
                ]
            }
        )


class FakeSheetsMissingBranchService:
    def spreadsheets(self):  # type: ignore[no-untyped-def]
        return self

    def values(self):  # type: ignore[no-untyped-def]
        return self

    def get(self, **_kwargs):  # type: ignore[no-untyped-def]
        return FakeExecute(
            {
                "values": [
                    ["料件編號 新", "採購分類", "凱惠料號", "品名", "盒入數", "庫存\n 單位", "隸屬部門", "2026/6/25"],
                    [None, None, None, None, None, None, None, "站前4樓", "忠孝7樓", "站前11樓", "忠孝國際醫學3樓"],
                    ["MP001", "針劑", "6050010", "商品A", "1", "PCS", "護理部", "12", "4", "3", "5"],
                ]
            }
        )


class FakeGmailService:
    last_send_body = None

    def users(self):  # type: ignore[no-untyped-def]
        return self

    def messages(self):  # type: ignore[no-untyped-def]
        return self

    def send(self, **kwargs):  # type: ignore[no-untyped-def]
        self.__class__.last_send_body = kwargs.get("body")
        return FakeExecute({"id": "gmail-message-1"})


class FakeMediaFileUpload:
    calls = []

    def __init__(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((_args, _kwargs))


def fake_build(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "oauth2":
        return FakeOAuth2Service()
    if api_name == "drive":
        return FakeDriveService()
    if api_name == "gmail":
        return FakeGmailService()
    if api_name == "sheets":
        return FakeSheetsValuesService()
    raise AssertionError(api_name)


def fake_build_invalid_sheets(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "sheets":
        return FakeSheetsValuesServiceWithInvalidNumber()
    raise AssertionError(api_name)


def fake_build_department_sheets(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "sheets":
        return FakeSheetsDepartmentService()
    raise AssertionError(api_name)


def fake_build_department_conflict_sheets(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "sheets":
        return FakeSheetsDepartmentConflictService()
    raise AssertionError(api_name)


def fake_build_missing_branch_sheets(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "sheets":
        return FakeSheetsMissingBranchService()
    raise AssertionError(api_name)


def fake_build_without_drive_file_id(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "oauth2":
        return FakeOAuth2Service()
    if api_name == "drive":
        return FakeDriveServiceWithoutFileId()
    raise AssertionError(api_name)


def fake_build_with_existing_drive_file(api_name, _version, *, credentials):  # type: ignore[no-untyped-def]
    if api_name == "oauth2":
        return FakeOAuth2Service()
    if api_name == "drive":
        return FakeDriveServiceWithExistingFile()
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


def test_google_oauth_combined_consent_includes_every_runtime_profile_scope() -> None:
    assert set(GOOGLE_DRIVE_SCOPES).issubset(GOOGLE_OAUTH_SCOPES)
    assert set(GOOGLE_SHEETS_SCOPES).issubset(GOOGLE_OAUTH_SCOPES)
    assert set(GOOGLE_GMAIL_SCOPES).issubset(GOOGLE_OAUTH_SCOPES)


def test_google_oauth_reconnect_replaces_stale_service_profile_tokens(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    config.google_drive.client_secret_path = str(credentials_path)
    config.google_drive.token_storage = "plaintext_test"
    config.app.state_dir = str(tmp_path)
    for filename in (
        "google_drive_user_token.bin",
        "google_sheets_user_token.bin",
        "google_gmail_user_token.bin",
    ):
        (tmp_path / filename).write_text('{"token":"stale-profile-token"}', encoding="utf-8")

    result = GoogleOAuthService(
        config,
        flow_factory=FakeFlow,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
    ).connect()

    assert result.ok is True
    expected = '{"token":"fake"}'
    assert (tmp_path / "google_user_token.bin").read_text(encoding="utf-8") == expected
    assert (tmp_path / "google_drive_user_token.bin").read_text(encoding="utf-8") == expected
    assert (tmp_path / "google_sheets_user_token.bin").read_text(encoding="utf-8") == expected
    assert (tmp_path / "google_gmail_user_token.bin").read_text(encoding="utf-8") == expected


def test_google_oauth_reconnect_overwrites_stale_windows_keyring_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    config.google_drive.client_secret_path = str(credentials_path)
    config.google_drive.token_storage = "keyring"
    config.app.state_dir = str(tmp_path)
    stored = {
        ("POSReportBot Google OAuth", "google_user_token"): '{"token":"old-combined"}',
        ("POSReportBot Google OAuth", "google_drive_user_token"): '{"token":"stale-drive"}',
        ("POSReportBot Google OAuth", "google_sheets_user_token"): '{"token":"stale-sheets"}',
        ("POSReportBot Google OAuth", "google_gmail_user_token"): '{"token":"stale-gmail"}',
    }
    fake_keyring = SimpleNamespace(
        get_password=lambda service, user: stored.get((service, user)),
        set_password=lambda service, user, value: stored.__setitem__((service, user), value),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    result = GoogleOAuthService(
        config,
        flow_factory=FakeFlow,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
    ).connect()

    assert result.ok is True
    assert {
        stored[("POSReportBot Google OAuth", user)]
        for user in (
            "google_user_token",
            "google_drive_user_token",
            "google_sheets_user_token",
            "google_gmail_user_token",
        )
    } == {'{"token":"fake"}'}


def test_google_oauth_blank_client_secret_path_uses_installed_config_directory(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.work_dir = str(tmp_path / "POSReportBot")
    config.google_drive.client_secret_path = ""
    expected = tmp_path / "POSReportBot" / "config" / "client_secret.json"
    expected.parent.mkdir(parents=True)
    expected.write_text("{}", encoding="utf-8")

    service = GoogleOAuthService(config)

    assert service.credentials_path == expected


def test_google_oauth_client_secret_directory_resolves_standard_filename(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    secret_directory = tmp_path / "config"
    expected = secret_directory / "client_secret.json"
    secret_directory.mkdir()
    expected.write_text("{}", encoding="utf-8")
    config.google_drive.client_secret_path = str(secret_directory)

    service = GoogleOAuthService(config)

    assert service.credentials_path == expected


def test_google_oauth_missing_client_secret_returns_setup_error_before_flow(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.work_dir = str(tmp_path / "POSReportBot")
    config.google_drive.client_secret_path = "."

    result = GoogleOAuthService(config, flow_factory=FakeFlow).connect()

    assert result.ok is False
    assert result.error_code == "GOOGLE_CREDENTIALS_JSON_NOT_FOUND"
    assert "Permission denied: '.'" not in result.message
    assert "client_secret.json" in result.message


def test_google_oauth_credentials_uses_configured_service_scopes(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    FakeCredentials.last_scopes = []

    GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
        scopes=GOOGLE_DRIVE_SCOPES,
        profile=GOOGLE_DRIVE_PROFILE,
    ).credentials()

    assert FakeCredentials.last_scopes == list(GOOGLE_DRIVE_SCOPES)


def test_google_oauth_service_profile_uses_separate_token_path_with_legacy_fallback(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    service = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
        scopes=GOOGLE_DRIVE_SCOPES,
        profile=GOOGLE_DRIVE_PROFILE,
    )
    FakeCredentials.last_scopes = []

    service.credentials()

    assert service.token_path.name == "google_drive_user_token.bin"
    assert not service.token_path.exists()
    assert FakeCredentials.last_scopes == list(GOOGLE_DRIVE_SCOPES)


def test_google_drive_uploader_uploads_with_oauth_credentials(tmp_path: Path) -> None:
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
        build_func=fake_build,
        scopes=GOOGLE_DRIVE_SCOPES,
        profile=GOOGLE_DRIVE_PROFILE,
    )

    result = GoogleDriveUploader(oauth, media_file_upload_cls=FakeMediaFileUpload, build_func=fake_build).upload(
        file_path,
        "folder123",
        "report.xls",
    )

    assert result.success is True
    assert result.drive_file_id == "drive-file-1"
    assert FakeDriveService.list_calls[-1]["q"] == "name = 'report.xls' and 'folder123' in parents and trashed = false"


def test_google_oauth_status_reports_reauth_required_for_invalid_scope(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")

    result = GoogleOAuthService(
        config,
        credentials_cls=FakeInvalidScopeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
    ).status()

    assert result.ok is False
    assert result.error_code == "GOOGLE_OAUTH_REAUTH_REQUIRED"
    assert "重新連接 Google Drive" in result.message


def test_google_drive_uploader_reports_reauth_required_for_invalid_scope(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    file_path = tmp_path / "report.xls"
    file_path.write_bytes(b"excel")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeInvalidScopeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
        scopes=GOOGLE_DRIVE_SCOPES,
        profile=GOOGLE_DRIVE_PROFILE,
    )

    result = GoogleDriveUploader(oauth, media_file_upload_cls=FakeMediaFileUpload, build_func=fake_build).upload(
        file_path,
        "folder123",
        "report.xls",
    )

    assert result.success is False
    assert result.error_code == "GOOGLE_OAUTH_REAUTH_REQUIRED"
    assert "重新連接 Google Drive" in result.message


def test_google_sheets_inventory_client_reads_r14_inventory(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
        scopes=GOOGLE_SHEETS_SCOPES,
        profile=GOOGLE_SHEETS_PROFILE,
    )
    FakeSheetsValuesService.last_get_kwargs = None

    result = GoogleSheetsInventoryClient(oauth, build_func=fake_build).read_r14_inventory(config.r14_inventory_source)

    assert FakeSheetsValuesService.last_get_kwargs == {
        "spreadsheetId": "1LfKl6LevlSuk8-OVQZNp8VFaTHyAB1NTR7Y1bpgEWUE",
        "range": "'Summary'!A:ZZ",
    }
    assert result.rows_read == 5
    assert result.inventory_date == date(2026, 6, 25)
    assert result.item_names["6050010"] == "商品A"
    assert result.item_names["6050011"] == "商品B"
    assert result.inventories["站前4樓"]["6050010"] == 12
    assert result.inventories["站前4樓"]["6050011"] == 0
    assert "6050011" not in result.inventories["站前11樓"]
    assert result.inventories["忠孝健康7樓"]["6050011"] == 10
    assert result.inventories["忠孝預防醫學3樓"]["6050011"] == 11
    assert result.inventories["忠孝7樓"]["6050010"] == 4
    assert result.inventories["站前11樓"]["6050010"] == 3


def test_google_sheets_inventory_client_rejects_non_numeric_inventory(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build_invalid_sheets,
        scopes=GOOGLE_SHEETS_SCOPES,
        profile=GOOGLE_SHEETS_PROFILE,
    )

    with pytest.raises(R14InventorySheetError) as error:
        GoogleSheetsInventoryClient(oauth, build_func=fake_build_invalid_sheets).read_r14_inventory(
            config.r14_inventory_source
        )

    assert error.value.error_code == "R14_INVENTORY_VALUE_INVALID"
    assert "不是數字" in error.value.message


def test_google_sheets_inventory_client_rejects_missing_branch_header(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build_missing_branch_sheets,
        scopes=GOOGLE_SHEETS_SCOPES,
        profile=GOOGLE_SHEETS_PROFILE,
    )

    with pytest.raises(R14InventorySheetError) as error:
        GoogleSheetsInventoryClient(oauth, build_func=fake_build_missing_branch_sheets).read_r14_inventory(
            config.r14_inventory_source
        )

    assert error.value.error_code == "R14_INVENTORY_BRANCH_COLUMN_MISSING"
    assert "忠孝健康7樓" in error.value.message


def test_google_sheets_inventory_client_reads_w02_departments(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build_department_sheets,
        scopes=GOOGLE_SHEETS_SCOPES,
        profile=GOOGLE_SHEETS_PROFILE,
    )
    FakeSheetsDepartmentService.last_get_kwargs = None

    result = GoogleSheetsInventoryClient(oauth, build_func=fake_build_department_sheets).read_w02_departments(
        config.r14_inventory_source
    )

    assert FakeSheetsDepartmentService.last_get_kwargs == {
        "spreadsheetId": "1LfKl6LevlSuk8-OVQZNp8VFaTHyAB1NTR7Y1bpgEWUE",
        "range": "'Summary'!A:ZZ",
    }
    assert result.rows_read == 4
    assert result.item_departments == {
        "6150001": "護理部",
        "6150002": "美容部",
    }
    assert result.known_item_codes == {"6150001", "6150002", "6150003"}


def test_google_sheets_inventory_client_rejects_conflicting_w02_departments(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build_department_conflict_sheets,
        scopes=GOOGLE_SHEETS_SCOPES,
        profile=GOOGLE_SHEETS_PROFILE,
    )

    with pytest.raises(R14InventorySheetError) as error:
        GoogleSheetsInventoryClient(oauth, build_func=fake_build_department_conflict_sheets).read_w02_departments(
            config.r14_inventory_source
        )

    assert error.value.error_code == "W02_DEPARTMENT_DUPLICATE"
    assert "6150001" in error.value.message


def test_google_drive_uploader_uses_xlsx_mime_type(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    file_path = tmp_path / "report.xlsx"
    file_path.write_bytes(b"excel")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
        scopes=GOOGLE_DRIVE_SCOPES,
        profile=GOOGLE_DRIVE_PROFILE,
    )
    FakeMediaFileUpload.calls.clear()

    result = GoogleDriveUploader(oauth, media_file_upload_cls=FakeMediaFileUpload, build_func=fake_build).upload(
        file_path,
        "folder123",
        "report.xlsx",
    )

    assert result.success is True
    assert FakeMediaFileUpload.calls[-1][1]["mimetype"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_google_drive_uploader_updates_existing_same_name_file_in_folder(tmp_path: Path) -> None:
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
        build_func=fake_build_with_existing_drive_file,
        scopes=GOOGLE_DRIVE_SCOPES,
        profile=GOOGLE_DRIVE_PROFILE,
    )
    FakeDriveServiceWithExistingFile.create_calls = 0
    FakeDriveServiceWithExistingFile.update_calls = 0
    FakeDriveServiceWithExistingFile.update_kwargs = None

    result = GoogleDriveUploader(
        oauth,
        media_file_upload_cls=FakeMediaFileUpload,
        build_func=fake_build_with_existing_drive_file,
    ).upload(file_path, "folder123", "report.xls")

    assert result.success is True
    assert result.drive_file_id == "existing-file"
    assert FakeDriveServiceWithExistingFile.create_calls == 0
    assert FakeDriveServiceWithExistingFile.update_calls == 1
    assert FakeDriveServiceWithExistingFile.update_kwargs["fileId"] == "existing-file"


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
        scopes=GOOGLE_DRIVE_SCOPES,
        profile=GOOGLE_DRIVE_PROFILE,
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
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
        scopes=GOOGLE_GMAIL_SCOPES,
        profile=GOOGLE_GMAIL_PROFILE,
    )

    result = GmailOAuthSender(oauth, build_func=fake_build).send(config.email, subject="Test", body="Body")

    assert result.ok is True
    assert result.gmail_message_id == "gmail-message-1"


def test_gmail_oauth_sender_sends_message_with_attachment(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    config.email.recipients = ["ops@example.com"]
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    attachment = tmp_path / "report.xlsx"
    attachment.write_bytes(b"excel")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
        scopes=GOOGLE_GMAIL_SCOPES,
        profile=GOOGLE_GMAIL_PROFILE,
    )
    FakeGmailService.last_send_body = None

    result = GmailOAuthSender(oauth, build_func=fake_build).send(
        config.email,
        subject="Test",
        body="Body",
        attachments=[attachment],
    )

    assert result.ok is True
    assert FakeGmailService.last_send_body is not None
    assert "raw" in FakeGmailService.last_send_body


def test_gmail_oauth_sender_sends_html_table_as_html_alternative(tmp_path: Path) -> None:
    config = load_project_config(ROOT / "config_templates" / "app.template.yaml")
    config.app.state_dir = str(tmp_path)
    config.google_drive.token_storage = "plaintext_test"
    config.email.recipients = ["ops@example.com"]
    (tmp_path / "google_user_token.bin").write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthService(
        config,
        credentials_cls=FakeCredentials,
        request_factory=lambda: object,
        build_func=fake_build,
        scopes=GOOGLE_GMAIL_SCOPES,
        profile=GOOGLE_GMAIL_PROFILE,
    )
    FakeGmailService.last_send_body = None

    result = GmailOAuthSender(oauth, build_func=fake_build).send(
        config.email,
        subject="Test",
        body='週耗用量暴漲/暴跌超過30%:<table style="border:1px solid #444;"><tr><td>站前4樓</td></tr></table>',
    )

    assert result.ok is True
    raw = FakeGmailService.last_send_body["raw"]
    padded = raw + ("=" * (-len(raw) % 4))
    message = message_from_bytes(base64.urlsafe_b64decode(padded.encode("ascii")))
    assert message.is_multipart()
    payload = message.get_payload()
    assert any(part.get_content_type() == "text/plain" for part in payload)
    html_parts = [part for part in payload if part.get_content_type() == "text/html"]
    assert len(html_parts) == 1
    assert "<table" in html_parts[0].get_payload(decode=True).decode(html_parts[0].get_content_charset())
