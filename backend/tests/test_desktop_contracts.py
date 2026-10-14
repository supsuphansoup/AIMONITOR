"""Cross-feature UI contracts with synthetic metadata and no GUI/network access."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("customtkinter")
from desktop import app_gui, context_manager, display


def capture_widgets(monkeypatch):
    labels, buttons = [], []
    monkeypatch.setattr(app_gui.theme, "font", lambda *_a, **_kw: ())
    monkeypatch.setattr(app_gui.ctk, "CTkFrame", lambda *_a, **_kw: Mock())
    monkeypatch.setattr(app_gui.ctk, "CTkEntry", lambda *_a, **_kw: Mock())
    def label(*_a, **kw):
        labels.append(kw.get("text", ""))
        return Mock()
    def button(*_a, **kw):
        buttons.append(kw)
        result = Mock()
        result.configure.side_effect = lambda **changes: kw.update(changes)
        return result
    monkeypatch.setattr(app_gui.ctk, "CTkLabel", label)
    monkeypatch.setattr(app_gui.ctk, "CTkButton", button)
    return labels, buttons


@pytest.mark.parametrize("status,expected", [
    ("valid", "유효"), ("invalid", "인증 실패"),
    ("verification_unavailable", "확인 불가"), ("storage_error", "저장소 확인 불가"),
    ("unreadable", "복호화 불가"), ("connected", "미검증"),
    ("unexpected-test-secret", "알 수 없음"),
])
def test_key_rows_map_status_and_offer_individual_verification(monkeypatch, status, expected):
    labels, buttons = capture_widgets(monkeypatch)
    key = {"provider": "openai", "key_id": "synthetic-id", "label": "실험",
           "masked": "sk-***test", "status": status, "has_key": True, "source": "stored"}
    monkeypatch.setattr(app_gui, "api_key_manager", Mock(list_keys=lambda: [
        {"name": "OpenAI", "keys": [key]}]))
    ui = SimpleNamespace(byok_keys_scroll=Mock(), official_spend={}, krw_rate=1400,
                         _verify_key_action=Mock())
    ui.byok_keys_scroll.winfo_children.return_value = []
    app_gui.DesktopMainWindow._refresh_keys_list(ui)
    assert expected in labels
    assert "unexpected-test-secret" not in labels
    verify = next(b for b in buttons if b["text"] == "검증")
    verify["command"]()
    assert ui._verify_key_action.call_args.args[:2] == ("openai", "synthetic-id")


@pytest.mark.parametrize("result,expected", [
    ({"valid": True, "balance": "untrusted-test-secret"}, "유효"),
    ({"valid": False, "error": "HTTP 401 untrusted-test-secret"}, "인증 실패"),
    ({"valid": False, "error": "HTTP 503 untrusted-test-secret"}, "확인 불가"),
    (RuntimeError("untrusted-test-secret"), "확인 불가"),
])
def test_key_verification_runs_in_worker_and_updates_only_safe_status(monkeypatch, result, expected):
    targets, pending = [], []
    manager = Mock()
    if isinstance(result, Exception):
        manager.verify_key.side_effect = result
    else:
        manager.verify_key.return_value = result
    monkeypatch.setattr(app_gui, "api_key_manager", manager)
    monkeypatch.setattr(app_gui.threading, "Thread", lambda **kw: targets.append(kw["target"]) or Mock())
    button, status = Mock(), Mock()
    ui = SimpleNamespace(after=lambda _delay, callback: pending.append(callback))
    app_gui.DesktopMainWindow._verify_key_action(ui, "openai", "synthetic-id", button, status)
    button.configure.assert_called_with(state="disabled")
    manager.verify_key.assert_not_called()
    targets[0]()
    manager.verify_key.assert_called_once_with("openai", "synthetic-id")
    status.configure.assert_called_once_with(text="검증 중…")
    pending[0]()
    assert status.configure.call_args.kwargs["text"] == expected
    button.configure.assert_called_with(state="normal")
    assert "untrusted-test-secret" not in str(status.configure.call_args_list)


def test_diagnostic_unavailable_is_distinct_from_disconnected(monkeypatch):
    labels, _ = capture_widgets(monkeypatch)
    monkeypatch.setattr(app_gui, "scan_all_ai_status", lambda: {
        "claude": {"is_connected": None, "detection_status": "unavailable",
                   "display_name": "Claude", "plan_name": "감지 불가", "detail": "읽기 실패"}})
    ui = SimpleNamespace(diag_frame=Mock())
    ui.diag_frame.winfo_children.return_value = []
    app_gui.DesktopMainWindow._refresh_diagnostic_table(ui)
    assert any("감지 불가" in text and "[" not in text for text in labels)
    assert not any("미연동" in text for text in labels)


def test_custom_folder_only_tool_does_not_read_default_login_quota(monkeypatch):
    windows = Mock(return_value=[])
    monkeypatch.setattr(context_manager, "windows_for", windows)
    ai = {"key": "claude", "use_default_quota": False}
    assert context_manager.get_connected_ai_summary([ai])["items"][0]["quota_windows"] == []
    windows.assert_not_called()
    ai.pop("use_default_quota")
    context_manager.get_connected_ai_summary([ai])
    windows.assert_called_once()


@pytest.mark.parametrize("account_id", [None, "copilot:synthetic-id"])
def test_copilot_empty_account_windows_preserve_tool_counter(account_id):
    quota = {"id": "premium", "label": "프리미엄", "remaining_pct": 97.7, "source": "user_limit"}
    summary = {"items": [{"key": "copilot", "quota_windows": [quota]}]}
    active = {"copilot": {"accounts": [{"account_id": account_id}]}}
    display.attach_accounts(summary, active, [], [], 1400)
    assert summary["items"][0]["quota_windows"] == [quota]
    assert display.widget_entries(summary)[0]["value"] == "98% 남음"
    if account_id is not None:
        assert not summary["items"][0]["accounts"][0]["quota_windows"]


def test_partial_unavailable_quota_keeps_both_qualifiers():
    headline, _color, detail = display.window_text({"remaining_pct": None, "partial": True})
    assert headline == "확인 불가"
    assert "일부 데이터" in detail


@pytest.mark.parametrize("period,expected", [
    ("2026-10-01T00:00:00+00:00", "이번 달"),
    ("2026-09-01T00:00:00+00:00", "2026-09"),
    ("2026-10-02T00:00:00+00:00", "2026-10"),
    (None, "기간 확인 불가"),
])
def test_official_spend_month_claim_uses_utc_period(period, expected):
    spend = {"kind": "spend", "amount": 1.0, "currency": "USD", "period_start": period,
             "source_label": "OpenAI 공식 비용 (이번 달)"}
    text = display.spend_text(spend, 1400, now=datetime(2026, 10, 14, tzinfo=timezone.utc))
    assert expected in text
    if expected != "이번 달":
        assert "이번 달" not in text
