# Spec: 알림 (Alerts)

## Requirements

- ALR-1 사용자가 지표·연산자·임계치·시간 창으로 규칙을 만들고, 수정(일시 중지 포함)·삭제한다.
- ALR-2 백그라운드 스케줄러가 활성 규칙을 주기적으로 평가한다. 대시보드를 열지 않아도 감시된다.
- ALR-3 한 장애에 한 번만 알린다. 규칙별 쿨다운 동안 재발동하지 않으며, 워커가 여러 개여도 같다.
- ALR-4 조건 충족 시 이력을 남기고 Slack/Discord Webhook을 보낸다. Webhook 실패가 평가를 멈추지 않는다.
- ALR-5 수동 평가(`POST /alerts/evaluate`)는 결과를 기록·발송하되 쿨다운 상태를 바꾸지 않는다.

## Design

### API

| Method | Path | 비고 |
| :--- | :--- | :--- |
| GET | `/alerts/metrics` | 사용 가능한 지표·단위·연산자 |
| GET·POST | `/alerts/rules` | 목록 / 생성 |
| PATCH·DELETE | `/alerts/rules/{id}` | 부분 수정 / 규칙과 이력 함께 삭제 |
| POST | `/alerts/evaluate?ignore_cooldown=` | 수동 평가 |
| GET | `/alerts/history` | 발동 이력 |

### 데이터 (`alert_rules`, `alert_histories`)

| 필드 | 규칙 |
| :--- | :--- |
| `metric_type` | `core/metrics_catalog.py`의 이름·별칭만 허용 (예: `error_rate`, `latency_p95`, `total_cost`, `total_requests`, `cache_read_tokens`) |
| `operator` | `>` `>=` `<` `<=` `==` `!=` |
| `window_minutes` | 기본 5, 1–1440 |
| `cooldown_minutes` | 기본 15, 0–10,080(1주) |
| `webhook_url` | 선택. `http(s)://`만 허용 |
| `is_active` | 일시 중지 = `false` |
| `last_triggered_at` | 쿨다운 기준 시각 (UTC) |
| 이력 | `rule_id`(FK, 규칙 삭제 시 함께 삭제), `rule_name`, `triggered_value`, `message`(단위 포함), `sent_successfully`, `created_at` |

### 평가 흐름 (`AlertEvaluator.evaluate_rules`)

1. 활성 규칙을 읽는다. 같은 `window_minutes`를 쓰는 규칙은 overview 집계 1회를 공유한다.
2. 쿨다운 중이면 건너뛴다 (`ignore_cooldown`이면 무시).
3. 조건을 먼저 평가한다 — 조건이 거짓인 규칙은 쿨다운을 소모하지 않는다.
4. 발동 권한을 조건부 UPDATE로 선점한다. `rowcount == 1`인 평가자만 진행한다.
   ```sql
   UPDATE alert_rules SET last_triggered_at = :now
   WHERE id = :id AND (last_triggered_at IS NULL OR last_triggered_at <= :cutoff)
   ```
   수동 평가는 이 단계를 건너뛰고 `last_triggered_at`도 쓰지 않는다.
5. 이력 기록 + Webhook 발송 (타임아웃 5초). Discord URL이면 `{"content"}`, 그 외 Slack 형식 `{"text"}`.

스케줄러: `SCHEDULER_ENABLED`(기본 true), 주기 `ALERT_INTERVAL_SECONDS`(기본 60).

## Open questions

- 이메일·PagerDuty 채널 → 이월: Webhook만.
- 해소(resolved) 알림 → 이월: 현재는 발동만 알린다.

## Areas of concern

- **빈 시간 창이 0으로 평가된다.** 창 안에 트레이스가 없으면 P95·에러율·비용이 모두 0이라 `latency_p95 < 100` 같은 `<` 규칙이 트래픽이 없을 때 발동한다. "데이터 없음"을 건너뛸지(단, `total_requests < 1` 같은 무트래픽 감시는 살려야 함) 결정 필요.
- **비용 규칙은 미상 비용을 모른다.** `total_cost` 규칙은 `계산 불가` 호출을 0으로 합산한다. 단가표가 오래될수록 비용 알림이 덜 울린다 ([cost.md](cost.md) 3번과 연결).
- `cost_hourly` 별칭은 "시간당"이 아니라 **창 전체 합계**(`total_cost_usd`)로 해석된다. 창이 60분이 아니면 이름과 의미가 다르다.
