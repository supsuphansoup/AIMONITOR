"""Router for managing user API keys and provider balance verification."""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from backend.app.core.security import require_read_auth, require_write_auth
from backend.app.services.api_key_manager import api_key_manager, spend_service


class SecretRoute(APIRoute):
    """Validation errors must not echo a submitted secret in Pydantic input."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(status_code=422, detail="Invalid key request") from None

        return safe_handler


router = APIRouter(prefix="/keys", tags=["API Keys"], route_class=SecretRoute)


class KeyUpdateRequest(BaseModel):
    provider: str = Field(..., description="Provider key name (e.g. openai, deepseek, openrouter)")
    key: str = Field(..., description="API key; stored sealed, never returned")
    label: str = Field(default="기본", min_length=1, max_length=40)


class KeyLabelRequest(BaseModel):
    label: str = Field(min_length=1, max_length=40)


@router.get("", response_model=List[Dict[str, Any]], dependencies=[Depends(require_read_auth)])
def list_keys() -> List[Dict[str, Any]]:
    """List supported API key providers with their masked key and verification status."""
    return api_key_manager.list_keys()


@router.get("/spend", response_model=Dict[str, Any], dependencies=[Depends(require_read_auth)])
def get_spend(force: bool = Query(False, description="Bypass the 15-minute cache")) -> Dict[str, Any]:
    """Provider-reported spend or balance for every API card that has a key.

    Keyed by desktop card key (`openai_api`, `anthropic_api`, ...). A provider
    with no billing endpoint for its key reports kind `unavailable`.
    """
    registered = {k["provider"] for k in api_key_manager.list_keys() if k["has_key"]}
    cards = {f"{p}_api" for p in registered if not p.endswith("_admin")}
    cards |= {p.replace("_admin", "_api") for p in registered if p.endswith("_admin")}
    result = spend_service.spend_all(sorted(cards), force=force)
    items = []
    for provider in sorted(registered):
        for entry in api_key_manager.list_keys(provider):
            if not entry["has_key"]:
                continue
            secret = api_key_manager.get_key(provider, entry["key_id"])
            spend = spend_service.spend_for_key(provider, entry["key_id"], secret, force=force)
            items.append({**spend, "provider": provider, "key_id": entry["key_id"], "label": entry["label"]})
    result["items"] = items
    return result


@router.post(
    "",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_write_auth)],
)
def set_key(payload: KeyUpdateRequest) -> Dict[str, Any]:
    """Register or update an API key for a provider."""
    provider = payload.provider.lower().strip()
    try:
        res = api_key_manager.add_key(provider=provider, key_value=payload.key, label=payload.label)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid provider, key, or storage unavailable"
        ) from None
    # Adding a second key must not verify a different default key by accident.
    return res


@router.patch("/{provider}/{key_id}", dependencies=[Depends(require_write_auth)])
def update_label(
    provider: str, payload: KeyLabelRequest,
    key_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,32}$"),
) -> Dict[str, Any]:
    try:
        result = api_key_manager.update_label(provider.lower().strip(), key_id, payload.label)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid key label or storage unavailable") from None
    if result is None:
        raise HTTPException(status_code=404, detail="No registered key found")
    return result


@router.delete("/{provider}/{key_id}", dependencies=[Depends(require_write_auth)])
def delete_individual_key(
    provider: str, key_id: str = Path(pattern=r"^[A-Za-z0-9_-]{1,32}$"),
) -> Dict[str, Any]:
    if not api_key_manager.delete_key(provider.lower().strip(), key_id):
        raise HTTPException(status_code=404, detail="No registered key found")
    return {"provider": provider, "key_id": key_id, "deleted": True}


@router.delete("/{provider}", response_model=Dict[str, Any], dependencies=[Depends(require_write_auth)])
def delete_key(provider: str) -> Dict[str, Any]:
    """Remove a registered API key."""
    deleted = api_key_manager.delete_key(provider.lower().strip())
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No registered key found")
    return {"provider": provider, "deleted": True}


@router.post("/{provider}/verify", response_model=Dict[str, Any], dependencies=[Depends(require_write_auth)])
def verify_key(provider: str) -> Dict[str, Any]:
    """Verify an API key against provider official API and retrieve balance or model status."""
    return api_key_manager.verify_key(provider.lower().strip())
