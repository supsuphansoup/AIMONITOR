"""Automatic AI Discovery and Subscription Detector for Windows PC.

Scans the local PC environment to find:
1. Installed AI tools and CLIs (Claude Code, Antigravity, GitHub Copilot, Cursor, Ollama)
2. Active user credentials and subscriptions (Claude.ai OAuth, Copilot auth.db, API keys)
3. Filters to ONLY AIs that the user actually has connected, subscribed, or paid for.
"""

import base64
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aimonitor.desktop.ai_detector")


CLAUDE_PLAN_NAMES = {
    "free": "Claude Free",
    "pro": "Claude Pro",
    "max": "Claude Max",
    "team": "Claude Team",
    "enterprise": "Claude Enterprise",
}


def _connect_read_only(path: Path) -> sqlite3.Connection:
    """These databases belong to other apps; open them so a write is impossible."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _codex_plan_type(auth_file: Path) -> Optional[str]:
    """Plan claim from the ChatGPT id_token Codex stores. Read-only, never logged."""
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
        token = ((data.get("tokens") or {}).get("id_token")) or ""
        if isinstance(token, dict):  # some versions store the parsed token
            claims = token
        else:
            payload = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        auth = claims.get("https://api.openai.com/auth") or {}
        plan = auth.get("chatgpt_plan_type") or claims.get("chatgpt_plan_type")
        return str(plan) if plan else None
    except Exception:
        return None


class AIDetector:
    """Detects connected and subscribed AI services on the current PC."""

    def __init__(self):
        self.home = Path.home()
        self.appdata = Path(os.environ.get("APPDATA", ""))
        self.localappdata = Path(os.environ.get("LOCALAPPDATA", ""))

    def scan_all_candidates(self) -> Dict[str, Dict[str, Any]]:
        """Check all known AI services and return detailed connection & subscription status."""
        results: Dict[str, Dict[str, Any]] = {}

        checks = (
            ("claude", self._check_claude, "Claude Code", "anthropic"),
            ("antigravity", self._check_antigravity, "Antigravity", "google"),
            ("copilot", self._check_copilot, "GitHub Copilot", "github"),
            ("cursor", self._check_cursor, "Cursor IDE", "openai"),
            ("windsurf", self._check_windsurf, "Windsurf IDE", "codeium"),
            ("ollama", self._check_ollama, "Ollama", "ollama"),
            ("codex", self._check_codex, "OpenAI Codex CLI", "openai"),
            ("openai_api", self._check_openai_api, "OpenAI API", "openai"),
        )
        for key, check, name, provider in checks:
            try:
                results[key] = check()
            except Exception:
                # Exceptions can contain credential contents or private paths.
                logger.warning("AI detection unavailable: %s", key)
                results[key] = {
                    "key": key, "name": name, "display_name": name, "provider": provider,
                    "is_connected": None, "is_subscribed": None,
                    "detection_status": "unavailable", "plan_name": "감지 불가",
                    "detail": "도구 상태를 읽을 수 없습니다", "badge_color": "#64748b",
                    "max_context_tokens": None, "path": "",
                }

        return results

    def get_connected_and_paid_ais(self) -> List[Dict[str, Any]]:
        """Return ONLY the AIs that are actually connected and active/paid on this PC."""
        candidates = self.scan_all_candidates()
        active = []
        for info in candidates.values():
            if info["is_connected"] or info.get("detection_status") == "unavailable":
                active.append(info)

        # Include any registered or environment API keys
        try:
            extra_apis = self._check_registered_api_keys()
            active_keys = {x["key"] for x in active}
            for ea in extra_apis:
                if ea["key"] not in active_keys:
                    active.append(ea)
        except Exception:
            pass

        return active

    def _check_claude(self) -> Dict[str, Any]:
        """Check Claude Code installation and Claude.ai subscription."""
        claude_dir = self.home / ".claude"
        cred_file = claude_dir / ".credentials.json"
        projects_dir = claude_dir / "projects"

        is_connected = False
        is_subscribed = False
        plan_name = "미연동"
        detail = "Claude Code 미설치"
        max_context = 200_000

        if claude_dir.exists():
            is_connected = True
            # Check credentials for paid subscription
            if cred_file.exists():
                try:
                    cred_data = json.loads(cred_file.read_text(encoding="utf-8", errors="ignore"))
                    if "claudeAiOauth" in cred_data:
                        is_subscribed = True
                        sub = str(cred_data["claudeAiOauth"].get("subscriptionType") or "").lower()
                        plan_name = CLAUDE_PLAN_NAMES.get(sub, "Claude 구독 (플랜 확인 불가)")
                        detail = "Claude.ai 계정 로그인 (공식 사용량 조회 가능)"
                    elif "anthropicApiKey" in cred_data or os.environ.get("ANTHROPIC_API_KEY"):
                        is_subscribed = True
                        plan_name = "Anthropic API 종량제"
                        detail = "API Key 직접 결제 연동됨"
                    else:
                        plan_name = "Claude Code (로컬)"
                        detail = "로그인 정보 확인 중"
                except Exception as e:
                    detail = f"자격 증명 읽기 오류: {e}"
            elif projects_dir.exists() and any(projects_dir.glob("**/*.jsonl")):
                plan_name = "Claude Code (로그인 정보 없음)"
                detail = "대화 기록은 있으나 로그인 정보가 없어 플랜 확인 불가"

        return {
            "key": "claude",
            "name": "Claude",
            "display_name": "Claude Code",
            "provider": "anthropic",
            "is_connected": is_connected,
            "is_subscribed": is_subscribed,
            "plan_name": plan_name,
            "detail": detail,
            "max_context_tokens": max_context,
            "badge_color": "#a855f7",
            "path": str(claude_dir),
        }

    def _decode_antigravity_status(self, raw_val: str) -> Dict[str, Any]:
        """Decode base64 protobuf userStatus from Antigravity state.vscdb."""
        # Defaults are "unknown"; only what the decoded status names is reported.
        info = {
            "plan_name": "플랜 확인 불가",
            "tier": "",
            "active_model": "Gemini",
        }
        try:
            dec1 = base64.b64decode(raw_val + "=" * ((4 - len(raw_val) % 4) % 4))
            parts = re.findall(rb"[A-Za-z0-9+/=]{16,}", dec1)
            found_models: List[str] = []
            for p in parts:
                try:
                    d2 = base64.b64decode(p + b"==")
                    if b"g1-pro-tier" in d2:
                        info["tier"] = "g1-pro-tier"
                        info["plan_name"] = "Google AI Pro (g1-pro-tier)"
                    elif b"g1-ultra-tier" in d2:
                        info["tier"] = "g1-ultra-tier"
                        info["plan_name"] = "Google AI Ultra (g1-ultra-tier)"

                    models = re.findall(rb"Gemini [0-9\.]+ (?:Pro|Flash)(?: \([a-zA-Z]+\))?", d2)
                    for m in models:
                        m_str = m.decode("utf-8", errors="ignore")
                        if m_str not in found_models:
                            found_models.append(m_str)
                except Exception:
                    pass
            if found_models:
                info["active_model"] = found_models[0].split(" (")[0]
        except Exception:
            pass
        return info

    def _check_antigravity(self) -> Dict[str, Any]:
        """Check Antigravity (Google Gemini IDE) installation, account, and official Google AI Pro tier."""
        antigravity_dir = self.home / ".gemini" / "antigravity"
        brain_dir = antigravity_dir / "brain"
        vscdb = self.appdata / "Antigravity" / "User" / "globalStorage" / "state.vscdb"

        is_connected = False
        is_subscribed = False
        plan_name = "미연동"
        detail = "Antigravity 미설치"
        max_context = 2_000_000
        active_model = "Gemini 3.1 Pro"
        display_name = "Google Antigravity (Gemini 3.1 Pro)"

        # 2. Inspect state.vscdb for official subscription tier and active models
        if vscdb.exists():
            is_connected = True
            try:
                conn = _connect_read_only(vscdb)
                cur = conn.cursor()
                cur.execute(
                    "SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.userStatus'"
                )
                row = cur.fetchone()
                if row and row[0]:
                    status_info = self._decode_antigravity_status(row[0])
                    is_subscribed = True
                    plan_name = status_info["plan_name"]
                    active_model = status_info["active_model"]
                    display_name = f"Google Antigravity ({active_model})"
                conn.close()
            except Exception:
                pass

        # 3. Check ~/.gemini/antigravity brain directory
        if antigravity_dir.exists():
            is_connected = True
            if not is_subscribed and brain_dir.exists():
                plan_name = "플랜 확인 불가"

        if is_subscribed:
            detail = f"{plan_name} ({active_model})"
        elif is_connected:
            detail = "Antigravity 세션 대기 중"

        chosen_path = str(antigravity_dir if antigravity_dir.exists() else vscdb)
        return {
            "key": "antigravity",
            "name": active_model,
            "display_name": display_name,
            "provider": "gemini",
            "is_connected": is_connected,
            "is_subscribed": is_subscribed,
            "plan_name": plan_name,
            "detail": detail,
            "max_context_tokens": max_context,
            "badge_color": "#38bdf8",
            "path": chosen_path,
        }

    def _check_copilot(self) -> Dict[str, Any]:
        """Check GitHub Copilot installation and authentication."""
        copilot_user_dir = self.home / ".copilot"
        session_state_dir = copilot_user_dir / "session-state"
        copilot_dir = self.localappdata / "github-copilot"
        auth_db = copilot_dir / "auth.db"
        vscdb = self.appdata / "Code" / "User" / "globalStorage" / "state.vscdb"

        is_connected = False
        is_subscribed = False
        plan_name = "미연동"
        detail = "GitHub Copilot 로그인 토큰 없음"
        max_context = 128_000
        chosen_path = str(copilot_user_dir if copilot_user_dir.exists() else copilot_dir)

        # 1. Check user-level ~/.copilot sessions
        if session_state_dir.exists():
            try:
                subdirs = [p for p in session_state_dir.iterdir() if p.is_dir()]
                if subdirs:
                    is_connected = True
                    is_subscribed = True
                    plan_name = "GitHub Copilot (플랜 확인 불가)"
                    detail = f"Copilot 로컬 활성 세션 {len(subdirs)}개 연동됨"
            except Exception:
                pass

        # 2. Check localappdata auth.db
        if not is_connected and auth_db.exists():
            try:
                conn = _connect_read_only(auth_db)
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM oauth_tokens")
                cnt = cur.fetchone()[0]
                conn.close()
                if cnt > 0:
                    is_connected = True
                    is_subscribed = True
                    plan_name = "GitHub Copilot (플랜 확인 불가)"
                    detail = f"활성 GitHub 로그인 토큰 {cnt}개 연동됨"
            except Exception as e:
                detail = f"Copilot DB 검사 실패: {e}"

        # 3. Check VS Code state.vscdb for Copilot Chat activation
        if not is_connected and vscdb.exists():
            try:
                conn = _connect_read_only(vscdb)
                cur = conn.cursor()
                cur.execute(
                    "SELECT value FROM ItemTable WHERE key LIKE '%copilotSku%' OR key LIKE '%github%usages%'"
                )
                row = cur.fetchone()
                conn.close()
                if row:
                    is_connected = True
                    is_subscribed = True
                    plan_name = "GitHub Copilot (플랜 확인 불가)"
                    detail = "VS Code Copilot 확장 및 계정 연동됨"
            except Exception:
                pass

        return {
            "key": "copilot",
            "name": "GitHub Copilot",
            "display_name": "GitHub Copilot",
            "provider": "openai",
            "is_connected": is_connected,
            "is_subscribed": is_subscribed,
            "plan_name": plan_name,
            "detail": detail,
            "max_context_tokens": max_context,
            "badge_color": "#10b981",
            "path": chosen_path,
        }

    def _check_cursor(self) -> Dict[str, Any]:
        """Check Cursor IDE active sessions or subscription."""
        cursor_ws = self.appdata / "Cursor" / "User" / "workspaceStorage"
        is_connected = False
        is_subscribed = False
        plan_name = "미연동"
        detail = "Cursor 작업 영역 없음"
        max_context = 128_000

        if cursor_ws.exists() and any(cursor_ws.iterdir()):
            is_connected = True
            is_subscribed = True
            plan_name = "Cursor (플랜 확인 불가)"
            detail = "Cursor 활성 작업 영역 감지됨"

        return {
            "key": "cursor",
            "name": "Cursor",
            "display_name": "Cursor IDE",
            "provider": "openai",
            "is_connected": is_connected,
            "is_subscribed": is_subscribed,
            "plan_name": plan_name,
            "detail": detail,
            "max_context_tokens": max_context,
            "badge_color": "#059669",
            "path": str(cursor_ws),
        }

    def _check_codex(self) -> Dict[str, Any]:
        """Codex CLI signed in with a ChatGPT plan (its logs carry official usage limits)."""
        codex_dir = self.home / ".codex"
        auth_file = codex_dir / "auth.json"
        is_connected = codex_dir.exists() and (auth_file.exists() or (codex_dir / "sessions").exists())
        plan_name = "미연동"
        detail = "Codex CLI 미설치"
        if is_connected:
            plan = _codex_plan_type(auth_file)
            plan_name = f"ChatGPT {plan.capitalize()}" if plan else "ChatGPT 플랜 (확인 불가)"
            detail = "Codex CLI 세션 로그에서 공식 사용 한도 확인"
        return {
            "key": "codex",
            "name": "Codex",
            "display_name": "OpenAI Codex CLI",
            "provider": "openai",
            "is_connected": is_connected,
            "is_subscribed": is_connected,
            "plan_name": plan_name,
            "detail": detail,
            "max_context_tokens": 400_000,
            "badge_color": "#10b981",
            "path": str(codex_dir),
        }

    def _check_openai_api(self) -> Dict[str, Any]:
        """Check direct OpenAI API key in environment."""
        has_key = bool(os.environ.get("OPENAI_API_KEY"))
        return {
            "key": "openai_api",
            "name": "OpenAI API",
            "display_name": "OpenAI API (GPT-4o)",
            "provider": "openai",
            "is_connected": has_key,
            "is_subscribed": has_key,
            "plan_name": "OpenAI API 종량제" if has_key else "미연동",
            "detail": "OPENAI_API_KEY 환경 변수 등록됨" if has_key else "환경 변수 미설정",
            "max_context_tokens": 128_000,
            "badge_color": "#10b981",
            "path": "Environment Variable",
        }

    def _check_windsurf(self) -> Dict[str, Any]:
        """Check Windsurf IDE (Codeium) installation and login."""
        codeium_dir = self.home / ".codeium"
        windsurf_appdata = self.appdata / "Windsurf"
        is_connected = False
        detail = "Windsurf 미설치"
        if codeium_dir.exists() or windsurf_appdata.exists():
            is_connected = True
            detail = "Windsurf / Codeium 활성 프로필 연동됨"
        return {
            "key": "windsurf",
            "name": "Windsurf",
            "display_name": "Windsurf IDE",
            "provider": "codeium",
            "is_connected": is_connected,
            "is_subscribed": is_connected,
            "plan_name": "Windsurf (플랜 확인 불가)" if is_connected else "미연동",
            "detail": detail,
            "max_context_tokens": 128_000,
            "badge_color": "#0ea5e9",
            "path": str(codeium_dir if codeium_dir.exists() else windsurf_appdata),
        }

    def _check_ollama(self) -> Dict[str, Any]:
        """Check local Ollama daemon and installed LLMs."""
        import httpx

        is_connected = False
        models = []
        detail = "Ollama 데몬 미실행"
        try:
            # Windows는 닫힌 localhost 포트 연결 거부에 ~2초를 쓴다. 로컬 데몬은 즉시 받으므로
            # 연결 단계만 짧게 끊는다 (감지는 시작 시 UI 스레드에서 돈다).
            with httpx.Client(timeout=httpx.Timeout(1.5, connect=0.3)) as client:
                resp = client.get("http://127.0.0.1:11434/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m.get("name") for m in data.get("models", [])]
                    is_connected = True
                    detail = (
                        f"로컬 모델 {len(models)}개 실행 중 ({', '.join(models[:2]) if models else '대기'})"
                    )
        except Exception:
            ollama_dir = self.localappdata / "Ollama"
            if ollama_dir.exists():
                detail = "Ollama 설치됨 (데몬 정지)"

        return {
            "key": "ollama",
            "name": "Ollama",
            "display_name": "Ollama (로컬 LLM)",
            "provider": "ollama",
            "is_connected": is_connected,
            "is_subscribed": is_connected,
            "plan_name": "로컬 무제한 (0원 무료)",
            "detail": detail,
            "max_context_tokens": 131_072,
            "badge_color": "#f97316",
            "path": "http://127.0.0.1:11434",
            "billing_note": "로컬 자체 연산 (비용 0원)",
        }

    def _check_registered_api_keys(self) -> List[Dict[str, Any]]:
        """Add any provider that has a configured or environment API key."""
        try:
            from backend.app.services.api_key_manager import api_key_manager

            keys_list = api_key_manager.list_keys()
        except Exception:
            return []

        results = []
        color_map = {
            "openai": "#10b981",
            "anthropic": "#a855f7",
            "gemini": "#38bdf8",
            "deepseek": "#3b82f6",
            "openrouter": "#6366f1",
            "groq": "#f43f5e",
        }
        for k_info in keys_list:
            if k_info.get("has_key"):
                prov = k_info["provider"]
                # An API key is billed separately from any subscription, so
                # Anthropic/Gemini keys get their own card next to Claude/Antigravity.
                results.append(
                    {
                        "key": f"{prov}_api",
                        "name": k_info["name"],
                        "display_name": f"{k_info['name']} API",
                        "provider": prov,
                        "is_connected": True,
                        "is_subscribed": True,
                        "plan_name": f"{k_info['name']} API 종량제",
                        "detail": k_info.get("balance_info") or f"API Key 연동 ({k_info['source']})",
                        "max_context_tokens": 128_000,
                        "badge_color": color_map.get(prov, "#64748b"),
                        "path": f"Key: {k_info['masked_key']}",
                    }
                )
        return results


# Global singleton
detector = AIDetector()


def detect_connected_ais() -> List[Dict[str, Any]]:
    """Convenience function to get only connected & paid AIs."""
    return detector.get_connected_and_paid_ais()


def scan_all_ai_status() -> Dict[str, Dict[str, Any]]:
    """Convenience function to get diagnostic status of all AI services."""
    return detector.scan_all_candidates()
