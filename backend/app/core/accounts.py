"""Opaque account identifiers shared by ingestion and query validation."""

from typing import Optional

from fastapi import HTTPException

ACCOUNT_ID_PATTERN = r"^(claude_code|codex|antigravity|copilot|cursor|api):[A-Za-z0-9:_-]+$"
ACCOUNT_ID_MAX_LENGTH = 64


def validate_account_filter(account_id: Optional[str], account_unknown: bool) -> None:
    if account_id is not None and account_unknown:
        raise HTTPException(status_code=422, detail=[{
            "loc": ["query", "account_unknown"], "type": "value_error",
            "msg": "account_id and account_unknown=true are mutually exclusive",
        }])
