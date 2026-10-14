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
