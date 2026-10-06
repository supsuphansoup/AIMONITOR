"""API Key Manager for BYOK (Bring Your Own Key) & Provider Balance Tracking."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from backend.app.core.timeutil import to_aware_utc, utc_now
from backend.app.services import secret_store
from backend.app.services.provider_spend import SpendService, month_start_utc

logger = logging.getLogger("aimonitor.api_key_manager")

KEYS_DIR = Path.home() / ".aimonitor"
KEYS_FILE = KEYS_DIR / "api_keys.json"

KNOWN_PROVIDERS = [
    {"key": "openai", "name": "OpenAI", "env_vars": ["OPENAI_API_KEY"], "placeholder": "sk-proj-..."},
    {"key": "anthropic", "name": "Anthropic", "env_vars": ["ANTHROPIC_API_KEY"], "placeholder": "sk-ant-..."},
    {
        "key": "gemini",
        "name": "Google Gemini",
        "env_vars": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "placeholder": "AIzaSy...",
    },
    {"key": "deepseek", "name": "DeepSeek", "env_vars": ["DEEPSEEK_API_KEY"], "placeholder": "sk-..."},
    {
        "key": "openrouter",
        "name": "OpenRouter",
        "env_vars": ["OPENROUTER_API_KEY"],
        "placeholder": "sk-or-...",
    },
    {"key": "groq", "name": "Groq", "env_vars": ["GROQ_API_KEY"], "placeholder": "gsk_..."},
    # Admin keys only read billing; they are what makes the official spend visible.
    {
        "key": "anthropic_admin",
        "name": "Anthropic 관리자 키 (공식 비용 조회)",
        "env_vars": ["ANTHROPIC_ADMIN_KEY"],
        "placeholder": "sk-ant-admin01-...",
    },
    {
        "key": "openai_admin",
        "name": "OpenAI 관리자 키 (공식 비용 조회)",
        "env_vars": ["OPENAI_ADMIN_KEY"],
        "placeholder": "sk-admin-...",
    },
]

ADMIN_PROVIDERS = {"anthropic_admin", "openai_admin"}


def mask_key(key: str) -> str:
    """Mask key preserving start and end characters."""
    if not key:
        return ""
    if len(key) <= 8:
        return "••••••••"
    prefix = key[:4]
    suffix = key[-4:]
    return f"{prefix}••••••••{suffix}"


class APIKeyManager:
    """Manages secure local storage of user-provided API keys."""

    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath or KEYS_FILE

    def _load_raw(self) -> Dict[str, Dict[str, Any]]:
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.warning("Could not parse API key storage: %s", type(e).__name__)
        return {}

    def _save_raw(self, data: Dict[str, Dict[str, Any]]) -> None:
        # Updating one provider must not re-write another provider's legacy
        # plaintext key, including files with both old and sealed fields.
        for entry in data.values():
            legacy = entry.pop("key", None)
            if legacy and not entry.get("sealed"):
                entry["scheme"], entry["sealed"] = secret_store.seal(str(legacy).strip())
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        secret_store.restrict_file(self.filepath)

    @staticmethod
    def _stored_secret(entry: Dict[str, Any]) -> Optional[str]:
        if entry.get("sealed"):
            return secret_store.unseal(entry.get("scheme", ""), entry["sealed"])
        # Files written by older versions kept the key in plain text.
        return entry.get("key") or None

    def migrate_plaintext(self) -> int:
        """Re-save keys from older plain-text files in sealed form. Returns how many."""
        raw = self._load_raw()
        moved = 0
        for entry in raw.values():
            if isinstance(entry, dict) and entry.get("key") and not entry.get("sealed"):
                entry["scheme"], entry["sealed"] = secret_store.seal(str(entry.pop("key")).strip())
                moved += 1
        if moved:
            self._save_raw(raw)
        return moved

    def get_registered_key(self, provider: str) -> Optional[str]:
        """Get the key for a provider if stored locally or in environment."""
        self.migrate_plaintext()
        raw = self._load_raw()
        if provider in raw:
            secret = self._stored_secret(raw[provider])
            if secret:
                return secret.strip()

        # Check environment variables
        for p_info in KNOWN_PROVIDERS:
            if p_info["key"] == provider:
                for ev in p_info["env_vars"]:
                    val = os.environ.get(ev)
                    if val and val.strip():
                        return val.strip()
        return None

    def list_keys(self) -> List[Dict[str, Any]]:
        """List all supported providers with registered/detected status and masked keys."""
        self.migrate_plaintext()
        raw = self._load_raw()
        results = []

        for p_info in KNOWN_PROVIDERS:
            prov = p_info["key"]
            stored_entry = raw.get(prov, {})
            stored_key = self._stored_secret(stored_entry) if stored_entry else None

            # Check environment variables for auto-detection
            detected_env = None
            detected_key = None
            for ev in p_info["env_vars"]:
                val = os.environ.get(ev)
                if val and val.strip():
                    detected_env = ev
                    detected_key = val.strip()
                    break

            active_key = stored_key or detected_key
            source = "stored" if stored_key else ("env" if detected_key else "none")

            results.append(
                {
                    "provider": prov,
                    "name": p_info["name"],
                    "placeholder": p_info["placeholder"],
                    "has_key": bool(active_key),
                    "masked_key": mask_key(active_key) if active_key else "",
                    "source": source,
                    "detected_env_var": detected_env,
                    "balance_info": stored_entry.get("balance_info"),
                    "status": stored_entry.get("status", "connected" if active_key else "unconfigured"),
                    "last_verified": stored_entry.get("last_verified"),
                    "protection": (
                        "Windows DPAPI 암호화"
                        if stored_entry.get("scheme") == secret_store.SCHEME_DPAPI
                        else "평문 (OS 암호화 미지원)"
                    )
                    if stored_key
                    else None,
                    "is_admin_key": prov in ADMIN_PROVIDERS,
                }
            )

        return results

    def set_key(self, provider: str, key_value: str) -> Dict[str, Any]:
        """Save a new or updated API key for a provider."""
        key_value = key_value.strip()
        if not key_value:
            raise ValueError("API key cannot be empty")

        if provider not in {p["key"] for p in KNOWN_PROVIDERS}:
            raise ValueError("Unknown provider")
        raw = self._load_raw()
        scheme, sealed = secret_store.seal(key_value)
        raw[provider] = {
            "scheme": scheme,
            "sealed": sealed,
            "status": "connected",
            "last_verified": None,
            "balance_info": None,
        }
        self._save_raw(raw)
        return {
            "provider": provider,
            "masked_key": mask_key(key_value),
            "status": "connected",
        }

    def delete_key(self, provider: str) -> bool:
        """Remove a stored API key."""
        raw = self._load_raw()
        if provider in raw:
            del raw[provider]
            self._save_raw(raw)
            return True
        return False

    def verify_key(self, provider: str) -> Dict[str, Any]:
        """Test the API key with the provider's official endpoint and retrieve balance/model status."""
        key = self.get_registered_key(provider)
        if not key:
            return {"valid": False, "error": "No API key found"}

        try:
            with httpx.Client(timeout=8.0) as client:
                if provider == "openrouter":
                    resp = client.get(
                        "https://openrouter.ai/api/v1/auth/key",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        usage = data.get("usage", 0.0)
                        limit = data.get("limit")
                        balance_str = f"사용: ${usage:.2f}" + (
                            f" / 한도: ${limit:.2f}" if limit else " (무제한)"
                        )
                        self._update_verification(provider, "valid", balance_str)
                        return {"valid": True, "balance": balance_str}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "deepseek":
                    resp = client.get(
                        "https://api.deepseek.com/user/balance",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        infos = data.get("balance_infos", [])
                        total_cny = sum(float(i.get("total_balance", 0)) for i in infos)
                        curr = infos[0].get("currency", "CNY") if infos else "CNY"
                        balance_str = f"잔여: {total_cny:.2f} {curr}"
                        self._update_verification(provider, "valid", balance_str)
                        return {"valid": True, "balance": balance_str}
                    elif resp.status_code in (401, 403):
                        return {"valid": False, "error": "인증 실패: 유효하지 않은 DeepSeek API 키"}
                    # Fallback to models endpoint if user endpoint not enabled
                    m_resp = client.get(
                        "https://api.deepseek.com/models", headers={"Authorization": f"Bearer {key}"}
                    )
                    if m_resp.status_code == 200:
                        self._update_verification(provider, "valid", "정상 연동됨")
                        return {"valid": True, "balance": "정상 연동됨"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "openai":
                    resp = client.get(
                        "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}
                    )
                    if resp.status_code == 200:
                        self._update_verification(provider, "valid", "정상 연동됨 (API 가용)")
                        return {"valid": True, "balance": "정상 연동됨 (API 가용)"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "anthropic":
                    resp = client.get(
                        "https://api.anthropic.com/v1/models",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    )
                    if resp.status_code == 200:
                        self._update_verification(provider, "valid", "정상 연동됨 (API 가용)")
                        return {"valid": True, "balance": "정상 연동됨 (API 가용)"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "gemini":
                    resp = client.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        headers={"x-goog-api-key": key},
                    )
                    if resp.status_code == 200:
                        self._update_verification(provider, "valid", "정상 연동됨 (Gemini 가용)")
                        return {"valid": True, "balance": "정상 연동됨 (Gemini 가용)"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "anthropic_admin":
                    resp = client.get(
                        "https://api.anthropic.com/v1/organizations/cost_report",
                        params={"starting_at": month_start_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 1},
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    )
                    if resp.status_code == 200:
                        self._update_verification(provider, "valid", "비용 리포트 조회 가능")
                        return {"valid": True, "balance": "비용 리포트 조회 가능"}
                    return {"valid": False, "error": f"HTTP {resp.status_code} (관리자 키가 필요합니다)"}

                elif provider == "openai_admin":
                    resp = client.get(
                        "https://api.openai.com/v1/organization/costs",
                        params={"start_time": int(month_start_utc().timestamp()), "limit": 1},
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if resp.status_code == 200:
                        self._update_verification(provider, "valid", "비용 조회 가능")
                        return {"valid": True, "balance": "비용 조회 가능"}
                    return {"valid": False, "error": f"HTTP {resp.status_code} (관리자 키가 필요합니다)"}

                elif provider == "groq":
                    resp = client.get(
                        "https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}
                    )
                    if resp.status_code == 200:
                        self._update_verification(provider, "valid", "정상 연동됨 (Groq 초고속 가용)")
                        return {"valid": True, "balance": "정상 연동됨"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                else:
                    return {"valid": True, "balance": "연동됨"}

        except Exception as e:
            logger.debug("Verification exception for %s: %s", provider, type(e).__name__)
            return {"valid": False, "error": f"연결 실패 ({type(e).__name__})"}

    def _update_verification(self, provider: str, status: str, balance_info: Optional[str]) -> None:
        raw = self._load_raw()
        if provider in raw:
            raw[provider]["status"] = status
            raw[provider]["balance_info"] = balance_info
            raw[provider]["last_verified"] = to_aware_utc(utc_now()).isoformat()
            self._save_raw(raw)


# Global singleton
api_key_manager = APIKeyManager()

spend_service = SpendService(api_key_manager.get_registered_key)
