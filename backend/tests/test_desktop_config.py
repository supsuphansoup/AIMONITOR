"""Atomic desktop settings and concurrent UI/rate patches, using synthetic files only."""
import json
import threading
from unittest.mock import Mock

import pytest

from desktop import config, rate_fetcher


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "desktop_config.json")
    monkeypatch.delenv("AIMONITOR_API_KEY", raising=False)


def test_replace_failure_preserves_old_config_and_reports_failure(monkeypatch):
    config.save_config({"widget_y": 999})
    original = config.CONFIG_FILE.read_bytes()
    monkeypatch.setattr(config.os, "replace", Mock(side_effect=OSError("synthetic disk error")))
    with pytest.raises(OSError):
        config.save_config({"widget_y": 60})
    assert config.CONFIG_FILE.read_bytes() == original
    assert list(config.CONFIG_DIR.iterdir()) == [config.CONFIG_FILE]


def test_concurrent_patches_preserve_server_key_and_unrelated_settings(monkeypatch):
    from backend.app.services import secret_store

    monkeypatch.setattr(secret_store, "seal", lambda key: ("test", "sealed:" + key))
    monkeypatch.setattr(secret_store, "unseal", lambda scheme, key: key.removeprefix("sealed:"))
    config.save_config({"api_key": "test-only-secret", "widget_y": 60})
    start = threading.Barrier(3)
    errors = []

    def patch(values):
        start.wait()
        try:
            config.update_config(values)
        except Exception as exc:
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=patch, args=(values,)) for values in
               ({"widget_y": 999}, {"krw_per_usd": 1400, "krw_rate_source": "test"})]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert not errors
    result = config.load_config()
    assert result["widget_y"] == 999 and result["krw_per_usd"] == 1400
    assert config.backend_headers(result) == {"X-API-Key": "test-only-secret"}


def test_rate_persistence_sends_only_rate_patch(monkeypatch):
    patch = Mock()
    monkeypatch.setattr(config, "update_config", patch, raising=False)
    updater = rate_fetcher.AutoRateUpdater(current_rate=1400, source="test")
    rate_fetcher._persist(updater)
    patch.assert_called_once_with({"krw_per_usd": 1400, "krw_rate_source": "test", "krw_rate_as_of": None})


def test_rate_update_reports_persistence_failure(monkeypatch):
    monkeypatch.setattr(config, "update_config", Mock(side_effect=OSError("synthetic disk error")))
    monkeypatch.setattr(rate_fetcher, "fetch_usd_krw_rate", lambda: (1400, "test", None))
    updater = rate_fetcher.AutoRateUpdater(current_rate=1300)
    assert updater.update_now() == 1400
    assert updater.persistence_failed is True


def test_large_invalid_setting_repairs_only_bad_field(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config.CONFIG_FILE.write_text(json.dumps({"copilot_monthly_premium_limit": 2 ** 2048,
                                            "widget_y": 999}), encoding="utf-8")
    result = config.load_config()
    assert result["copilot_monthly_premium_limit"] is None
    assert result["widget_y"] == 999


@pytest.mark.parametrize("key,value", [
    ("widget_x", "bad"), ("widget_y", []), ("poll_interval_ms", None), ("poll_interval_ms", -1),
    ("widget_opacity", 2), ("krw_per_usd", float("nan")), ("always_on_top", "false"),
    ("widget_mode", "invalid"), ("widget_items", [None]), ("hidden_ais", "claude"),
    ("copilot_monthly_premium_limit", -1), ("backend_url", None), ("api_key", []),
])
def test_invalid_setting_recovers_only_invalid_field(key, value):
    config.CONFIG_FILE.write_text(json.dumps({key: value, "claude_watch_enabled": False}), encoding="utf-8")
    loaded = config.load_config()
    assert loaded[key] == config.DEFAULT_CONFIG[key]
    assert loaded["claude_watch_enabled"] is False


def test_valid_negative_monitor_position_and_obsolete_keys():
    config.CONFIG_FILE.write_text(json.dumps({"widget_x": -1000, "widget_y": -300, "weekly_limits": {}}))
    loaded = config.load_config()
    assert loaded["widget_x"] == -1000 and loaded["widget_y"] == -300
    assert "weekly_limits" not in loaded


def test_unfinished_temp_file_is_not_read():
    config.save_config({"widget_y": 999})
    (config.CONFIG_DIR / "desktop_config.pending").write_text("{broken", encoding="utf-8")
    assert config.load_config()["widget_y"] == 999


def test_gui_save_failure_does_not_claim_success(monkeypatch):
    pytest.importorskip("customtkinter")
    pytest.importorskip("matplotlib")
    pytest.importorskip("pystray")
    from types import SimpleNamespace

    from desktop import app_gui

    monkeypatch.setattr(app_gui, "update_config", Mock(side_effect=OSError("synthetic disk error")))
    ui = SimpleNamespace(entry_copilot_limit=Mock(), lbl_status_msg=Mock(), refresh_data=Mock())
    ui.entry_copilot_limit.get.return_value = "300"
    app_gui.DesktopMainWindow._save_copilot_limit(ui)
    assert "못했습니다" in ui.lbl_status_msg.configure.call_args.kwargs["text"]
    ui.refresh_data.assert_not_called()
