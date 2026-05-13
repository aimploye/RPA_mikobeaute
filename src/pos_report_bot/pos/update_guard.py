from enum import StrEnum

from pydantic import BaseModel

from pos_report_bot.config.models import PosUpdateSettings


class UpdatePolicy(StrEnum):
    CLICK_YES_AND_RESTART = "click_yes_and_restart"
    DETECT_ONLY = "detect_only"


class UpdateDialogSnapshot(BaseModel):
    title: str
    message: str
    buttons: list[str]


class DetectedUpdateDialog(BaseModel):
    title: str
    message: str
    can_confirm: bool


class UpdateHandlePlan(BaseModel):
    action: str
    requires_restart: bool
    resume_unfinished_tasks: bool
    restart_wait_seconds: int
    max_restart_wait_seconds: int


class UpdateGuard:
    def __init__(self, settings: PosUpdateSettings) -> None:
        self.settings = settings

    def detect_from_snapshot(
        self,
        snapshot: UpdateDialogSnapshot,
    ) -> DetectedUpdateDialog | None:
        title_matches = self.settings.update_dialog_title_contains in snapshot.title
        message_matches = self.settings.update_message_contains in snapshot.message
        if not title_matches or not message_matches:
            return None

        return DetectedUpdateDialog(
            title=snapshot.title,
            message=snapshot.message,
            can_confirm=any(button.startswith("是") for button in snapshot.buttons),
        )

    def plan_handle(
        self,
        dialog: DetectedUpdateDialog,
        *,
        policy: UpdatePolicy = UpdatePolicy.CLICK_YES_AND_RESTART,
    ) -> UpdateHandlePlan:
        requires_restart = policy == UpdatePolicy.CLICK_YES_AND_RESTART and dialog.can_confirm
        return UpdateHandlePlan(
            action=policy.value,
            requires_restart=requires_restart,
            resume_unfinished_tasks=self.settings.resume_unfinished_tasks,
            restart_wait_seconds=self.settings.restart_wait_seconds,
            max_restart_wait_seconds=self.settings.max_restart_wait_seconds,
        )
