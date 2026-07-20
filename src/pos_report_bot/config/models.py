from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AppSettings(StrictBaseModel):
    name: str
    work_dir: str
    downloads_dir: str
    output_dir: str
    logs_dir: str
    screenshots_dir: str
    state_dir: str
    language: str = "zh-TW"


class PosSettings(StrictBaseModel):
    executable_path: str = (
        "C:\\Users\\MIKO\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\"
        "台灣凱惠資訊科技有限公司\\SPA1\\SPA資訊服務應用系統.appref-ms"
    )
    launch_args: str = ""
    working_dir: str = ""
    window_title_contains: str = "SPA-POS"
    window_title_regex: str = ""
    backend: Literal["auto", "uia", "win32"] = "auto"
    startup_wait_seconds: int = 20
    startup_ini_selection_enabled: bool = True
    startup_ini_profile: str = "c:\\tkhspa\\tkhspa-測試區.ini"
    run_as_admin: bool = False
    close_after_run: bool = False


class LoginSettings(StrictBaseModel):
    required: bool = True
    username: str = "A0042"
    company_code: str = ""
    login_button_text: str = "登入"
    login_success_text: str = "統計報表"
    login_failure_text: str = "帳號或密碼錯誤"
    timeout_seconds: int = 30


class SaveAsSettings(StrictBaseModel):
    dialog_title_contains: str = "另存新檔"
    filename_label: str = "檔案名稱"
    save_button_text: str = "存檔"
    default_extension: str = ".xls"
    overwrite_policy: Literal["rename_unique", "overwrite", "fail"] = "rename_unique"
    wait_timeout_seconds: int = 300
    stable_seconds: int = 3
    recovery_search_dirs: list[str] = Field(default_factory=list)


class GoogleDriveSettings(StrictBaseModel):
    upload_enabled: bool = True
    auth_mode: str = "drive_api"
    allow_folder_url_or_id: bool = True
    token_storage: str = "keyring"
    client_secret_path: str = ""


class EmailSettings(StrictBaseModel):
    enabled: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    use_tls: bool = True
    username: str = ""
    recipients: list[str] = Field(
        default_factory=lambda: ["joe.little7208@gmail.com", "mickey.chen@mikobeaute.com"]
    )
    cc: list[str] = Field(default_factory=list)
    notify_on_failure: bool = True
    notify_on_success_summary: bool = True


R14_EMAIL_DEFAULT_RECIPIENTS = [
    "joe.little7208@gmail.com",
    "mickey.chen@mikobeaute.com",
    "rae.hsu@mikobeaute.com",
    "miko_03@mikobeaute.com",
    "bbone_pu@bebetterone.com",
]
R14_EMAIL_DEFAULT_SUBJECT_TEMPLATE = "{date}耗材領用報表"
R14_EMAIL_DEFAULT_BODY = "Hi,\n\n附件為本日耗材領用報表，請您查收。\n\nRPA程式\n"


class R14EmailSettings(StrictBaseModel):
    enabled: bool = True
    recipients: list[str] = Field(default_factory=lambda: list(R14_EMAIL_DEFAULT_RECIPIENTS))
    cc: list[str] = Field(default_factory=list)
    subject_template: str = R14_EMAIL_DEFAULT_SUBJECT_TEMPLATE
    body: str = R14_EMAIL_DEFAULT_BODY


class W02OrderSettings(StrictBaseModel):
    enabled: bool = True
    next_run_date: str = "2026/07/03"
    recipients: list[str] = Field(default_factory=lambda: list(R14_EMAIL_DEFAULT_RECIPIENTS))
    cc: list[str] = Field(default_factory=list)
    subject_template: str = "W02 下單異常通知 {date}"
    body: str = "Hi,\n\nW02 自動下單流程有需要人工確認的品項，請查看下方明細。\n\nRPA程式\n"
    pos_submission_enabled: bool = False
    diagnostic_mode: bool = False


class SchedulerSettings(StrictBaseModel):
    enabled: bool = False
    daily_time: str = "01:00"
    weekly_enabled: bool = False
    weekly_day: str = "Thursday"
    weekly_time: str = "01:00"
    retry_count: int = 2
    retry_interval_seconds: int = 60


class PosUpdateSettings(StrictBaseModel):
    enabled: bool = True
    update_dialog_title_contains: str = "程式更新需重新啟動"
    update_message_contains: str = "新版程式已經下載安裝完成"
    action: str = "click_yes_and_restart"
    expected_update_weekday: str = "Thursday"
    restart_wait_seconds: int = 180
    max_restart_wait_seconds: int = 300
    resume_unfinished_tasks: bool = True


class PosRecoverySettings(StrictBaseModel):
    enabled: bool = True
    health_check_interval_seconds: int = 5
    restart_delay_seconds: int = 5
    max_restarts_per_run: int = 2
    kill_process_on_hang: bool = True
    relaunch_after_kill: bool = True
    retry_current_task_after_restart: bool = True
    credential_keyring_service: str = "POSReportBot POS"


class R14TransformSettings(StrictBaseModel):
    template_path: str = ""
    template_search_dir: str = ""
    raw_search_dir: str = ""
    raw_filename_glob: str = "診所stock status - * demand planning-*-rawdata.xls"
    output_extension: str = ".xlsx"


class R14InventorySourceSettings(StrictBaseModel):
    enabled: bool = True
    spreadsheet_url: str = (
        "https://docs.google.com/spreadsheets/d/"
        "1LfKl6LevlSuk8-OVQZNp8VFaTHyAB1NTR7Y1bpgEWUE/edit?gid=651674627#gid=651674627"
    )
    spreadsheet_id: str = ""
    sheet_name: str = "Summary"
    item_code_column: str = "B"
    branch_inventory_columns: dict[str, str] = Field(
        default_factory=lambda: {
            "站前4樓": "G",
            "站前11樓": "H",
            "忠孝7樓": "I",
            "忠孝國際醫學3樓": "J",
            "忠孝健康7樓": "K",
        }
    )
    apply_weekday: Literal[
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日",
        "星期天",
        "週一",
        "週二",
        "週三",
        "週四",
        "週五",
        "週六",
        "週日",
        "週天",
    ] = "Friday"


class BranchConfig(StrictBaseModel):
    code: str
    pos_code: str
    pos_text: str
    display_name: str
    note: str = ""
    drive_folder_id: str = ""
    enabled: bool = True


class DateRangeConfig(StrictBaseModel):
    start: str
    end: str


class ReportOptions(StrictBaseModel):
    check: list[str] = Field(default_factory=list)
    uncheck: list[str] = Field(default_factory=list)
    other_conditions: list[str] = Field(default_factory=list)


class ReportConfig(StrictBaseModel):
    id: str
    enabled: bool = True
    frequency: Literal["daily", "weekly", "biweekly"] = "daily"
    name: str
    handler: str
    report_menu_text: str
    menu_path: list[str] = Field(default_factory=list)
    branch_mode: Literal["all", "each_branch", "multi_select", "single"]
    date_range: DateRangeConfig
    output_filename: str
    drive_folder_id: str = ""
    upload_enabled: bool = True
    branch_drive_folder_ids: dict[str, str] = Field(default_factory=dict)
    options: ReportOptions = Field(default_factory=ReportOptions)
    max_wait_seconds: int = 300
    retry_count: int = 1
    real_pos_validation_status: str = "pending_real_pos_validation"
    variant: str | None = None


class TaskDriveTarget(StrictBaseModel):
    folder_id_or_url: str = ""
    branches: dict[str, str] = Field(default_factory=dict)


class DriveTargetsConfig(StrictBaseModel):
    targets: dict[str, TaskDriveTarget] = Field(default_factory=dict)


class ProjectConfig(StrictBaseModel):
    app: AppSettings
    pos: PosSettings
    login: LoginSettings
    save_as: SaveAsSettings
    google_drive: GoogleDriveSettings
    email: EmailSettings
    scheduler: SchedulerSettings
    pos_update: PosUpdateSettings
    pos_recovery: PosRecoverySettings = Field(default_factory=PosRecoverySettings)
    r14_transform: R14TransformSettings = Field(default_factory=R14TransformSettings)
    r14_inventory_source: R14InventorySourceSettings = Field(default_factory=R14InventorySourceSettings)
    r14_email: R14EmailSettings = Field(default_factory=R14EmailSettings)
    w02_order: W02OrderSettings = Field(default_factory=W02OrderSettings)
    reports: list[ReportConfig]
    branches: list[BranchConfig]
    drive_targets: DriveTargetsConfig
