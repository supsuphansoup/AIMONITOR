"""ACC-20: per-folder evidence and per-window billing, using only fake credentials."""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.core.timeutil import local_period_start_utc, to_aware_utc
from backend.app.routers import metrics
from backend.app.services.api_key_manager import spend_service
from desktop import ai_detector, config, context_manager, display
from desktop.accounts import AUTH_FIELDS, AccountRegistry, auth_info_for

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
SECRET = "fixture-only-secret"


@pytest.fixture(autouse=True)
def isolated_pc(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(context_manager, "utc_now", lambda: NOW, raising=False)
    monkeypatch.setattr(context_manager, "windows_for", lambda *args, **kwargs: [])
    monkeypatch.setattr(config, "load_config", lambda: {"krw_per_usd": 100})
    monkeypatch.setattr(spend_service, "spend_all", lambda keys: {})


def set_mode(path, tool, mode, identifier="one"):
    if tool == "claude_code":
        data = {"claudeAiOauth": {"subscriptionType": "pro", "accessToken": SECRET}}
        if mode == "api":
            data = {"anthropicApiKey": SECRET}
        elif mode == "both":
            data["anthropicApiKey"] = SECRET
        elif mode == "unknown":
            data = {}
        filename = ".credentials.json"
    else:
        data = {"account_id": identifier, "tokens": {"account_id": identifier, "id_token": {
            "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}}}}
        if mode == "api":
            data = {"account_id": identifier, "OPENAI_API_KEY": SECRET}
        elif mode == "both":
            data["OPENAI_API_KEY"] = SECRET
        elif mode == "unknown":
            data = {"account_id": identifier}
        filename = "auth.json"
    (path / filename).write_text(json.dumps(data), encoding="utf-8")


def profile(home, tool="claude_code", suffix="", mode="subscription", identifier="one"):
    path = home / ((".claude" if tool == "claude_code" else ".codex") + suffix)
    (path / ("projects" if tool == "claude_code" else "sessions")).mkdir(parents=True)
    if tool == "claude_code":
        (path / ".claude.json").write_text(json.dumps({"oauthAccount": {"accountUuid": identifier}}))
    set_mode(path, tool, mode, identifier)
    return path


def ai_for(tool, mode="subscription"):
    key = "claude" if tool == "claude_code" else "codex"
    plan = "Claude Pro" if key == "claude" else "ChatGPT Pro"
    if mode == "api":
        plan = "Anthropic API 종량제" if key == "claude" else "OpenAI API 종량제"
    return {"key": key, "plan_name": plan, "provider": "anthropic" if key == "claude" else "openai"}


def active_for(key, rows, costs):
    starts = {"5h": NOW - timedelta(hours=5), "today": to_aware_utc(local_period_start_utc(NOW)),
              "7d": NOW - timedelta(days=7), "month": to_aware_utc(local_period_start_utc(NOW, True))}
    accounts = [{"account_id": row["account_id"], "windows": {
        window: {"since": since.isoformat(), "requests": 1, "tokens": 100,
                 "cost_usd": cost, "cost_unknown": 0} for window, since in starts.items()
    }} for row, cost in zip(rows, costs, strict=True)]
    return {key: {"accounts": accounts, "windows": {
        window: {"since": since.isoformat(), "requests": len(accounts), "tokens": 100 * len(accounts),
                 "cost_usd": sum(costs), "cost_unknown": 0} for window, since in starts.items()
    }}}


def desktop_summary(ai, active, rows):
    summary = context_manager.get_connected_ai_summary([ai], active, krw_rate=100, now=NOW)
    return display.attach_accounts(summary, active, rows, [], 100)


@pytest.mark.parametrize("tool", ["claude_code", "codex"])
@pytest.mark.parametrize("modes", [("subscription", "api"), ("api", "subscription")])
def test_mixed_accounts_use_their_own_evidence_without_double_counting(tmp_path, tool, modes):
    profile(tmp_path, tool, mode=modes[0])
    profile(tmp_path, tool, "-work", modes[1], "two")
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    key = ai_for(tool)["key"]
    summary = desktop_summary(ai_for(tool, modes[0]), active_for(key, rows, [10, 3]), rows)
    item = summary["items"][0]
    assert item["cost_meaning"] == "mixed"
    assert "혼합" in item["billing_note"]
    for account, mode in zip(item["accounts"], modes, strict=True):
        assert account["cost_meaning"] == ("billed" if mode == "api" else "api_equivalent")
        text = display.account_usage_lines(account, item["cost_meaning"])[-1]
        assert ("API 환산" in text) == (mode == "subscription")
    assert summary["total"]["billed_month_usd"] == (3 if modes[0] == "subscription" else 10)
    assert summary["total"]["api_equivalent_month_usd"] == (10 if modes[0] == "subscription" else 3)
    assert summary["total"]["requests_today"] == 2


@pytest.mark.parametrize("tool", ["claude_code", "codex"])
def test_mode_change_is_persisted_and_only_affected_windows_are_unclassified(tmp_path, tool):
    path = profile(tmp_path, tool)
    registry = AccountRegistry(home=tmp_path, environ={})
    first = registry.refresh(NOW - timedelta(days=3))[0]
    set_mode(path, tool, "api")
    changed = NOW - timedelta(days=1)
    registry.refresh(changed)
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    assert rows[0]["account_id"] == first["account_id"]
    assert rows[0]["auth_mode"] == "api"
    assert rows[0]["auth_mode_since"] == changed.isoformat()
    assert rows[0]["auth_mode_changed_at"] == changed.isoformat()
    key = ai_for(tool)["key"]
    summary = desktop_summary(ai_for(tool), active_for(key, rows, [5]), rows)
    account = summary["items"][0]["accounts"][0]
    assert account["usage"]["today"]["cost_meaning"] == "billed"
    assert account["usage"]["month"]["cost_meaning"] == "unknown"
    assert "구분 미상" in display.account_usage_lines(account, "billed")[-1]
    assert summary["total"]["billed_today_usd"] == 5
    assert summary["total"]["billed_month_usd"] == 0
    assert summary["total"]["api_equivalent_month_usd"] == 0
    assert summary["total"]["unclassified_month_usd"] == 5
    entry = next(e for e in display.widget_entries(summary) if e["id"] == "total:unclassified_month")
    assert entry["default"] and "구분 미상" in entry["label"] and entry["value"] == "￦500"


@pytest.mark.parametrize("tool", ["claude_code", "codex"])
@pytest.mark.parametrize("credential", [None, "broken-json", "[]", "both"])
def test_missing_corrupt_or_conflicting_evidence_is_unknown(tmp_path, tool, credential):
    path = profile(tmp_path, tool)
    file = path / (".credentials.json" if tool == "claude_code" else "auth.json")
    if credential is None:
        file.unlink()
    elif credential == "both":
        set_mode(path, tool, "both")
    else:
        file.write_text(credential)
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    assert rows[0]["auth_mode"] == "unknown"
    key = ai_for(tool)["key"]
    summary = desktop_summary(ai_for(tool), active_for(key, rows, [4]), rows)
    assert summary["total"]["unclassified_month_usd"] == 4
    assert summary["total"]["billed_month_usd"] == summary["total"]["api_equivalent_month_usd"] == 0


@pytest.mark.parametrize("tool,environment", [("claude_code", "ANTHROPIC_API_KEY"),
                                               ("codex", "OPENAI_API_KEY")])
def test_environment_key_applies_only_to_default_without_oauth(tmp_path, tool, environment, monkeypatch):
    profile(tmp_path, tool, mode="unknown")
    profile(tmp_path, tool, "-work", "unknown", "two")
    monkeypatch.setenv(environment, SECRET)
    registry = AccountRegistry(home=tmp_path)
    rows = registry.refresh(NOW)
    assert [row["auth_mode"] for row in rows] == ["api", "unknown"]
    set_mode(Path(rows[0]["config_dir"]), tool, "subscription")
    assert registry.refresh(NOW + timedelta(minutes=1))[0]["auth_mode"] == "subscription"
    assert SECRET not in registry.accounts_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("tool,mode", [("claude_code", "subscription"), ("claude_code", "api"),
                                      ("codex", "subscription")])
def test_single_unchanged_account_keeps_legacy_numbers_and_text(tmp_path, tool, mode):
    profile(tmp_path, tool, mode=mode)
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    ai = ai_for(tool, mode)
    active = active_for(ai["key"], rows, [7])
    old = context_manager.get_connected_ai_summary([ai], active, krw_rate=100, now=NOW)
    result = desktop_summary(ai, active, rows)
    for field in ("cost_meaning", "billing_note", "plan_name", "usage"):
        assert result["items"][0][field] == old["items"][0][field]
    for field in old["total"]:
        assert result["total"][field] == old["total"][field]


@pytest.mark.parametrize("tool", ["claude_code", "codex"])
@pytest.mark.parametrize("modes", [("subscription", "api"), ("api", "subscription")])
def test_connected_api_uses_account_modes_and_exposes_no_private_evidence(
    client, tmp_path, monkeypatch, tool, modes,
):
    profile(tmp_path, tool, mode=modes[0])
    profile(tmp_path, tool, "-work", modes[1], "two")
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    ai = ai_for(tool, modes[0])
    active = active_for(ai["key"], rows, [10, 3])
    monkeypatch.setattr(ai_detector, "detect_connected_ais", lambda: [ai])
    monkeypatch.setattr(metrics.MetricAggregator, "get_active_contexts", lambda **kwargs: deepcopy(active))
    monkeypatch.setattr(metrics, "get_account_quota_windows", lambda _: {})
    before = AccountRegistry(home=tmp_path).accounts_file.read_bytes()
    response = client.get("/api/v1/metrics/connected_ais")
    assert response.status_code == 200
    body = response.json()
    assert body["total"]["billed_month_usd"] == (3 if modes[0] == "subscription" else 10)
    assert body["total"]["api_equivalent_month_usd"] == (10 if modes[0] == "subscription" else 3)
    assert [a["auth_mode"] for a in body["items"][0]["accounts"]] == list(modes)
    assert body["items"][0]["cost_meaning"] == "mixed"
    for private in (SECRET, str(tmp_path), "one", "two", "accessToken", "OPENAI_API_KEY"):
        assert private not in response.text
    assert AccountRegistry(home=tmp_path).accounts_file.read_bytes() == before


@pytest.mark.parametrize("tool", ["claude_code", "codex"])
@pytest.mark.parametrize("value", [None, "", " \t "])
def test_empty_key_fields_do_not_conflict_with_oauth(tmp_path, tool, value):
    path = profile(tmp_path, tool)
    file = path / (".credentials.json" if tool == "claude_code" else "auth.json")
    data = json.loads(file.read_text(encoding="utf-8"))
    data["anthropicApiKey" if tool == "claude_code" else "OPENAI_API_KEY"] = value
    file.write_text(json.dumps(data), encoding="utf-8")
    assert auth_info_for(tool, path, tmp_path, {})["auth_mode"] == "subscription"
    assert AccountRegistry(home=tmp_path, environ={}).refresh(NOW)[0]["auth_mode"] == "subscription"


@pytest.mark.parametrize("tool", ["claude_code", "codex"])
def test_unreadable_auth_is_unknown_without_logging_exception_details(tmp_path, monkeypatch, caplog, tool):
    path = profile(tmp_path, tool)
    filename = ".credentials.json" if tool == "claude_code" else "auth.json"
    original = Path.read_text

    def deny(file, *args, **kwargs):
        if file.name == filename:
            raise PermissionError(SECRET)
        return original(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny)
    assert auth_info_for(tool, path, tmp_path, {})["auth_mode"] == "unknown"
    assert SECRET not in caplog.text


@pytest.mark.parametrize("tool", ["claude_code", "codex"])
def test_old_registry_migrates_without_inventing_a_mode_change(tmp_path, tool):
    profile(tmp_path, tool)
    registry = AccountRegistry(home=tmp_path, environ={})
    old_rows = registry.refresh(NOW - timedelta(days=1))
    for row in old_rows:
        for field in AUTH_FIELDS:
            row.pop(field, None)
    registry.accounts_file.write_text(json.dumps(old_rows), encoding="utf-8")
    rows = registry.refresh(NOW)
    assert rows[0]["auth_mode"] == "subscription"
    assert rows[0]["auth_mode_since"] == NOW.isoformat()
    assert rows[0]["auth_mode_changed_at"] is None
    key = ai_for(tool)["key"]
    summary = desktop_summary(ai_for(tool), active_for(key, rows, [2]), rows)
    assert summary["total"]["api_equivalent_month_usd"] == 2
    assert summary["total"]["unclassified_month_usd"] == 0


@pytest.mark.parametrize("tool,mode", [("claude_code", "subscription"), ("claude_code", "api"),
                                      ("codex", "subscription")])
def test_same_mode_multiple_accounts_keep_legacy_tool_totals(tmp_path, tool, mode):
    profile(tmp_path, tool, mode=mode)
    profile(tmp_path, tool, "-work", mode, "two")
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    ai = ai_for(tool, mode)
    active = active_for(ai["key"], rows, [2, 7])
    old = context_manager.get_connected_ai_summary([ai], active, krw_rate=100, now=NOW)
    summary = desktop_summary(ai, active, rows)
    for field in ("cost_meaning", "billing_note", "plan_name", "usage"):
        assert summary["items"][0][field] == old["items"][0][field]
    assert summary["total"] == old["total"]


def test_mixed_accounts_preserve_other_billed_tools_and_no_account_double_counting(tmp_path):
    profile(tmp_path)
    profile(tmp_path, suffix="-work", mode="api", identifier="two")
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    active = active_for("claude", rows, [10, 3])
    active["openai_api"] = deepcopy(active_for("openai_api", rows[:1], [5])["openai_api"])
    ais = [ai_for("claude_code"), {"key": "openai_api", "plan_name": "API 종량제"}]
    summary = context_manager.get_connected_ai_summary(ais, active, krw_rate=100)
    display.attach_accounts(summary, active, rows, [], 100)
    assert summary["total"]["billed_month_usd"] == 8
    assert summary["total"]["api_equivalent_month_usd"] == 10
    assert summary["total"]["requests_today"] == 3
    assert summary["total"]["tokens_today"] == 300


@pytest.mark.parametrize("tool", ["claude_code", "codex"])
def test_same_account_conflicting_folders_are_unknown_and_share_history(tmp_path, tool):
    profile(tmp_path, tool)
    second = profile(tmp_path, tool, "-work")
    registry = AccountRegistry(home=tmp_path, environ={})
    registry.refresh(NOW - timedelta(days=2))
    set_mode(second, tool, "api")
    rows = registry.refresh(NOW - timedelta(days=1))
    assert rows[0]["account_id"] == rows[1]["account_id"]
    assert [row["auth_mode"] for row in rows] == ["unknown", "unknown"]
    assert rows[0]["auth_mode_changed_at"] == rows[1]["auth_mode_changed_at"]
    key = ai_for(tool)["key"]
    summary = desktop_summary(ai_for(tool), active_for(key, rows[:1], [9]), rows)
    assert len(summary["items"][0]["accounts"]) == 1
    assert summary["total"]["unclassified_month_usd"] == 9


@pytest.mark.parametrize("window", ["today", "month"])
@pytest.mark.parametrize("delta,expected", [(timedelta(microseconds=-1), "billed"),
                                           (timedelta(0), "unknown")])
def test_mode_change_at_local_period_boundary_is_inclusive(tmp_path, window, delta, expected):
    path = profile(tmp_path)
    registry = AccountRegistry(home=tmp_path, environ={})
    registry.refresh(NOW - timedelta(days=45))
    changed = to_aware_utc(local_period_start_utc(NOW, month_start=window == "month")) + delta
    set_mode(path, "claude_code", "api")
    rows = registry.refresh(changed)
    summary = desktop_summary(ai_for("claude_code"), active_for("claude", rows, [8]), rows)
    assert summary["items"][0]["accounts"][0]["usage"][window]["cost_meaning"] == expected


def test_unknown_billing_and_unknown_price_are_separate_and_visible(tmp_path):
    profile(tmp_path, mode="unknown")
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    active = active_for("claude", rows, [0])
    for group in [active["claude"], *active["claude"]["accounts"]]:
        for block in group["windows"].values():
            block["cost_unknown"] = 1
    summary = desktop_summary(ai_for("claude_code"), active, rows)
    total = summary["total"]
    assert total["unclassified_month_requests"] == total["unclassified_unknown_month"] == 1
    assert total["billed_unknown_month"] == 0
    assert display.unclassified_total_text(total) == "계산 불가"
    entry = next(e for e in display.widget_entries(summary) if e["id"] == "total:unclassified_month")
    assert entry["value"] == "계산 불가" and entry["default"]


def test_different_subscription_plans_preserve_the_legacy_tool_note(tmp_path):
    profile(tmp_path)
    second = profile(tmp_path, suffix="-work", identifier="two")
    file = second / ".credentials.json"
    data = json.loads(file.read_text(encoding="utf-8"))
    data["claudeAiOauth"]["subscriptionType"] = "max"
    file.write_text(json.dumps(data), encoding="utf-8")
    rows = AccountRegistry(home=tmp_path, environ={}).refresh(NOW)
    ai = ai_for("claude_code")
    active = active_for("claude", rows, [2, 7])
    old = context_manager.get_connected_ai_summary([ai], active, krw_rate=100)
    summary = desktop_summary(ai, active, rows)
    assert summary["items"][0]["billing_note"] == old["items"][0]["billing_note"]
    assert summary["items"][0]["accounts"][1]["plan_name"] == "Claude Max"


def test_unobserved_change_is_read_only_and_unclassified_until_collector_records_it(tmp_path):
    path = profile(tmp_path)
    registry = AccountRegistry(home=tmp_path, environ={})
    rows = registry.refresh(NOW - timedelta(days=1))
    before = registry.accounts_file.read_bytes(), registry.timeline_file.read_bytes()
    set_mode(path, "claude_code", "api")
    summary = desktop_summary(ai_for("claude_code"), active_for("claude", rows, [6]), rows)
    assert summary["total"]["unclassified_month_usd"] == summary["total"]["unclassified_today_usd"] == 6
    assert (registry.accounts_file.read_bytes(), registry.timeline_file.read_bytes()) == before
    observed = registry.refresh(NOW + timedelta(minutes=1))[0]
    assert observed["auth_mode_changed_at"] == (NOW + timedelta(minutes=1)).isoformat()
