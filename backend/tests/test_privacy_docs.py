"""Keep the Korean and English privacy inventories complete."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["privacy.md", "privacy.en.md"])
def privacy_document(request) -> str:
    return (ROOT / "docs" / request.param).read_text(encoding="utf-8")


def _section(document: str, *titles: str) -> str:
    for section in document.split("\n## ")[1:]:
        heading, _, body = section.partition("\n")
        if heading in titles:
            return body
    raise AssertionError(f"Missing privacy section: {titles}")


def test_privacy_database_location_covers_configured_storage(privacy_document):
    stored = _section(privacy_document, "저장하는 것", "Stored")
    for value in ("aimonitor.db", "DATABASE_URL", "/app/data/aimonitor.db", "aimonitor-data"):
        assert value in stored
    assert "기본" in stored or "default" in stored.lower()


def test_privacy_removal_covers_nondefault_storage(privacy_document):
    removal = _section(privacy_document, "지우기", "Removing everything")
    for value in ("DATABASE_URL", "-wal", "-shm", "-journal", "aimonitor-data", "backend_url"):
        assert value in removal
    assert "기본" in removal or "default" in removal.lower()
    assert "따로" in removal or "separately" in removal.lower()
    assert "백업" in removal or "backup" in removal.lower()


def test_privacy_read_inventory_covers_settings_and_path_environment(privacy_document):
    reads = _section(privacy_document, "이 PC에서 읽는 것", "Read on this PC")
    for value in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"):
        assert value in reads
    settings_row = next((line for line in reads.splitlines() if "`.env`" in line), "")
    for value in ("API_KEY", "DATABASE_URL", "desktop_config.json", "DPAPI"):
        assert value in settings_row
    assert "평문" in settings_row or "plain text" in settings_row.lower()


def test_privacy_stored_inventory_covers_watcher_lock(privacy_document):
    stored = _section(privacy_document, "저장하는 것", "Stored")
    lock_row = next((line for line in stored.splitlines() if "ai_watcher.lock" in line), "")
    assert "~/.aimonitor/ai_watcher.lock" in lock_row
    assert "비밀값" in lock_row or "secrets" in lock_row.lower()


def test_privacy_local_communication_covers_single_instance_control(privacy_document):
    sent = _section(privacy_document, "밖으로 보내는 곳 (전부)", "Sent off this PC (complete list)")
    for value in ("127.0.0.1:47821", "AIMONITOR:show", "AIMONITOR:ping", "AIMONITOR:ack:v1"):
        assert value in sent
