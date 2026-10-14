"""Keep unreadable server credentials through unrelated settings writes."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.services import secret_store
from desktop import config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "desktop_config.json")
    monkeypatch.delenv("AIMONITOR_API_KEY", raising=False)


@pytest.mark.parametrize("write", ["patch", "snapshot", "partial_snapshot"])
@pytest.mark.parametrize("real_dpapi", [False, True])
def test_unreadable_key_survives_unrelated_writes_and_recovers(monkeypatch, write, real_dpapi):
    if real_dpapi:
        if secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI:
            pytest.skip("Windows DPAPI")
    else:
        monkeypatch.setattr(secret_store, "seal", lambda key: ("test", "sealed:" + key))
        monkeypatch.setattr(secret_store, "unseal", lambda _scheme, value: value.removeprefix("sealed:"))
    config.save_config({"api_key": "test-only-server-key", "widget_y": 60})
    original = json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))
    recover = secret_store.unseal
    monkeypatch.setattr(secret_store, "unseal", lambda *_args: None)
    settings = config.load_config()
    assert config.backend_headers(settings) == {}
    if write == "patch":
        config.update_config({"krw_per_usd": 1450})
        config.update_config(lambda latest: latest.update(widget_y=999))
    elif write == "snapshot":
        settings.update(krw_per_usd=1450, widget_y=999)
        config.save_config(settings)
    else:
        config.save_config({"krw_per_usd": 1450, "widget_y": 999})
    stored = json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))
    assert stored.get("api_key_sealed") == original["api_key_sealed"]
    assert stored.get("api_key_scheme") == original["api_key_scheme"]
    assert "api_key" not in stored and "api_key_unreadable" not in stored
    monkeypatch.setattr(secret_store, "unseal", recover)
    assert config.backend_headers() == {"X-API-Key": "test-only-server-key"}
    assert config.load_config()["widget_y"] == 999
    assert config.load_config()["krw_per_usd"] == 1450


@pytest.mark.parametrize("replacement", [None, "test-only-replacement-key"])
def test_explicit_key_delete_or_replacement_changes_unreadable_ciphertext(monkeypatch, replacement):
    monkeypatch.setattr(secret_store, "seal", lambda key: ("test", "sealed:" + key))
    monkeypatch.setattr(secret_store, "unseal", lambda *_args: None)
    config.save_config({"api_key": "test-only-original-key"})
    config.update_config({"api_key": replacement})
    stored = json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))
    if replacement is None:
        assert "api_key_sealed" not in stored and "api_key_scheme" not in stored
    else:
        assert stored["api_key_sealed"] == "sealed:" + replacement
    monkeypatch.setattr(secret_store, "unseal", lambda _scheme, value: value.removeprefix("sealed:"))
    assert config.backend_headers() == ({"X-API-Key": replacement} if replacement else {})


def test_unreadable_key_status_is_distinct_and_sends_no_header(monkeypatch):
    config.CONFIG_FILE.write_text(json.dumps({"api_key_scheme": "unknown", "api_key_sealed": "fake"}))
    assert config.load_config().get("api_key_unreadable") is True
    monkeypatch.setenv("AIMONITOR_API_KEY", "test-only-environment-key")
    assert config.backend_headers() == {}
    config.update_config({"api_key": None})
    assert config.load_config().get("api_key_unreadable") is False


def test_server_key_settings_warning_and_explicit_actions(monkeypatch):
    pytest.importorskip("customtkinter")
    pytest.importorskip("matplotlib")
    from desktop import app_gui

    monkeypatch.setattr(app_gui, "load_config", lambda: {"api_key_unreadable": True})
    ui = SimpleNamespace(lbl_server_key_status=Mock(), entry_server_key=Mock(), lbl_status_msg=Mock(),
                         _refresh_server_key_status=Mock(), refresh_data=Mock())
    app_gui.DesktopMainWindow._refresh_server_key_status(ui)
    assert ui.lbl_server_key_status.configure.call_args.kwargs["text"] == (
        "저장된 서버 인증 키를 이 Windows 계정에서 읽을 수 없습니다. 다시 입력하세요.")
    patch = Mock(return_value={})
    monkeypatch.setattr(app_gui, "update_config", patch)
    ui.entry_server_key.get.return_value = " test-only-server-key "
    app_gui.DesktopMainWindow._save_server_api_key(ui)
    patch.assert_called_once_with({"api_key": "test-only-server-key"})
    ui.entry_server_key.delete.assert_called_once_with(0, "end")
    app_gui.DesktopMainWindow._save_server_api_key(ui, clear=True)
    assert patch.call_args.args == ({"api_key": None},)
    calls = patch.call_count
    ui.entry_server_key.get.return_value = " "
    app_gui.DesktopMainWindow._save_server_api_key(ui)
    assert patch.call_count == calls
    ui.entry_server_key.get.return_value = "test-only-server-key"
    patch.side_effect = OSError("test-only-server-key raw exception")
    app_gui.DesktopMainWindow._save_server_api_key(ui)
    assert ui.lbl_status_msg.configure.call_args.kwargs["text"] == "서버 인증 키를 저장하지 못했습니다."
