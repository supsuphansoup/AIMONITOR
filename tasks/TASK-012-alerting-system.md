# 📋 TASK-012: Alerting & Anomaly Detection System

> **Status**: `Done`  
> **Assignee**: Worker (GitHub Copilot)  
> **Supervisor**: Coordinator (Antigravity)  
> **Phase**: 4 (Alerting & Automated Operations)  

---

## 🎯 1. Objective (목표)
- 임계치 기반 이상 감지 엔진(`AlertEvaluator`)과 알림 규칙 CRUD 및 발송 이력 조회 REST API(`backend/app/routers/alerts.py`)를 구현하고, 단위 테스트를 작성합니다.

---

## 📂 2. Target Files (대상 파일)
- **Create**:
  - `backend/app/services/alert.py`: 이상 감지 평가 및 Webhook 발송 서비스
  - `backend/app/routers/alerts.py`: 알림 규칙 관리 및 히스토리 조회 API
  - `backend/tests/test_alert.py`: 알림 평가 및 Webhook 전송 단위 테스트
- **Modify**:
  - `backend/app/main.py`: `alerts_router` 등록
  - `backend/app/routers/__init__.py`: `alerts_router` export

---

## 📝 3. Detailed Requirements (상세 요구사항)
1. **`backend/app/services/alert.py`**:
   - `evaluate_rules(session: Session) -> List[AlertHistory]`:
     - 활성화된 `AlertRule` 목록 조회.
     - 각 룰의 `metric_type` (예: `error_rate`, `latency_p95`)에 따라 `MetricAggregator`로 최근 `window_minutes` 메트릭 계산.
     - `operator` (`>`, `>=`, `<`, `<=`)와 `threshold_value` 비교.
     - 조건 충족 시 `AlertHistory` 생성 및 DB 저장.
     - `webhook_url`이 존재할 경우 `httpx.post`로 슬랙/디스코드 호환 페이로드 전송.
2. **`backend/app/routers/alerts.py`**:
   - `POST /api/v1/alerts/rules` : 알림 규칙 생성
   - `GET /api/v1/alerts/rules` : 알림 규칙 목록 조회
   - `GET /api/v1/alerts/history` : 발생된 알림 이력 조회
   - `POST /api/v1/alerts/evaluate` : 수동 알림 평가 트리거

---

## 🧪 4. Acceptance Criteria & Test Cases
- [ ] 에러율이 임계치를 초과할 때 정상적으로 알림 히스토리가 기록됨.
- [ ] Webhook URL 호출 실패 시에도 예외로 서버가 크래시되지 않음.
- [ ] `pytest backend/tests/test_alert.py` 통과.

---

## 🤖 5. Copilot Prompt Snippet (워커 실행용 프롬프트)

```markdown
You are the Worker AI (GitHub Copilot). Please execute TASK-012 according to `.github/copilot-instructions.md` and `tasks/TASK-012-alerting-system.md`.

### Implementation:
1. Create `backend/app/services/alert.py` with `AlertEvaluator` class.
2. Create `backend/app/routers/alerts.py` with CRUD routes for AlertRule and AlertHistory.
3. Register the router in `backend/app/main.py`.
4. Create `backend/tests/test_alert.py` using `TestClient` and in-memory DB to verify evaluation and triggering.
5. Run pytest and report the completion summary.
```
