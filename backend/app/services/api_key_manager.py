"""API Key Manager for BYOK (Bring Your Own Key) & Provider Balance Tracking."""

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from backend.app.core.timeutil import to_aware_utc, utc_now
from backend.app.services import secret_store
from backend.app.services.provider_spend import SpendService, _amount, month_start_utc

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
MAX_KEY_LENGTH = 4096


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
        self._lock = threading.RLock()
        self._transaction_depth = 0
        self._env_verification = {}

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._transaction_depth:
                yield
                return
            try:
                with secret_store.lock_file(self.filepath.with_name(self.filepath.name + ".lock")):
                    self._transaction_depth += 1
                    try:
                        yield
                    finally:
                        self._transaction_depth -= 1
            except OSError:
                raise secret_store.StorageError("Key storage unavailable") from None

    @staticmethod
    def _validate_store(data):
        if not isinstance(data, dict):
            raise secret_store.StorageError("Invalid key storage")
        for entries in data.values():
            if not isinstance(entries, dict):
                raise secret_store.StorageError("Invalid key storage")
            legacy = any(field in entries for field in ("key", "sealed", "scheme"))
            for entry in ([entries] if legacy else entries.values()):
                if not isinstance(entry, dict):
                    raise secret_store.StorageError("Invalid key storage")
                for field in ("key", "sealed", "scheme", "label", "status", "last_verified", "balance_info"):
                    nullable = field in ("last_verified", "balance_info") and entry.get(field) is None
                    if field in entry and not isinstance(entry[field], str) and not nullable:
                        raise secret_store.StorageError("Invalid key storage")
        return data

    def _load_raw(self) -> Dict[str, Dict[str, Any]]:
        try:
            return self._validate_store(json.loads(self.filepath.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, ValueError, RecursionError):
            raise secret_store.StorageError("Invalid or unavailable key storage") from None

    def _read_store(self):
        if secret_store.AVAILABLE_SCHEME == "unavailable":
            raw = self._load_raw()
            return {provider: ({"default": {**entries, "label": "기본"}}
                               if any(f in entries for f in ("key", "sealed", "scheme")) else entries)
                    for provider, entries in raw.items()}
        with self._transaction():
            self.migrate_plaintext()
            return self._load_raw()

    def _save_raw(self, data: Dict[str, Dict[str, Any]]) -> None:
        if secret_store.AVAILABLE_SCHEME == "unavailable":
            raise secret_store.StorageError("Encrypted key storage is unavailable")
        for provider, entries in list(data.items()):
            if any(field in entries for field in ("key", "sealed", "scheme")):
                entries = {"default": {**entries, "label": "기본"}}
                data[provider] = entries
            for entry in entries.values():
                legacy = entry.pop("key", None)
                if legacy and not entry.get("sealed"):
                    entry["scheme"], entry["sealed"] = secret_store.seal(str(legacy).strip())
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=self.filepath.name + ".", dir=self.filepath.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                secret_store.restrict_file(temporary)
                output.write(json.dumps(data, indent=2, ensure_ascii=False))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.filepath)
            secret_store.restrict_file(self.filepath)
        except OSError:
            raise secret_store.StorageError("Key storage write failed") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _stored_secret(entry: Dict[str, Any]) -> Optional[str]:
        if secret_store.AVAILABLE_SCHEME == "unavailable":
            return None
        if entry.get("sealed"):
            return secret_store.unseal(entry.get("scheme", ""), entry["sealed"])
        return entry.get("key") or None

    def migrate_plaintext(self) -> int:
        """Seal legacy secrets and migrate single-key entries, without inventing accounts."""
        if secret_store.AVAILABLE_SCHEME == "unavailable":
            return 0
        with self._transaction():
            raw = self._load_raw()
            moved, changed = 0, False
            for provider, entries in list(raw.items()):
                if any(field in entries for field in ("key", "sealed", "scheme")):
                    entries = {"default": {**entries, "label": "기본"}}
                    raw[provider] = entries
                    changed = True
                for entry in entries.values():
                    legacy = entry.pop("key", None)
                    if legacy:
                        changed = True
                        if not entry.get("sealed"):
                            entry["scheme"], entry["sealed"] = secret_store.seal(str(legacy).strip())
                            moved += 1
            if changed:
                self._save_raw(raw)
            return moved

    @staticmethod
    def _env_key(provider: str) -> Optional[str]:
        for info in KNOWN_PROVIDERS:
            if info["key"] == provider:
                for name in info["env_vars"]:
                    value = os.environ.get(name)
                    if value and value.strip():
                        return value.strip()
        return None

    def get_key(self, provider: str, key_id: str) -> Optional[str]:
        """Resolve one key; an unknown identity never falls back to another key."""
        with self._lock:
            if key_id == "env":
                return self._env_key(provider)
            try:
                entry = self._read_store().get(provider, {}).get(key_id)
            except ValueError:
                return None
            if entry is not None:
                secret = self._stored_secret(entry)
                return secret.strip() if secret else None
            return self._env_key(provider) if key_id == "env" else None

    def get_registered_key(self, provider: str) -> Optional[str]:
        """Backward-compatible default or first usable stored key, then environment."""
        with self._lock:
            try:
                entries = self._read_store().get(provider, {})
            except ValueError:
                entries = {}
            ids = (["default"] if "default" in entries else []) + [k for k in entries if k != "default"]
            for key_id in ids:
                secret = self._stored_secret(entries[key_id])
                if secret:
                    return secret.strip()
            return self._env_key(provider)

    def list_keys(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """With a provider list individual keys; no argument preserves desktop cards."""
        with self._lock:
            storage_error = False
            try:
                raw = self._read_store()
            except ValueError:
                raw, storage_error = {}, True
            results = []
            for info in KNOWN_PROVIDERS:
                prov = info["key"]
                if provider is not None and provider != prov:
                    continue
                entries = raw.get(prov, {})
                individual = []
                if storage_error:
                    individual.append(self._key_view(prov, "storage_error", {
                        "label": "저장소 확인 불가", "status": "storage_error"}, None, "stored"))
                env_secret = self._env_key(prov)
                detected = next((v for v in info["env_vars"] if os.environ.get(v, "").strip()), None)
                for key_id, entry in entries.items():
                    secret = self._stored_secret(entry)
                    individual.append(self._key_view(prov, key_id, entry, secret, "stored"))
                if env_secret:
                    metadata = self._env_verification.get(prov, {})
                    if metadata.get("identity") != hashlib.sha256(env_secret.encode()).digest():
                        metadata = {}
                    individual.append(self._key_view(prov, "env", {**metadata, "label": "환경 변수"},
                                                     env_secret, "env"))
                if provider is not None:
                    results.extend(individual)
                    continue
                chosen = next((k for k in individual if k["key_id"] == "default" and k["has_key"]), None)
                chosen = chosen or next((k for k in individual if k["has_key"]), None)
                card = dict(chosen or (individual[0] if individual else
                                      self._key_view(prov, None, {}, None, "none")))
                card.update({"name": info["name"], "placeholder": info["placeholder"],
                             "detected_env_var": detected, "keys": individual})
                results.append(card)
            return results

    @staticmethod
    def _key_view(provider, key_id, entry, secret, source) -> Dict[str, Any]:
        masked = mask_key(secret) if secret else ""
        return {
            "provider": provider, "key_id": key_id, "label": entry.get("label", "기본"),
            "masked": masked, "masked_key": masked, "has_key": bool(secret), "source": source,
            "status": ("unreadable" if source == "stored" and not secret
                       and entry.get("status") != "storage_error"
                       else entry.get("status", "connected" if secret else "unconfigured")),
            "last_verified": entry.get("last_verified"), "balance_info": entry.get("balance_info"),
            "protection": (
                "Windows DPAPI 암호화" if entry.get("scheme") == secret_store.SCHEME_DPAPI else None
            ),
            "is_admin_key": provider in ADMIN_PROVIDERS,
        }

    def _validate_label(self, label: str, new_secret: Optional[str] = None) -> str:
        label = label.strip()
        if not label or len(label) > 40:
            raise ValueError("Invalid key label")
        # Reject recognizable credentials, emails, and every secret this store can resolve.
        if re.search(r"(?:sk-|gsk_|AIza|Bearer\s|@|[\r\n])", label, re.IGNORECASE):
            raise ValueError("Invalid key label")
        secrets = [new_secret] if new_secret else []
        for info in KNOWN_PROVIDERS:
            for entry in self.list_keys(info["key"]):
                secret = self.get_key(info["key"], entry["key_id"])
                if secret:
                    secrets.append(secret)
        if any(secret and secret in label for secret in secrets):
            raise ValueError("Invalid key label")
        return label

    def _write_key(self, provider: str, key_id: str, key_value: str, label: str) -> Dict[str, Any]:
        if secret_store.AVAILABLE_SCHEME == "unavailable":
            raise secret_store.StorageError("Encrypted key storage is unavailable")
        if len(key_value) > MAX_KEY_LENGTH or any(ord(c) < 32 or ord(c) > 126 for c in key_value):
            raise ValueError("Invalid provider or key")
        key_value = key_value.strip()
        if not key_value or provider not in {p["key"] for p in KNOWN_PROVIDERS}:
            raise ValueError("Invalid provider or key")
        label = self._validate_label(label, key_value)
        scheme, sealed = secret_store.seal(key_value)
        raw = self._load_raw()
        raw.setdefault(provider, {})[key_id] = {
            "scheme": scheme, "sealed": sealed, "label": label,
            "status": "connected", "last_verified": None, "balance_info": None,
        }
        self._save_raw(raw)
        spend_service.invalidate(provider)
        return self._key_view(provider, key_id, raw[provider][key_id], key_value, "stored")

    def add_key(self, provider: str, key_value: str, label: str = "기본") -> Dict[str, Any]:
        if provider not in {p["key"] for p in KNOWN_PROVIDERS} or not key_value.strip():
            raise ValueError("Invalid provider or key")
        with self._transaction():
            self.migrate_plaintext()
            return self._write_key(provider, uuid.uuid4().hex, key_value, label)

    def set_key(self, provider: str, key_value: str) -> Dict[str, Any]:
        """Desktop compatibility: replace the default or first key without losing others."""
        if provider not in {p["key"] for p in KNOWN_PROVIDERS} or not key_value.strip():
            raise ValueError("Invalid provider or key")
        with self._transaction():
            self.migrate_plaintext()
            entries = self._load_raw().get(provider, {})
            key_id = "default" if "default" in entries else next(iter(entries), "default")
            return self._write_key(provider, key_id, key_value, entries.get(key_id, {}).get("label", "기본"))

    def update_label(self, provider: str, key_id: str, label: str) -> Optional[Dict[str, Any]]:
        with self._transaction():
            self.migrate_plaintext()
            raw = self._load_raw()
            entry = raw.get(provider, {}).get(key_id)
            if entry is None:
                return None
            entry["label"] = self._validate_label(label)
            self._save_raw(raw)
            return self._key_view(provider, key_id, entry, self._stored_secret(entry), "stored")

    def delete_key(self, provider: str, key_id: Optional[str] = None) -> bool:
        with self._transaction():
            self.migrate_plaintext()
            raw = self._load_raw()
            if provider not in raw or (key_id is not None and key_id not in raw[provider]):
                return False
            if key_id is None:
                del raw[provider]
            else:
                del raw[provider][key_id]
                if not raw[provider]:
                    del raw[provider]
            self._save_raw(raw)
            spend_service.invalidate(provider)
            return True

    def billing_snapshot(self) -> List[Dict[str, Any]]:
        """One consistent store read; secrets only live in this in-memory request."""
        with self._lock:
            try:
                raw = self._read_store()
            except ValueError:
                raw = {}
            entries = []
            for info in KNOWN_PROVIDERS:
                provider = info["key"]
                for key_id, entry in raw.get(provider, {}).items():
                    secret = self._stored_secret(entry)
                    if secret:
                        entries.append({"provider": provider, "key_id": key_id,
                                        "label": entry.get("label", "기본"), "secret": secret})
                secret = self._env_key(provider)
                if secret:
                    entries.append({"provider": provider, "key_id": "env",
                                    "label": "환경 변수", "secret": secret})
            return entries

    def verify_key(self, provider: str, key_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if key_id is None:
                keys = self.list_keys(provider)
                chosen = next((k for k in keys if k["key_id"] == "default" and k["has_key"]), None)
                chosen = chosen or next((k for k in keys if k["has_key"]), None)
                key_id = chosen["key_id"] if chosen else None
            secret = self.get_key(provider, key_id) if key_id is not None else None
        if not secret:
            return {"valid": False, "error": "등록된 키가 없습니다"}
        identity = hashlib.sha256(secret.encode()).digest()
        result = self._verify_secret(provider, secret)
        error = result.get("error", "")
        verification_status = "valid" if result["valid"] else (
            "invalid" if error.startswith(("HTTP 401", "HTTP 403", "인증 실패"))
            else "verification_unavailable"
        )
        self._update_verification(provider, key_id, identity, verification_status, result.get("balance"))
        return result

    def _verify_secret(self, provider: str, key: str) -> Dict[str, Any]:
        """Test the API key with the provider's official endpoint and retrieve balance/model status."""
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
                        try:
                            usage = _amount(data.get("usage"))
                            limit = data.get("limit")
                            balance_str = f"사용: ${usage:.2f}" + (
                                f" / 한도: ${_amount(limit):.2f}" if limit is not None else " (무제한)"
                            )
                        except (ValueError, TypeError, AttributeError):
                            balance_str = "잔액 확인 불가"
                        return {"valid": True, "balance": balance_str}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "deepseek":
                    resp = client.get(
                        "https://api.deepseek.com/user/balance",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        try:
                            infos = data.get("balance_infos")
                            if not isinstance(infos, list) or not infos:
                                raise ValueError("Missing balance")
                            balances = {}
                            for info in infos:
                                currency = info["currency"].upper()
                                if currency not in ("CNY", "USD"):
                                    raise ValueError("Unknown currency")
                                balances[currency] = _amount(balances.get(currency, 0) +
                                                             _amount(info.get("total_balance")))
                            balance_str = "잔여: " + " / ".join(
                                f"{amount:.2f} {currency}" for currency, amount in balances.items()
                            )
                        except (ValueError, TypeError, KeyError, AttributeError):
                            balance_str = "잔액 확인 불가"
                        return {"valid": True, "balance": balance_str}
                    elif resp.status_code in (401, 403):
                        return {"valid": False, "error": "인증 실패: 유효하지 않은 DeepSeek API 키"}
                    # Fallback to models endpoint if user endpoint not enabled
                    m_resp = client.get(
                        "https://api.deepseek.com/models", headers={"Authorization": f"Bearer {key}"}
                    )
                    if m_resp.status_code == 200:
                        return {"valid": True, "balance": "정상 연동됨"}
                    return {"valid": False, "error": f"HTTP {m_resp.status_code}"}

                elif provider == "openai":
                    resp = client.get(
                        "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}
                    )
                    if resp.status_code == 200:
                        return {"valid": True, "balance": "정상 연동됨 (API 가용)"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "anthropic":
                    resp = client.get(
                        "https://api.anthropic.com/v1/models",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    )
                    if resp.status_code == 200:
                        return {"valid": True, "balance": "정상 연동됨 (API 가용)"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "gemini":
                    resp = client.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        headers={"x-goog-api-key": key},
                    )
                    if resp.status_code == 200:
                        return {"valid": True, "balance": "정상 연동됨 (Gemini 가용)"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                elif provider == "anthropic_admin":
                    resp = client.get(
                        "https://api.anthropic.com/v1/organizations/cost_report",
                        params={"starting_at": month_start_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 1},
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    )
                    if resp.status_code == 200:
                        return {"valid": True, "balance": "비용 리포트 조회 가능"}
                    return {"valid": False, "error": f"HTTP {resp.status_code} (관리자 키가 필요합니다)"}

                elif provider == "openai_admin":
                    resp = client.get(
                        "https://api.openai.com/v1/organization/costs",
                        params={"start_time": int(month_start_utc().timestamp()), "limit": 1},
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if resp.status_code == 200:
                        return {"valid": True, "balance": "비용 조회 가능"}
                    return {"valid": False, "error": f"HTTP {resp.status_code} (관리자 키가 필요합니다)"}

                elif provider == "groq":
                    resp = client.get(
                        "https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}
                    )
                    if resp.status_code == 200:
                        return {"valid": True, "balance": "정상 연동됨"}
                    return {"valid": False, "error": f"HTTP {resp.status_code}"}

                else:
                    return {"valid": True, "balance": "연동됨"}

        except Exception as e:
            logger.debug("Verification exception for %s: %s", provider, type(e).__name__)
            return {"valid": False, "error": f"연결 실패 ({type(e).__name__})"}

    def _update_verification(self, provider: str, key_id: str, identity: bytes, status: str,
                             balance_info: Optional[str]) -> None:
        with self._lock:
            metadata = {"status": status, "balance_info": balance_info,
                        "last_verified": to_aware_utc(utc_now()).isoformat()}
            if key_id == "env":
                current = self._env_key(provider)
                if current and hashlib.sha256(current.encode()).digest() == identity:
                    self._env_verification[provider] = {**metadata, "identity": identity}
                return
            try:
                with self._transaction():
                    self.migrate_plaintext()
                    raw = self._load_raw()
                    entry = raw.get(provider, {}).get(key_id)
                    current = self._stored_secret(entry) if entry is not None else None
                    if current and hashlib.sha256(current.encode()).digest() == identity:
                        entry.update(metadata)
                        self._save_raw(raw)
            except ValueError:
                logger.debug("Could not persist verification status")


# Global singleton
api_key_manager = APIKeyManager()

spend_service = SpendService(api_key_manager.get_registered_key)
