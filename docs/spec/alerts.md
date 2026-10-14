# Spec: 알림 (Alerts)

## Requirements

- ALR-1 사용자가 지표·연산자·임계치·시간 창으로 규칙을 만들고, 수정(일시 중지 포함)·삭제한다.
- ALR-2 백그라운드 스케줄러가 활성 규칙을 주기적으로 평가한다. 데스크톱 메인 창을 열지 않아도 서버가 실행 중이면 감시된다.
- ALR-3 한 장애에 한 번만 알린다. 규칙별 쿨다운 동안 재발동하지 않으며, 워커가 여러 개여도 같다.
- ALR-4 조건 충족 시 이력을 남기고 Slack/Discord Webhook을 보낸다. Webhook 실패가 평가를 멈추지 않는다.
- ALR-5 수동 평가(`POST /alerts/evaluate`)는 결과를 기록·발송하되 쿨다운 상태를 바꾸지 않는다.
- ALR-6 정의되지 않는 값으로 알리지 않는다. 요청이 없는 창의 지연·에러율, 미상 비용이 섞인 창의 비용 총액으로는 판정할 수 있는 경우에만 판정한다.
- ALR-7 요청이 있더라도 지연 측정값이 전혀 없으면 지연 규칙을 평가하지 않는다.

## Design

### API

| Method | Path | 비고 |
| :--- | :--- | :--- |
| GET | `/alerts/metrics` | 사용 가능한 지표·단위·연산자 |
| GET·POST | `/alerts/rules` | 목록 / 생성 |
| PATCH·DELETE | `/alerts/rules/{id}` | 부분 수정 / 규칙과 이력 함께 삭제 |
| POST | `/alerts/evaluate?ignore_cooldown=` | 수동 평가 |
| GET | `/alerts/history` | 발동 이력 |

`POST /alerts/evaluate`는 기본적으로 기존 이력 배열을 반환한다.
`?include_skipped=true`이면 `{histories: [...], skipped_rules: [...]}` 객체를 반환한다.
표본 상한 때문에 건너뛴 항목의 형식은 다음과 같고 비밀 URL·규칙명은 포함하지 않는다.

```json
{"rule_id": 1, "reason": "latency_sampled", "sample_count": 200000, "sample_limit": 200000}
```

기본 응답에서도 서버 로그에 규칙 ID와 `latency_sampled` 이유 코드를 남긴다.

### 데이터 (`alert_rules`, `alert_histories`)

| 필드 | 규칙 |
| :--- | :--- |
| `metric_type` | `core/metrics_catalog.py`의 이름·별칭만 허용 (예: `error_rate`, `latency_p95`, `total_cost`, `total_requests`, `cache_read_tokens`) |
| `operator` | `>` `>=` `<` `<=` `==` `!=` |
| `window_minutes` | 기본 5, 1–1440 |
| `cooldown_minutes` | 기본 15, 0–10,080(1주) |
| `webhook_url` | 선택. 절대 `http(s)://` URL; 생성·수정·발송 시 대상 검사 |
| `is_active` | 일시 중지 = `false` |
| `last_triggered_at` | 쿨다운 기준 시각 (UTC) |
| 이력 | `rule_id`(FK, 규칙 삭제 시 함께 삭제), `rule_name`, `triggered_value`, `message`(단위 포함), `sent_successfully`, `created_at` |

규칙 생성·수정·목록·단건 응답의 `webhook_url`은 `scheme://host/…`로 마스킹하고
`webhook_configured`로 설정 여부를 제공한다. 경로·쿼리·userinfo·포트는 반환하지 않는다.
Webhook 미설정은 `webhook_url: null`, `webhook_configured: false`다. 저장된 실제 URL은 반환하지 않는다.
마스킹 값을 PATCH에 넣으면 422이며, URL을 유지하려면 필드를 생략한다. `webhook_url: null`은 설정을 지운다.
그 외 필드의 명시적 null과 임계치 NaN·±Infinity는 생성·수정에서 422다. PATCH의 생략 필드는 유지된다.

Webhook은 기본적으로 공개 유니캐스트 주소만 허용한다. 루프백·사설·링크 로컬
(`169.254.0.0/16`, `fe80::/10` 포함)·예약·멀티캐스트 주소와 userinfo를 거부한다.
정수·8진·16진·축약 IPv4도 검사하며 DNS 결과 중 하나라도 비공개이면 거부한다.
`ALERT_ALLOW_PRIVATE_WEBHOOKS=true`로 명시적으로 허용할 수 있다(기본 false).
이름 해석 실패는 저장 시 422, 발송 시 실패다. 발송 직전 다시 검사하며 환경 프록시는 사용하지 않는다.
검사와 실제 연결의 이름 해석 사이에는 DNS rebinding 경쟁이 남는다. 외부 서버에 노출할 때는
네트워크의 송신 제한도 함께 적용해야 한다.

Webhook은 연결·헤더 수신을 포함한 전체 요청에 5초 마감을 적용하고, 마감 시 실제 요청을 취소하고
응답·클라이언트를 닫는다. 응답 본문은 읽거나 저장하지 않아 본문 수신량은 0이다.
OS 이름 해석은 취소 후에도 운영체제에서 끝날 수 있으나 이후 HTTP 요청을 시작하지 않는다.
2xx만 성공이며 3xx를 포함한 나머지는 실패다. `follow_redirects=False`로 리디렉션을 따르지 않는다.
Discord는 소문자로 정규화한 `discord.com`·`discordapp.com` 호스트와
`/api/(vN/)?webhooks` 경로일 때만 `content`·`username` 형식으로 발송하며 그 외는 Slack `text` 형식이다.

### 평가 흐름 (`AlertEvaluator.evaluate_rules`)

1. 활성 규칙을 읽는다. 같은 `window_minutes`를 쓰는 규칙은 overview 집계 1회를 공유한다.
2. 쿨다운 중이면 건너뛴다 (`ignore_cooldown`이면 무시).
3. 판정 불가 규칙을 건너뛴다.
   - `latency_sampled=true`인 창의 P50·P95·P99 규칙(별칭 포함)은 건너뛰고 쿨다운을 소비하지 않는다.
     평균 지연·건수·비용의 SQL 집계는 전체 창을 사용하므로 그대로 평가한다.
   - 창의 `total_requests == 0`이고 지표가 에러율·평균/P50/P95/P99 지연이면 건너뜀 (`UNDEFINED_WITHOUT_TRAFFIC`). 요청 수·비용·토큰은 0이 실제 값이므로 평가 — `total_requests < 1` 무트래픽 감시는 동작.
   - 비용 지표이고 창에 `cost_unknown_count > 0`이면 알려진 비용은 하한값이다. `>`·`>=`만 평가하고 메시지 끝에 `- excludes N call(s) with unknown pricing; the actual cost may be higher`를 붙인다. `<`·`<=`·`==`·`!=`는 건너뜀.
4. 조건을 평가한다 — 조건이 거짓인 규칙은 쿨다운을 소모하지 않는다.
5. 규칙별 트랜잭션에서 발동 권한을 조건부 UPDATE로 선점한다. `rowcount == 1`인 평가자만 진행한다.
   ```sql
   UPDATE alert_rules SET last_triggered_at = :now
   WHERE id = :id AND is_active = true
     AND (last_triggered_at IS NULL OR last_triggered_at <= :cutoff)
   ```
   수동 평가는 이 단계를 건너뛰고 `last_triggered_at`도 쓰지 않는다.
6. 같은 트랜잭션에서 `sent_successfully=false`, `message` 접두어 `[delivery pending] `인 발송 대기
   이력을 저장하고 선점과 함께 커밋한다. 실패하면 둘 다 롤백하며 발송하지 않는다.
7. Webhook 발송 후 해당 이력의 접두어를 제거하고 성공 여부를 별도 트랜잭션으로 갱신한다.

이력 응답의 `delivery_status`는 `pending`·`succeeded`·`failed`다(기존 컬럼에서 계산, 스키마 변경 없음).
`pending`은 발송 결과가 확정 저장되지 않았다는 뜻이며, 미발송·발송 중·발송 후 결과 저장 실패를
모두 포함한다. 프로세스 종료·DB 오류로 미완료 이력이 남아도 자동 재발송하지 않는다.
외부 발송과 DB의 exactly-once를 보장하지 않는다. Webhook 미설정의 성공은 기록 전용 성공이다.
일시 중지가 선점 전에 커밋되면 발동하지 않는다. 선점 이후 일시 중지는 이미 선점된 발송을 취소하지 않는다.

스케줄러: `SCHEDULER_ENABLED`(기본 true), 주기 `ALERT_INTERVAL_SECONDS`(기본 60).
최소 주기는 10초이며 첫 실행은 최대 10초 뒤, 이후에는 작업 완료 뒤 주기만큼 기다린다.
종료 시 새 규칙의 선점·발송을 중단하고 진행 중 작업 스레드가 끝난 뒤 반환하므로 DB 엔진 정리보다
작업이 먼저 끝난다. 이미 시작한 요청은 5초 마감과 연결 정리를 완료한다. DB 작업 정리 시간은 별도다.
종료가 선점·이력 커밋 직후 발송 전에 도착하면 이력은 `pending`으로 남으며 자동 재발송하지 않는다.

## Open questions

- 이메일·PagerDuty 채널 → 이월: Webhook만.
- 해소(resolved) 알림 → 이월: 현재는 발동만 알린다.

## Areas of concern

- 미상 비용이 있는 동안 `<` 계열 비용 규칙("비용이 너무 적다")은 침묵한다. 단가표에 없는 모델을 계속 쓰면 영원히 울리지 않는다 — 의도된 동작.
- 해결됨(v0.5.0): 빈 창의 지연·에러율을 0으로 평가해 `<` 규칙이 오발동하던 문제, 비용 규칙이 미상 비용을 0으로 보던 문제.
- `cost_hourly` 별칭은 "시간당"이 아니라 **창 전체 합계**(`total_cost_usd`)로 해석된다. 창이 60분이 아니면 이름과 의미가 다르다.
