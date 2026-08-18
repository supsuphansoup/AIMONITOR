# 📋 TASK-001: Backend Environment & Project Scaffolding

> **Status**: `In Progress`  
> **Assignee**: Worker (GitHub Copilot)  
> **Supervisor**: Coordinator (Antigravity)  
> **Phase**: 1 (Core Engine & Data Models)  

---

## 🎯 1. Objective (목표)
- AIMONITOR 백엔드의 기본 구조(FastAPI, Pydantic, Uvicorn, Pytest)를 설정하고, 헬스 체크 엔드포인트 및 기본 환경설정 모듈을 구성합니다.

---

## 📂 2. Target Files (대상 파일)
- **Create**:
  - `backend/requirements.txt`: 의존성 정의 (`fastapi`, `uvicorn[standard]`, `pydantic>=2.0`, `sqlmodel`, `pytest`, `httpx`)
  - `backend/app/main.py`: FastAPI 애플리케이션 엔트리포인트 (`/health` 엔드포인트 및 CORS 미들웨어 포함)
  - `backend/app/core/config.py`: Pydantic BaseSettings 기반의 환경설정 (DB URL, Port, API Prefix 등)
  - `backend/tests/test_health.py`: 헬스체크 엔드포인트 단위 테스트
  - `backend/tests/__init__.py`, `backend/app/__init__.py`, `backend/app/core/__init__.py`

---

## 📝 3. Detailed Requirements (상세 요구사항)
1. **`backend/app/core/config.py`**:
   - `Settings` 클래스 (`pydantic_settings.BaseSettings` 또는 `pydantic.BaseModel` 기반)
   - `PROJECT_NAME`: "AIMONITOR"
   - `API_V1_STR`: "/api/v1"
   - `DATABASE_URL`: `sqlite:///./aimonitor.db`
   - `CORS_ORIGINS`: `["*"]`
2. **`backend/app/main.py`**:
   - FastAPI 인스턴스 생성 (title="AIMONITOR Backend", version="0.1.0")
   - CORS 미들웨어 등록
   - `GET /health` -> `{"status": "ok", "app": "AIMONITOR", "version": "0.1.0"}`
   - `GET /api/v1/health` -> 동일하게 헬스 체크 응답
3. **`backend/tests/test_health.py`**:
   - `httpx.AsyncClient` 또는 `fastapi.testclient.TestClient`를 사용하여 `/health`와 `/api/v1/health`의 상태 코드(200) 및 응답 JSON 검증.

---

## 🧪 4. Acceptance Criteria & Test Cases
- [ ] `backend/requirements.txt`에 필수 패키지가 명시되어 있음.
- [ ] `backend/app/main.py` 및 `backend/app/core/config.py`가 에러 없이 임포트됨.
- [ ] `pytest backend/tests` 실행 시 `test_health.py` 테스트가 정상 PASS됨.

---

## 🤖 5. Copilot Prompt Snippet (워커 실행용 프롬프트)

```markdown
You are the Worker AI (GitHub Copilot). Please execute TASK-001 according to `.github/copilot-instructions.md`.

### Target:
1. Create `backend/requirements.txt` with:
   - fastapi>=0.110.0
   - uvicorn[standard]>=0.28.0
   - pydantic>=2.6.0
   - pydantic-settings>=2.2.0
   - sqlmodel>=0.0.16
   - pytest>=8.0.0
   - httpx>=0.27.0
2. Create `backend/app/core/config.py` with Pydantic-based configuration (PROJECT_NAME, API_V1_STR, DATABASE_URL, CORS_ORIGINS).
3. Create `backend/app/main.py` with FastAPI app instance, CORS middleware, and `/health` & `/api/v1/health` routes.
4. Create `backend/tests/test_health.py` with tests for the health check routes using `TestClient`.
5. Ensure all `__init__.py` files are created in `backend/app`, `backend/app/core`, and `backend/tests`.

After creating files, output the completion summary in the standard format!
```
