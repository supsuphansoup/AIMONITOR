# 📋 TASK-014: Docker & Docker Compose Packaging

> **Status**: `Done`  
> **Assignee**: Worker (GitHub Copilot)  
> **Supervisor**: Coordinator (Antigravity)  
> **Phase**: 4 (Production Deployment)  

---

## 🎯 1. Objective (목표)
- AIMONITOR 시스템을 단 한 번의 명령(`docker compose up`)으로 실행할 수 있도록 프론트엔드 빌드 및 백엔드 서버를 패키징하는 `Dockerfile`과 `docker-compose.yml`을 작성합니다.

---

## 📂 2. Target Files (대상 파일)
- **Create**:
  - `Dockerfile`: Multi-stage build (Node 22 for Frontend build + Python 3.11/3.12 for Backend and static file serving)
  - `docker-compose.yml`: Container orchestration file
  - `.dockerignore`: Ignore `.git`, `node_modules`, `.venv`, `__pycache__`

---

## 📝 3. Detailed Requirements (상세 요구사항)
1. **`Dockerfile`**:
   - Stage 1 (`frontend-builder`): `node:22-alpine`에서 `frontend/` 빌드 (`npm install && npm run build`).
   - Stage 2 (`runtime`): `python:3.11-slim`에서 `backend/requirements.txt` 설치 및 소스 코드 복사, 프론트엔드 빌드 결과물(`dist/`)을 마운트/서빙 준비.
2. **`docker-compose.yml`**:
   - `aimonitor` 서비스 정의 (Port 8000:8000, 환경변수 설정, 볼륨 매핑).
3. **`backend/app/main.py`**:
   - 프로덕션 모드에서 프론트엔드 정적 파일(`frontend/dist`)이 존재할 경우 `StaticFiles` 및 루트 SPA 마운트 추가.

---

## 🧪 4. Acceptance Criteria
- [ ] `Dockerfile` 및 `docker-compose.yml` 문법 검증 완료.
- [ ] 정적 파일 마운트 후에도 기존 API 테스트(26개)가 모두 통과함.
