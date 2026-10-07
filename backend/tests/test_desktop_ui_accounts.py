"""Account UI contracts without reading credentials or writing the user's settings."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("customtkinter")
from desktop import app_gui, display, widget
from desktop.context_manager import get_connected_ai_summary

A = "claude_code:0123456789ab"
B = "claude_code:abcdef012345"
ROWS = [{"account_id": A, "tool": "claude_code", "label": "Claude 개인"},
        {"account_id": B, "tool": "claude_code", "label": "Claude 작업"}]
AI = {"key": "claude", "plan_name": "Pro"}
WINDOW = {"id": "5h", "label": "5시간", "remaining_pct": 60, "source": "official"}


def summary_for(ids, registered=None):
    usage = {"requests": 2, "tokens": 100, "cost_usd": 1, "cost_unknown": 0}
    active = {"claude": {"accounts": [{"account_id": aid, "windows": {
        key: usage for key in ("5h", "today", "7d", "month")}} for aid in ids]}}
    summary = get_connected_ai_summary([AI], quota_overrides={"claude": [WINDOW]})
    rows = registered if registered is not None else ROWS
    quotas = [{**ROWS[0], "quota_windows": [WINDOW]}] if any(r["account_id"] == A for r in rows) else []
    return display.attach_accounts(summary, active, rows, quotas, 1400)


def test_multiple_accounts_and_unknown_have_independent_usage_and_quota():
    summary = summary_for([A, B, None])
    groups = summary["items"][0]["accounts"]
    assert [g["label"] for g in groups] == ["Claude 개인", "Claude 작업", "계정 미상"]
    assert groups[0]["quota_windows"] == [WINDOW]
    assert groups[1]["quota_windows"] == [] and groups[2]["quota_windows"] == []
    assert all(len(display.account_usage_lines(g, "api_equivalent")) == 4 for g in groups)
    entries = display.widget_entries(summary)
    assert entries[0]["value"] == "60% 남음"
    assert entries[1]["value"] == entries[2]["value"] == "확인 불가"
    assert "계정 미상" == entries[2]["label"]


def test_single_account_preserves_widget_shape_and_missing_quota_does_not_borrow():
    summary = summary_for([A], registered=ROWS[:1])
    entries = display.widget_entries(summary)
    assert entries[0]["id"] == "claude:5h"
    summary = summary_for([B], registered=ROWS[1:])
    assert summary["items"][0]["quota_windows"] == []
    assert display.widget_entries(summary)[0]["value"] == "확인 불가"
    summary = summary_for([None], registered=[])
    assert "계정 미상" in display.widget_entries(summary)[0]["group"]


def test_registered_account_without_records_is_unknown_and_widget_is_bounded():
    rows = [dict(account_id=f"claude_code:{i:012x}", tool="claude_code", label=f"Claude {i}")
            for i in range(6)]
    summary = summary_for([], registered=rows)
    assert display.account_usage_lines(summary["items"][0]["accounts"][0], "api_equivalent") == [
        "5시간 · 기록 없음", "오늘 · 기록 없음", "7일 · 기록 없음", "월 · 기록 없음"]
    entries = display.widget_entries(summary)
    assert len(entries) == 6  # Three accounts, more, and two legacy totals.
    assert entries[3]["value"] == "+3개 · 메인 창"
    assert display.select_entries(entries, ["claude:5h"]) == entries[:4]
    assert display.usage_cost_text({"cost_krw": None}, "billed") == "기록 없음"
    assert display.usage_cost_text({"requests": 2, "cost_unknown": 2, "cost_krw": 0}, "billed") == "계산 불가"


def test_account_management_calls_registry_and_rejects_relative_folder(monkeypatch, tmp_path):
    registry = Mock()
    monkeypatch.setattr(app_gui, "accounts", registry)
    ui = SimpleNamespace(_account_changed=Mock(), lbl_status_msg=Mock(),
                         account_folder_entry=Mock(), account_tool_menu=Mock(), account_label_entry=Mock())
    ui.account_folder_entry.get.return_value = str(tmp_path)
    ui.account_tool_menu.get.return_value = "codex"
    ui.account_label_entry.get.return_value = "작업 계정"
    app_gui.DesktopMainWindow._rename_account(ui, A, " 새 라벨 ")
    registry.rename_account.assert_called_once_with(A, "새 라벨")
    app_gui.DesktopMainWindow._add_account_folder(ui)
    registry.add_folder.assert_called_once_with("codex", tmp_path, "작업 계정")
    app_gui.DesktopMainWindow._remove_account_folder(ui, str(tmp_path))
    registry.remove_folder.assert_called_once_with(Path(tmp_path))
    assert ui._account_changed.call_count == 3
    ui.account_folder_entry.get.return_value = "relative-folder"
    app_gui.DesktopMainWindow._add_account_folder(ui)
    assert registry.add_folder.call_count == 1


def test_multikey_actions_clear_secret_and_validation_never_echoes_it(monkeypatch):
    manager = Mock()
    monkeypatch.setattr(app_gui, "api_key_manager", manager)
    ui = SimpleNamespace(byok_opt_prov=Mock(), byok_entry_key=Mock(), byok_entry_label=Mock(),
                         lbl_status_msg=Mock(), _refresh_keys_list=Mock(),
                         _refresh_diagnostic_table=Mock(), refresh_data=Mock())
    ui.byok_opt_prov.get.return_value = "OpenAI"
    ui.byok_entry_key.get.return_value = " test-only-secret "
    ui.byok_entry_label.get.return_value = "실험"
    app_gui.DesktopMainWindow._save_api_key_dedicated(ui)
    manager.add_key.assert_called_once_with("openai", "test-only-secret", "실험")
    manager.set_key.assert_not_called()
    manager.verify_key.assert_not_called()
    ui.byok_entry_key.delete.assert_called_once_with(0, "end")
    app_gui.DesktopMainWindow._rename_key_action(ui, "openai", "id2", " 새 라벨 ")
    manager.update_label.assert_called_once_with("openai", "id2", "새 라벨")
    app_gui.DesktopMainWindow._delete_key_action(ui, "openai", "id2")
    manager.delete_key.assert_called_once_with("openai", "id2")
    manager.add_key.side_effect = ValueError("test-only-secret raw exception")
    app_gui.DesktopMainWindow._save_api_key_dedicated(ui)
    assert "test-only-secret" not in str(ui.lbl_status_msg.configure.call_args_list)


@pytest.mark.parametrize("selection,expected", [("전체", {}), ("계정 미상", {"account_unknown": "true"}),
                                               ("Claude 개인 · " + A, {"account_id": A})])
def test_logs_and_charts_send_account_filter_to_backend(monkeypatch, selection, expected):
    requests = []
    real_client = httpx.Client

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(app_gui.httpx, "Client", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(app_gui, "get_connected_ai_summary", lambda *_a, **_kw: {"items": []})
    monkeypatch.setattr(app_gui.accounts, "list_accounts", lambda: deepcopy(ROWS))
    monkeypatch.setattr(app_gui, "api_key_manager", Mock(list_keys=lambda: []))
    ui = SimpleNamespace(backend_url="http://testserver", official_spend={},
                         log_account_selection=selection, chart_account_selection=selection,
                         account_choices=display.account_options(ROWS), _is_updating=True,
                         after=lambda _delay, cb: cb(), _apply_data=Mock())
    app_gui.DesktopMainWindow._fetch_worker(ui, [AI], {}, 1400)
    for request in requests:
        if request.url.path.endswith(("/traces", "/overview", "/breakdown")):
            assert {k: v for k, v in request.url.params.items() if k.startswith("account_")} == expected
    assert len(requests) == 4
    assert ui._is_updating is False


def test_card_multi_and_single_layout_render_labels_without_parent_quota(monkeypatch):
    labels = []

    def label(_parent, **kwargs):
        labels.append(kwargs["text"])
        return Mock()

    monkeypatch.setattr(app_gui.ctk, "CTkFrame", lambda *_a, **_kw: Mock())
    monkeypatch.setattr(app_gui.ctk, "CTkLabel", label)
    monkeypatch.setattr(app_gui.theme, "font", lambda *_a, **_kw: ())
    boxes = {key: Mock() for key in ("account_box", "quota_box", "ctx_box", "cost_box")}
    boxes["account_box"].winfo_children.return_value = []
    item = summary_for([A, B, None])["items"][0]
    assert app_gui.DesktopMainWindow._render_account_card(None, boxes, item)
    assert "Claude 개인" in labels and "Claude 작업" in labels and "계정 미상" in labels
    assert labels.count("한도 확인 불가") == 2
    assert "5시간 60% 남음" in labels
    for key in ("quota_box", "ctx_box", "cost_box"):
        boxes[key].pack_forget.assert_called_once()
    item["accounts"] = item["accounts"][:1]
    assert not app_gui.DesktopMainWindow._render_account_card(None, boxes, item)
    boxes["quota_box"].pack.assert_called_once()


def test_log_row_displays_account_label():
    row = {key: Mock() for key in ("frame", "time", "stat", "model", "tokens", "cost", "latency", "account")}
    row.update(vals=None, shown=True)
    ui = SimpleNamespace(traces_data=[{"account_id": A, "trace_id": "trace", "provider": "anthropic"}],
                         log_filter_provider="전체", log_filter_status="전체", log_filter_search="",
                         account_rows=ROWS, krw_rate=1400, lbl_logs_empty=Mock(), _log_rows=[row])
    app_gui.DesktopMainWindow._render_filtered_logs(ui)
    row["account"].configure.assert_called_once_with(text="Claude 개인")


def test_account_card_theme_change_redraws_cached_groups(monkeypatch):
    monkeypatch.setattr(app_gui.theme, "font", lambda *_a, **_kw: ())
    monkeypatch.setattr(app_gui.ctk, "CTkFrame", lambda *_a, **_kw: Mock())
    label = Mock(side_effect=lambda *_a, **_kw: Mock())
    monkeypatch.setattr(app_gui.ctk, "CTkLabel", label)
    mode = ["Light"]
    monkeypatch.setattr(app_gui.ctk, "get_appearance_mode", lambda: mode[0])
    boxes = {key: Mock() for key in ("account_box", "quota_box", "ctx_box", "cost_box")}
    boxes["account_box"].winfo_children.return_value = []
    item = summary_for([A, B, None])["items"][0]
    app_gui.DesktopMainWindow._render_account_card(None, boxes, item)
    count = label.call_count
    app_gui.DesktopMainWindow._render_account_card(None, boxes, item)
    assert label.call_count == count
    mode[0] = "Dark"
    app_gui.DesktopMainWindow._render_account_card(None, boxes, item)
    assert label.call_count == count * 2


def test_widget_fetch_attaches_account_groups(monkeypatch):
    real_client = httpx.Client
    active = {"claude": {"accounts": [{"account_id": None, "windows": {}}]}}
    monkeypatch.setattr(widget.httpx, "Client", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=active))))
    monkeypatch.setattr(widget, "get_connected_ai_summary", lambda *_a, **_kw:
                        get_connected_ai_summary([AI], quota_overrides={"claude": []}))
    monkeypatch.setattr(widget, "list_accounts", lambda: deepcopy(ROWS))
    monkeypatch.setattr(widget, "account_quotas", lambda **_kw: [{**ROWS[0], "quota_windows": [WINDOW]}])
    monkeypatch.setattr(widget, "backend_headers", lambda: {})
    ui = SimpleNamespace(backend_url="http://testserver", _connected_ais=[AI], _fetching=True,
                         rate_updater=SimpleNamespace(rate=1400), config={}, _on_data=Mock(),
                         root=SimpleNamespace(after=lambda _delay, callback: callback()))
    widget.FloatingWidget._fetch_worker(ui)
    summary = ui._on_data.call_args.args[0]
    assert len(summary["items"][0]["accounts"]) == 3
    assert display.widget_entries(summary)[2]["label"] == "계정 미상"


def test_quota_view_does_not_refresh_the_collector_timeline(monkeypatch):
    monkeypatch.setattr(display.AccountRegistry, "__init__", lambda self: None)
    original_refresh = Mock(side_effect=AssertionError("must not observe accounts from UI"))
    monkeypatch.setattr(display.AccountRegistry, "refresh", original_refresh)
    view = display.AccountQuotaView(ROWS)
    assert view.refresh() == ROWS
    original_refresh.assert_not_called()


def test_manual_account_tools_are_visible_without_a_default_folder():
    ais = display.account_tools([], ROWS)
    assert len(ais) == 1 and ais[0]["key"] == "claude"
    assert ais[0]["plan_name"] == "요금제 확인 불가"
    assert display.account_tools(ais, ROWS) == ais


def test_key_rows_only_show_masks_and_join_bill_by_key_id(monkeypatch):
    texts = []
    monkeypatch.setattr(app_gui.theme, "font", lambda *_a, **_kw: ())
    monkeypatch.setattr(app_gui.ctk, "CTkFrame", lambda *_a, **_kw: Mock())
    monkeypatch.setattr(app_gui.ctk, "CTkButton", lambda *_a, **_kw: Mock())

    def label(*_a, **kwargs):
        texts.append(kwargs["text"])
        return Mock()

    def entry(*_a, **_kw):
        return Mock(insert=lambda _position, value: texts.append(value))

    monkeypatch.setattr(app_gui.ctk, "CTkLabel", label)
    monkeypatch.setattr(app_gui.ctk, "CTkEntry", entry)
    keys = [dict(provider="openai", key_id=k, label=k, masked=f"masked-{k}", status="connected",
                 key="test-only-raw-secret", source="stored") for k in ("id1", "id2")]
    monkeypatch.setattr(app_gui, "api_key_manager", Mock(list_keys=lambda: [dict(
        provider="openai", name="OpenAI", has_key=True, keys=keys)]))
    ui = SimpleNamespace(byok_keys_scroll=Mock(), krw_rate=1400, official_spend={"items": [dict(
        provider="openai", key_id="id1", amount=3, currency="USD", kind="spend", source_label="공식")],
        "openai_api": {"amount": 99}}, _rename_key_action=Mock(), _delete_key_action=Mock())
    ui.byok_keys_scroll.winfo_children.return_value = []
    app_gui.DesktopMainWindow._refresh_keys_list(ui)
    assert "masked-id1" in texts and "masked-id2" in texts
    assert any("4,200" in t for t in texts)
    assert "청구액 확인 불가" in texts
    assert not any("138,600" in t or "test-only-raw-secret" in t for t in texts)


def test_renamed_account_keeps_filter_id_and_stale_fetch_cannot_overwrite(monkeypatch):
    choices = display.account_options(ROWS)
    selected = next(label for label, aid in choices.items() if aid == A)
    renamed = [{**ROWS[0], "label": "변경된 라벨"}, ROWS[1]]
    ui = SimpleNamespace(account_rows=renamed, traces_data=[], account_choices=choices,
                         log_account_selection=selected, chart_account_selection="계정 미상",
                         log_account_menu=Mock(), chart_account_menu=Mock())
    app_gui.DesktopMainWindow._refresh_account_choices(ui)
    assert ui.account_choices[ui.log_account_selection] == A
    assert ui.log_account_selection.startswith("변경된 라벨")
    assert ui.chart_account_selection == "계정 미상"

    real_client = httpx.Client
    monkeypatch.setattr(app_gui.httpx, "Client", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={}))))
    monkeypatch.setattr(app_gui, "get_connected_ai_summary", lambda *_a, **_kw: {"items": []})
    monkeypatch.setattr(app_gui.accounts, "list_accounts", lambda: [])
    monkeypatch.setattr(app_gui, "api_key_manager", Mock(list_keys=lambda: []))
    pending = []
    ui.backend_url = "http://testserver"
    ui.official_spend = {}
    ui.after = lambda _delay, cb: pending.append(cb)
    ui._apply_data = Mock()
    ui._schedule_refresh = Mock()
    app_gui.DesktopMainWindow._fetch_worker(ui, [], {}, 1400)
    ui.log_account_selection = "전체"
    pending[0]()
    ui._apply_data.assert_not_called()
    ui._schedule_refresh.assert_called_once_with(0)
