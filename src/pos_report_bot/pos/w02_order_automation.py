from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic, sleep
from typing import Any, Callable, cast

from pywinauto.uia_defines import NoPatternInterfaceError  # type: ignore[import-untyped]

from pos_report_bot.config.models import BranchConfig
from pos_report_bot.pos.report_automation import ReportAutomationError
from pos_report_bot.reports.w02_order_builder import W02OrderForm, W02OrderIssue, W02OrderItem, W02OrderPlan


W02_BRANCH_ALIASES: dict[str, tuple[str, ...]] = {
    "站前4樓": ("N001", "站前4樓", "站前4F"),
    "站前11樓": ("N002", "站前11樓", "站前11F"),
    "忠孝7樓": ("N003", "忠孝7樓", "忠孝7F"),
    "忠孝國際醫學3樓": ("N004", "忠孝國際醫學3樓", "忠孝國際3F"),
    "忠孝健康7樓": ("N005", "忠孝健康7樓", "忠孝健康7F"),
    "忠孝預防醫學3樓": ("N006", "忠孝預防醫學3樓", "忠孝預防醫學3F"),
}
W02_TERMINAL_FORM_STATUSES = {"completed", "completed_with_skipped_items"}
W02_RETRYABLE_FORM_STATUSES = {"failed_before_pos_submission", "failed_before_save"}
W02_RETRYABLE_SUBMISSION_PHASES = {"draft_started", "items_in_progress", "failed_before_save"}
W02_ALL_FORM_STATUSES = W02_TERMINAL_FORM_STATUSES | W02_RETRYABLE_FORM_STATUSES | {
    "in_progress",
    "submitted_pending_verification",
}
W02_SKIPPABLE_ITEM_ERROR_CODES = {"W02_POS_ITEM_NOT_FOUND"}
W02_FAILURE_CONTEXT_CONTROL_LIMIT = 800
W02_DEFAULT_USE_TYPE = "常態訂貨"
W02_INVENTORY_MENU_FIRST_LEVEL_ORDER = ("分店訂貨單", "相關報表")
W02_ACTION_SETTLE_SECONDS = 0.15
W02_KEY_SETTLE_SECONDS = 0.1
W02_POLL_SECONDS = 0.1
W02_DRAFT_EXTRA_ROW_SCAN_LIMIT = 32
W02_PROMPT_CONTAINER_NAMES = {"提示訊息", "注意事項", "錯誤", "警告", "核准確認"}
W02_PROMPT_CONTAINER_TYPES = {"window", "dialog", "pane", "custom"}


@dataclass(frozen=True)
class W02PosOrderSubmissionResult:
    ok: bool
    message: str
    actions: list[str]
    completed_forms: int = 0
    skipped_forms: int = 0
    error_code: str | None = None
    diagnostic_path: Path | None = None
    skipped_issues: tuple[W02OrderIssue, ...] = ()
    completed_form_counts_by_branch: dict[str, int] | None = None
    preserve_pos_draft: bool = False


@dataclass(frozen=True)
class _W02SubmittedFormResult:
    submitted_item_count: int
    skipped_issues: tuple[W02OrderIssue, ...]


@dataclass(frozen=True)
class _W02ResumeState:
    processed_item_count: int
    submitted_items: tuple[W02OrderItem, ...]
    skipped_issues: tuple[W02OrderIssue, ...]


class W02PosOrderAutomator:
    def __init__(
        self,
        window: Any,
        *,
        branches: list[BranchConfig],
        logs_dir: Path,
        state_dir: Path,
        run_date: date,
        keyboard_sender: Callable[..., Any] | None = None,
    ) -> None:
        self.window = window
        self.branches = branches
        self.logs_dir = logs_dir
        self.state_dir = state_dir
        self.run_date = run_date
        self.keyboard_sender = keyboard_sender or _default_keyboard_sender
        self.actions: list[str] = []
        self._failure_context_snapshots: list[dict[str, Any]] = []
        self._control_cache: dict[bool, list[Any]] = {}
        self._order_window_controls_cache: list[Any] | None = None
        self._order_item_grid_controls_cache: list[Any] | None = None
        self._item_picker_controls_cache: list[Any] | None = None
        self._explicitly_selected_branches: set[str] = set()
        self._active_ledger: dict[str, Any] | None = None
        self._active_form_key: str | None = None
        self._retryable_draft_entry: dict[str, Any] | None = None
        self._ledger_path = _w02_ledger_dir(state_dir, run_date) / "w02_pos_submission_ledger.json"
        self._diagnostic_path = logs_dir / (
            f"automation_w02_pos_order_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}.json"
        )

    def submit_plan(self, plan: W02OrderPlan) -> W02PosOrderSubmissionResult:
        ledger: dict[str, Any] = {
            "schema_version": 1,
            "run_date": self.run_date.isoformat(),
            "forms": {},
        }
        completed = 0
        skipped = 0
        skipped_issues: list[W02OrderIssue] = []
        completed_form_counts_by_branch: dict[str, int] = {}
        active_key: str | None = None
        active_action_start_index = 0
        try:
            ledger = self._load_ledger()
            for form in plan.forms:
                key = _form_key(form)
                active_key = key
                active_action_start_index = len(self.actions)
                signature = _form_signature(form)
                ledger_entry = ledger.get("forms", {}).get(key, {})
                recorded_signature = ledger_entry.get("plan_signature")
                if ledger_entry.get("status") in W02_TERMINAL_FORM_STATUSES:
                    if recorded_signature and recorded_signature != signature:
                        raise ReportAutomationError(
                            "W02_POS_ORDER_PLAN_CHANGED_AFTER_COMPLETION",
                            (
                                "W02 偵測到同日同分館同部門已完成建單，但本次計畫品項或數量不同；"
                                "為避免重複或漏下單，請人工確認 POS 訂貨單與 ledger 後再重跑。"
                                f"分館/部門：{key}。"
                            ),
                        )
                    skipped += 1
                    existing_skipped_issues = _issues_from_payload(ledger_entry.get("skipped_issues"))
                    skipped_issues.extend(existing_skipped_issues)
                    submitted_item_count = _optional_int(ledger_entry.get("submitted_item_count")) or 0
                    if submitted_item_count > 0:
                        completed_form_counts_by_branch[form.branch] = (
                            completed_form_counts_by_branch.get(form.branch, 0) + submitted_item_count
                        )
                    self.actions.append(f"w02_form_skip_existing:{key}")
                    continue
                ledger_status = str(ledger_entry.get("status") or "")
                submission_phase = str(ledger_entry.get("submission_phase") or "")
                retryable_draft = ledger_status in W02_RETRYABLE_FORM_STATUSES or (
                    ledger_status == "in_progress" and submission_phase in W02_RETRYABLE_SUBMISSION_PHASES
                )
                resume_existing_draft = bool(
                    retryable_draft
                    and (
                        ledger_status == "failed_before_save"
                        or submission_phase in {"items_in_progress", "failed_before_save"}
                    )
                )
                retryable_draft_entry = dict(ledger_entry) if resume_existing_draft else None
                if retryable_draft:
                    if not isinstance(recorded_signature, str) or not recorded_signature.strip():
                        raise ReportAutomationError(
                            "W02_POS_LEDGER_CORRUPT",
                            (
                                "W02 未完成草稿缺少可驗證的完整計畫簽章；"
                                "為避免把本次計畫接到來源不明的舊草稿，已停止。"
                                f"分館/部門：{key}。"
                            ),
                        )
                    if recorded_signature != signature:
                        raise ReportAutomationError(
                            "W02_POS_ORDER_PLAN_CHANGED_DURING_RETRY",
                            (
                                "W02 偵測到同日同分館同部門的未完成草稿與本次完整計畫不同；"
                                "為避免把新計畫接到舊草稿，請人工確認 POS 與 ledger。"
                                f"分館/部門：{key}。"
                            ),
                        )
                    self.actions.append(f"w02_form_retry_{ledger_status or submission_phase}:{key}")
                elif ledger_status in {"in_progress", "submitted_pending_verification"}:
                    raise ReportAutomationError(
                        "W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED",
                        (
                            "W02 偵測到同日同分館同部門的舊建單狀態無法確認是否已按過存檔；"
                            "只有這種可能已寫入 POS 的狀態才需要人工查核。"
                            f"分館/部門：{key}。"
                        ),
                    )
                ledger.setdefault("forms", {})[key] = {
                    "status": "in_progress",
                    "submission_phase": "draft_started",
                    "started_at": datetime.now(tz=UTC).isoformat(),
                    "branch": form.branch,
                    "department": form.department,
                    "item_count": len(form.items),
                    "plan_signature": signature,
                    "items": _form_items_payload(form),
                }
                if retryable_draft:
                    ledger["forms"][key]["retry_of_status"] = ledger_status
                    ledger["forms"][key]["retry_started_at"] = datetime.now(tz=UTC).isoformat()
                self._write_ledger(ledger)
                self._active_ledger = ledger
                self._active_form_key = key
                self._retryable_draft_entry = retryable_draft_entry
                form_result = self._submit_form(form)
                skipped_issues.extend(form_result.skipped_issues)
                if form_result.submitted_item_count > 0:
                    completed += 1
                    completed_form_counts_by_branch[form.branch] = (
                        completed_form_counts_by_branch.get(form.branch, 0) + form_result.submitted_item_count
                    )
                ledger.setdefault("forms", {})[key] = {
                    "status": "completed" if form_result.submitted_item_count > 0 else "completed_with_skipped_items",
                    "completed_at": datetime.now(tz=UTC).isoformat(),
                    "branch": form.branch,
                    "department": form.department,
                    "item_count": len(form.items),
                    "submitted_item_count": form_result.submitted_item_count,
                    "skipped_issue_count": len(form_result.skipped_issues),
                    "plan_signature": signature,
                    "items": _form_items_payload(form),
                    "skipped_issues": [_issue_payload(issue) for issue in form_result.skipped_issues],
                }
                self._write_ledger(ledger)
                self._active_ledger = None
                self._active_form_key = None
                self._retryable_draft_entry = None
                active_key = None
        except ReportAutomationError as exc:
            active_actions = self.actions[active_action_start_index:]
            try:
                self._record_active_form_failure(
                    ledger,
                    active_key=active_key,
                    active_actions=active_actions,
                    error_code=exc.error_code,
                    message=exc.message,
                )
            except Exception:
                self.actions.append("w02_failure_state_persistence_failed")
            self._active_ledger = None
            self._active_form_key = None
            preserve_pos_draft = _ledger_form_is_unsaved_draft(ledger, active_key)
            self._retryable_draft_entry = None
            diagnostic_path: Path | None = None
            try:
                diagnostic_path = self._write_diagnostic(
                    status="failed",
                    error_code=exc.error_code,
                    message=exc.message,
                    plan=plan,
                    ledger=ledger,
                )
            except Exception:
                self.actions.append("w02_failure_diagnostic_write_failed")
            return W02PosOrderSubmissionResult(
                ok=False,
                error_code=exc.error_code,
                message=exc.message,
                actions=[*self.actions, *exc.actions],
                completed_forms=completed,
                skipped_forms=skipped,
                diagnostic_path=diagnostic_path,
                skipped_issues=tuple(skipped_issues),
                completed_form_counts_by_branch=completed_form_counts_by_branch,
                preserve_pos_draft=preserve_pos_draft,
            )
        except Exception as exc:
            active_actions = self.actions[active_action_start_index:]
            try:
                self._record_active_form_failure(
                    ledger,
                    active_key=active_key,
                    active_actions=active_actions,
                    error_code="W02_POS_ORDER_UNEXPECTED_ERROR",
                    message=str(exc),
                )
            except Exception:
                self.actions.append("w02_failure_state_persistence_failed")
            self._active_ledger = None
            self._active_form_key = None
            preserve_pos_draft = _ledger_form_is_unsaved_draft(ledger, active_key)
            self._retryable_draft_entry = None
            diagnostic_path = None
            try:
                diagnostic_path = self._write_diagnostic(
                    status="failed",
                    error_code="W02_POS_ORDER_UNEXPECTED_ERROR",
                    message=str(exc),
                    plan=plan,
                    ledger=ledger,
                )
            except Exception:
                self.actions.append("w02_failure_diagnostic_write_failed")
            return W02PosOrderSubmissionResult(
                ok=False,
                error_code="W02_POS_ORDER_UNEXPECTED_ERROR",
                message=f"W02 POS 建單發生未預期錯誤：{exc}",
                actions=self.actions,
                completed_forms=completed,
                skipped_forms=skipped,
                diagnostic_path=diagnostic_path,
                skipped_issues=tuple(skipped_issues),
                completed_form_counts_by_branch=completed_form_counts_by_branch,
                preserve_pos_draft=preserve_pos_draft,
            )
        diagnostic_path = self._write_diagnostic(
            status="success",
            error_code=None,
            message="W02 POS 建單完成。",
            plan=plan,
            ledger=ledger,
        )
        return W02PosOrderSubmissionResult(
            ok=True,
            message=f"W02 POS 建單完成：新建 {completed} 張，略過已完成 {skipped} 張。",
            actions=self.actions,
            completed_forms=completed,
            skipped_forms=skipped,
            diagnostic_path=diagnostic_path,
            skipped_issues=tuple(skipped_issues),
            completed_form_counts_by_branch=completed_form_counts_by_branch,
        )

    def _record_active_form_failure(
        self,
        ledger: dict[str, Any],
        *,
        active_key: str | None,
        active_actions: list[str],
        error_code: str,
        message: str,
    ) -> None:
        if active_key is None or error_code == "W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED":
            return
        entry = ledger.get("forms", {}).get(active_key)
        if not isinstance(entry, dict):
            return
        status = entry.get("status")
        if status == "in_progress":
            entered_items = any(
                action.startswith(("w02_item_added:", "click:w02_item_picker_ok:"))
                for action in active_actions
            ) or bool(
                self._retryable_draft_entry
                and self._retryable_draft_entry.get("status") == "failed_before_save"
            )
            entry.update(
                {
                    "status": "failed_before_save" if entered_items else "failed_before_pos_submission",
                    "submission_phase": "failed_before_save" if entered_items else "draft_started",
                    "failed_at": datetime.now(tz=UTC).isoformat(),
                    "error_code": error_code,
                    "message": message,
                }
            )
            self._write_ledger(ledger)
            return
        if status == "submitted_pending_verification":
            entry.update(
                {
                    "failed_at": datetime.now(tz=UTC).isoformat(),
                    "error_code": error_code,
                    "message": message,
                }
            )
            self._write_ledger(ledger)

    def _mark_active_form_save_attempted(self) -> None:
        if self._active_ledger is None or self._active_form_key is None:
            raise RuntimeError("W02 active ledger context is missing before save.")
        entry = self._active_ledger.get("forms", {}).get(self._active_form_key)
        if not isinstance(entry, dict):
            raise RuntimeError("W02 active ledger entry is missing before save.")
        previous_entry = dict(entry)
        entry.update(
            {
                "status": "submitted_pending_verification",
                "submission_phase": "save_attempted",
                "save_attempted_at": datetime.now(tz=UTC).isoformat(),
            }
        )
        try:
            self._write_ledger(self._active_ledger)
        except Exception:
            entry.clear()
            entry.update(previous_entry)
            raise

    def _submit_form(self, form: W02OrderForm) -> "_W02SubmittedFormResult":
        resume_state = self._resume_existing_unsaved_draft(form)
        if resume_state is None:
            self._switch_branch(form.branch)
            self._open_order_window()
            self._click_by_id("B_Add", action_name="w02_add_order", error_code="W02_POS_ADD_ORDER_NOT_FOUND")
            if not self._wait_until(lambda: self._order_window_branch_matches(form.branch), timeout_seconds=3):
                self._cancel_current_order_if_possible()
                observed_branch = self._order_window_branch_text() or self._visible_option_summary()
                self._record_failure_context("order_branch_not_verified", message=f"expected: {form.branch}; observed: {observed_branch}")
                raise ReportAutomationError(
                    "W02_POS_ORDER_BRANCH_NOT_VERIFIED",
                    f"W02 訂貨單分館未確認為 {form.branch}，目前可讀分館：{observed_branch or '無'}；已停止避免下錯分館。",
                )
            self._select_department(form.department)
            self._select_use_type(W02_DEFAULT_USE_TYPE)
            submitted_item_count = 0
            submitted_items: list[W02OrderItem] = []
            skipped_issues: list[W02OrderIssue] = []
            processed_item_count = 0
        else:
            submitted_items = list(resume_state.submitted_items)
            submitted_item_count = len(submitted_items)
            skipped_issues = list(resume_state.skipped_issues)
            processed_item_count = resume_state.processed_item_count
        for item in form.items[processed_item_count:]:
            try:
                self._add_item(submitted_item_count, item.item_code, item.quantity)
            except ReportAutomationError as exc:
                if exc.error_code not in W02_SKIPPABLE_ITEM_ERROR_CODES:
                    raise
                self._record_failure_context(
                    "skippable_item_error",
                    item_code=item.item_code,
                    row_index=submitted_item_count,
                    error_code=exc.error_code,
                    message=exc.message,
                )
                if not self._close_item_picker_if_present():
                    self._record_failure_context(
                        "item_picker_close_failed_after_skip",
                        item_code=item.item_code,
                        row_index=submitted_item_count,
                        error_code=exc.error_code,
                        message=exc.message,
                    )
                    raise ReportAutomationError(
                        "W02_POS_ITEM_PICKER_CLOSE_FAILED",
                        (
                            "W02 已跳過品項，但商品選擇視窗無法關閉；"
                            "為避免後續品項輸入到未知 POS 狀態，本次停止。"
                        ),
                    ) from exc
                skipped_issue = W02OrderIssue(
                    branch=item.branch,
                    item_code=item.item_code,
                    item_name=item.item_name,
                    quantity=item.quantity,
                    reason=f"POS 建單時跳過：{exc.message}",
                )
                skipped_issues.append(skipped_issue)
                processed_item_count += 1
                self.actions.append(f"w02_item_skipped:{item.item_code}:{exc.error_code}")
                self._checkpoint_active_form_items(
                    form,
                    submitted_items,
                    skipped_issues,
                )
                continue
            submitted_item_count += 1
            processed_item_count += 1
            submitted_items.append(item)
            self._checkpoint_active_form_items(
                form,
                submitted_items,
                skipped_issues,
            )
        if submitted_item_count <= 0:
            self._cancel_current_order_if_possible()
            self.actions.append(f"w02_form_skipped_no_submitted_items:{_form_key(form)}")
            return _W02SubmittedFormResult(submitted_item_count=0, skipped_issues=tuple(skipped_issues))
        save_control = self._find_control_by_id("B_Save")
        if save_control is None:
            raise ReportAutomationError("W02_POS_SAVE_NOT_FOUND", "W02 找不到控制項：B_Save")
        # Write ahead immediately before touching Save.  A crash from this
        # point onward is ambiguous and must never be auto-retried as a draft.
        self._click(
            save_control,
            "w02_save_order",
            before_attempt=self._mark_active_form_save_attempted,
        )
        if not self._dismiss_prompt_if_present(
            error_code="W02_POS_SAVE_REJECTED",
            success_tokens=("存檔完成", "存檔成功"),
            timeout_seconds=5,
        ):
            self.actions.append("w02_save_prompt_not_confirmed")
            self._record_failure_context(
                "save_prompt_not_confirmed",
                error_code="W02_POS_SAVE_REJECTED",
                message="save was dispatched but a scoped success prompt was not confirmed",
            )
            raise ReportAutomationError(
                "W02_POS_SAVE_REJECTED",
                "W02 按下存檔後未觀察到可確認的 POS 存檔成功提示；已停止，未繼續按確認以避免誤建單。",
            )
        self._click_by_id("B_Confirm", action_name="w02_confirm_order", error_code="W02_POS_CONFIRM_NOT_FOUND")
        self._dismiss_prompt_if_present(
            error_code="W02_POS_CONFIRM_REJECTED",
            success_tokens=("確認完成",),
            timeout_seconds=5,
        )
        if not self._wait_until(self._form_confirmation_verified, timeout_seconds=5):
            self._record_failure_context(
                "order_confirmation_not_verified",
                error_code="W02_POS_CONFIRM_REJECTED",
                message="confirmation dispatch completed but confirmed state was not observed",
            )
            raise ReportAutomationError(
                "W02_POS_CONFIRM_REJECTED",
                "W02 已點擊確認，但未在 POS 畫面確認訂貨單狀態為訂貨確認；本次不繼續核准。",
            )
        self._click_by_id("B_Appv", action_name="w02_approve_order", error_code="W02_POS_APPROVE_NOT_FOUND")
        if not self._dismiss_prompt_if_present(
            error_code="W02_POS_APPROVE_CONFIRM_REJECTED",
            accepted_tokens=("是",),
            required_prompt_tokens=("確認要核准此訂貨單",),
            timeout_seconds=5,
        ):
            self._record_failure_context(
                "approve_confirmation_prompt_not_confirmed",
                error_code="W02_POS_APPROVE_CONFIRM_REJECTED",
                message="approve was dispatched but the scoped yes/no prompt was not confirmed",
            )
            raise ReportAutomationError(
                "W02_POS_APPROVE_CONFIRM_REJECTED",
                "W02 已點擊核准，但未找到並完成本張訂貨單的『核准確認 → 是(Y)』提示；已停止。",
            )
        if not self._dismiss_prompt_if_present(
            error_code="W02_POS_APPROVE_REJECTED",
            success_tokens=("核准完成",),
            timeout_seconds=5,
        ):
            self._record_failure_context(
                "approve_success_prompt_not_confirmed",
                error_code="W02_POS_APPROVE_REJECTED",
                message="approve yes was confirmed but the scoped completion prompt was not confirmed",
            )
            raise ReportAutomationError(
                "W02_POS_APPROVE_REJECTED",
                "W02 已完成核准確認，但未找到並關閉本張訂貨單的『核准完成 → 確定』提示；已停止。",
            )
        if not self._wait_until(self._form_approval_verified, timeout_seconds=10):
            self._record_failure_context(
                "order_approval_not_verified",
                error_code="W02_POS_ORDER_APPROVAL_NOT_VERIFIED",
                message="approval dispatch completed but approved state was not confirmed",
            )
            raise ReportAutomationError(
                "W02_POS_ORDER_APPROVAL_NOT_VERIFIED",
                "W02 已點擊核准，但未在 POS 畫面確認訂貨單狀態為已核准/訂貨核准；本次不視為完成。",
            )
        self.actions.append(f"w02_form_completed:{_form_key(form)}")
        self._close_order_window_if_present("after_form_completed")
        return _W02SubmittedFormResult(submitted_item_count=submitted_item_count, skipped_issues=tuple(skipped_issues))

    def _resume_existing_unsaved_draft(self, form: W02OrderForm) -> _W02ResumeState | None:
        """Resume only an exact, still-visible draft whose Save was never attempted."""

        if self._retryable_draft_entry is None:
            return None
        order_window = self._find_control_by_id("BrOrder")
        if order_window is None or not _control_visible(order_window):
            self.actions.append(f"w02_draft_resume_unavailable:{_form_key(form)}:order_window_missing")
            return None
        if not self._order_controls_contain_exact_text("新單"):
            raise ReportAutomationError(
                "W02_POS_DRAFT_RESUME_STATE_UNVERIFIED",
                "W02 找到殘留訂貨視窗，但無法確認狀態仍為未存檔的新單；已停止避免重複或誤改訂貨單。",
            )
        if not self._order_window_branch_matches(form.branch):
            raise ReportAutomationError(
                "W02_POS_DRAFT_RESUME_MISMATCH",
                f"W02 殘留草稿分館與計畫不符，預期 {form.branch}；已停止避免接錯訂貨單。",
            )
        department = self._find_control_by_id("cM_BrOrderDepCode")
        if department is None or not self._selected_text_matches(department, form.department):
            raise ReportAutomationError(
                "W02_POS_DRAFT_RESUME_MISMATCH",
                f"W02 殘留草稿部門無法確認為 {form.department}；已停止避免接錯訂貨單。",
            )
        use_type = self._find_control_by_id("cT_BrOrderUseType")
        if use_type is None or not (
            self._selected_text_matches(use_type, W02_DEFAULT_USE_TYPE)
            or self._control_has_exact_text_token(use_type, W02_DEFAULT_USE_TYPE)
        ):
            raise ReportAutomationError(
                "W02_POS_DRAFT_RESUME_MISMATCH",
                f"W02 殘留草稿用途類型無法確認為 {W02_DEFAULT_USE_TYPE}；已停止避免接錯訂貨單。",
            )

        entry = self._retryable_draft_entry
        recorded_processed_count = _optional_int(entry.get("processed_item_count"))
        recorded_verified_payload = entry.get("verified_items")
        recorded_skipped_issues = _issues_from_payload(entry.get("skipped_issues"))
        has_exact_checkpoint = recorded_processed_count is not None and isinstance(recorded_verified_payload, list)
        if has_exact_checkpoint:
            assert recorded_processed_count is not None
            if recorded_processed_count < 0 or recorded_processed_count > len(form.items):
                raise ReportAutomationError(
                    "W02_POS_DRAFT_RESUME_MISMATCH",
                    "W02 殘留草稿的已處理品項數超出本次計畫；已停止避免錯位續接。",
                )
            expected_verified_items: list[W02OrderItem] = []
            skipped_cursor = 0
            for item in form.items[:recorded_processed_count]:
                if skipped_cursor < len(recorded_skipped_issues):
                    issue = recorded_skipped_issues[skipped_cursor]
                    if (
                        issue.item_code == item.item_code
                        and issue.item_name == item.item_name
                        and issue.quantity == item.quantity
                    ):
                        skipped_cursor += 1
                        continue
                expected_verified_items.append(item)
            if skipped_cursor != len(recorded_skipped_issues):
                raise ReportAutomationError(
                    "W02_POS_DRAFT_RESUME_MISMATCH",
                    "W02 殘留草稿的跳過品項與本次計畫順序不一致；已停止避免錯位續接。",
                )
            expected_payload = _form_items_payload(
                W02OrderForm(
                    branch=form.branch,
                    department=form.department,
                    items=tuple(expected_verified_items),
                )
            )
            if recorded_verified_payload != expected_payload:
                raise ReportAutomationError(
                    "W02_POS_DRAFT_RESUME_MISMATCH",
                    "W02 殘留草稿的已驗證品項與本次計畫不一致；已停止避免錯位續接。",
                )
            candidate_items = expected_verified_items
        else:
            if recorded_skipped_issues:
                raise ReportAutomationError(
                    "W02_POS_DRAFT_RESUME_MISMATCH",
                    "W02 舊版殘留草稿含跳過品項但缺少精確 checkpoint；已停止避免錯位續接。",
                )
            candidate_items = list(form.items)

        verified_items: list[W02OrderItem] = []
        for row_index, item in enumerate(candidate_items):
            observed_code = self._order_row_item_code(row_index)
            if observed_code is None:
                if has_exact_checkpoint:
                    raise ReportAutomationError(
                        "W02_POS_DRAFT_RESUME_MISMATCH",
                        f"W02 殘留草稿缺少已驗證的第 {row_index + 1} 列；已停止避免錯位續接。",
                    )
                later_code = next(
                    (
                        self._order_row_item_code(later_index)
                        for later_index in range(row_index + 1, len(candidate_items))
                        if self._order_row_item_code(later_index) is not None
                    ),
                    None,
                )
                if later_code is not None:
                    raise ReportAutomationError(
                        "W02_POS_DRAFT_RESUME_MISMATCH",
                        "W02 殘留草稿中間有空列但後方仍有商品；已停止避免錯位續接。",
                    )
                break
            if observed_code != item.item_code:
                raise ReportAutomationError(
                    "W02_POS_DRAFT_RESUME_MISMATCH",
                    (
                        f"W02 殘留草稿第 {row_index + 1} 列料號為 {observed_code}，"
                        f"但計畫為 {item.item_code}；已停止避免接錯訂貨單。"
                    ),
                )
            quantity_text = str(item.quantity)
            quantity_cell = self._find_grid_cell("訂貨 數量", row_index)
            if not self._order_row_quantity_matches(
                row_index,
                item.item_code,
                quantity_text,
                quantity_cell,
            ):
                corrected = (
                    quantity_cell is not None
                    and self._write_order_quantity(
                        row_index,
                        item.item_code,
                        quantity_cell,
                        quantity_text,
                    )
                ) or self._write_order_quantity_by_geometry(
                    row_index,
                    item.item_code,
                    quantity_text,
                )
                if not corrected:
                    raise ReportAutomationError(
                        "W02_POS_DRAFT_RESUME_QUANTITY_UNVERIFIED",
                        (
                            f"W02 殘留草稿第 {row_index + 1} 列料號正確，"
                            f"但數量無法修正並驗證為 {quantity_text}；已停止且未存檔。"
                        ),
                    )
            verified_items.append(item)

        unexpected_row = next(
            (
                (row_index, item_code)
                for row_index in range(
                    len(verified_items),
                    len(form.items) + W02_DRAFT_EXTRA_ROW_SCAN_LIMIT,
                )
                if (item_code := self._order_row_item_code(row_index)) is not None
            ),
            None,
        )
        if unexpected_row is not None:
            raise ReportAutomationError(
                "W02_POS_DRAFT_RESUME_MISMATCH",
                (
                    "W02 殘留草稿含有本次計畫或 checkpoint 未記錄的額外商品列"
                    f"（第 {unexpected_row[0] + 1} 列：{unexpected_row[1]}）；已停止避免錯位續接。"
                ),
            )

        self._checkpoint_active_form_items(
            form,
            verified_items,
            recorded_skipped_issues,
        )
        self.actions.append(
            f"w02_draft_resumed:{_form_key(form)}:verified_items={len(verified_items)}"
        )
        processed_item_count = (
            recorded_processed_count
            if has_exact_checkpoint and recorded_processed_count is not None
            else len(verified_items)
        )
        return _W02ResumeState(
            processed_item_count=processed_item_count,
            submitted_items=tuple(verified_items),
            skipped_issues=recorded_skipped_issues,
        )

    def _order_row_item_code(self, row_index: int) -> str | None:
        normalized_row = f"資料列{row_index}"
        for control in self._order_item_grid_controls():
            name = _normalize(_control_name(control))
            texts = _control_text_candidates(control)
            if name == normalized_row:
                for text in texts:
                    parts = [part.strip() for part in str(text).split(";")]
                    if len(parts) > 1 and re.fullmatch(r"[0-9A-Za-z_-]+", parts[1] or ""):
                        return parts[1]
            if "商品碼" in name and normalized_row in name:
                for text in texts:
                    for token in re.findall(r"[0-9A-Za-z_-]+", str(text)):
                        if token.casefold() not in {"dataitem", str(row_index)} and len(token) >= 4:
                            return str(token)
        return None

    def _checkpoint_active_form_items(
        self,
        form: W02OrderForm,
        verified_items: list[W02OrderItem],
        skipped_issues: list[W02OrderIssue] | tuple[W02OrderIssue, ...],
    ) -> None:
        if self._active_ledger is None or self._active_form_key is None:
            return
        entry = self._active_ledger.get("forms", {}).get(self._active_form_key)
        if not isinstance(entry, dict):
            return
        verified_count = len(verified_items)
        entry["submission_phase"] = "items_in_progress" if verified_count else "draft_started"
        entry["verified_item_count"] = verified_count
        entry["verified_items"] = _form_items_payload(
            W02OrderForm(
                branch=form.branch,
                department=form.department,
                items=tuple(verified_items),
            )
        )
        entry["processed_item_count"] = verified_count + len(skipped_issues)
        entry["skipped_issue_count"] = len(skipped_issues)
        entry["skipped_issues"] = [_issue_payload(issue) for issue in skipped_issues]
        entry["last_item_verified_at"] = datetime.now(tz=UTC).isoformat()
        self._write_ledger(self._active_ledger)

    def _switch_branch(self, branch: str) -> None:
        if branch in self._explicitly_selected_branches and self._window_title_matches_branch(branch):
            self.actions.append(f"w02_branch_already_selected_after_explicit_selection:{branch}")
            return
        self._focus_pos_window("branch_switch")
        self._close_order_window_if_present("before_branch_switch")
        account_menu = self._find_control_by_name_contains(("AI自動化",), control_types=("MenuItem", "Button"))
        if account_menu is None:
            raise ReportAutomationError("W02_POS_BRANCH_MENU_NOT_FOUND", f"W02 找不到右上角帳號/分館選單，無法切換到 {branch}。")
        self._click(account_menu, f"w02_open_branch_menu:{branch}")
        aliases = self._branch_aliases(branch)
        if not self._select_branch_from_account_popup(branch, aliases, force_select=True):
            observed = self._visible_option_summary()
            if _observed_summary_has_branch_alias(observed, aliases):
                self._record_failure_context(
                    "branch_selection_failed",
                    error_code="W02_POS_BRANCH_SELECTION_FAILED",
                    message=f"observed branch but selection was not verified: {branch}; observed: {observed}",
                )
                raise ReportAutomationError(
                    "W02_POS_BRANCH_SELECTION_FAILED",
                    (
                        f"W02 已找到分館選項 {branch}，但無法安全完成或驗證選取。"
                        f"目前可見/可讀選項：{observed}。"
                    ),
                )
            self._record_failure_context("branch_option_not_found", message=f"missing branch: {branch}; observed: {observed}")
            raise ReportAutomationError(
                "W02_POS_BRANCH_OPTION_NOT_FOUND",
                f"W02 找不到分館選項：{branch}。目前可見/可讀選項：{observed or '無'}。",
            )
        self._dismiss_prompt_if_present(allow_generic_confirmation=True)
        if not self._wait_until(lambda: self._window_title_matches_branch(branch), timeout_seconds=15):
            self._record_failure_context(
                "branch_switch_not_verified",
                error_code="W02_POS_BRANCH_SWITCH_NOT_VERIFIED",
                message=f"expected branch title: {branch}; observed: {_control_name(self.window)}",
            )
            raise ReportAutomationError("W02_POS_BRANCH_SWITCH_NOT_VERIFIED", f"W02 已嘗試切換分館，但視窗標題未確認為 {branch}。")
        self._explicitly_selected_branches.add(branch)
        self.actions.append(f"w02_branch_selected:{branch}")

    def _focus_pos_window(self, reason: str) -> None:
        """Put the POS root in the foreground before any physical screen click."""

        last_error: Exception | None = None
        restore = getattr(self.window, "restore", None)
        if callable(restore):
            try:
                restore()
            except Exception as exc:
                last_error = exc

        set_focus = getattr(self.window, "set_focus", None)
        if callable(set_focus):
            try:
                set_focus()
                sleep(W02_POLL_SECONDS)
            except Exception as exc:
                last_error = exc

        handle = _control_native_handle(self.window)
        if handle is None:
            if _control_reports_active(self.window):
                self._invalidate_control_cache()
                self.actions.append(f"w02_pos_window_focused:{reason}")
                return
            detail = f"：{last_error}" if last_error is not None else ""
            raise ReportAutomationError(
                "W02_POS_WINDOW_FOCUS_FAILED",
                f"W02 無法將 SPA-POS 切到前景，已停止實體點擊{detail}。",
            )

        expected_process_id = _control_process_id(self.window)
        if sys.platform.startswith("win") and not _native_window_owns_foreground(
            handle,
            expected_process_id=expected_process_id,
        ):
            try:
                import win32con  # type: ignore[import-untyped]
                import win32gui  # type: ignore[import-untyped]

                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(handle)
            except Exception as exc:
                last_error = exc

        if sys.platform.startswith("win") and not self._wait_until(
            lambda: _native_window_owns_foreground(
                handle,
                expected_process_id=expected_process_id,
            ),
            timeout_seconds=2,
        ):
            detail = f"：{last_error}" if last_error is not None else ""
            raise ReportAutomationError(
                "W02_POS_WINDOW_FOCUS_FAILED",
                f"W02 無法確認 SPA-POS 已取得前景，已停止實體點擊{detail}。",
            )

        self._invalidate_control_cache()
        self.actions.append(f"w02_pos_window_focused:{reason}")

    def _pos_window_foreground_verified(self, control: Any | None = None) -> bool:
        handle = _control_native_handle(self.window)
        if handle is None:
            return _control_reports_active(self.window)
        if _native_window_owns_foreground(
            handle,
            target_handle=_control_native_handle(control) if control is not None else None,
            expected_process_id=_control_process_id(self.window),
        ):
            return True
        # A WinForms ComboBox popup may be a short-lived, ownerless HWND.  Its
        # UIA child often exposes no native handle, so root-owner validation
        # cannot bind the wrapper even while the popup visibly owns the input
        # rectangle.  Accept only a same-PID foreground HWND whose native
        # rectangle contains the strictly readable target control rectangle.
        return control is not None and _native_foreground_contains_control(
            control,
            pos_window_handle=handle,
            expected_process_id=_control_process_id(self.window),
        )

    def _ensure_pos_window_foreground(self, reason: str, *, control: Any | None = None) -> None:
        if self._pos_window_foreground_verified(control):
            return
        self._focus_pos_window(reason)
        if not self._pos_window_foreground_verified(control):
            raise ReportAutomationError(
                "W02_POS_WINDOW_FOCUS_FAILED",
                "W02 已將 SPA-POS 切到前景，但無法確認本次實體操作的目標控制項屬於前景視窗；已停止操作。",
            )

    def _select_branch_from_account_popup(
        self,
        branch: str,
        aliases: tuple[str, ...],
        *,
        force_select: bool = False,
    ) -> bool:
        deadline = monotonic() + 3
        while monotonic() < deadline:
            self._invalidate_control_cache()
            for control in self._branch_combo_candidates(aliases):
                selected_text = self._selected_text(control)
                if (
                    not force_select
                    and selected_text
                    and any(_text_matches(selected_text, alias) for alias in aliases)
                ):
                    self.actions.append(f"w02_branch_combo_already_selected:{_action_text(selected_text)}")
                    return True
                if self._select_combo_by_alias(control, aliases):
                    self.actions.append(f"w02_branch_combo_selected:{branch}")
                    return True
            if self._select_visible_option(
                branch,
                aliases,
                allow_combobox=False,
                verify=lambda: self._branch_selection_readback_matches(aliases),
                allow_same_process_ownerless_popup=True,
            ):
                return True
            sleep(0.2)
        return False

    def _branch_combo_candidates(self, aliases: tuple[str, ...]) -> list[Any]:
        def matching_candidates(
            controls: list[Any],
            *,
            require_pos_ownership: bool = False,
        ) -> list[Any]:
            candidates: list[Any] = []
            for control in controls:
                if not _control_visible_strict(control) or not _control_enabled_strict(control):
                    continue
                control_type = _control_type(control).lower()
                if control_type not in {"combobox", "combo box"}:
                    continue
                name = _control_name(control)
                item_texts = _control_item_texts(control)
                if (
                    any(_text_matches(name, alias) for alias in aliases)
                    or any(any(_text_matches(item, alias) for alias in aliases) for item in item_texts)
                    or any(_looks_like_pos_branch_option(item) for item in item_texts)
                    or _looks_like_pos_branch_option(name)
                ):
                    if require_pos_ownership and not (
                        _control_owned_by_pos_window(control, self.window)
                        or _same_process_popup_or_pos_is_foreground(control, self.window)
                    ):
                        self.actions.append(
                            f"w02_desktop_branch_candidate_rejected:{_action_text('|'.join(item_texts[:12]) or name)}"
                        )
                        continue
                    candidates.append(control)
                    summary = "|".join(item_texts[:12]) or name
                    self.actions.append(f"w02_branch_combo_candidate:{_action_text(summary)}")
            return candidates

        # Most account-menu controls are already descendants of the connected
        # POS root. A process-wide Desktop UIA descendants scan is both slower
        # and vulnerable to RPC_E_CANTCALLOUT_ININPUTSYNCCALL on this POS host,
        # so use it only for popup controls that are genuinely outside the root.
        local_candidates = matching_candidates(self._all_controls())
        if local_candidates:
            return local_candidates
        return matching_candidates(
            self._pos_desktop_controls(),
            require_pos_ownership=True,
        )

    def _select_combo_by_alias(self, control: Any, aliases: tuple[str, ...]) -> bool:
        item_texts = _control_item_texts(control)
        matched_index: int | None = None
        matched_text: str | None = None
        for index, item_text in enumerate(item_texts):
            if any(_text_matches(item_text, alias) for alias in aliases):
                matched_index = index
                matched_text = item_text
                break

        # A materialized branch list is already a bounded, exact selection
        # source. Prefer non-physical UIA selection before touching the
        # transient account-menu ComboBox HWND. Some WinForms popup controls
        # are separate top-level windows without a stable owner relationship;
        # forcing a geometry click first can collapse that popup when the POS
        # root is focused and leave a stale target wrapper.
        candidates = ([matched_text] if matched_text is not None else []) + list(aliases)
        programmatic_attempted = False
        programmatic_wrapper_failed = False
        programmatic_pattern_unavailable = False
        text_selection_finished = False
        for candidate in candidates:
            for method_name in ("select", "Select"):
                method = getattr(control, method_name, None)
                if not callable(method):
                    continue
                programmatic_attempted = True
                try:
                    method(candidate)
                except Exception as exc:
                    if self._wait_until(
                        lambda: self._branch_selection_readback_matches(aliases),
                        timeout_seconds=2,
                    ):
                        self.actions.append(f"w02_branch_combo_select_text:{_action_text(candidate)}")
                        return True
                    self.actions.append(
                        (
                            "w02_branch_combo_pattern_unavailable:"
                            if isinstance(exc, NoPatternInterfaceError)
                            else "w02_branch_combo_programmatic_exception:"
                        )
                        + f"method={method_name}:candidate={_action_text(candidate)}:"
                        + f"error={_exception_evidence(exc)}"
                    )
                    if isinstance(exc, NoPatternInterfaceError):
                        # pywinauto raises this from ComboBoxWrapper.expand()
                        # before it can dispatch a selection.  Skip the same
                        # unsupported UIA API, then prefer an exact native HWND
                        # selection; keyboard remains a last resort only when
                        # foreground ownership is independently proven. Other
                        # exceptions remain ambiguous and abandon the wrapper
                        # without a second dispatch.
                        programmatic_pattern_unavailable = True
                    else:
                        programmatic_wrapper_failed = True
                    text_selection_finished = True
                    break
                if self._wait_until(
                    lambda: self._branch_selection_readback_matches(aliases),
                    timeout_seconds=2,
                ):
                    self.actions.append(f"w02_branch_combo_select_text:{_action_text(candidate)}")
                    return True
                self.actions.append(
                    "w02_branch_combo_programmatic_noop:"
                    f"method={method_name}:candidate={_action_text(candidate)}"
                )
                text_selection_finished = True
                break
            if text_selection_finished:
                break

        if matched_index is not None and not programmatic_wrapper_failed:
            if programmatic_pattern_unavailable and matched_text is not None:
                native_dispatched, native_detail = _native_combo_select_index(
                    control,
                    matched_index,
                    matched_text,
                    aliases,
                    expected_process_id=_control_process_id(self.window),
                )
                if native_dispatched:
                    if self._wait_until(
                        lambda: self._branch_selection_readback_matches(aliases),
                        timeout_seconds=2,
                    ):
                        self.actions.append(
                            "w02_branch_combo_native_select:"
                            f"{matched_index}:{_action_text(matched_text)}:{_action_text(native_detail)}"
                        )
                        return True
                    self.actions.append(
                        "w02_branch_combo_native_selection_unverified:"
                        f"{matched_index}:{_action_text(matched_text)}:{_action_text(native_detail)}"
                    )
                    # The native ComboBox wrapper may have sent CB_SETCURSEL
                    # before a later notification/readback failure.  Never
                    # follow an unverified native dispatch with keyboard input.
                    return False
                self.actions.append(
                    "w02_branch_combo_native_unavailable:"
                    f"{matched_index}:{_action_text(matched_text)}:{_action_text(native_detail)}"
                )
            index_methods = (
                ()
                if programmatic_pattern_unavailable
                else ("SelectedIndex", "SelectedIndex_", "select", "Select")
            )
            for method_name in index_methods:
                method = getattr(control, method_name, None)
                if not callable(method):
                    continue
                programmatic_attempted = True
                try:
                    method(matched_index)
                except Exception as exc:
                    if self._wait_until(
                        lambda: self._branch_selection_readback_matches(aliases),
                        timeout_seconds=2,
                    ):
                        self.actions.append(
                            f"w02_branch_combo_select_index:{matched_index}:{_action_text(matched_text or '')}"
                        )
                        return True
                    self.actions.append(
                        "w02_branch_combo_programmatic_exception:"
                        f"method={method_name}:index={matched_index}:"
                        f"error={_exception_evidence(exc)}"
                    )
                    return False
                if self._wait_until(
                    lambda: self._branch_selection_readback_matches(aliases),
                    timeout_seconds=2,
                ):
                    self.actions.append(f"w02_branch_combo_select_index:{matched_index}:{_action_text(matched_text or '')}")
                    return True
                self.actions.append(
                    "w02_branch_combo_programmatic_noop:"
                    f"method={method_name}:index={matched_index}"
                )
                break
            keys = "{HOME}" + (f"{{DOWN {matched_index}}}" if matched_index else "") + "{ENTER}"
            keyboard_allowed = bool(
                (
                    _control_owned_by_pos_window(control, self.window)
                    or any(_same_control(control, candidate) for candidate in self._all_controls())
                )
                and _control_visible_strict(control)
                and _control_enabled_strict(control)
                and self._pos_window_foreground_verified(control)
            )
            self.actions.append(
                "w02_branch_combo_keyboard_gate:"
                f"allowed={keyboard_allowed}:"
                f"direct_hwnd={_control_direct_native_handle(control) is not None}:"
                f"resolved_hwnd={_control_native_handle(control) is not None}"
            )
            for method_name in (("type_keys", "TypeKeys") if keyboard_allowed else ()):
                method = getattr(control, method_name, None)
                if not callable(method):
                    continue
                programmatic_attempted = True
                try:
                    method(keys)
                except Exception as exc:
                    if self._wait_until(
                        lambda: self._branch_selection_readback_matches(aliases),
                        timeout_seconds=2,
                    ):
                        self.actions.append(
                            "w02_branch_combo_keyboard_select_after_exception:"
                            f"{matched_index}:{_action_text(matched_text or '')}:"
                            f"error={_exception_evidence(exc)}"
                        )
                        return True
                    self.actions.append(
                        "w02_branch_combo_keyboard_exception:"
                        f"method={method_name}:error={_exception_evidence(exc)}"
                    )
                    break
                if self._wait_until(
                    lambda: self._branch_selection_readback_matches(aliases),
                    timeout_seconds=2,
                ):
                    self.actions.append(
                        f"w02_branch_combo_keyboard_select:{matched_index}:{_action_text(matched_text or '')}"
                    )
                    return True
                self.actions.append(
                    f"w02_branch_combo_keyboard_noop:{matched_index}:{_action_text(matched_text or '')}"
                )
                break
            if not keyboard_allowed and any(callable(getattr(control, name, None)) for name in ("type_keys", "TypeKeys")):
                self.actions.append("w02_branch_combo_keyboard_skipped:target_foreground_not_proven")
        if programmatic_attempted:
            return False
        self._click_dropdown(control, "w02_branch_combo_dropdown")
        if self._select_visible_option(
            matched_text or aliases[0],
            aliases,
            allow_combobox=False,
            verify=lambda: self._branch_selection_readback_matches(aliases),
            allow_same_process_ownerless_popup=True,
        ):
            return True
        return False

    def _branch_selection_readback_matches(self, aliases: tuple[str, ...]) -> bool:
        title = _control_name(self.window)
        if any(_text_contains_exact_token(title, alias) for alias in aliases):
            return True
        for candidate in self._all_controls():
            if not _control_visible(candidate):
                continue
            if _control_type(candidate).lower() not in {"combobox", "combo box"}:
                continue
            if self._selected_text_matches_any(candidate, aliases):
                return True
        return False

    def _selected_text_matches_any(self, control: Any, aliases: tuple[str, ...]) -> bool:
        selected_text = self._selected_text(control)
        return selected_text is not None and any(_text_matches(selected_text, alias) for alias in aliases)

    def _selected_text(self, control: Any) -> str | None:
        for attr in ("selected_text", "SelectedText"):
            method = getattr(control, attr, None)
            if callable(method):
                try:
                    value = method()
                    if value:
                        return str(value)
                except Exception:
                    pass
        selected_value = getattr(control, "selected_value", None)
        if selected_value:
            return str(selected_value)
        texts = _safe_call(control, "texts", default=[])
        if texts:
            for text in texts:
                if text:
                    return str(text)
        name = _control_name(control)
        return name or None

    def _visible_option_summary(self) -> str:
        texts: list[str] = []
        # Keep the failure path bounded to the connected POS tree.  The host
        # has repeatedly raised RPC_E_CANTCALLOUT_ININPUTSYNCCALL during a
        # process-wide Desktop UIA scan; a screenshot and the automatic run
        # bundle preserve external popup pixels without risking a second COM
        # failure while reporting the first one.
        for control in self._all_controls():
            if not _control_visible(control):
                continue
            name = _control_name(control)
            if name and (_looks_like_pos_branch_option(name) or _control_type(control).lower() in {"listitem", "menuitem", "combobox"}):
                texts.append(name)
            texts.extend(_control_item_texts(control))
        unique_texts = list(dict.fromkeys(_action_text(text) for text in texts if text))
        return "、".join(unique_texts[:20])

    def _branch_aliases(self, branch: str) -> tuple[str, ...]:
        aliases: list[str] = [branch, *W02_BRANCH_ALIASES.get(branch, ())]
        normalized_seed_aliases = {_normalize(alias) for alias in aliases if alias}
        for config in self.branches:
            values = _branch_config_alias_values(config)
            normalized_values = {_normalize(value) for value in values if value}
            if normalized_seed_aliases.intersection(normalized_values):
                aliases.extend(values)
                aliases.append(f"{config.code}:{branch}")
                aliases.append(f"{config.code}:{config.display_name}")
                aliases.append(f"{config.code}:{config.display_name.replace('F', '樓')}")
        return tuple(dict.fromkeys(alias for alias in aliases if alias))

    def _open_order_window(self) -> None:
        order_window = self._find_control_by_id("BrOrder")
        if order_window is not None and _control_visible(order_window):
            self.actions.append("w02_order_window_already_open")
            return
        if str(getattr(self.window, "_pos_report_bot_backend", "") or "").casefold() == "uia":
            # SPA-POS's UIA MenuWrapper.menu_select can trigger native
            # RPC_E_CANTCALLOUT_ININPUTSYNCCALL (0x8001010d) even when Python
            # later catches the wrapper exception.  The first inventory popup
            # is owner-drawn and may expose its rows as UIA-visible=False, so
            # use a visible leaf when available and otherwise a menu-order-
            # verified, foreground-bound keyboard transaction.
            self.actions.append("w02_menu_select_skipped:uia_native_menu_select_disabled")
            self._open_order_window_via_uia_inventory_menu()
            return
        menu_select = getattr(self.window, "menu_select", None)
        if callable(menu_select):
            try:
                menu_select("庫存管理->分店訂貨單")
                self.actions.append("w02_menu_select:庫存管理->分店訂貨單")
            except Exception:
                self._click_menu_path(("庫存管理", "分店訂貨單"))
        else:
            self._click_menu_path(("庫存管理", "分店訂貨單"))
        if not self._wait_until(lambda: self._find_control_by_id("BrOrder") is not None, timeout_seconds=10):
            raise ReportAutomationError("W02_POS_ORDER_WINDOW_NOT_FOUND", "W02 找不到「分店訂貨單」視窗。")

    def _open_order_window_via_uia_inventory_menu(self) -> None:
        root = self._find_local_visible_menu_control("庫存管理")
        if root is None:
            self._record_failure_context("inventory_menu_root_not_found", message="missing menu root: 庫存管理")
            raise ReportAutomationError("W02_POS_MENU_NOT_FOUND", "W02 找不到 POS 選單：庫存管理。")

        observed_order = self._observed_inventory_first_level_order()
        self._click(root, "w02_menu:庫存管理")
        leaf = self._find_local_visible_menu_control("分店訂貨單")
        keyboard_dispatched = False
        if leaf is not None:
            self._click(leaf, "w02_menu:分店訂貨單")
            self.actions.append("w02_inventory_menu_visible_leaf_dispatched:分店訂貨單")
        else:
            if observed_order[:2] != list(W02_INVENTORY_MENU_FIRST_LEVEL_ORDER):
                observed_text = "|".join(observed_order[:2]) or "無"
                self._record_failure_context(
                    "inventory_menu_order_unverified",
                    error_code="W02_POS_MENU_ORDER_UNVERIFIED",
                    message=f"observed first-level inventory menu order: {observed_text}",
                )
                raise ReportAutomationError(
                    "W02_POS_MENU_ORDER_UNVERIFIED",
                    (
                        "W02 可開啟「庫存管理」，但無法驗證前兩個選項仍為「分店訂貨單、相關報表」；"
                        "為避免鍵盤誤開其他功能，本次已停止。"
                    ),
                )
            self.actions.append(
                "w02_inventory_menu_order_verified:"
                + "|".join(W02_INVENTORY_MENU_FIRST_LEVEL_ORDER)
            )
            self.actions.append("w02_inventory_menu_keyboard_dispatch:分店訂貨單")
            self._send_keys("{HOME}", control=root)
            self._send_keys("{ENTER}", control=root)
            keyboard_dispatched = True

        if not self._wait_until(
            lambda: (
                (order_window := self._find_control_by_id("BrOrder")) is not None
                and _control_visible(order_window)
            ),
            timeout_seconds=10,
        ):
            self._record_failure_context(
                "order_window_not_opened",
                error_code="W02_POS_ORDER_WINDOW_NOT_FOUND",
                message="bounded inventory menu dispatch did not expose BrOrder",
            )
            raise ReportAutomationError("W02_POS_ORDER_WINDOW_NOT_FOUND", "W02 找不到「分店訂貨單」視窗。")
        route = "keyboard" if keyboard_dispatched else "visible_leaf"
        self.actions.append(f"w02_inventory_menu_target_confirmed:{route}:分店訂貨單")

    def _find_local_visible_menu_control(self, expected_name: str) -> Any | None:
        expected = _normalize(expected_name)
        for control in self._all_controls():
            if _control_type(control).lower() != "menuitem":
                continue
            if _normalize(_control_name(control)) != expected:
                continue
            if _control_visible(control) and _control_enabled(control):
                return control
        return None

    def _observed_inventory_first_level_order(self) -> list[str]:
        observed: list[str] = []
        expected_by_normalized = {
            _normalize(name): name for name in W02_INVENTORY_MENU_FIRST_LEVEL_ORDER
        }
        for control in self._all_controls():
            if _control_type(control).lower() != "menuitem":
                continue
            expected = expected_by_normalized.get(_normalize(_control_name(control)))
            if expected is not None and expected not in observed:
                observed.append(expected)
        return observed

    def _click_menu_path(self, names: tuple[str, ...]) -> None:
        for name in names:
            control = self._find_control_by_name_contains((name,), control_types=("MenuItem",))
            if control is None:
                raise ReportAutomationError("W02_POS_MENU_NOT_FOUND", f"W02 找不到 POS 選單：{name}。")
            self._click(control, f"w02_menu:{name}")

    def _select_department(self, department: str) -> None:
        control = self._find_control_by_id("cM_BrOrderDepCode")
        if control is None:
            raise ReportAutomationError("W02_POS_DEPARTMENT_COMBO_NOT_FOUND", "W02 找不到分店訂貨單的部門下拉選單。")
        if self._select_combo_text(control, department):
            self.actions.append(f"w02_department_selected:{department}")
            return
        raise ReportAutomationError("W02_POS_DEPARTMENT_OPTION_NOT_FOUND", f"W02 找不到 POS 部門選項：{department}。")

    def _order_window_branch_matches(self, branch: str) -> bool:
        branch_text = self._order_window_branch_text()
        return bool(branch_text and any(_text_matches(branch_text, alias) for alias in self._branch_aliases(branch)))

    def _order_window_branch_text(self) -> str:
        control = self._find_control_by_id("cL_BranchName")
        if control is not None:
            text = " ".join(_control_text_candidates(control)).strip()
            if text:
                return text
        for control in self._order_window_controls():
            automation_id = _control_automation_id(control)
            name = _control_name(control)
            if automation_id == "cL_BranchName" or "訂貨分店" in name:
                text = " ".join(_control_text_candidates(control)).strip()
                if text:
                    return text
        return ""

    def _select_use_type(self, use_type: str) -> None:
        control = self._find_control_by_id("cT_BrOrderUseType")
        if control is None:
            control = self._find_control_by_name_contains(("用途類型",), control_types=("ComboBox", "Edit", "DataItem", "Custom"))
        if control is None:
            raise ReportAutomationError("W02_POS_USE_TYPE_CONTROL_NOT_FOUND", "W02 找不到分店訂貨單的用途類型下拉欄位。")
        if self._selected_text_matches(control, use_type) or self._control_has_exact_text_token(control, use_type):
            self.actions.append(f"w02_use_type_selected:{use_type}:already")
            return
        if _control_type(control).lower() in {"combobox", "combo box"} and self._select_combo_text(control, use_type):
            self.actions.append(f"w02_use_type_selected:{use_type}")
            return
        if self._select_use_type_popup_value(control, use_type):
            self.actions.append(f"w02_use_type_selected:{use_type}:popup_grid")
            return
        self._record_failure_context("use_type_option_not_found", message=f"missing use type: {use_type}")
        raise ReportAutomationError("W02_POS_USE_TYPE_OPTION_NOT_FOUND", f"W02 找不到 POS 用途類型選項：{use_type}。")

    def _select_use_type_popup_value(self, control: Any, use_type: str) -> bool:
        popup_button = self._find_control_by_id("pb_BrOrderUseType")
        if popup_button is None:
            return False
        self._click(popup_button, f"w02_use_type_popup_open:{use_type}")
        if self._use_type_value_matches(control, use_type):
            return True
        if self._wait_until(lambda: self._click_visible_option_near_control(control, use_type), timeout_seconds=3):
            if self._wait_until(lambda: self._use_type_value_matches(control, use_type), timeout_seconds=2):
                return True
            self.actions.append(f"w02_use_type_popup_selection_not_verified:{use_type}")
            return False
        self.actions.append(f"w02_use_type_popup_option_not_visible:{use_type}")
        return False

    def _use_type_value_matches(self, control: Any, use_type: str) -> bool:
        if self._selected_text_matches(control, use_type) or self._control_has_exact_text_token(control, use_type):
            return True
        refreshed = self._find_control_by_id("cT_BrOrderUseType")
        return refreshed is not None and (
            self._selected_text_matches(refreshed, use_type) or self._control_has_exact_text_token(refreshed, use_type)
        )

    def _click_visible_option_near_control(self, anchor: Any, value: str) -> bool:
        anchor_rect = _rect_dict(anchor)
        def click_from(controls: list[Any], *, require_pos_ownership: bool = False) -> bool:
            for control in controls:
                if not _control_visible(control) or not _control_enabled(control):
                    continue
                if _same_control(control, anchor):
                    continue
                if not any(_text_matches(text, value) for text in _control_text_candidates(control)):
                    continue
                control_type = _control_type(control).lower()
                if control_type not in {"listitem", "dataitem", "text", "button", "custom", "pane"}:
                    continue
                if anchor_rect is not None and not _rect_near_anchor_popup(_rect_dict(control), anchor_rect):
                    continue
                if require_pos_ownership and not _control_owned_by_pos_window(control, self.window):
                    continue
                self._click(control, f"w02_use_type_popup_option:{value}")
                return True
            return False

        if click_from(self._all_controls()):
            return True
        if click_from(self._pos_desktop_controls(), require_pos_ownership=True):
            return True
        return False

    def _add_item(self, row_index: int, item_code: str, quantity: int) -> None:
        selector_cell = self._find_grid_cell("選取商品", row_index)
        if selector_cell is None:
            if not self._click_grid_cell_by_geometry(
                "選取商品",
                row_index,
                f"w02_open_item_picker:{item_code}",
            ):
                self._record_failure_context("item_selector_cell_not_found", item_code=item_code, row_index=row_index)
                raise ReportAutomationError(
                    "W02_POS_ITEM_SELECTOR_CELL_NOT_FOUND",
                    f"W02 找不到第 {row_index + 1} 列選取商品欄。",
                )
        else:
            self._click(selector_cell, f"w02_open_item_picker:{item_code}")
        if not self._wait_until(lambda: self._find_control_by_id("ItemsWin") is not None, timeout_seconds=10):
            self._record_failure_context("item_picker_not_found", item_code=item_code, row_index=row_index)
            raise ReportAutomationError("W02_POS_ITEM_PICKER_NOT_FOUND", f"W02 點選商品欄後未出現商品選擇視窗：{item_code}。")
        # ItemsWin can contain hundreds of grid-row controls.  Searching the
        # whole POS window is capped at 1200 controls and can stop before the
        # picker toolbar, even though the filter is visibly present.  Keep
        # this lookup scoped to the picker subtree.
        search_edit = self._find_item_picker_control_by_id("T_Find")
        if search_edit is None:
            self._record_failure_context("item_search_not_found", item_code=item_code, row_index=row_index)
            raise ReportAutomationError("W02_POS_ITEM_SEARCH_NOT_FOUND", "W02 找不到商品選擇視窗的篩選欄。")
        self._set_text(search_edit, item_code)
        self._send_keys("{ENTER}", control=search_edit)
        if not self._wait_until(lambda: self._item_picker_has_selectable_result(item_code), timeout_seconds=5):
            self._record_failure_context("item_picker_no_selectable_result", item_code=item_code, row_index=row_index)
            raise ReportAutomationError("W02_POS_ITEM_NOT_FOUND", f"W02 商品選擇視窗找不到料號：{item_code}。")
        if self._select_item_picker_row(item_code) is None:
            self._record_failure_context("item_picker_select_failed", item_code=item_code, row_index=row_index)
            raise ReportAutomationError("W02_POS_ITEM_NOT_FOUND", f"W02 商品選擇視窗找不到料號：{item_code}。")
        ok_control = self._find_item_picker_control_by_id("B_OK")
        if ok_control is None:
            self._record_failure_context("item_picker_ok_not_found", item_code=item_code, row_index=row_index)
            raise ReportAutomationError(
                "W02_POS_ITEM_PICKER_OK_NOT_FOUND",
                "W02 找不到商品選擇視窗的勾選完成按鈕。",
            )
        self._click(ok_control, f"w02_item_picker_ok:{item_code}")
        if not self._wait_until(lambda: self._find_control_by_id("ItemsWin") is None, timeout_seconds=10):
            self._dismiss_prompt_if_present(allow_generic_confirmation=True)
        if self._find_control_by_id("ItemsWin") is not None:
            self._record_failure_context("item_picker_ok_did_not_close", item_code=item_code, row_index=row_index)
            raise ReportAutomationError(
                "W02_POS_ITEM_PICKER_OK_NOT_CLOSED",
                (
                    f"W02 已勾選料號 {item_code} 並按下勾選完成，但商品選擇視窗仍未關閉；"
                    "為避免把商品視窗內容誤判為訂貨單明細，本次停止。"
                ),
            )
        if not self._wait_until(lambda: self._order_row_contains_item(row_index, item_code), timeout_seconds=5):
            self._record_failure_context("item_selection_not_verified", item_code=item_code, row_index=row_index)
            raise ReportAutomationError(
                "W02_POS_ITEM_SELECTION_NOT_VERIFIED",
                f"W02 已選取料號 {item_code}，但訂貨單明細第 {row_index + 1} 列未確認帶入相同料號。",
            )
        quantity_cell = self._find_grid_cell("訂貨 數量", row_index)
        if quantity_cell is None:
            if self._write_order_quantity_by_geometry(row_index, item_code, str(quantity)):
                self.actions.append(f"w02_item_added:{item_code}:{quantity}")
                return
            self._record_failure_context("quantity_cell_not_found", item_code=item_code, row_index=row_index)
            raise ReportAutomationError(
                "W02_POS_QUANTITY_CELL_NOT_FOUND",
                f"W02 找不到第 {row_index + 1} 列訂貨數量欄，且無法從已驗證的明細列與欄頭安全定位。",
            )
        if not self._write_order_quantity(row_index, item_code, quantity_cell, str(quantity)):
            self._record_failure_context("quantity_not_verified", item_code=item_code, row_index=row_index)
            raise ReportAutomationError(
                "W02_POS_QUANTITY_NOT_VERIFIED",
                (
                    f"W02 已嘗試在第 {row_index + 1} 列輸入下單數 {quantity}，"
                    "但未能從 POS 訂貨數量欄讀回相同數值；本次不進行存檔/核准。"
                ),
            )
        self.actions.append(f"w02_item_added:{item_code}:{quantity}")

    def _write_order_quantity_by_geometry(
        self,
        row_index: int,
        item_code: str,
        quantity_text: str,
    ) -> bool:
        """Edit a painted quantity cell only from exact grid row/header proof."""

        if not self._click_grid_cell_by_geometry(
            "訂貨 數量",
            row_index,
            f"w02_quantity_cell:{item_code}",
        ):
            return False
        grid = self._find_control_by_id("gv_BrOrderItem")
        if grid is None:
            return False
        self._replace_focused_text(quantity_text, control=grid)
        self._send_keys("{ENTER}", control=grid)
        if not self._wait_until(
            lambda: self._order_row_quantity_matches(
                row_index,
                item_code,
                quantity_text,
                None,
            ),
            timeout_seconds=2,
        ):
            return False
        self.actions.append(
            f"w02_quantity_verified:{item_code}:{quantity_text}:derived_grid_geometry"
        )
        return True

    def _write_order_quantity(self, row_index: int, item_code: str, quantity_cell: Any, quantity_text: str) -> bool:
        attempts: tuple[tuple[str, Callable[[], None]], ...] = (
            ("direct", lambda: self._set_grid_cell_text_direct(quantity_cell, quantity_text, item_code)),
            ("f2_paste_enter", lambda: self._paste_grid_cell_text(quantity_cell, quantity_text, "{F2}", "{ENTER}", item_code)),
            (
                "double_click_paste_enter",
                lambda: self._paste_grid_cell_text(quantity_cell, quantity_text, "double_click", "{ENTER}", item_code),
            ),
            ("f2_paste_tab", lambda: self._paste_grid_cell_text(quantity_cell, quantity_text, "{F2}", "{TAB}", item_code)),
        )
        for attempt_name, attempt in attempts:
            attempt()
            if self._wait_until(
                lambda: self._order_row_quantity_matches(row_index, item_code, quantity_text, quantity_cell),
                timeout_seconds=2,
            ):
                self.actions.append(f"w02_quantity_verified:{item_code}:{quantity_text}:{attempt_name}")
                return True
        return False

    def _set_grid_cell_text_direct(self, control: Any, value: str, item_code: str) -> None:
        self._click(control, f"w02_quantity_cell:{item_code}")
        self._set_text(control, value)
        self._send_keys("{ENTER}", control=control)

    def _paste_grid_cell_text(self, control: Any, value: str, edit_trigger: str, commit_key: str, item_code: str) -> None:
        self._click(control, f"w02_quantity_cell:{item_code}:{edit_trigger}")
        if edit_trigger == "double_click":
            self._double_click(control, f"w02_quantity_cell_double_click:{item_code}")
        else:
            self._send_keys(edit_trigger, control=control)
        self._replace_focused_text(value, control=control)
        self._send_keys(commit_key, control=control)

    def _replace_focused_text(self, value: str, *, control: Any) -> None:
        original_clipboard = _clipboard_get_text()
        clipboard_ready = _clipboard_set_text(value)
        try:
            self._send_keys("^a{BACKSPACE}", control=control)
            if clipboard_ready:
                self._send_keys("^v", control=control)
            else:
                self._send_keys(value, control=control, with_spaces=True)
        finally:
            if original_clipboard is not None:
                _clipboard_set_text(original_clipboard)

    def _select_item_picker_row(self, item_code: str) -> str | None:
        code_cell = self._find_item_picker_code_cell(item_code)
        if code_cell is not None:
            self._click(code_cell, f"w02_item_picker_code:{item_code}")
            checkbox = self._find_item_picker_checkbox_for_code_cell(code_cell)
            if checkbox is not None:
                self._check_or_click(checkbox, f"w02_item_picker_select:{item_code}")
                return "code_cell"
            if self._click_item_picker_checkbox_by_geometry(code_cell, item_code):
                return "code_cell"
        if self._select_single_filtered_item_picker_result(item_code):
            return "single_filtered_result"
        return None

    def _item_picker_has_selectable_result(self, item_code: str) -> bool:
        if self._find_item_picker_code_cell(item_code) is not None:
            return True
        return self._item_picker_has_single_visible_result() and (
            self._find_item_picker_checkbox_for_row_index(0) is not None
            or self._find_item_picker_row(0) is not None
        )

    def _find_item_picker_code_cell(self, item_code: str) -> Any | None:
        normalized_code = _normalize(item_code)
        for control in self._item_picker_controls():
            if not _control_visible(control):
                continue
            control_type = _control_type(control).lower()
            if control_type not in {"dataitem", "text", "custom"}:
                continue
            name = _control_name(control)
            if normalized_code and _text_contains_exact_token(name, item_code):
                return control
        return None

    def _find_item_picker_checkbox_for_code_cell(self, code_cell: Any) -> Any | None:
        code_rect = _rect_dict(code_cell)
        row_token = _grid_row_token(code_cell)
        fallback: Any | None = None
        row_fallback: Any | None = None
        for control in self._item_picker_controls():
            if not _control_visible(control) or not _control_enabled(control):
                continue
            control_type = _control_type(control).lower()
            name = _normalize(_control_name(control))
            if control_type not in {"checkbox", "dataitem", "button", "custom"} and "選" not in name:
                continue
            if row_token and row_token in name:
                if control_type == "checkbox" or "選" in name:
                    return control
                row_fallback = row_fallback or control
            if code_rect is None:
                if "選資料列0" in name or name == "選":
                    return control
                continue
            rect = _rect_dict(control)
            if rect is None:
                continue
            vertical_overlap = rect["bottom"] >= code_rect["top"] and rect["top"] <= code_rect["bottom"]
            left_of_code = rect["right"] <= code_rect["left"] + 12
            if vertical_overlap and left_of_code:
                if control_type == "checkbox":
                    return control
                fallback = fallback or control
        if row_fallback is not None:
            return row_fallback
        if fallback is not None:
            return fallback
        return self._single_visible_item_picker_checkbox()

    def _select_single_filtered_item_picker_result(self, item_code: str) -> bool:
        if not self._item_picker_has_single_visible_result():
            return False
        checkbox = self._find_item_picker_checkbox_for_row_index(0)
        if checkbox is not None:
            self._select_item_picker_checkbox_cell(checkbox, f"w02_item_picker_select:{item_code}:single_result")
            return True
        row = self._find_item_picker_row(0)
        if row is None:
            return False
        self._click(row, f"w02_item_picker_row:{item_code}")
        checkbox = self._find_item_picker_checkbox_for_row_index(0)
        if checkbox is not None:
            self._select_item_picker_checkbox_cell(checkbox, f"w02_item_picker_select:{item_code}:single_result")
            return True
        rect = _rect_dict(row)
        if rect is None:
            return False
        header = self._find_item_picker_header("選")
        if header is not None:
            header_rect = _rect_dict(header)
            if header_rect is not None:
                x = header_rect["left"] + ((header_rect["right"] - header_rect["left"]) // 2)
                y = rect["top"] + ((rect["bottom"] - rect["top"]) // 2)
                if self._click_screen_point(
                    x,
                    y,
                    f"w02_item_picker_select_single_result_geometry:{item_code}",
                    control=row,
                ):
                    self.actions.append(f"click:w02_item_picker_select:{item_code}:single_result_geometry")
                    self._wait_after_action()
                    return True
        return False

    def _item_picker_has_single_visible_result(self) -> bool:
        return self._item_picker_result_count() == 1

    def _item_picker_result_count(self) -> int | None:
        count_label = self._find_item_picker_control_by_id("L_Count")
        if count_label is None:
            return None
        match = re.search(r"\d+", _control_name(count_label))
        return int(match.group(0)) if match else None

    def _find_item_picker_row(self, row_index: int) -> Any | None:
        row_token = f"資料列{row_index}"
        for control in self._item_picker_controls():
            if not _control_visible(control):
                continue
            control_type = _control_type(control).lower()
            name = _normalize(_control_name(control))
            if name == row_token and control_type in {"custom", "dataitem"}:
                return control
        return None

    def _find_item_picker_checkbox_for_row_index(self, row_index: int) -> Any | None:
        row_token = f"資料列{row_index}"
        for control in self._item_picker_controls():
            if not _control_visible(control) or not _control_enabled(control):
                continue
            control_type = _control_type(control).lower()
            name = _normalize(_control_name(control))
            if row_token in name and "選" in name and control_type in {"dataitem", "checkbox", "custom", "button"}:
                return control
        return None

    def _find_item_picker_header(self, text: str) -> Any | None:
        normalized = _normalize(text)
        for control in self._item_picker_controls():
            if not _control_visible(control):
                continue
            if _control_type(control).lower() == "header" and _normalize(_control_name(control)) == normalized:
                return control
        return None

    def _item_picker_controls(self) -> list[Any]:
        if self._item_picker_controls_cache is not None:
            return self._item_picker_controls_cache
        picker = self._find_control_by_id("ItemsWin")
        if picker is None:
            return []
        self._item_picker_controls_cache = _collect_controls(picker, max_depth=7, max_controls=2000)
        return self._item_picker_controls_cache

    def _find_item_picker_control_by_id(self, automation_id: str) -> Any | None:
        for control in self._item_picker_controls():
            if _control_automation_id(control) == automation_id and _control_visible(control):
                return control
        return None

    def _control_is_inside_item_picker(self, target: Any) -> bool:
        return any(_same_control(control, target) for control in self._item_picker_controls())

    def _single_visible_item_picker_checkbox(self) -> Any | None:
        candidates: list[Any] = []
        for control in self._item_picker_controls():
            if not _control_visible(control) or not _control_enabled(control):
                continue
            control_type = _control_type(control).lower()
            name = _normalize(_control_name(control))
            if control_type == "checkbox" or "選" in name:
                candidates.append(control)
        return candidates[0] if len(candidates) == 1 else None

    def _click_item_picker_checkbox_by_geometry(self, code_cell: Any, item_code: str) -> bool:
        rect = _rect_dict(code_cell)
        if rect is None:
            return False
        x = max(0, rect["left"] - 28)
        y = rect["top"] + ((rect["bottom"] - rect["top"]) // 2)
        if self._click_screen_point(
            x,
            y,
            f"w02_item_picker_select_geometry:{item_code}",
            control=code_cell,
        ):
            self.actions.append(f"click:w02_item_picker_select:{item_code}:row_geometry")
            self._wait_after_action()
            return True
        return False

    def _select_item_picker_checkbox_cell(self, checkbox: Any, action_name: str) -> None:
        control_type = _control_type(checkbox).lower()
        if control_type == "checkbox":
            self._check_or_click(checkbox, action_name)
            return
        self._click(checkbox, action_name)

    def _check_or_click(self, control: Any, action_name: str) -> None:
        for method_name in ("check", "Check", "CheckByClickInput"):
            method = getattr(control, method_name, None)
            if not callable(method):
                continue
            try:
                method()
                self.actions.append(f"check:{action_name}")
                self._wait_after_action()
                return
            except Exception:
                continue
        self._click(control, action_name)

    def _close_item_picker_if_present(self) -> bool:
        item_picker = self._find_control_by_id("ItemsWin")
        if item_picker is None:
            return True
        close_control = self._find_item_picker_close_control()
        if close_control is not None:
            try:
                self._click(close_control, "w02_item_picker_close_after_skip")
                return self._wait_until(lambda: self._find_control_by_id("ItemsWin") is None, timeout_seconds=2)
            except ReportAutomationError:
                pass
        try:
            self._send_keys("{ESC}", control=item_picker)
            self.actions.append("w02_item_picker_close_after_skip:esc")
            return self._wait_until(lambda: self._find_control_by_id("ItemsWin") is None, timeout_seconds=2)
        except ReportAutomationError:
            return False

    def _find_item_picker_close_control(self) -> Any | None:
        fallback: Any | None = None
        for control in self._item_picker_controls():
            if not _control_visible(control) or not _control_enabled(control):
                continue
            automation_id = _control_automation_id(control)
            name = _normalize(_control_name(control))
            control_type = _control_type(control).lower()
            if automation_id == "L_Close":
                return control
            if automation_id in {"pb_ClsV", "pb_Cls", "B_Close"}:
                fallback = fallback or control
                continue
            if name in {"x", "關閉"} and control_type in {"button", "text", "pane", "custom"}:
                fallback = fallback or control
        return fallback

    def _cancel_current_order_if_possible(self) -> None:
        cancel = self._find_control_by_name_contains(("取消新增", "取消訂貨", "取消"), control_types=("Button",))
        if cancel is None:
            return
        try:
            self._click(cancel, "w02_cancel_empty_order")
            self._dismiss_prompt_if_present(allow_generic_confirmation=True)
        except ReportAutomationError:
            return

    def _close_order_window_if_present(self, reason: str) -> bool:
        order_window = self._find_control_by_id("BrOrder")
        if order_window is None or not _control_visible(order_window):
            return True
        for method_name in ("close", "Close"):
            method = getattr(order_window, method_name, None)
            if not callable(method):
                continue
            try:
                method()
                self.actions.append(f"w02_order_window_close:{reason}:method")
                if self._wait_until(lambda: self._find_control_by_id("BrOrder") is None, timeout_seconds=3):
                    return True
            except Exception:
                pass
        close_control = self._find_order_window_close_control()
        if close_control is not None:
            try:
                self._click(close_control, f"w02_order_window_close:{reason}")
                return self._wait_until(lambda: self._find_control_by_id("BrOrder") is None, timeout_seconds=3)
            except ReportAutomationError:
                pass
        self._record_failure_context("order_window_close_failed", message=f"reason: {reason}")
        raise ReportAutomationError(
            "W02_POS_ORDER_WINDOW_CLOSE_FAILED",
            "W02 已完成目前訂貨單，但無法關閉「分店訂貨單」視窗；為避免切換分館失敗，本次停止。",
        )

    def _find_order_window_close_control(self) -> Any | None:
        for control in self._order_item_grid_controls():
            if not _control_visible(control) or not _control_enabled(control):
                continue
            if _normalize(_control_name(control)) != "關閉":
                continue
            if _control_type(control).lower() == "button":
                return control
        return None

    def _order_row_contains_item(self, row_index: int, item_code: str) -> bool:
        normalized_row = f"資料列{row_index}"
        code_cell = self._find_grid_cell("商品碼", row_index)
        if code_cell is not None and self._control_has_exact_text_token(code_cell, item_code):
            self.actions.append(f"w02_order_row_verified:{item_code}:accessibility")
            return True
        if code_cell is not None and self._copy_grid_cell_text_matches(code_cell, item_code):
            self.actions.append(f"w02_order_row_verified:{item_code}:clipboard")
            return True
        for control in self._order_window_controls():
            name = _normalize(_control_name(control))
            if (normalized_row in name or "商品碼" in name) and self._control_has_exact_text_token(control, item_code):
                return True
        return False

    def _control_has_exact_text_token(self, control: Any, expected: str) -> bool:
        return any(_text_contains_exact_token(text, expected) for text in _control_text_candidates(control))

    def _copy_grid_cell_text_matches(self, control: Any, expected: str) -> bool:
        original_clipboard = _clipboard_get_text()
        try:
            _clipboard_set_text("")
            self._click(control, f"w02_copy_cell:{expected}")
            self._send_keys("^c", control=control)
            copied_text = _clipboard_get_text()
            if copied_text and _text_contains_exact_token(copied_text, expected):
                return True
            if copied_text:
                self.actions.append(f"w02_copy_cell_mismatch:{_action_text(copied_text)}")
            return False
        finally:
            if original_clipboard is not None:
                _clipboard_set_text(original_clipboard)

    def _grid_cell_value_matches(self, control: Any, expected: str) -> bool:
        if self._control_has_exact_text_token(control, expected):
            self.actions.append(f"w02_grid_cell_verified:{expected}:accessibility")
            return True
        if self._copy_grid_cell_text_matches(control, expected):
            self.actions.append(f"w02_grid_cell_verified:{expected}:clipboard")
            return True
        return False

    def _order_row_quantity_matches(
        self,
        row_index: int,
        item_code: str,
        expected: str,
        quantity_cell: Any | None,
    ) -> bool:
        for control in self._order_item_grid_controls():
            if not _control_visible(control):
                continue
            name = _normalize(_control_name(control))
            if name != f"資料列{row_index}":
                continue
            for text in _control_text_candidates(control):
                if _order_row_text_has_item_quantity(text, item_code, expected):
                    self.actions.append(f"w02_order_quantity_verified:{item_code}:{expected}:row_text")
                    return True
        if quantity_cell is not None and self._grid_cell_value_matches(quantity_cell, expected):
            self.actions.append(f"w02_order_quantity_verified:{item_code}:{expected}:cell")
            return True
        return False

    def _form_approval_verified(self) -> bool:
        if _prompt_text_has_success(self._visible_prompt_text(), ("核准確認", "確認要核准")):
            return False
        state_control = self._find_control_by_id("cL_BrOrderStateName")
        if state_control is not None:
            state_text = _normalize(" ".join(_control_text_candidates(state_control)))
            if _order_state_has_any(state_text, ("訂貨確認", "已確認", "新單")):
                return False
            if _order_state_has_any(state_text, ("訂貨核准", "已核准")):
                return True
        for control in self._order_window_controls():
            if not _control_visible(control):
                continue
            name = _normalize(_control_name(control))
            control_type = _control_type(control).lower()
            automation_id = _control_automation_id(control)
            if control_type in {"button", "menuitem", "checkbox"} or automation_id.startswith("K_State"):
                continue
            if ("狀態" in name or "訂貨核准" in name) and ("訂貨核准" in name or "已核准" in name):
                return True
        return False

    def _form_confirmation_verified(self) -> bool:
        state_control = self._find_control_by_id("cL_BrOrderStateName")
        if state_control is not None:
            state_text = _normalize(" ".join(_control_text_candidates(state_control)))
            if _order_state_has_any(state_text, ("訂貨確認", "已確認")):
                return True
        for control in self._order_window_controls():
            if not _control_visible(control):
                continue
            name = _normalize(_control_name(control))
            control_type = _control_type(control).lower()
            automation_id = _control_automation_id(control)
            if control_type in {"button", "menuitem", "checkbox"} or automation_id.startswith("K_State"):
                continue
            if ("狀態" in name or "訂貨確認" in name) and (
                "訂貨確認" in name or "已確認" in name
            ):
                return True
        return False

    def _click_by_id(self, automation_id: str, *, action_name: str, error_code: str) -> None:
        control = self._find_control_by_id(automation_id)
        if control is None:
            raise ReportAutomationError(error_code, f"W02 找不到控制項：{automation_id}。")
        self._click(control, action_name)

    def _dismiss_prompt_if_present(
        self,
        *,
        error_code: str = "W02_POS_PROMPT_ERROR",
        success_tokens: tuple[str, ...] = (),
        accepted_tokens: tuple[str, ...] = (),
        required_prompt_tokens: tuple[str, ...] = (),
        allow_generic_confirmation: bool = False,
        timeout_seconds: float = 1.5,
        ) -> bool:
        deadline = monotonic() + timeout_seconds
        last_success_text = ""
        while True:
            self._invalidate_control_cache()
            prompt_scopes = [
                (root, controls, _visible_prompt_text_from_controls(controls))
                for root, controls in self._prompt_control_scopes(
                    # Native discovery is exact-HWND and same-PID; it is not a
                    # global Desktop UIA descendants scan.  Check it on the
                    # first poll so a large local BrOrder tree cannot consume
                    # the whole prompt timeout before a visible modal is seen.
                    include_desktop=True,
                    success_tokens=success_tokens,
                    accepted_tokens=accepted_tokens,
                    required_prompt_tokens=required_prompt_tokens,
                    allow_generic_confirmation=allow_generic_confirmation,
                )
            ]
            prompt_text = " | ".join(text for _root, _controls, text in prompt_scopes if text)
            success_scopes = [
                (root, controls, text)
                for root, controls, text in prompt_scopes
                if success_tokens
                and _prompt_scope_has_body_text(root, controls)
                and _prompt_text_has_success(text, success_tokens)
                and self._find_prompt_confirmation_control(controls) is not None
            ]
            accepted_scopes = [
                (root, controls, text)
                for root, controls, text in prompt_scopes
                if accepted_tokens
                and (
                    not required_prompt_tokens
                    or _prompt_text_has_success(text, required_prompt_tokens)
                )
                and self._find_prompt_confirmation_control(
                    controls,
                    confirm_names=accepted_tokens,
                )
                is not None
            ]

            def distinct_scopes(
                scopes: list[tuple[Any, list[Any], str]],
            ) -> list[tuple[Any, list[Any], str]]:
                distinct: list[tuple[Any, list[Any], str]] = []
                for candidate in scopes:
                    candidate_root = candidate[0]
                    if any(
                        candidate_root is existing[0] or _same_control(candidate_root, existing[0])
                        for existing in distinct
                    ):
                        continue
                    distinct.append(candidate)
                return distinct

            success_scopes = distinct_scopes(success_scopes)
            accepted_scopes = distinct_scopes(accepted_scopes)
            matching_scopes = success_scopes or accepted_scopes
            if len(matching_scopes) > 1:
                self.actions.append(f"w02_prompt_ambiguous:{len(matching_scopes)}")
                raise ReportAutomationError(
                    "W02_POS_PROMPT_AMBIGUOUS",
                    "W02 同時找到多個符合目前階段的 POS 提示視窗；為避免誤按其他視窗，已停止且未點擊。",
                )
            success_scope = success_scopes[0] if success_scopes else None
            accepted_scope = accepted_scopes[0] if accepted_scopes else None
            if success_scope is not None:
                last_success_text = success_scope[2]
            target_scope = success_scope or accepted_scope
            control = None
            if target_scope is not None:
                control = self._find_prompt_confirmation_control(
                    target_scope[1],
                    confirm_names=accepted_tokens if accepted_scope is not None and success_scope is None else None,
                )
            if control is None and allow_generic_confirmation and not success_tokens and not accepted_tokens:
                generic_scopes = distinct_scopes(
                    [
                        (root, controls, text)
                        for root, controls, text in prompt_scopes
                        if self._find_prompt_confirmation_control(controls) is not None
                    ]
                )
                if len(generic_scopes) > 1:
                    self.actions.append(f"w02_prompt_ambiguous:{len(generic_scopes)}")
                    raise ReportAutomationError(
                        "W02_POS_PROMPT_AMBIGUOUS",
                        "W02 同時找到多個可確認的 POS 提示視窗；為避免誤按，已停止且未點擊。",
                    )
                if generic_scopes:
                    control = self._find_prompt_confirmation_control(generic_scopes[0][1])
            if control is not None:
                control_name = _control_name(control).strip() or "confirm"
                if success_scope is not None:
                    self._click(control, f"w02_prompt:{control_name}")
                    if self._wait_until(
                        lambda: not self._prompt_scope_has_success(success_scope[0], success_tokens),
                        timeout_seconds=1.5,
                    ):
                        return True
                    raise ReportAutomationError(error_code, f"W02 POS 成功提示無法關閉：{last_success_text}")
                accepted_control = bool(accepted_tokens and _prompt_confirmation_name_matches(control, accepted_tokens))
                if accepted_control and accepted_scope is not None:
                    accepted_root, _accepted_controls, accepted_text = accepted_scope
                    self._click(control, f"w02_prompt:{control_name}")
                    if self._wait_until(
                        lambda: not self._prompt_scope_has_acceptance(
                            accepted_root,
                            accepted_tokens,
                            required_prompt_tokens=required_prompt_tokens,
                        ),
                        timeout_seconds=1.5,
                    ):
                        return True
                    raise ReportAutomationError(error_code, f"W02 POS 確認提示無法關閉：{accepted_text}")
                if _prompt_text_has_error(prompt_text):
                    raise ReportAutomationError(error_code, f"W02 POS 彈窗回報異常：{prompt_text}")
                if allow_generic_confirmation and not success_tokens and not accepted_tokens:
                    self._click(control, f"w02_prompt:{control_name}")
                    return True
            if _prompt_text_has_error(prompt_text) and _prompt_modal_title_visible(prompt_text):
                raise ReportAutomationError(error_code, f"W02 POS 彈窗回報異常：{prompt_text}")
            if monotonic() >= deadline:
                return False
            sleep(W02_POLL_SECONDS)
        return False

    def _find_prompt_confirmation_control(
        self,
        controls: list[Any] | None = None,
        *,
        confirm_names: tuple[str, ...] | None = None,
    ) -> Any | None:
        expected_names = confirm_names or ("確定", "是", "ok")
        confirm_types = {"button", "pane", "text", "custom", "dataitem"}
        for control in controls if controls is not None else self._prompt_controls():
            if not _control_visible(control) or not _control_enabled(control):
                continue
            name = _normalize(_control_name(control))
            if not _prompt_confirmation_name_matches_normalized(name, expected_names):
                continue
            control_type = _control_type(control).lower()
            if control_type in confirm_types:
                return control
        return None

    def _prompt_control_scopes(
        self,
        *,
        include_desktop: bool = True,
        success_tokens: tuple[str, ...] = (),
        accepted_tokens: tuple[str, ...] = (),
        required_prompt_tokens: tuple[str, ...] = (),
        allow_generic_confirmation: bool = False,
    ) -> list[tuple[Any, list[Any]]]:
        def scopes_for_roots(roots: list[Any]) -> list[tuple[Any, list[Any]]]:
            scopes: list[tuple[Any, list[Any]]] = []
            for root in roots:
                scopes.extend(
                    _collect_prompt_scopes(
                        root,
                        max_depth=9,
                        max_controls=600,
                        max_scope_controls=300,
                    )
                )
            return scopes

        def scope_matches_requested_prompt(root: Any, controls: list[Any]) -> bool:
            confirmation = self._find_prompt_confirmation_control(
                controls,
                confirm_names=accepted_tokens or None,
            )
            if confirmation is None:
                return False
            text = _visible_prompt_text_from_controls(controls)
            if required_prompt_tokens and not _prompt_text_has_success(text, required_prompt_tokens):
                return False
            if success_tokens:
                return _prompt_scope_has_body_text(root, controls) and _prompt_text_has_success(
                    text,
                    success_tokens,
                )
            if accepted_tokens:
                return _prompt_scope_has_body_text(root, controls)
            return allow_generic_confirmation

        desktop_scopes: list[tuple[Any, list[Any]]] = []
        if include_desktop and sys.platform.startswith("win"):
            desktop_roots: list[Any] = []
            for root in _desktop_windows(expected_process_id=_control_process_id(self.window)):
                if root is self.window or _same_control(root, self.window):
                    continue
                if not _prompt_root_allowed_for_window(root, self.window):
                    continue
                desktop_roots.append(root)
            # Collect every explicit top-level prompt before considering other
            # same-process roots.  Do not stop at the first complete prompt: it
            # may be an unrelated/stale notification whose token does not match
            # the current Save/Confirm/Approve stage.
            explicit_prompt_roots = [root for root in desktop_roots if _is_prompt_container(root)]
            other_roots = [root for root in desktop_roots if not _is_prompt_container(root)]
            desktop_scopes = scopes_for_roots(explicit_prompt_roots)
            if any(
                scope_matches_requested_prompt(root, controls)
                for root, controls in desktop_scopes
            ):
                return desktop_scopes
            desktop_scopes.extend(scopes_for_roots(other_roots))
            if any(
                scope_matches_requested_prompt(root, controls)
                for root, controls in desktop_scopes
            ):
                return desktop_scopes

        local_scopes = scopes_for_roots([self.window])
        if any(
            scope_matches_requested_prompt(root, controls)
            for root, controls in local_scopes
        ):
            return [*desktop_scopes, *local_scopes]
        if not include_desktop or not sys.platform.startswith("win"):
            return local_scopes

        prompt_scopes: list[tuple[Any, list[Any]]] = []
        prompt_scopes.extend(local_scopes)
        prompt_scopes.extend(desktop_scopes)
        return prompt_scopes

    def _prompt_controls(self) -> list[Any]:
        return _unique_controls_by_identity(
            [control for _root, controls in self._prompt_control_scopes() for control in controls]
        )

    def _prompt_scope_has_success(self, prompt_root: Any, success_tokens: tuple[str, ...]) -> bool:
        matching_stage_prompt_visible = False
        for root, controls in self._prompt_control_scopes(success_tokens=success_tokens):
            text = _visible_prompt_text_from_controls(controls)
            if root is prompt_root or _same_control(root, prompt_root):
                return _prompt_text_has_success(text, success_tokens)
            if (
                _prompt_scope_has_body_text(root, controls)
                and _prompt_text_has_success(text, success_tokens)
            ):
                # The same visible modal can be rebound from a handleless local
                # wrapper to an exact native HWND between click and readback.
                # Keep identity strict, but do not mistake an unprovable wrapper
                # transition for disappearance while the complete stage signal
                # is still visible in the same POS process.
                matching_stage_prompt_visible = True
        return matching_stage_prompt_visible

    def _prompt_scope_has_acceptance(
        self,
        prompt_root: Any,
        accepted_tokens: tuple[str, ...],
        *,
        required_prompt_tokens: tuple[str, ...] = (),
    ) -> bool:
        matching_stage_prompt_visible = False
        for root, controls in self._prompt_control_scopes(
            accepted_tokens=accepted_tokens,
            required_prompt_tokens=required_prompt_tokens,
        ):
            text = _visible_prompt_text_from_controls(controls)
            matches_required_body = not required_prompt_tokens or _prompt_text_has_success(
                text,
                required_prompt_tokens,
            )
            if root is prompt_root or _same_control(root, prompt_root):
                # Disappearance is about the approval-question body/modal, not
                # whether the Yes button remains exposed.  POS may temporarily
                # hide or disable the button while the modal is still active.
                return matches_required_body
            if matches_required_body:
                matching_stage_prompt_visible = True
        return matching_stage_prompt_visible

    def _visible_prompt_text(self) -> str:
        return _visible_prompt_text_from_controls(self._prompt_controls())

    def _select_combo_text(self, control: Any, value: str) -> bool:
        select = getattr(control, "select", None)
        if callable(select):
            try:
                select(value)
                if self._selected_text_matches(control, value):
                    return True
            except Exception:
                pass
        self._click_dropdown(control, f"w02_combo_open:{value}")
        return self._select_visible_option(value, (value,))

    def _select_combo_known_item(self, control: Any, value: str) -> bool:
        item_texts = _control_item_texts(control)
        if item_texts:
            self.actions.append(f"w02_combo_items:{_action_text('|'.join(item_texts[:12]))}")
        matched_index: int | None = None
        matched_text: str | None = None
        for index, item_text in enumerate(item_texts):
            if _text_matches(item_text, value):
                matched_index = index
                matched_text = item_text
                break
        if matched_text is None:
            return False
        for candidate in (matched_text, value):
            for method_name in ("select", "Select"):
                method = getattr(control, method_name, None)
                if not callable(method):
                    continue
                try:
                    method(candidate)
                    self._wait_after_action(W02_KEY_SETTLE_SECONDS)
                    if self._selected_text_matches(control, value) or self._control_has_exact_text_token(control, value):
                        self.actions.append(f"w02_combo_select_text:{_action_text(candidate)}")
                        return True
                    self.actions.append(f"w02_combo_select_text_unverified:{_action_text(candidate)}")
                    return True
                except Exception:
                    continue
        if matched_index is None:
            return False
        for method_name in ("SelectedIndex", "SelectedIndex_", "select", "Select"):
            method = getattr(control, method_name, None)
            if not callable(method):
                continue
            try:
                method(matched_index)
                self._wait_after_action(W02_KEY_SETTLE_SECONDS)
                if self._selected_text_matches(control, value) or self._control_has_exact_text_token(control, value):
                    self.actions.append(f"w02_combo_select_index:{matched_index}:{_action_text(matched_text)}")
                    return True
                self.actions.append(f"w02_combo_select_index_unverified:{matched_index}:{_action_text(matched_text)}")
                return True
            except Exception:
                continue
        self._click_dropdown(control, f"w02_combo_known_open:{value}")
        keys = "{HOME}" + (f"{{DOWN {matched_index}}}" if matched_index else "") + "{ENTER}"
        self._send_keys(keys, control=control)
        if self._selected_text_matches(control, value) or self._control_has_exact_text_token(control, value):
            self.actions.append(f"w02_combo_keyboard_select:{matched_index}:{_action_text(matched_text)}")
            return True
        self.actions.append(f"w02_combo_keyboard_select_unverified:{matched_index}:{_action_text(matched_text)}")
        return True
        return False

    def _order_controls_contain_exact_text(self, expected: str) -> bool:
        return any(self._control_has_exact_text_token(control, expected) for control in self._order_window_controls())

    def _select_visible_option(
        self,
        value: str,
        aliases: tuple[str, ...],
        *,
        allow_combobox: bool = True,
        verify: Callable[[], bool] | None = None,
        allow_same_process_ownerless_popup: bool = False,
    ) -> bool:
        candidates = tuple(dict.fromkeys((value, *aliases)))
        def select_from(controls: list[Any], *, require_pos_ownership: bool = False) -> bool:
            for control in controls:
                name = _control_name(control)
                if not name or not _control_visible_strict(control) or not _control_enabled_strict(control):
                    continue
                if not any(_text_matches(name, candidate) for candidate in candidates):
                    continue
                allowed_types = {"listitem", "menuitem", "dataitem", "text", "button"}
                if allow_combobox:
                    allowed_types.add("combobox")
                if _control_type(control).lower() not in allowed_types:
                    continue
                ownerless_process_popup = False
                if require_pos_ownership and not _control_owned_by_pos_window(control, self.window):
                    ownerless_process_popup = bool(
                        allow_same_process_ownerless_popup
                        and _same_process_popup_candidate(control, self.window)
                        and _same_process_popup_or_pos_is_foreground(control, self.window)
                    )
                    if not ownerless_process_popup:
                        continue
                if not allow_combobox:
                    wrapper_failed = False
                    for method_name in ("invoke", "select", "click"):
                        method = getattr(control, method_name, None)
                        if not callable(method):
                            continue
                        try:
                            method()
                            self.actions.append(f"activate:w02_option:{_action_text(value)}:{method_name}")
                            self._wait_after_action()
                            if verify is None or self._wait_until(verify, timeout_seconds=2):
                                return True
                            self.actions.append(
                                "w02_branch_visible_option_noop:"
                                f"method={method_name}:option={_action_text(name)}"
                            )
                        except Exception as exc:
                            if verify is not None and self._wait_until(
                                verify,
                                timeout_seconds=2,
                            ):
                                self.actions.append(
                                    "w02_branch_visible_option_selected_after_exception:"
                                    f"method={method_name}:option={_action_text(name)}:"
                                    f"error={_exception_evidence(exc)}"
                                )
                                return True
                            self.actions.append(
                                "w02_branch_visible_option_exception:"
                                f"method={method_name}:option={_action_text(name)}:"
                                f"error={_exception_evidence(exc)}"
                            )
                            wrapper_failed = True
                            break
                    if wrapper_failed:
                        # An automation dispatch can reach WinForms and still
                        # raise because the wrapper becomes stale during the
                        # call.  After a failed readback, never dispatch a
                        # second method or a physical click to that wrapper;
                        # let the caller reacquire the popup/control.
                        continue
                    if ownerless_process_popup:
                        # The popup is a same-process top-level WinForms HWND
                        # without an owner link.  Non-physical methods above
                        # are safe; do not bind a screen click to an HWND whose
                        # native ownership cannot be proven.
                        continue
                self._click(control, f"w02_option:{value}")
                if verify is None or self._wait_until(verify, timeout_seconds=2):
                    return True
                self.actions.append(f"w02_visible_option_click_noop:{_action_text(name)}")
            return False

        if select_from(self._all_controls()):
            return True
        if select_from(self._pos_desktop_controls(), require_pos_ownership=True):
            return True
        return False

    def _selected_text_matches(self, control: Any, expected: str) -> bool:
        for attr in ("selected_text", "SelectedText"):
            method = getattr(control, attr, None)
            if callable(method):
                try:
                    value = str(method())
                    if _text_matches(value, expected):
                        return True
                except Exception:
                    pass
        texts = _safe_call(control, "texts", default=[])
        if any(_text_matches(str(text), expected) for text in texts):
            return True
        return _text_matches(_control_name(control), expected)

    def _find_grid_cell(self, header: str, row_index: int) -> Any | None:
        normalized_header = _normalize(header)
        normalized_row = f"資料列{row_index}"
        for control in self._order_item_grid_controls():
            name = _normalize(_control_name(control))
            if normalized_header in name and normalized_row in name and _control_visible(control):
                return control
        return None

    def _click_grid_cell_by_geometry(self, header: str, row_index: int, action_name: str) -> bool:
        """Click a visible DataGridView cell when its UIA cell is virtualized.

        Prefer an exact visible row rectangle.  WinForms DataGridView can
        paint its trailing '*' insertion row without exposing that row to
        UIA.  For that one case, derive the next row only from the exact order
        grid and three consecutive, equal-height committed rows.
        """
        normalized_header = _normalize(header)
        normalized_row = f"資料列{row_index}"
        controls = self._order_item_grid_controls()
        grid_owner = self._find_control_by_id("gv_BrOrderItem")
        if grid_owner is None:
            grid_owner = self._find_control_by_id("BrOrder")
        if grid_owner is None:
            return False
        headers: list[dict[str, int]] = []
        rows: list[dict[str, int]] = []
        for control in controls:
            if not _control_visible(control) or not _control_enabled(control):
                continue
            name = _normalize(_control_name(control))
            rect = _rect_dict(control)
            if rect is None:
                continue
            control_type = _control_type(control).lower()
            if name == normalized_header and control_type in {"header", "headeritem", "dataitem", "text"}:
                headers.append(rect)
            elif name == normalized_row and control_type in {"custom", "dataitem", "row"}:
                rows.append(rect)

        for header_rect in headers:
            x = (header_rect["left"] + header_rect["right"]) // 2
            for row_rect in rows:
                if not (row_rect["left"] <= x < row_rect["right"]):
                    continue
                if row_rect["top"] < header_rect["bottom"]:
                    continue
                if row_rect["top"] - header_rect["bottom"] > 1000:
                    continue
                y = (row_rect["top"] + row_rect["bottom"]) // 2
                geometry_action = f"{action_name}:derived_grid_geometry"
                if not self._click_screen_point(x, y, geometry_action, control=grid_owner):
                    continue
                self.actions.append(f"click:{geometry_action}")
                self._wait_after_action()
                return True

        virtual_point = self._virtual_new_order_row_click_point(
            controls=controls,
            headers=headers,
            row_index=row_index,
        )
        if virtual_point is not None:
            x, y = virtual_point
            geometry_action = f"{action_name}:derived_virtual_new_row_geometry"
            if self._click_screen_point(x, y, geometry_action, control=grid_owner):
                self.actions.append(f"click:{geometry_action}")
                self._wait_after_action()
                return True
        return False

    def _virtual_new_order_row_click_point(
        self,
        *,
        controls: list[Any],
        headers: list[dict[str, int]],
        row_index: int,
    ) -> tuple[int, int] | None:
        """Return a safe click point for an unexposed DataGridView new row."""
        if row_index < 3:
            return None

        grid_rects: list[dict[str, int]] = []
        for control in controls:
            if not _control_visible(control) or not _control_enabled(control):
                continue
            if _control_automation_id(control) != "gv_BrOrderItem":
                continue
            if _control_type(control).lower() not in {"table", "datagrid", "custom"}:
                continue
            rect = _rect_dict(control)
            if rect is not None:
                grid_rects.append(rect)

        for header_rect in headers:
            x = (header_rect["left"] + header_rect["right"]) // 2
            for grid_rect in grid_rects:
                if not _rect_contains_point(grid_rect, x, header_rect["top"]):
                    continue
                row_rects_by_index: dict[int, dict[str, int]] = {}
                for control in controls:
                    if not _control_visible(control) or not _control_enabled(control):
                        continue
                    if _control_type(control).lower() not in {"custom", "dataitem", "row"}:
                        continue
                    match = re.fullmatch(r"資料列\s*(\d+)", _control_name(control).strip())
                    if match is None:
                        continue
                    rect = _rect_dict(control)
                    if rect is None or not (rect["left"] <= x < rect["right"]):
                        continue
                    if rect["top"] < header_rect["bottom"] or rect["bottom"] > grid_rect["bottom"]:
                        continue
                    if rect["left"] < grid_rect["left"] - 2 or rect["right"] > grid_rect["right"] + 2:
                        continue
                    index = int(match.group(1))
                    previous = row_rects_by_index.get(index)
                    if previous is None or rect["right"] - rect["left"] > previous["right"] - previous["left"]:
                        row_rects_by_index[index] = rect

                recent_indexes = (row_index - 3, row_index - 2, row_index - 1)
                if any(index not in row_rects_by_index for index in recent_indexes):
                    continue
                recent_rows = [row_rects_by_index[index] for index in recent_indexes]
                heights = [row["bottom"] - row["top"] for row in recent_rows]
                if min(heights) <= 0 or max(heights) - min(heights) > 2:
                    continue
                if any(abs(current["top"] - previous["bottom"]) > 2 for previous, current in zip(recent_rows, recent_rows[1:])):
                    continue

                row_height = sorted(heights)[1]
                inferred_top = recent_rows[-1]["bottom"]
                inferred_bottom = inferred_top + row_height
                if inferred_bottom > grid_rect["bottom"]:
                    continue
                if not all(row["left"] <= x < row["right"] for row in recent_rows):
                    continue
                return x, inferred_top + (row_height // 2)
        return None

    def _find_data_item_with_text(self, header: str, value: str) -> Any | None:
        normalized_header = _normalize(header)
        for control in self._all_controls():
            name = _control_name(control)
            normalized_name = _normalize(name)
            if normalized_header in normalized_name and _text_contains_exact_token(name, value) and _control_visible(control):
                return control
        return None

    def _find_control_by_id(self, automation_id: str) -> Any | None:
        for control in self._all_controls():
            if _control_automation_id(control) == automation_id and _control_visible(control):
                return control
        return None

    def _find_control_by_name_contains(self, names: tuple[str, ...], *, control_types: tuple[str, ...]) -> Any | None:
        normalized_names = tuple(_normalize(name) for name in names)
        normalized_types = tuple(item.lower() for item in control_types)

        def find_in(controls: list[Any]) -> Any | None:
            for control in controls:
                control_type = _control_type(control).lower()
                if normalized_types and control_type not in normalized_types:
                    continue
                name = _normalize(_control_name(control))
                if name and any(candidate in name for candidate in normalized_names) and _control_visible(control):
                    return control
            return None

        local_control = find_in(self._all_controls())
        if local_control is not None:
            return local_control
        # Popup controls may be separate top-level windows, so retain the
        # Desktop fallback but avoid paying its COM/RPC risk for ordinary POS
        # descendants such as the account button, menus and use-type field.
        desktop_control = find_in(self._all_controls(include_desktop=True))
        if desktop_control is not None:
            return desktop_control
        return None

    def _all_controls(self, *, include_desktop: bool = False) -> list[Any]:
        cached = self._control_cache.get(include_desktop)
        if cached is not None:
            return cached
        controls = _collect_controls(self.window, max_depth=9, max_controls=1200)
        if include_desktop and sys.platform.startswith("win"):
            controls.extend(self._pos_desktop_controls())
        self._control_cache[include_desktop] = controls
        return controls

    def _pos_desktop_controls(self) -> list[Any]:
        return _desktop_controls(expected_process_id=_control_process_id(self.window))

    def _window_title_matches_branch(self, branch: str) -> bool:
        title = _control_name(self.window)
        return any(_text_matches(title, alias) for alias in self._branch_aliases(branch))

    def _click(
        self,
        control: Any,
        action_name: str,
        *,
        before_attempt: Callable[[], None] | None = None,
    ) -> None:
        last_error: Exception | None = None
        attempt_started = False

        def prepare_attempt() -> None:
            nonlocal attempt_started
            if attempt_started:
                return
            if before_attempt is not None:
                before_attempt()
            attempt_started = True

        for method_name in ("click_input", "click", "invoke"):
            method = getattr(control, method_name, None)
            if not callable(method):
                continue
            if method_name == "click_input":
                self._ensure_pos_window_foreground(f"physical_click:{action_name}", control=control)
            prepare_attempt()
            try:
                method()
                self.actions.append(f"click:{action_name}")
                self._wait_after_action()
                return
            except Exception as exc:
                last_error = exc
                if before_attempt is not None:
                    break
        if before_attempt is None and _rect_dict(control) is not None:
            self._ensure_pos_window_foreground(f"physical_geometry:{action_name}", control=control)
            prepare_attempt()
            if self._click_geometry(control, action_name):
                self.actions.append(f"click:{action_name}:geometry")
                self._wait_after_action()
                return
        detail = f"：{last_error}" if last_error is not None else ""
        raise ReportAutomationError("W02_POS_CONTROL_NOT_CLICKABLE", f"W02 控制項無法點擊：{action_name}{detail}")

    def _double_click(self, control: Any, action_name: str) -> None:
        for method_name in ("double_click_input", "DoubleClickInput", "double_click"):
            method = getattr(control, method_name, None)
            if not callable(method):
                continue
            if method_name in {"double_click_input", "DoubleClickInput"}:
                self._ensure_pos_window_foreground(
                    f"physical_double_click:{action_name}",
                    control=control,
                )
            try:
                method()
                self.actions.append(f"double_click:{action_name}")
                self._wait_after_action()
                return
            except Exception:
                continue
        self._click(control, f"{action_name}:first")
        self._click(control, f"{action_name}:second")

    def _click_dropdown(self, control: Any, action_name: str) -> None:
        expand = getattr(control, "expand", None)
        if callable(expand):
            try:
                expand()
                self.actions.append(f"expand:{action_name}")
                self._wait_after_action()
                return
            except Exception:
                pass
        if self._click_dropdown_geometry(control, action_name):
            self.actions.append(f"click:{action_name}:dropdown_geometry")
            self._wait_after_action()
            return
        self._click(control, action_name)

    def _click_geometry(self, control: Any, action_name: str) -> bool:
        rect = _rect_dict(control)
        if rect is None:
            return False
        x = rect["left"] + ((rect["right"] - rect["left"]) // 2)
        y = rect["top"] + ((rect["bottom"] - rect["top"]) // 2)
        return self._click_screen_point(x, y, action_name, control=control)

    def _click_dropdown_geometry(self, control: Any, action_name: str) -> bool:
        rect = _rect_dict(control)
        if rect is None:
            return False
        x = rect["right"] - min(8, max(2, (rect["right"] - rect["left"]) // 5))
        y = rect["top"] + ((rect["bottom"] - rect["top"]) // 2)
        return self._click_screen_point(x, y, action_name, control=control)

    def _click_screen_point(
        self,
        x: int,
        y: int,
        action_name: str,
        *,
        control: Any | None = None,
    ) -> bool:
        self._ensure_pos_window_foreground(
            f"physical_geometry:{action_name}",
            control=control,
        )
        return _click_screen_point(x, y, action_name)

    def _set_text(self, control: Any, value: str) -> None:
        for method_name in ("set_edit_text", "set_text"):
            method = getattr(control, method_name, None)
            if callable(method):
                try:
                    method(value)
                    self.actions.append(f"set_text:{value}")
                    self._wait_after_action(W02_KEY_SETTLE_SECONDS)
                    return
                except Exception:
                    pass
        self._click(control, f"focus_text:{value}")
        self._send_keys("^a{BACKSPACE}", control=control)
        self._send_keys(value, control=control, with_spaces=True)

    def _send_keys(self, keys: str, *, control: Any, **kwargs: Any) -> None:
        self._ensure_pos_window_foreground(
            f"keyboard:{_action_text(keys)}",
            control=control,
        )
        try:
            self.keyboard_sender(keys, **kwargs)
            self.actions.append(f"keys:{keys}")
            self._wait_after_action(W02_KEY_SETTLE_SECONDS)
        except Exception as exc:
            raise ReportAutomationError("W02_POS_KEYS_FAILED", f"W02 鍵盤輸入失敗：{keys}") from exc

    def _wait_until(self, predicate: Callable[[], bool], *, timeout_seconds: float) -> bool:
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            self._invalidate_control_cache()
            try:
                if predicate():
                    return True
            except Exception:
                pass
            sleep(W02_POLL_SECONDS)
        return False

    def _wait_after_action(self, seconds: float = W02_ACTION_SETTLE_SECONDS) -> None:
        self._invalidate_control_cache()
        sleep(seconds)

    def _invalidate_control_cache(self) -> None:
        self._control_cache.clear()
        self._order_window_controls_cache = None
        self._order_item_grid_controls_cache = None
        self._item_picker_controls_cache = None

    def _load_ledger(self) -> dict[str, Any]:
        if not self._ledger_path.exists():
            return {
                "schema_version": 1,
                "run_date": self.run_date.isoformat(),
                "forms": {},
            }
        try:
            payload = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ReportAutomationError(
                "W02_POS_LEDGER_CORRUPT",
                f"W02 建單 ledger 無法讀取或解析，為避免重複下單已停止：{self._ledger_path}。",
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("forms"), dict):
            raise ReportAutomationError(
                "W02_POS_LEDGER_CORRUPT",
                f"W02 建單 ledger 結構不完整，為避免重複下單已停止：{self._ledger_path}。",
            )
        if payload.get("schema_version") != 1 or payload.get("run_date") != self.run_date.isoformat():
            raise ReportAutomationError(
                "W02_POS_LEDGER_CORRUPT",
                f"W02 建單 ledger 版本或日期不符，為避免重複下單已停止：{self._ledger_path}。",
            )
        for form_key, entry in payload["forms"].items():
            if (
                not isinstance(form_key, str)
                or not form_key.strip()
                or not isinstance(entry, dict)
                or entry.get("status") not in W02_ALL_FORM_STATUSES
            ):
                raise ReportAutomationError(
                    "W02_POS_LEDGER_CORRUPT",
                    f"W02 建單 ledger 含有未知或不完整的表單狀態，為避免重複下單已停止：{self._ledger_path}。",
                )
        return cast(dict[str, Any], payload)

    def _write_ledger(self, ledger: dict[str, Any]) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._ledger_path.with_name(f".{self._ledger_path.name}.{os.getpid()}.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(ledger, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            _durable_replace(temporary_path, self._ledger_path)
        except OSError as exc:
            raise ReportAutomationError(
                "W02_POS_LEDGER_WRITE_FAILED",
                f"W02 無法安全寫入建單 ledger，已停止 POS 操作：{self._ledger_path}。",
            ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)

    def _write_diagnostic(
        self,
        *,
        status: str,
        error_code: str | None,
        message: str,
        plan: W02OrderPlan,
        ledger: dict[str, Any],
    ) -> Path:
        self._diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "status": status,
            "error_code": error_code,
            "message": message,
            "run_date": self.run_date.isoformat(),
            "plan": {
                "r14_path": str(plan.r14_path),
                "form_count": len(plan.forms),
                "order_item_count": plan.order_item_count,
                "issue_count": len(plan.issues),
            },
            "ledger_path": str(self._ledger_path),
            "ledger": ledger,
            "actions": self.actions,
            "failure_context_snapshots": self._failure_context_snapshots,
        }
        self._diagnostic_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return self._diagnostic_path

    def _record_failure_context(
        self,
        context: str,
        *,
        item_code: str | None = None,
        row_index: int | None = None,
        error_code: str | None = None,
        message: str | None = None,
    ) -> None:
        screenshot_path, screenshot_error, screenshot_source = self._capture_failure_screenshot(context, item_code)
        snapshot = {
            "created_at": datetime.now(tz=UTC).isoformat(),
            "context": context,
            "item_code": item_code,
            "row_index": row_index,
            "error_code": error_code,
            "message": message,
            "item_picker_present": self._find_control_by_id("ItemsWin") is not None,
            "item_picker_result_count": self._item_picker_result_count(),
            "screenshot_path": str(screenshot_path) if screenshot_path is not None else None,
            "screenshot_error": screenshot_error,
            "screenshot_source": screenshot_source,
            "order_window_controls": self._control_records(self._order_window_controls()),
            "item_picker_controls": self._control_records(self._item_picker_controls()),
            "visible_controls": self._control_records(self._all_controls()),
            "desktop_scan_skipped": True,
        }
        self._failure_context_snapshots.append(snapshot)
        self.actions.append(f"probe:w02_failure_context:{context}:{item_code or ''}")

    def _order_window_controls(self) -> list[Any]:
        if self._order_window_controls_cache is not None:
            return self._order_window_controls_cache
        order_window = self._find_control_by_id("BrOrder")
        if order_window is None:
            return []
        self._order_window_controls_cache = _collect_controls(order_window, max_depth=7, max_controls=600)
        return self._order_window_controls_cache

    def _order_item_grid_controls(self) -> list[Any]:
        """Return a bounded snapshot of only the editable order-item grid.

        BrOrder also contains a large order-history grid.  On production data
        that left-hand grid can exhaust the broader 600-control snapshot before
        the trailing cells of row 18 in gv_BrOrderItem are reached.  Current
        order row/cell lookup must therefore own its own budget at the exact
        grid root instead of depending on traversal order in the parent form.
        """

        if self._order_item_grid_controls_cache is not None:
            return self._order_item_grid_controls_cache
        grid = self._find_control_by_id("gv_BrOrderItem")
        if grid is None:
            # Offline fakes and older POS variants may not expose the grid ID.
            # Keep the bounded legacy scope as a compatibility fallback.
            self._order_item_grid_controls_cache = self._order_window_controls()
            return self._order_item_grid_controls_cache
        self._order_item_grid_controls_cache = _collect_controls(
            grid,
            max_depth=6,
            max_controls=800,
        )
        return self._order_item_grid_controls_cache

    def _control_records(self, controls: list[Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for control in controls:
            if len(records) >= W02_FAILURE_CONTEXT_CONTROL_LIMIT:
                break
            try:
                records.append(
                    {
                        "name": _control_name(control),
                        "automation_id": _control_automation_id(control),
                        "control_type": _control_type(control),
                        "text_values": _control_text_candidates(control)[:8],
                        "visible": _control_visible(control),
                        "enabled": _control_enabled(control),
                        "rectangle": _rect_dict(control),
                    }
                )
            except Exception as exc:
                records.append({"probe_error": str(exc)})
        return records

    def _capture_failure_screenshot(
        self,
        context: str,
        item_code: str | None,
    ) -> tuple[Path | None, str | None, str | None]:
        if not sys.platform.startswith("win"):
            return None, "screenshot only available on Windows", None
        try:
            from PIL import ImageGrab

            self.logs_dir.mkdir(parents=True, exist_ok=True)
            safe_context = re.sub(r"[^0-9A-Za-z_-]+", "_", context).strip("_") or "w02"
            safe_item_code = re.sub(r"[^0-9A-Za-z_-]+", "_", item_code or "").strip("_")
            suffix = f"_{safe_item_code}" if safe_item_code else ""
            path = self.logs_dir / (
                f"automation_w02_failure_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S_%f')}_{safe_context}{suffix}.png"
            )
            image = ImageGrab.grab(all_screens=True)
            image.save(path)
            return path, None, "PIL.ImageGrab.grab(all_screens=True)"
        except Exception as exc:
            return None, str(exc), "PIL.ImageGrab.grab(all_screens=True)"


def _form_key(form: W02OrderForm) -> str:
    return f"{form.branch}|{form.department}"


def _form_signature(form: W02OrderForm) -> str:
    payload = {
        "branch": form.branch,
        "department": form.department,
        "items": _form_items_payload(form),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ledger_form_is_unsaved_draft(ledger: dict[str, Any], form_key: str | None) -> bool:
    if form_key is None:
        return False
    entry = ledger.get("forms", {}).get(form_key)
    return bool(
        isinstance(entry, dict)
        and entry.get("status") == "failed_before_save"
        and entry.get("submission_phase") == "failed_before_save"
    )


def _form_items_payload(form: W02OrderForm) -> list[dict[str, Any]]:
    return [
        {
            "item_code": item.item_code,
            "item_name": item.item_name,
            "quantity": item.quantity,
        }
        for item in form.items
    ]


def _issue_payload(issue: W02OrderIssue) -> dict[str, Any]:
    return {
        "branch": issue.branch,
        "item_code": issue.item_code,
        "item_name": issue.item_name,
        "quantity": issue.quantity,
        "reason": issue.reason,
    }


def _issues_from_payload(payload: Any) -> tuple[W02OrderIssue, ...]:
    if not isinstance(payload, list):
        return ()
    issues: list[W02OrderIssue] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        issues.append(
            W02OrderIssue(
                branch=str(item.get("branch") or ""),
                item_code=str(item.get("item_code") or ""),
                item_name=str(item.get("item_name") or ""),
                quantity=_optional_int(item.get("quantity")),
                reason=str(item.get("reason") or ""),
            )
        )
    return tuple(issues)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _failure_happened_before_pos_submission(actions: list[str]) -> bool:
    committed_prefixes = (
        "w02_item_added:",
        "click:w02_item_picker_ok:",
        "click:w02_save_order",
        "click:w02_confirm_order",
        "click:w02_approve_order",
    )
    return not any(any(action.startswith(prefix) for prefix in committed_prefixes) for action in actions)


def _grid_row_token(control: Any) -> str | None:
    name = _normalize(_control_name(control))
    marker = "資料列"
    marker_index = name.find(marker)
    if marker_index < 0:
        return None
    digit_start = marker_index + len(marker)
    digits: list[str] = []
    for character in name[digit_start:]:
        if not character.isdigit():
            break
        digits.append(character)
    if not digits:
        return None
    return f"{marker}{''.join(digits)}"


def _branch_config_alias_values(config: BranchConfig) -> list[str]:
    values = [
        config.code,
        config.pos_code,
        config.pos_text,
        config.display_name,
        config.display_name.replace("F", "樓"),
        config.display_name.replace("樓", "F"),
        config.pos_text.replace("F", "樓"),
        config.pos_text.replace("樓", "F"),
    ]
    return list(dict.fromkeys(value for value in values if value))


def _collect_controls(control: Any, *, max_depth: int, max_controls: int, depth: int = 0) -> list[Any]:
    if control is None or max_controls <= 0:
        return []
    controls = [control]
    if depth >= max_depth:
        return controls
    for child in _safe_call(control, "children", default=[]):
        if len(controls) >= max_controls:
            break
        controls.extend(_collect_controls(child, max_depth=max_depth, max_controls=max_controls - len(controls), depth=depth + 1))
    return controls


def _desktop_controls(*, expected_process_id: int | None) -> list[Any]:
    """Collect bounded controls from exact native same-process top-level HWNDs."""

    controls: list[Any] = []
    for root in _desktop_windows(expected_process_id=expected_process_id):
        remaining = 1000 - len(controls)
        if remaining <= 0:
            break
        controls.extend(
            _collect_controls(
                root,
                max_depth=6,
                max_controls=min(remaining, 400),
            )
        )
    return _unique_controls_by_identity(controls)


def _visible_prompt_text_from_controls(controls: list[Any]) -> str:
    names: list[str] = []
    for control in controls:
        if not _control_visible(control):
            continue
        control_type = _control_type(control).lower()
        name = _control_name(control).strip()
        if not name:
            continue
        if control_type in {"window", "dialog", "text", "static", "pane"} or name in {"提示訊息", "錯誤", "警告"}:
            names.append(name)
    return " | ".join(dict.fromkeys(names))


def _prompt_scope_has_body_text(prompt_root: Any, controls: list[Any]) -> bool:
    """Distinguish a complete prompt from a stale title-only UIA wrapper."""

    readable_types = {"window", "dialog", "text", "static", "pane"}
    for control in controls:
        if control is prompt_root or _same_control(control, prompt_root):
            continue
        if not _control_visible(control):
            continue
        name = _control_name(control).strip()
        if not name or _normalize(name) in W02_PROMPT_CONTAINER_NAMES:
            continue
        if _prompt_confirmation_name_matches_normalized(
            _normalize(name),
            ("確定", "是", "否", "ok", "yes", "no"),
        ):
            continue
        if _control_type(control).lower() in readable_types:
            return True
    return False


def _desktop_windows(*, expected_process_id: int | None) -> list[Any]:
    """Resolve exact visible top-level HWNDs without global Desktop UIA enumeration."""

    if expected_process_id is None or not sys.platform.startswith("win"):
        return []
    try:
        from pywinauto import Application  # type: ignore[import-untyped]
        import win32gui
        import win32process  # type: ignore[import-untyped]
    except Exception:
        return []

    foreground_handle = 0
    try:
        foreground_handle = int(win32gui.GetForegroundWindow())
    except Exception:
        pass
    handles: list[int] = []

    def collect(handle: int, _data: Any) -> bool:
        try:
            if hasattr(win32gui, "IsWindow") and not win32gui.IsWindow(handle):
                return True
            if hasattr(win32gui, "IsWindowVisible") and not win32gui.IsWindowVisible(handle):
                return True
            _thread_id, process_id = win32process.GetWindowThreadProcessId(handle)
            if int(process_id) == expected_process_id:
                handles.append(int(handle))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return []
    handles = list(dict.fromkeys(handles))[:80]
    handles.sort(key=lambda handle: (0 if handle == foreground_handle else 1, handle))

    roots: list[Any] = []
    for handle in handles:
        for backend in ("uia", "win32"):
            try:
                app = Application(backend=backend).connect(handle=handle)
                spec = app.window(handle=handle)
                root = _safe_call(spec, "wrapper_object", default=spec)
                native_handle = _control_direct_native_handle(root)
                # Exact-HWND connection is the provenance boundary.  A wrapper
                # that cannot report the requested handle is not safe to use
                # for physical input, even when its cached PID appears valid.
                if native_handle != handle:
                    continue
                if _control_process_id(root) != expected_process_id:
                    continue
                roots.append(root)
                break
            except Exception:
                continue
    return _unique_controls_by_identity(roots)


def _find_prompt_roots(
    control: Any,
    *,
    depth: int = 0,
    max_depth: int = 9,
    max_controls: int = 600,
) -> list[Any]:
    """Find shallow prompt scopes before large sibling subtrees exhaust the budget."""

    return [
        root
        for root, _controls in _collect_prompt_scopes(
            control,
            depth=depth,
            max_depth=max_depth,
            max_controls=max_controls,
        )
    ]


def _collect_prompt_scopes(
    control: Any,
    *,
    depth: int = 0,
    max_depth: int = 9,
    max_controls: int = 600,
    max_scope_controls: int = 300,
) -> list[tuple[Any, list[Any]]]:
    """Build prompt scopes from one breadth-first, globally bounded child snapshot."""

    if control is None or depth > max_depth or max_controls <= 0 or max_scope_controls <= 0:
        return []
    roots: list[Any] = []
    scope_controls: dict[int, list[Any]] = {}
    frontier: list[tuple[Any, int, tuple[Any, ...]]] = [(control, depth, ())]
    cursor = 0
    seen: set[int] = set()
    while cursor < len(frontier) and cursor < max_controls:
        candidate, candidate_depth, inherited_scopes = frontier[cursor]
        cursor += 1
        identity = id(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        active_scopes = inherited_scopes
        if _is_prompt_container(candidate):
            roots.append(candidate)
            scope_controls[identity] = []
            # A nested prompt is a separate modal boundary.  Its controls must
            # never satisfy the outer prompt's body/button pairing.
            active_scopes = (candidate,)
        for prompt_root in active_scopes:
            prompt_controls = scope_controls[id(prompt_root)]
            if len(prompt_controls) < max_scope_controls:
                prompt_controls.append(candidate)
        if candidate_depth >= max_depth:
            continue
        for child in _safe_call(candidate, "children", default=[]):
            if len(frontier) >= max_controls:
                break
            frontier.append((child, candidate_depth + 1, active_scopes))
    return [
        (root, _unique_controls_by_identity(scope_controls[id(root)]))
        for root in _unique_controls_by_identity(roots)
    ]


def _is_prompt_title_control(control: Any) -> bool:
    return _normalize(_control_name(control)) in W02_PROMPT_CONTAINER_NAMES


def _is_prompt_container(control: Any) -> bool:
    return _is_prompt_title_control(control) and _control_type(control).lower() in W02_PROMPT_CONTAINER_TYPES


def _prompt_root_allowed_for_window(root: Any, main_window: Any) -> bool:
    main_process_id = _control_process_id(main_window)
    owner = _safe_call(root, "owner", default=None)
    if owner is not None:
        if _same_control(owner, main_window):
            return True
        owner_process_id = _control_process_id(owner)
        if owner_process_id is not None and main_process_id is not None:
            return owner_process_id == main_process_id
    root_process_id = _control_process_id(root)
    if root_process_id is not None and main_process_id is not None:
        return root_process_id == main_process_id
    return False


def _control_process_id(control: Any) -> int | None:
    for attr in ("process_id", "process_id_"):
        try:
            value = getattr(control, attr, None)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            continue
    return None


def _control_owned_by_pos_window(control: Any, main_window: Any) -> bool:
    """Validate a Desktop-derived control before any programmatic mutation."""

    main_process_id = _control_process_id(main_window)
    control_process_id = _control_process_id(control)
    if (
        main_process_id is None
        or control_process_id is None
        or main_process_id != control_process_id
    ):
        return False

    main_handle = _control_native_handle(main_window)
    target_handle = _control_direct_native_handle(control)
    if main_handle is not None and target_handle is not None:
        return _native_target_owned_by_pos(
            main_handle,
            target_handle,
            expected_process_id=main_process_id,
        )

    frontier = [control]
    seen: set[int] = set()
    for _ in range(12):
        if not frontier:
            break
        current = frontier.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if _same_control(current, main_window):
            return True
        for relation_name in ("parent", "owner"):
            relation = _safe_call(current, relation_name, default=None)
            if relation is not None:
                frontier.append(relation)

    return False


def _same_process_popup_candidate(control: Any, main_window: Any) -> bool:
    """Allow an ownerless popup only when native process provenance matches."""

    main_process_id = _control_process_id(main_window)
    control_process_id = _control_process_id(control)
    basic_match = bool(
        main_process_id is not None
        and control_process_id is not None
        and main_process_id == control_process_id
        and _control_visible_strict(control)
        and _control_enabled_strict(control)
    )
    if not basic_match:
        return False
    target_handle = _control_direct_native_handle(control)
    if not sys.platform.startswith("win"):
        return True
    if target_handle is None:
        return False
    try:
        import win32gui
        import win32process

        if not win32gui.IsWindow(target_handle):
            return False
        _thread_id, native_process_id = win32process.GetWindowThreadProcessId(target_handle)
        return int(native_process_id) == main_process_id
    except Exception:
        return False


def _same_process_popup_or_pos_is_foreground(control: Any, main_window: Any) -> bool:
    """Accept a proven same-process ownerless popup as the active POS surface."""

    if not _same_process_popup_candidate(control, main_window):
        return False
    main_handle = _control_direct_native_handle(main_window)
    target_handle = _control_direct_native_handle(control)
    if not sys.platform.startswith("win"):
        return _control_reports_active(main_window)
    if main_handle is None or target_handle is None:
        return False
    try:
        import win32con
        import win32gui
        import win32process

        foreground_handle = int(win32gui.GetForegroundWindow())
        if not foreground_handle or not win32gui.IsWindow(foreground_handle):
            return False
        _thread_id, foreground_process_id = win32process.GetWindowThreadProcessId(foreground_handle)
        main_process_id = _control_process_id(main_window)
        if main_process_id is None or int(foreground_process_id) != main_process_id:
            return False
        foreground_root = int(win32gui.GetAncestor(foreground_handle, win32con.GA_ROOT))
        target_root = int(win32gui.GetAncestor(target_handle, win32con.GA_ROOT))
        main_root = int(win32gui.GetAncestor(main_handle, win32con.GA_ROOT))
        return foreground_root in {target_root, main_root}
    except Exception:
        return False


def _unique_controls_by_identity(controls: list[Any]) -> list[Any]:
    unique: list[Any] = []
    seen: set[int] = set()
    for control in controls:
        marker = id(control)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(control)
    return unique


def _default_keyboard_sender(keys: str, **kwargs: Any) -> None:
    if not sys.platform.startswith("win"):
        raise RuntimeError("W02 POS keyboard input requires Windows.")
    from pywinauto.keyboard import send_keys  # type: ignore[import-untyped]

    send_keys(keys, **kwargs)


def _click_screen_point(x: int, y: int, action_name: str) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        from pywinauto import mouse

        mouse.click(button="left", coords=(x, y))
        return True
    except Exception:
        return False


def _clipboard_get_text() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import win32clipboard  # type: ignore[import-untyped]

        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return ""
            return str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT))
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def _clipboard_set_text(value: str) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, value)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return False


def _safe_call(control: Any, method_name: str, *, default: Any) -> Any:
    method = getattr(control, method_name, None)
    if not callable(method):
        return default
    try:
        return method()
    except Exception:
        return default


def _control_name(control: Any) -> str:
    for attr in ("window_text", "friendly_class_name"):
        method = getattr(control, attr, None)
        if callable(method):
            try:
                value = method()
                if value:
                    return str(value)
            except Exception:
                pass
    for attr in ("name", "element_info"):
        try:
            value = getattr(control, attr, None)
        except Exception:
            continue
        if attr == "element_info" and value is not None:
            value = getattr(value, "name", None)
        if value:
            return str(value)
    return ""


def _control_automation_id(control: Any) -> str:
    try:
        element_info = getattr(control, "element_info", None)
        value = getattr(element_info, "automation_id", None) if element_info is not None else None
        if value:
            return str(value)
    except Exception:
        pass
    for attr in ("automation_id", "automation_id_"):
        try:
            value = getattr(control, attr, None)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if value:
            return str(value)
    return ""


def _control_type(control: Any) -> str:
    try:
        element_info = getattr(control, "element_info", None)
        value = getattr(element_info, "control_type", None) if element_info is not None else None
        if value:
            return str(value)
    except Exception:
        pass
    method = getattr(control, "friendly_class_name", None)
    if callable(method):
        try:
            return str(method())
        except Exception:
            pass
    return str(control.__class__.__name__)


def _control_item_texts(control: Any) -> list[str]:
    method_names = ["ItemTexts", "ItemTexts_", "item_texts"]
    control_type = _control_type(control).lower()
    if control_type in {"combobox", "combo box", "list", "listbox", "list item", "listitem"}:
        method_names.append("texts")
    for method_name in method_names:
        method = getattr(control, method_name, None)
        if not callable(method):
            continue
        try:
            values = method()
        except Exception:
            continue
        try:
            items = [str(value) for value in values if value]
        except TypeError:
            continue
        if items:
            return items
    return []


def _control_text_candidates(control: Any) -> list[str]:
    values: list[str] = []

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        text = str(value).strip()
        if text:
            values.append(text)

    add(_control_name(control))
    for attr in ("text_value", "selected_value"):
        try:
            add(getattr(control, attr, None))
        except Exception:
            pass
    for method_name in ("texts", "legacy_properties", "get_value", "value", "TextBlock"):
        method = getattr(control, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
        except Exception:
            continue
        if isinstance(result, dict):
            for key in ("Value", "value", "Name", "name", "Description", "description"):
                add(result.get(key))
        else:
            add(result)
    return list(dict.fromkeys(values))


def _control_visible(control: Any) -> bool:
    method = getattr(control, "is_visible", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            pass
    rect = _rect_dict(control)
    return rect is None or rect["right"] > rect["left"] and rect["bottom"] > rect["top"]


def _control_enabled(control: Any) -> bool:
    method = getattr(control, "is_enabled", None)
    if callable(method):
        try:
            return bool(method())
        except Exception:
            pass
    return True


def _control_visible_strict(control: Any) -> bool:
    """Require direct visibility and non-empty geometry before UI mutation."""

    method = getattr(control, "is_visible", None)
    if not callable(method):
        return False
    try:
        if not bool(method()):
            return False
    except Exception:
        return False
    rect = _rect_dict(control)
    return bool(
        rect is not None
        and rect["right"] > rect["left"]
        and rect["bottom"] > rect["top"]
    )


def _control_enabled_strict(control: Any) -> bool:
    """Require a successful enabled-state read before UI mutation."""

    method = getattr(control, "is_enabled", None)
    if not callable(method):
        return False
    try:
        return bool(method())
    except Exception:
        return False


def _control_native_handle(control: Any) -> int | None:
    current = control
    seen: set[int] = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            return None
        seen.add(id(current))
        handle = _control_direct_native_handle(current)
        if handle is not None:
            return handle
        try:
            parent = getattr(current, "parent", None)
        except Exception:
            return None
        if callable(parent):
            try:
                parent = parent()
            except Exception:
                return None
        current = parent
    return None


def _control_direct_native_handle(control: Any) -> int | None:
    try:
        handle = getattr(control, "handle", None)
    except Exception:
        handle = None
    if callable(handle):
        try:
            handle = handle()
        except Exception:
            handle = None
    if handle:
        try:
            return int(handle)
        except (TypeError, ValueError):
            pass
    try:
        element_info = getattr(control, "element_info", None)
    except Exception:
        element_info = None
    for attribute in ("handle", "native_window_handle"):
        value = getattr(element_info, attribute, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _native_combo_select_index(
    control: Any,
    index: int,
    matched_text: str,
    aliases: tuple[str, ...],
    *,
    expected_process_id: int | None,
) -> tuple[bool, str]:
    """Select an exact live Win32 ComboBox HWND without foreground keyboard input.

    The boolean reports whether a native selection may have been dispatched.
    Once it is true, callers must not attempt another mutation unless fresh
    readback proves the first operation did not happen.
    """

    if not sys.platform.startswith("win"):
        return False, "not_windows"
    if expected_process_id is None:
        return False, "missing_expected_process_id"
    if not _control_visible_strict(control) or not _control_enabled_strict(control):
        return False, "control_not_actionable"
    handle = _control_direct_native_handle(control)
    if handle is None:
        return False, "missing_direct_hwnd"
    control_rect = _rect_dict(control)
    if control_rect is None:
        return False, "missing_control_rectangle"
    try:
        import win32gui
        import win32process

        if not win32gui.IsWindow(handle):
            return False, "invalid_hwnd"
        if not win32gui.IsWindowVisible(handle):
            return False, "native_control_not_visible"
        if not win32gui.IsWindowEnabled(handle):
            return False, "native_control_not_enabled"
        class_name = str(win32gui.GetClassName(handle) or "")
        if "combobox" not in class_name.lower():
            return False, f"class_not_combobox:{class_name or '<empty>'}"
        _thread_id, process_id = win32process.GetWindowThreadProcessId(handle)
        if int(process_id or 0) != int(expected_process_id):
            return False, f"process_mismatch:{int(process_id or 0)}"
        native_left, native_top, native_right, native_bottom = (
            int(value) for value in win32gui.GetWindowRect(handle)
        )
        native_rect = {
            "left": native_left,
            "top": native_top,
            "right": native_right,
            "bottom": native_bottom,
        }
        if any(abs(native_rect[edge] - control_rect[edge]) > 8 for edge in native_rect):
            return False, "rectangle_mismatch"

        from pywinauto.controls.win32_controls import ComboBoxWrapper  # type: ignore[import-untyped]

        native_combo = ComboBoxWrapper(handle)
        native_items = [str(value) for value in native_combo.item_texts()]
        if index < 0 or index >= len(native_items):
            return False, f"index_out_of_range:{index}/{len(native_items)}"
        native_text = native_items[index]
        if _normalize(native_text) != _normalize(matched_text):
            return False, f"item_text_mismatch:{_action_text(native_text)}"
        if not any(_text_matches(native_text, alias) for alias in aliases):
            return False, f"item_alias_mismatch:{_action_text(native_text)}"
    except Exception as exc:
        return False, f"preflight_exception:{_exception_evidence(exc)}"

    try:
        native_combo.select(index)
    except Exception as exc:
        # ComboBoxWrapper.select sends CB_SETCURSEL before parent
        # notifications.  Treat any exception from this point as an ambiguous
        # dispatch and require caller readback rather than a second mutation.
        return True, f"dispatch_exception:{_exception_evidence(exc)}"
    return True, "selected"


def _control_reports_active(control: Any) -> bool:
    try:
        is_active = getattr(control, "is_active", None)
    except Exception:
        return False
    if not callable(is_active):
        return False
    try:
        return bool(is_active())
    except Exception:
        return False


def _native_window_owns_foreground(
    window_handle: int,
    *,
    target_handle: int | None = None,
    expected_process_id: int | None = None,
) -> bool:
    if not sys.platform.startswith("win"):
        return True
    try:
        import win32con
        import win32gui
        import win32process

        root_handle = int(window_handle)
        if not win32gui.IsWindow(root_handle):
            return False
        if "spa-pos" not in str(win32gui.GetWindowText(root_handle) or "").lower():
            return False
        foreground = int(win32gui.GetForegroundWindow())
        if not foreground or not win32gui.IsWindow(foreground):
            return False
        expected_handle = int(target_handle) if target_handle is not None else root_handle
        if not win32gui.IsWindow(expected_handle):
            return False
        expected_root = int(win32gui.GetAncestor(expected_handle, win32con.GA_ROOT)) or expected_handle
        foreground_root = int(win32gui.GetAncestor(foreground, win32con.GA_ROOT)) or foreground
        if expected_root != foreground_root:
            return False
        _, pos_process_id = win32process.GetWindowThreadProcessId(root_handle)
        _, target_process_id = win32process.GetWindowThreadProcessId(expected_root)
        if expected_process_id is not None and pos_process_id != expected_process_id:
            return False
        if not pos_process_id or pos_process_id != target_process_id:
            return False
        if expected_root == root_handle:
            return True
        root_owner = int(win32gui.GetAncestor(expected_root, win32con.GA_ROOTOWNER)) or expected_root
        if root_owner == root_handle:
            return True
        owner = expected_root
        seen: set[int] = set()
        while owner and owner not in seen:
            seen.add(owner)
            owner = int(win32gui.GetWindow(owner, win32con.GW_OWNER))
            if owner == root_handle:
                return True
        return False
    except Exception:
        return False


def _native_foreground_contains_control(
    control: Any,
    *,
    pos_window_handle: int,
    expected_process_id: int | None,
) -> bool:
    """Prove an ownerless foreground popup contains a target UIA rectangle."""

    if not sys.platform.startswith("win") or expected_process_id is None:
        return False
    if not _control_visible_strict(control) or not _control_enabled_strict(control):
        return False
    control_rect = _rect_dict(control)
    if control_rect is None:
        return False
    try:
        import win32gui
        import win32process

        if not win32gui.IsWindow(int(pos_window_handle)):
            return False
        foreground = int(win32gui.GetForegroundWindow())
        if not foreground or not win32gui.IsWindow(foreground):
            return False
        _thread_id, foreground_pid = win32process.GetWindowThreadProcessId(foreground)
        if int(foreground_pid) != int(expected_process_id):
            return False
        left, top, right, bottom = (int(value) for value in win32gui.GetWindowRect(foreground))
        contains_control = (
            left <= control_rect["left"]
            and top <= control_rect["top"]
            and right >= control_rect["right"]
            and bottom >= control_rect["bottom"]
        )
        if not contains_control:
            return False

        # Prefer a native root/owner relationship when the popup exposes one.
        # If WinForms leaves the popup ownerless, retain a conservative visual
        # proof: the foreground surface must be a small, untitled popup that
        # intersects the POS root and contains the target rectangle.  This
        # excludes the full-screen Settings Center even when it shares a PID.
        import win32con

        foreground_root = int(win32gui.GetAncestor(foreground, win32con.GA_ROOT)) or foreground
        if foreground_root == int(pos_window_handle):
            return True
        foreground_owner = int(win32gui.GetAncestor(foreground, win32con.GA_ROOTOWNER)) or foreground_root
        if foreground_owner == int(pos_window_handle):
            return True
        owner = foreground_root
        seen: set[int] = set()
        while owner and owner not in seen:
            seen.add(owner)
            owner = int(win32gui.GetWindow(owner, win32con.GW_OWNER))
            if owner == int(pos_window_handle):
                return True

        title = str(win32gui.GetWindowText(foreground) or "").strip().lower()
        if title:
            return False
        root_left, root_top, root_right, root_bottom = (
            int(value) for value in win32gui.GetWindowRect(int(pos_window_handle))
        )
        intersection_width = max(0, min(right, root_right) - max(left, root_left))
        intersection_height = max(0, min(bottom, root_bottom) - max(top, root_top))
        if intersection_width <= 0 or intersection_height <= 0:
            return False
        foreground_area = max(0, right - left) * max(0, bottom - top)
        pos_area = max(0, root_right - root_left) * max(0, root_bottom - root_top)
        if not (foreground_area > 0 and pos_area > 0 and foreground_area <= pos_area):
            return False
        return _native_foreground_surface_contains_control(control, foreground)
    except Exception:
        return False


def _native_foreground_surface_contains_control(control: Any, foreground_handle: int) -> bool:
    """Rebind an exact foreground HWND and match the target control in its tree."""

    try:
        from pywinauto import Application
    except Exception:
        return False
    target_id = _control_automation_id(control)
    target_type = _control_type(control).lower()
    target_rect = _rect_dict(control)
    if target_rect is None:
        return False
    for backend in ("uia", "win32"):
        try:
            app = Application(backend=backend).connect(handle=int(foreground_handle))
            spec = app.window(handle=int(foreground_handle))
            root = _safe_call(spec, "wrapper_object", default=spec)
            if _control_direct_native_handle(root) != int(foreground_handle):
                continue
            for candidate in _collect_controls(root, max_depth=6, max_controls=400):
                if not _control_visible_strict(candidate) or not _control_enabled_strict(candidate):
                    continue
                if target_id and _control_automation_id(candidate) == target_id:
                    if _control_type(candidate).lower() == target_type and _rect_dict(candidate) == target_rect:
                        return True
                if _same_control(candidate, control):
                    return True
        except Exception:
            continue
    return False


def _native_target_owned_by_pos(
    window_handle: int,
    target_handle: int,
    *,
    expected_process_id: int,
) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import win32con
        import win32gui
        import win32process

        root_handle = int(window_handle)
        expected_handle = int(target_handle)
        if not win32gui.IsWindow(root_handle) or not win32gui.IsWindow(expected_handle):
            return False
        if "spa-pos" not in str(win32gui.GetWindowText(root_handle) or "").lower():
            return False
        expected_root = int(win32gui.GetAncestor(expected_handle, win32con.GA_ROOT)) or expected_handle
        _, pos_process_id = win32process.GetWindowThreadProcessId(root_handle)
        _, target_process_id = win32process.GetWindowThreadProcessId(expected_root)
        if (
            not pos_process_id
            or pos_process_id != expected_process_id
            or target_process_id != pos_process_id
        ):
            return False
        if expected_root == root_handle:
            return True
        root_owner = int(win32gui.GetAncestor(expected_root, win32con.GA_ROOTOWNER)) or expected_root
        if root_owner == root_handle:
            return True
        owner = expected_root
        seen: set[int] = set()
        while owner and owner not in seen:
            seen.add(owner)
            owner = int(win32gui.GetWindow(owner, win32con.GW_OWNER))
            if owner == root_handle:
                return True
        return False
    except Exception:
        return False


def _durable_replace(source: Path, destination: Path) -> None:
    if sys.platform.startswith("win"):
        import ctypes

        movefile_replace_existing = 0x1
        movefile_write_through = 0x8
        moved = ctypes.windll.kernel32.MoveFileExW(
            str(source),
            str(destination),
            movefile_replace_existing | movefile_write_through,
        )
        if not moved:
            raise ctypes.WinError()
        with destination.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        return
    os.replace(source, destination)
    directory_handle = os.open(str(destination.parent), os.O_RDONLY)
    try:
        os.fsync(directory_handle)
    finally:
        os.close(directory_handle)


def _rect_dict(control: Any) -> dict[str, int] | None:
    rect = _safe_call(control, "rectangle", default=None)
    if rect is None:
        return None
    try:
        left = int(rect.left)
        top = int(rect.top)
        right = int(rect.right)
        bottom = int(rect.bottom)
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _rect_contains_point(rect: dict[str, int], x: int, y: int) -> bool:
    return rect["left"] <= x < rect["right"] and rect["top"] <= y < rect["bottom"]


def _rect_near_anchor_popup(rect: dict[str, int] | None, anchor_rect: dict[str, int]) -> bool:
    if rect is None:
        return False
    horizontal_overlap = rect["right"] >= anchor_rect["left"] - 80 and rect["left"] <= anchor_rect["right"] + 360
    below_or_same_row = rect["bottom"] >= anchor_rect["top"] - 40 and rect["top"] <= anchor_rect["bottom"] + 420
    not_left_history_grid = rect["right"] >= anchor_rect["left"] - 120
    return horizontal_overlap and below_or_same_row and not_left_history_grid


def _same_control(left: Any, right: Any) -> bool:
    if left is right:
        return True
    left_handle = _control_direct_native_handle(left)
    right_handle = _control_direct_native_handle(right)
    if left_handle is not None and right_handle is not None:
        return left_handle == right_handle
    if (left_handle is None) != (right_handle is None):
        return False
    left_rect = _rect_dict(left)
    right_rect = _rect_dict(right)
    if left_rect is None or right_rect is None or left_rect != right_rect:
        return False
    return (
        _control_name(left) == _control_name(right)
        and _control_automation_id(left) == _control_automation_id(right)
        and _control_type(left) == _control_type(right)
    )


def _normalize(text: str) -> str:
    return "".join(str(text).replace("\r", "").replace("\n", "").split()).lower()


def _order_state_has_any(text: str, expected_states: tuple[str, ...]) -> bool:
    normalized = _normalize(text)
    punctuation_folded = (
        normalized.replace("（", "(")
        .replace("）", ")")
        .replace("(", "")
        .replace(")", "")
    )
    return any(_normalize(expected) in punctuation_folded for expected in expected_states)


def _text_contains_exact_token(text: str, expected: str) -> bool:
    expected_text = str(expected).strip()
    if not expected_text:
        return False
    if expected_text.isdigit():
        return any(token == expected_text for token in re.findall(r"\d+", str(text)))
    normalized_expected = _normalize(expected_text)
    if not re.fullmatch(r"[0-9a-zA-Z_-]+", normalized_expected):
        return normalized_expected in _normalize(text)
    return any(token == normalized_expected for token in re.findall(r"[0-9a-zA-Z_-]+", _normalize(text)))


def _observed_summary_has_branch_alias(observed: str, aliases: tuple[str, ...]) -> bool:
    """Classify observed branch options without matching N001 in N0011 or PA in PANEL."""

    return bool(observed) and any(
        _text_contains_exact_token(observed, alias)
        for alias in aliases
        if str(alias).strip()
    )


def _order_row_text_has_item_quantity(text: str, item_code: str, expected_quantity: str) -> bool:
    parts = [part.strip() for part in str(text).split(";")]
    if len(parts) >= 7 and parts[1] == str(item_code).strip():
        return parts[6] == str(expected_quantity).strip()
    return False


def _text_matches(actual: str, expected: str) -> bool:
    normalized_actual = _normalize(actual)
    normalized_expected = _normalize(expected)
    return bool(normalized_actual and normalized_expected and normalized_expected in normalized_actual)


def _looks_like_pos_branch_option(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        normalized
        and (
            normalized.startswith("n00")
            or normalized.startswith("hq01")
            or "站前" in normalized
            or "忠孝" in normalized
            or "營運總部" in normalized
        )
    )


def _action_text(text: str, *, limit: int = 120) -> str:
    cleaned = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    return cleaned[:limit]


def _exception_evidence(exc: BaseException) -> str:
    parts = [f"type={type(exc).__name__}"]
    hresult = getattr(exc, "hresult", None)
    if hresult is not None:
        parts.append(f"hresult={hresult}")
    message = _action_text(str(exc), limit=180)
    if message:
        parts.append(f"message={message}")
    return ",".join(parts)


def _w02_ledger_dir(state_dir: Path, run_date: date) -> Path:
    date_token = run_date.strftime("%Y%m%d")
    if state_dir.name == date_token:
        return state_dir
    return state_dir / date_token


def _prompt_text_has_error(text: str) -> bool:
    normalized = _normalize(text)
    error_tokens = (
        "錯誤",
        "失敗",
        "異常",
        "無法",
        "不可",
        "未輸入",
        "不存在",
        "找不到",
        "必填",
        "請先",
        "尚未",
    )
    return any(token in normalized for token in error_tokens)


def _prompt_text_has_success(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = _normalize(text)
    return any(_normalize(token) in normalized for token in tokens)


def _prompt_confirmation_name_matches(control: Any, tokens: tuple[str, ...]) -> bool:
    return _prompt_confirmation_name_matches_normalized(_normalize(_control_name(control)), tokens)


def _prompt_confirmation_name_matches_normalized(normalized_name: str, tokens: tuple[str, ...]) -> bool:
    if not normalized_name:
        return False
    for token in tokens:
        normalized_token = _normalize(token)
        if not normalized_token:
            continue
        if normalized_name == normalized_token or normalized_name.startswith(f"{normalized_token}("):
            return True
    return False


def _prompt_modal_title_visible(text: str) -> bool:
    normalized = _normalize(text)
    return any(token in normalized for token in ("提示訊息", "注意事項", "錯誤", "警告"))
