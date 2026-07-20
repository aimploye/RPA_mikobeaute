from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from time import monotonic, sleep
from typing import Any, Callable, cast

from pos_report_bot.config.models import BranchConfig
from pos_report_bot.pos.report_automation import ReportAutomationError
from pos_report_bot.reports.w02_order_builder import W02OrderForm, W02OrderIssue, W02OrderPlan


W02_BRANCH_ALIASES: dict[str, tuple[str, ...]] = {
    "站前4樓": ("N001", "站前4樓", "站前4F"),
    "站前11樓": ("N002", "站前11樓", "站前11F"),
    "忠孝7樓": ("N003", "忠孝7樓", "忠孝7F"),
    "忠孝國際醫學3樓": ("N004", "忠孝國際醫學3樓", "忠孝國際3F"),
    "忠孝健康7樓": ("N005", "忠孝健康7樓", "忠孝健康7F"),
    "忠孝預防醫學3樓": ("N006", "忠孝預防醫學3樓", "忠孝預防醫學3F"),
}
W02_TERMINAL_FORM_STATUSES = {"completed", "completed_with_skipped_items"}
W02_SKIPPABLE_ITEM_ERROR_CODES = {"W02_POS_ITEM_NOT_FOUND"}
W02_FAILURE_CONTEXT_CONTROL_LIMIT = 240
W02_DEFAULT_USE_TYPE = "常態訂貨"
W02_ACTION_SETTLE_SECONDS = 0.15
W02_KEY_SETTLE_SECONDS = 0.1
W02_POLL_SECONDS = 0.1
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


@dataclass(frozen=True)
class _W02SubmittedFormResult:
    submitted_item_count: int
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
        self._item_picker_controls_cache: list[Any] | None = None
        self._explicitly_selected_branches: set[str] = set()
        self._ledger_path = _w02_ledger_dir(state_dir, run_date) / "w02_pos_submission_ledger.json"
        self._diagnostic_path = logs_dir / (
            f"automation_w02_pos_order_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}.json"
        )

    def submit_plan(self, plan: W02OrderPlan) -> W02PosOrderSubmissionResult:
        ledger = self._load_ledger()
        completed = 0
        skipped = 0
        skipped_issues: list[W02OrderIssue] = []
        completed_form_counts_by_branch: dict[str, int] = {}
        active_key: str | None = None
        active_action_start_index = 0
        try:
            for form in plan.forms:
                key = _form_key(form)
                active_key = key
                active_action_start_index = len(self.actions)
                signature = _form_signature(form)
                ledger_entry = ledger.get("forms", {}).get(key, {})
                if ledger_entry.get("status") in W02_TERMINAL_FORM_STATUSES:
                    completed_signature = ledger_entry.get("plan_signature")
                    if completed_signature and completed_signature != signature:
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
                if ledger_entry.get("status") in {"in_progress", "submitted_pending_verification"}:
                    raise ReportAutomationError(
                        "W02_POS_ORDER_PARTIAL_STATE_REVIEW_REQUIRED",
                        (
                            "W02 偵測到同日同分館同部門曾開始 POS 建單但未完成驗證；"
                            "為避免重複下單，請先人工確認 POS 是否已有該訂貨單。"
                            f"分館/部門：{key}。"
                        ),
                    )
                ledger.setdefault("forms", {})[key] = {
                    "status": "in_progress",
                    "started_at": datetime.now(tz=UTC).isoformat(),
                    "branch": form.branch,
                    "department": form.department,
                    "item_count": len(form.items),
                    "plan_signature": signature,
                    "items": _form_items_payload(form),
                }
                self._write_ledger(ledger)
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
                active_key = None
        except ReportAutomationError as exc:
            active_actions = self.actions[active_action_start_index:]
            if active_key is not None and _failure_happened_before_pos_submission(active_actions):
                ledger.setdefault("forms", {}).setdefault(active_key, {})
                ledger["forms"][active_key].update(
                    {
                        "status": "failed_before_pos_submission",
                        "failed_at": datetime.now(tz=UTC).isoformat(),
                        "error_code": exc.error_code,
                        "message": exc.message,
                    }
                )
                self._write_ledger(ledger)
            diagnostic_path = self._write_diagnostic(
                status="failed",
                error_code=exc.error_code,
                message=exc.message,
                plan=plan,
                ledger=ledger,
            )
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
            )
        except Exception as exc:
            diagnostic_path = self._write_diagnostic(
                status="failed",
                error_code="W02_POS_ORDER_UNEXPECTED_ERROR",
                message=str(exc),
                plan=plan,
                ledger=ledger,
            )
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

    def _submit_form(self, form: W02OrderForm) -> "_W02SubmittedFormResult":
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
        skipped_issues: list[W02OrderIssue] = []
        submitted_item_count = 0
        for item in form.items:
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
                self.actions.append(f"w02_item_skipped:{item.item_code}:{exc.error_code}")
                continue
            submitted_item_count += 1
        if submitted_item_count <= 0:
            self._cancel_current_order_if_possible()
            self.actions.append(f"w02_form_skipped_no_submitted_items:{_form_key(form)}")
            return _W02SubmittedFormResult(submitted_item_count=0, skipped_issues=tuple(skipped_issues))
        self._click_by_id("B_Save", action_name="w02_save_order", error_code="W02_POS_SAVE_NOT_FOUND")
        if not self._dismiss_prompt_if_present(
            error_code="W02_POS_SAVE_REJECTED",
            success_tokens=("存檔完成", "存檔成功"),
            timeout_seconds=5,
        ):
            self.actions.append("w02_save_prompt_not_confirmed")
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
        self._click_by_id("B_Appv", action_name="w02_approve_order", error_code="W02_POS_APPROVE_NOT_FOUND")
        self._dismiss_prompt_if_present(
            error_code="W02_POS_APPROVE_CONFIRM_REJECTED",
            accepted_tokens=("是",),
            timeout_seconds=5,
        )
        self._dismiss_prompt_if_present(
            error_code="W02_POS_APPROVE_REJECTED",
            success_tokens=("核准完成",),
            timeout_seconds=5,
        )
        if not self._wait_until(self._form_approval_verified, timeout_seconds=10):
            raise ReportAutomationError(
                "W02_POS_ORDER_APPROVAL_NOT_VERIFIED",
                "W02 已點擊核准，但未在 POS 畫面確認訂貨單狀態為已核准/訂貨核准；本次不視為完成。",
            )
        self.actions.append(f"w02_form_completed:{_form_key(form)}")
        self._close_order_window_if_present("after_form_completed")
        return _W02SubmittedFormResult(submitted_item_count=submitted_item_count, skipped_issues=tuple(skipped_issues))

    def _switch_branch(self, branch: str) -> None:
        if branch in self._explicitly_selected_branches and self._window_title_matches_branch(branch):
            self.actions.append(f"w02_branch_already_selected_after_explicit_selection:{branch}")
            return
        self._close_order_window_if_present("before_branch_switch")
        account_menu = self._find_control_by_name_contains(("AI自動化",), control_types=("MenuItem", "Button"))
        if account_menu is None:
            raise ReportAutomationError("W02_POS_BRANCH_MENU_NOT_FOUND", f"W02 找不到右上角帳號/分館選單，無法切換到 {branch}。")
        self._click(account_menu, f"w02_open_branch_menu:{branch}")
        aliases = self._branch_aliases(branch)
        if not self._select_branch_from_account_popup(branch, aliases, force_select=True):
            observed = self._visible_option_summary()
            self._record_failure_context("branch_option_not_found", message=f"missing branch: {branch}; observed: {observed}")
            raise ReportAutomationError(
                "W02_POS_BRANCH_OPTION_NOT_FOUND",
                f"W02 找不到分館選項：{branch}。目前可見/可讀選項：{observed or '無'}。",
            )
        self._dismiss_prompt_if_present(allow_generic_confirmation=True)
        if not self._wait_until(lambda: self._window_title_matches_branch(branch), timeout_seconds=15):
            raise ReportAutomationError("W02_POS_BRANCH_SWITCH_NOT_VERIFIED", f"W02 已嘗試切換分館，但視窗標題未確認為 {branch}。")
        self._explicitly_selected_branches.add(branch)
        self.actions.append(f"w02_branch_selected:{branch}")

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
            if self._select_visible_option(branch, aliases, allow_combobox=False):
                return True
            sleep(0.2)
        return False

    def _branch_combo_candidates(self, aliases: tuple[str, ...]) -> list[Any]:
        candidates: list[Any] = []
        for control in self._all_controls(include_desktop=True):
            if not _control_visible(control) or not _control_enabled(control):
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
                candidates.append(control)
                summary = "|".join(item_texts[:12]) or name
                self.actions.append(f"w02_branch_combo_candidate:{_action_text(summary)}")
        return candidates

    def _select_combo_by_alias(self, control: Any, aliases: tuple[str, ...]) -> bool:
        item_texts = _control_item_texts(control)
        matched_index: int | None = None
        matched_text: str | None = None
        for index, item_text in enumerate(item_texts):
            if any(_text_matches(item_text, alias) for alias in aliases):
                matched_index = index
                matched_text = item_text
                break

        # The POS account menu contains a nested branch ComboBox. On the real
        # POS screen the branch options are only materialized after expanding
        # that ComboBox, so prefer the visible dropdown path before wrapper
        # Select/SelectedIndex calls.
        self._click_dropdown(control, "w02_branch_combo_dropdown")
        if self._select_visible_option(matched_text or aliases[0], aliases, allow_combobox=False):
            return True

        candidates = ([matched_text] if matched_text is not None else []) + list(aliases)
        for candidate in candidates:
            for method_name in ("select", "Select"):
                method = getattr(control, method_name, None)
                if not callable(method):
                    continue
                try:
                    method(candidate)
                    if self._selected_text_matches_any(control, aliases):
                        self.actions.append(f"w02_branch_combo_select_text:{_action_text(candidate)}")
                        return True
                except Exception:
                    continue

        if matched_index is not None:
            for method_name in ("SelectedIndex", "SelectedIndex_", "select", "Select"):
                method = getattr(control, method_name, None)
                if not callable(method):
                    continue
                try:
                    method(matched_index)
                    if self._selected_text_matches_any(control, aliases):
                        self.actions.append(f"w02_branch_combo_select_index:{matched_index}:{_action_text(matched_text or '')}")
                        return True
                except Exception:
                    continue
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
        for control in self._all_controls(include_desktop=True):
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
        for control in self._all_controls(include_desktop=True):
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
            self._click(control, f"w02_use_type_popup_option:{value}")
            return True
        return False

    def _add_item(self, row_index: int, item_code: str, quantity: int) -> None:
        selector_cell = self._find_grid_cell("選取商品", row_index)
        if selector_cell is None:
            self._record_failure_context("item_selector_cell_not_found", item_code=item_code, row_index=row_index)
            raise ReportAutomationError("W02_POS_ITEM_SELECTOR_CELL_NOT_FOUND", f"W02 找不到第 {row_index + 1} 列選取商品欄。")
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
        self._send_keys("{ENTER}")
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
            self._record_failure_context("quantity_cell_not_found", item_code=item_code, row_index=row_index)
            raise ReportAutomationError("W02_POS_QUANTITY_CELL_NOT_FOUND", f"W02 找不到第 {row_index + 1} 列訂貨數量欄。")
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
        self._send_keys("{ENTER}")

    def _paste_grid_cell_text(self, control: Any, value: str, edit_trigger: str, commit_key: str, item_code: str) -> None:
        self._click(control, f"w02_quantity_cell:{item_code}:{edit_trigger}")
        if edit_trigger == "double_click":
            self._double_click(control, f"w02_quantity_cell_double_click:{item_code}")
        else:
            self._send_keys(edit_trigger)
        self._replace_focused_text(value)
        self._send_keys(commit_key)

    def _replace_focused_text(self, value: str) -> None:
        original_clipboard = _clipboard_get_text()
        clipboard_ready = _clipboard_set_text(value)
        try:
            self._send_keys("^a{BACKSPACE}")
            if clipboard_ready:
                self._send_keys("^v")
            else:
                self._send_keys(value, with_spaces=True)
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
                if _click_screen_point(x, y, f"w02_item_picker_select_single_result_geometry:{item_code}"):
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
        if _click_screen_point(x, y, f"w02_item_picker_select_geometry:{item_code}"):
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
        if self._find_control_by_id("ItemsWin") is None:
            return True
        close_control = self._find_item_picker_close_control()
        if close_control is not None:
            try:
                self._click(close_control, "w02_item_picker_close_after_skip")
                return self._wait_until(lambda: self._find_control_by_id("ItemsWin") is None, timeout_seconds=2)
            except ReportAutomationError:
                pass
        try:
            self._send_keys("{ESC}")
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
        for control in self._order_window_controls():
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
            self._send_keys("^c")
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

    def _order_row_quantity_matches(self, row_index: int, item_code: str, expected: str, quantity_cell: Any) -> bool:
        for control in self._order_window_controls():
            if not _control_visible(control):
                continue
            name = _normalize(_control_name(control))
            if name != f"資料列{row_index}":
                continue
            for text in _control_text_candidates(control):
                if _order_row_text_has_item_quantity(text, item_code, expected):
                    self.actions.append(f"w02_order_quantity_verified:{item_code}:{expected}:row_text")
                    return True
        if self._grid_cell_value_matches(quantity_cell, expected):
            self.actions.append(f"w02_order_quantity_verified:{item_code}:{expected}:cell")
            return True
        return False

    def _form_approval_verified(self) -> bool:
        if _prompt_text_has_success(self._visible_prompt_text(), ("核准確認", "確認要核准")):
            return False
        state_control = self._find_control_by_id("cL_BrOrderStateName")
        if state_control is not None:
            state_text = _normalize(" ".join(_control_text_candidates(state_control)))
            if "訂貨確認" in state_text or "新單" in state_text:
                return False
            if "訂貨核准" in state_text or "已核准" in state_text:
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
        allow_generic_confirmation: bool = False,
        timeout_seconds: float = 1.5,
        ) -> bool:
        deadline = monotonic() + timeout_seconds
        last_success_text = ""
        while True:
            self._invalidate_control_cache()
            prompt_scopes = [
                (root, controls, _visible_prompt_text_from_controls(controls))
                for root, controls in self._prompt_control_scopes()
            ]
            prompt_text = " | ".join(text for _root, _controls, text in prompt_scopes if text)
            success_scope = next(
                (
                    (root, controls, text)
                    for root, controls, text in prompt_scopes
                    if success_tokens and _prompt_text_has_success(text, success_tokens)
                ),
                None,
            )
            accepted_scope = next(
                (
                    (root, controls, text)
                    for root, controls, text in prompt_scopes
                    if accepted_tokens and _prompt_text_has_success(text, accepted_tokens)
                ),
                None,
            )
            if accepted_scope is None and accepted_tokens:
                accepted_scope = next(
                    (
                        (root, controls, text)
                        for root, controls, text in prompt_scopes
                        if any(
                            _prompt_confirmation_name_matches(candidate, accepted_tokens)
                            for candidate in controls
                        )
                    ),
                    None,
                )
            if success_scope is not None:
                last_success_text = success_scope[2]
            target_scope = success_scope or accepted_scope
            control = (
                self._find_prompt_confirmation_control(target_scope[1])
                if target_scope is not None
                else None
            )
            if control is None and allow_generic_confirmation and not success_tokens and not accepted_tokens:
                for _root, controls, _text in prompt_scopes:
                    control = self._find_prompt_confirmation_control(controls)
                    if control is not None:
                        break
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
                if accepted_control:
                    self._click(control, f"w02_prompt:{control_name}")
                    return True
                if _prompt_text_has_error(prompt_text):
                    raise ReportAutomationError(error_code, f"W02 POS 彈窗回報異常：{prompt_text}")
                if allow_generic_confirmation and not success_tokens and not accepted_tokens:
                    self._click(control, f"w02_prompt:{control_name}")
                    return True
            if success_scope is not None:
                self.actions.append("w02_prompt_success_enter_fallback")
                self._send_keys("{ENTER}")
                if self._wait_until(
                    lambda: not self._prompt_scope_has_success(success_scope[0], success_tokens),
                    timeout_seconds=1.5,
                ):
                    self.actions.append("w02_prompt_enter_dismissed")
                    return True
                raise ReportAutomationError(error_code, f"W02 POS 成功提示無法關閉：{last_success_text}")
            if _prompt_text_has_error(prompt_text) and _prompt_modal_title_visible(prompt_text):
                raise ReportAutomationError(error_code, f"W02 POS 彈窗回報異常：{prompt_text}")
            if monotonic() >= deadline:
                return False
            sleep(W02_POLL_SECONDS)
        return False

    def _find_prompt_confirmation_control(self, controls: list[Any] | None = None) -> Any | None:
        confirm_names = ("確定", "是", "ok")
        confirm_types = {"button", "pane", "text", "custom", "dataitem"}
        for control in controls if controls is not None else self._prompt_controls():
            if not _control_visible(control) or not _control_enabled(control):
                continue
            name = _normalize(_control_name(control))
            if not _prompt_confirmation_name_matches_normalized(name, confirm_names):
                continue
            control_type = _control_type(control).lower()
            if control_type in confirm_types:
                return control
        return None

    def _prompt_control_scopes(self) -> list[tuple[Any, list[Any]]]:
        roots = [self.window]
        if sys.platform.startswith("win"):
            for root in _desktop_windows():
                if root is self.window:
                    continue
                if not _prompt_root_allowed_for_window(root, self.window):
                    continue
                roots.append(root)
        prompt_scopes: list[tuple[Any, list[Any]]] = []
        for root in roots:
            for prompt_root in _find_prompt_roots(root):
                controls = _unique_controls_by_identity(_collect_controls(prompt_root, max_depth=7, max_controls=300))
                prompt_scopes.append((prompt_root, controls))
        return prompt_scopes

    def _prompt_controls(self) -> list[Any]:
        return _unique_controls_by_identity(
            [control for _root, controls in self._prompt_control_scopes() for control in controls]
        )

    def _prompt_scope_has_success(self, prompt_root: Any, success_tokens: tuple[str, ...]) -> bool:
        for root, controls in self._prompt_control_scopes():
            if root is prompt_root or _same_control(root, prompt_root):
                return _prompt_text_has_success(_visible_prompt_text_from_controls(controls), success_tokens)
        return False

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
        self._send_keys(keys)
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
    ) -> bool:
        candidates = tuple(dict.fromkeys((value, *aliases)))
        for control in self._all_controls(include_desktop=True):
            name = _control_name(control)
            if not name or not _control_visible(control) or not _control_enabled(control):
                continue
            if not any(_text_matches(name, candidate) for candidate in candidates):
                continue
            allowed_types = {"listitem", "menuitem", "dataitem", "text", "button"}
            if allow_combobox:
                allowed_types.add("combobox")
            if _control_type(control).lower() not in allowed_types:
                continue
            self._click(control, f"w02_option:{value}")
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
        for control in self._order_window_controls():
            name = _normalize(_control_name(control))
            if normalized_header in name and normalized_row in name and _control_visible(control):
                return control
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
        for control in self._all_controls(include_desktop=True):
            control_type = _control_type(control).lower()
            if normalized_types and control_type not in normalized_types:
                continue
            name = _normalize(_control_name(control))
            if name and any(candidate in name for candidate in normalized_names) and _control_visible(control):
                return control
        return None

    def _all_controls(self, *, include_desktop: bool = False) -> list[Any]:
        cached = self._control_cache.get(include_desktop)
        if cached is not None:
            return cached
        controls = _collect_controls(self.window, max_depth=9, max_controls=1200)
        if include_desktop and sys.platform.startswith("win"):
            controls.extend(_desktop_controls())
        self._control_cache[include_desktop] = controls
        return controls

    def _window_title_matches_branch(self, branch: str) -> bool:
        title = _control_name(self.window)
        return any(_text_matches(title, alias) for alias in self._branch_aliases(branch))

    def _click(self, control: Any, action_name: str) -> None:
        last_error: Exception | None = None
        for method_name in ("click_input", "click", "invoke"):
            method = getattr(control, method_name, None)
            if not callable(method):
                continue
            try:
                method()
                self.actions.append(f"click:{action_name}")
                self._wait_after_action()
                return
            except Exception as exc:
                last_error = exc
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
        return _click_screen_point(x, y, action_name)

    def _click_dropdown_geometry(self, control: Any, action_name: str) -> bool:
        rect = _rect_dict(control)
        if rect is None:
            return False
        x = rect["right"] - min(8, max(2, (rect["right"] - rect["left"]) // 5))
        y = rect["top"] + ((rect["bottom"] - rect["top"]) // 2)
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
        self._send_keys("^a{BACKSPACE}")
        self._send_keys(value, with_spaces=True)

    def _send_keys(self, keys: str, **kwargs: Any) -> None:
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
        self._item_picker_controls_cache = None

    def _load_ledger(self) -> dict[str, Any]:
        if not self._ledger_path.exists():
            return {
                "schema_version": 1,
                "run_date": self.run_date.isoformat(),
                "forms": {},
            }
        try:
            return cast(dict[str, Any], json.loads(self._ledger_path.read_text(encoding="utf-8")))
        except Exception:
            return {
                "schema_version": 1,
                "run_date": self.run_date.isoformat(),
                "forms": {},
            }

    def _write_ledger(self, ledger: dict[str, Any]) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

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
            "visible_controls": self._control_records(self._all_controls(include_desktop=True)),
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
            from PIL import ImageGrab  # type: ignore[import-not-found]

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
    if len(controls) == 1:
        for child in _safe_call(control, "descendants", default=[]):
            if len(controls) >= max_controls:
                break
            controls.append(child)
    return controls


def _desktop_controls() -> list[Any]:
    try:
        from pywinauto import Desktop  # type: ignore[import-untyped]

        return list(Desktop(backend="uia").windows()) + list(Desktop(backend="uia").descendants())
    except Exception:
        return []


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


def _desktop_windows() -> list[Any]:
    try:
        from pywinauto import Desktop  # type: ignore[import-untyped]

        return list(Desktop(backend="uia").windows())
    except Exception:
        return []


def _find_prompt_roots(control: Any, *, depth: int = 0, max_depth: int = 9) -> list[Any]:
    if control is None or depth > max_depth:
        return []
    children = list(_safe_call(control, "children", default=[]))
    if _is_prompt_container(control) or (
        _is_prompt_title_control(control) and bool(children)
    ):
        return [control]
    roots: list[Any] = []
    for child in children:
        roots.extend(_find_prompt_roots(child, depth=depth + 1, max_depth=max_depth))
    if roots:
        return roots
    for descendant in list(_safe_call(control, "descendants", default=[]))[:300]:
        if _is_prompt_container(descendant):
            return [descendant]
        descendant_children = list(_safe_call(descendant, "children", default=[]))
        if _is_prompt_title_control(descendant) and descendant_children:
            return [descendant]
    return roots


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
