"""Multiple-account cards retain the same quota provenance as single-account rows."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("customtkinter")
pytest.importorskip("matplotlib")

from desktop import app_gui, display


@pytest.mark.parametrize("remaining", [60, None])
def test_multiple_account_quota_cards_keep_stale_partial_source_and_reset(monkeypatch, remaining):
    labels = []
    monkeypatch.setattr(app_gui.ctk, "CTkFrame", lambda *_args, **_kwargs: Mock())
    monkeypatch.setattr(app_gui.ctk, "CTkLabel", lambda *_args, **kwargs: labels.append(kwargs["text"]) or Mock())
    monkeypatch.setattr(app_gui.theme, "font", lambda *_args, **_kwargs: ())
    quota = {"id": "5h", "label": "5시간", "remaining_pct": remaining, "source": "official",
             "source_label": "공식 API", "stale": True, "partial": True, "as_of": "2026-10-10T10:00:00Z",
             "resets_at": "2020-01-01T00:00:00Z", "note": "최근 조회 실패"}
    group = {"label": "합성 계정", "quota_windows": [quota], "usage": {}}
    item = {"accounts": [group, deepcopy(group)], "cost_meaning": "api_equivalent"}
    boxes = {key: Mock() for key in ("account_box", "quota_box", "ctx_box", "cost_box")}
    boxes["account_box"].winfo_children.return_value = []
    ui = SimpleNamespace()
    assert app_gui.DesktopMainWindow._render_account_card(ui, boxes, item)
    headline, _color, detail = display.window_text(quota)
    assert any(headline in text for text in labels)
    assert any(detail in text for text in labels), labels
    assert "일부 데이터" in detail and "오래됨" in detail and "공식 API" in detail
    assert "기준" in detail and "최근 조회 실패" in detail
    assert "리셋" in detail
