# Spec: 계정·로그 수집 (Accounts & collection)

Claude Code·Codex·Copilot·Antigravity의 로컬 로그를 읽어 트레이스로 보내고, 계정별로 나누고, 계정별 한도를 붙인다.
코드: `desktop/accounts.py`, `desktop/ai_watcher.py`, `desktop/quota.py`, `desktop/claude_quota_syncer.py`.

## Requirements

- ACC-1 로컬 로그에서는 카운터(토큰 수·요청 수)·모델·시각·식별자만 수집한다. 프롬프트·응답 원문을 저장하거나 전송하지 않는다.
- ACC-2 한 도구의 여러 계정(설정 폴더)을 각각 수집하고, 각 기록을 그 시각에 로그인돼 있던 계정에 귀속한다. 알 수 없으면 `account_id=null`(계정 미상)이다.
- ACC-3 계정 식별자에는 공급자의 이메일·이름·토큰을 쓰지 않는다. 해시만 저장한다.
- ACC-4 같은 로그 파일(복사본·하드링크·정션)은 한 번만 센다. 재시작·경로 표기 변경으로 다시 보내지 않는다.
- ACC-5 공식 한도는 계정마다 따로 조회하고, 한 계정의 한도를 다른 계정에 복사하지 않는다.
- ACC-6 Claude·Codex 비용은 계정별 로그인 근거로 API 종량제·구독 API 환산·구분 미상을 나눈다. 구분 미상 금액은 별도 합산하며 도구 합계와 계정 하위 합계를 중복해서 더하지 않는다.

## Design

### 계정 목록 (`AccountRegistry`, `~/.aimonitor/accounts.json`)

각 항목은 `account_id`, `tool`, `config_dir`, `label`, `source(auto/manual)`, `first_seen`, `last_seen`을 가진다.
Claude·Codex에는 `auth_mode(subscription/api/unknown)`, `auth_mode_since`(현재 방식 첫 관측),
`auth_mode_changed_at`(마지막 변경 관측, 없으면 null)을 추가한다. 두 시각은 UTC 오프셋 ISO이며 비밀값은 저장하지 않는다.
필드 없는 구버전 항목은 다음 관측 때 추가하며 최초 관측을 변경으로 간주하지 않는다. 타임라인 형식은 바꾸지 않는다.
기본 라벨은 `Claude 1`, `Codex 1`처럼 도구명과 번호다.
계정 식별자는 `<tool>:<sha256 앞 12자리>`이고, Claude의 `oauthAccount.accountUuid` 또는 Codex 인증의 계정 ID를 읽기만 하여 해시한다.
고유값이 없으면 실제 설정 폴더 경로의 해시를 사용한 `<tool>:dir-<12자리>`다.

자동 탐색 대상은 Claude·Codex 기본 폴더, `CLAUDE_CONFIG_DIR`·`CODEX_HOME`, 홈의 `.claude-*`·`.codex-*` 폴더이며
각각 `projects/`·`sessions/` 구조가 있어야 등록한다. 수동 등록(Claude·Codex·Copilot·Antigravity 폴더)은 `accounts.json`에서 읽는다.
심볼릭 링크·Windows 정션을 해석하여 같은 실제 경로는 한 번만 읽는다. 수동 등록 삭제는 수집 등록만 해제하며 기록을 지우지 않고, 자동 감지 폴더를 차단하지 않는다.

공개 함수: `list_accounts()`, `rename_account(account_id, label)`, `add_folder(tool, config_dir, label=None)`, `remove_folder(config_dir)`.
같은 이름의 `AccountRegistry` 메서드로 임시 홈·상태 경로를 주입할 수 있다.
`refresh()`는 현재 감지한 폴더의 계정 목록을, `account_at(config_dir, timestamp)`는 기록 시각의 계정을 반환한다.

### 계정별 청구 구분

- Claude는 **그 계정 폴더**의 `.credentials.json`에서 `claudeAiOauth` 객체가 있으면 구독,
  OAuth 없이 비어 있지 않은 `anthropicApiKey` 필드가 있으면 API 종량제다. 구독명은 `subscriptionType`이다.
- Codex는 **그 계정 폴더**의 `auth.json`에서 ChatGPT 로그인 토큰 필드가 설정돼 있으면 구독,
  로그인 토큰 없이 비어 있지 않은 `OPENAI_API_KEY` 필드가 있으면 API 종량제다. 플랜은 기존 `id_token` claim으로 확인한다.
- 키의 null·빈 문자열·공백뿐인 문자열은 키 없음이다. OAuth·키 양쪽 설정, 형식 손상, 읽기 실패, 근거 부재는 `unknown`이다.
  키·액세스/갱신 토큰은 설정 여부만 확인하고 값을 즉시 버리며 저장·로그·응답에 넣지 않는다.
- 환경 변수 `ANTHROPIC_API_KEY`·`OPENAI_API_KEY`는 OAuth가 없는 **기본 폴더** 계정에만 API 근거로 쓴다.
  다른 폴더에 복사하지 않는다. 같은 계정 ID를 공유하는 폴더의 근거가 서로 다르면 그 계정은 `unknown`이다.
- 수집기 `AccountRegistry.refresh`만 변경 관측 시각을 저장한다. GUI·위젯·`connected_ais`는 읽기 전용이며,
  저장된 모드와 현재 근거가 다르면 그 요청의 기간 금액을 구분 미상으로 두고 다음 수집 관측에서 변경 시각을 확정한다.
- 5시간·오늘·7일·이번 달 창의 시작부터 현재까지(양끝 포함) 변경 관측이 있으면 **그 창 전체 금액**을 구분 미상으로 둔다.
  변경이 없으면 현재 방식을 창 전체에 적용한다. 최초 관측 이전 구간도 현재 방식으로 분류한다.
- API 계정 금액은 `billed`, 구독 계정은 `api_equivalent`, 근거 미상·창 안의 변경은 `unknown`이다.
  도구 카드의 창에 여러 구분이 섞이면 `mixed`와 혼합 안내를 표시한다. 각 계정의 금액은 자체 구분으로 표시한다.
  구분 미상 합계는 청구·API 환산과 따로 표시하며 단가 미상(`cost_unknown`, 계산 불가)과도 구별한다.
  단일 계정 또는 모든 계정의 방식이 같고 변경이 없으면 기존 도구 카드 문구·금액 합계를 유지한다.

### 계정 타임라인 (`account_timeline.json`)

`config_dir_hash`, `account_id`, `since`(UTC 오프셋 ISO)만 저장한다.
로그의 원래 timestamp로 계정을 정하며 첫 관측 이전·시각 누락·오프셋 없는 시각은 `null`이다.
로그인 변경을 관측하면 마지막 관측부터 새 관측 사이를 미상으로 표시하고 이후 새 계정에 귀속한다.
재시작 시에는 같은 계정이어도 마지막 관측부터 재시작 사이를 미상으로 남긴다. 앱 종료 중에 계정이 바뀌었다가 돌아왔을 수도 있기 때문이다.
목록·타임라인은 최상위 배열과 행·필드 형식을 검증해 손상된 행만 제외한다. 유효한 시각의 잘못된 계정 ID는 미상 구간이다.
타임라인 전체를 읽지 못하거나 최상위 형식이 잘못되면 귀속은 미상이며 원본을 덮어쓰지 않는다.
폴더별 연속 중복 관측은 합치고 전체 최대 5,000개를 최신 시각순으로 보관한다. 제거된 과거 구간은 현재 계정으로 추정하지 않는다.
백엔드의 읽기 전용 타임라인도 오프셋 없는 시각을 거부하며, 등록 목록 생성 실패는 한도 확인 불가로 격리한다.
수집기는 스캔마다 타임라인을 한 번만 읽는다. 등록 목록 생성·갱신이 실패하면 기존 관측 폴더와 자동 탐색 폴더의
로그 수집을 계속하되 계정 귀속은 미상으로 보낸다.

### 수집 (`UnifiedAIWatcher`)

| 도구 | 읽는 로그 | 보내는 값 |
| :--- | :--- | :--- |
| Claude Code | `<폴더>/projects/**/*.jsonl`의 `assistant` 줄 | `message.usage` 토큰(입력·출력·캐시 쓰기·1시간 캐시 쓰기·캐시 읽기), 모델, 시각, 응답·요청 ID |
| Codex | `<폴더>/sessions/**/*.jsonl` | `token_count`의 `last_token_usage`(반복된 누적 스냅샷은 다시 세지 않음), 모델, 시각 |
| Copilot | `~/.copilot/session-state/*/events.jsonl`의 `session.shutdown` | 모델별 토큰·요청 수·프리미엄 요청, API 시간 |
| Antigravity | `~/.gemini/antigravity/brain/*/…/transcript.jsonl` | 본문 길이로 추정한 토큰(`metadata.tokens=estimated`). 본문은 메모리에서 길이만 잰다 |

Cursor에는 신뢰할 수 있는 호출별 사용량이 없어 트레이스를 만들지 않는다.
트레이스는 `account_id`를 포함하며 200건씩 `POST /traces/batch`로 보낸다. 전송 실패 시 커서·중복 상태를 되돌린다.
Claude 응답의 내용 블록 중복은 `ai_seen_claude.json`(최대 2만 키)으로 제거한다. 아직 개행이 없는 줄은 다음 주기에 읽는다.
네 도구 모두 바이너리 바이트 커서로 완성된 개행까지만 읽는다. 손상된 완성 줄은 건수만 로그에 남기고 다음 줄을 계속 수집한다.
시각·응답 ID·토큰 수의 형식이 잘못된 줄은 배치와 중복 상태에 넣지 않는다.
Claude의 잘못된 모델 형식은 그 줄을, Copilot의 잘못된 모델 이름은 해당 모델 항목을 제외한다.
Copilot의 프리미엄 요청 비용·API 시간은 유한한 음수 아닌 숫자만 사용하고 그 외 값은 미상이다.
Claude 응답 중복 키는 유효한 사용량 트레이스를 만든 뒤에만 등록한다.
수집기 종료 요청 뒤에는 새 배치 요청을 시작하지 않는다. 이미 진행 중인 작업이 끝나는 스레드의 `finally`에서
머신 잠금을 해제하므로 `stop()`의 2초 대기 시간이 끝나도 후속 수집기와 동시에 작업하지 않는다.
Antigravity는 로그의 `model`(또는 `model_name`)·`provider` 문자열만 사용하며 없으면 `unknown`으로 둔다.
출력 토큰은 `content`·`thinking` 문자열 길이 ÷ 4의 합, 입력 토큰은 `input_text`·`prompt`·`input` 중
첫 번째로 확인되는 문자열 길이 ÷ 4로 추정한다. 다른 JSON 필드·메타데이터·직렬화 바이트 수는 입력에 합치지 않는다.
식별 가능한 입력 본문이 없으면 입력은 0, `metadata.input_tokens_estimate=unavailable`이다.
입력 본문이 확인되면 `body_length`로 표시하며 모든 경우 `metadata.tokens=estimated`를 유지한다. 본문은 전송·저장하지 않는다.

**파일 정체성.** Claude·Codex 파일은 `projects/`·`sessions/` 아래 상대경로를 정체성 키로 사용하여 다른 폴더의 복사본도 한 번만 읽는다.
`(st_dev, st_ino)`가 같은 하드링크·정션 파일도 같은 그룹으로 묶으며, 파일 식별자가 0이면 물리적 동일성을 추측하지 않는다.
그룹의 가장 긴 복사본을 읽되 계정 귀속은 탐색 순서의 첫 폴더를 따른다.
서로 다른 계정이 같은 그룹을 공유하면 watcher의 `file_alias_conflicts`에 계정 해시·설정 경로·정체성 해시로 기록한다.

**커서.** 폴더별 커서는 `folder_offsets/<tool>-<config_dir_hash>.json`으로 분리한다.
경로 커서는 `resolve()`·플랫폼 `normcase`로, 세션 커서는 상대경로·구분자로 정규화해 저장하며, 이전 표기의 키는 로드 시 합쳐 최댓값을 쓴다.
기존 `ai_offsets.json`·`claude_offsets.json`의 절대경로 커서는 `claude:session:<상대경로>`·`codex:session:<상대경로>` 키로 이전한다.
물리적 별칭의 상대경로가 달라도 모든 별칭에 같은 커서를 저장하므로 첫 폴더가 없어져도 재전송하지 않는다.

**파일 세대.** `ai_cursors.json`에 파일 ID·생성 시각·크기·경로 해시와 제한된 앞부분 지문(최대 4KiB),
마지막 소비 위치 앞 64바이트의 지문을 저장한다. 동일 경로 교체·잘림·소비한 부분의 변경이면 새 세대로 처음부터 읽는다.
원문은 저장하지 않는다. 다른 폴더의 같은 세션 복사본은 기존 공유 커서를 유지한다.
기존 정수 커서 파일과 `folder_offsets/` 형식은 유지하며 세대 정보가 없는 커서는 첫 관측 시 이행한다.
이미 이행하기 전에 발생한 교체는 이전 정수 커서만으로 확정할 수 없다.
파일이 사라지면 최초 누락 시각(`missing_since`)을 기록하고 30일 후 전역·폴더별·이전 커서에서 정리한다.
다시 나타난 로그는 처음부터 읽고 서버의 중복 키로 재저장을 막는다.
Codex는 마지막 유효 모델을 세대 커서에 함께 저장해 새 바이트만 읽는다. 이전 커서 이행 때만 최대 512KiB를 역탐색한다.

**trace_id·dedupe_key.** 모든 이벤트에 같은 값의 결정적 `trace_id`와 `dedupe_key`를 보낸다.
Claude 응답 ID가 있으면 기존 내용 기반 `cc-<hash>`를, Copilot 이벤트 ID가 있으면 ID·모델의 `cp-<hash>`를 유지한다.
Codex·ID 없는 Claude는 복사본에 공통인 세션 정체성·줄 시작 바이트 위치·줄 내용 해시로 키를 만든다.
Antigravity·ID 없는 Copilot은 파일 ID·생성 시각으로 재구성하는 세대(`event_generation`)·줄 시작 위치·줄 내용 해시
(및 Copilot 모델)를 쓴다. 파일 ID·생성 시각이 없으면 제한된 첫 줄 지문을 사용한다.
보관한 커서의 변경 이력에 의존하지 않아 30일 누락 정리 후 같은 파일을 복구해도 같은 전송 키를 재구성한다.
키에는 원문을 포함하지 않으며 실패 재시도·재시작에도 같은 키다. 서버는 같은 키의 중복 배치를 다시 저장하지 않는다.

### 계정별 한도 (`desktop.quota.account_quotas`)

`account_quotas(registry=None, force=False, now=None)`는 `[{account_id, tool, label, config_dir, quota_windows}]`를 제공한다.

| 도구 | 한도 창 | 출처 |
| :--- | :--- | :--- |
| Claude | 5시간·주간 | 그 계정 폴더의 `.credentials.json` 토큰으로 `api.anthropic.com/api/oauth/usage` 조회(비공식). 계정별 60초 캐시, 429면 120초 대기. 결과만 `~/.aimonitor/quota/<account_id>.json`에 캐시하고 토큰은 저장하지 않는다 |
| Codex | 세션 로그의 `rate_limits` primary·secondary 창 | 각 폴더의 최신 rollout 로그 스냅샷 |
| Copilot | 이번 달 프리미엄 요청 ÷ 사용자가 입력한 한도 | 로컬 로그. GitHub는 잔여량을 제공하지 않는다 |
| Antigravity | 없음 | 공식 값 없음 |

Codex 스냅샷의 귀속이 미상이거나 이전 계정이면 현재 계정에 붙이지 않고 별도 항목으로 제공한다.
한도 조회에는 등록 목록의 읽기 전용 `AccountQuotaView`를 전달하며, GUI·위젯 폴링은 수집기의 관측 타임라인을 갱신하지 않는다.
같은 Claude 계정 UUID의 여러 폴더는 조회 객체·60초 캐시·429 대기를 공유하며, 한 번의 강제 갱신도 계정당 한 번 조회한다.
최초 조회 실패도 마지막 시도부터 60초간 재시도하지 않는다(강제 갱신 제외). 429의 120초 대기는 강제 갱신에도 우선한다.
캐시는 임시 파일을 완성한 뒤 원자적으로 교체한다. 기본 조회 객체는 첫 호출에 생성하며 모듈 import는 홈 상태를 읽지 않는다.
Claude 조회 객체는 1시간 동안 쓰지 않으면 회수한다.
Codex는 파일 수정 시각순으로 각 파일의 마지막 최대 512KiB를 읽어 스냅샷 기록 시각을 비교한다.
다음 파일의 수정 시각이 찾은 최신 기록보다 5분 이상 오래되면 중단한다. 기록이 수정 시각보다 5분 이상 미래이면
복사·시계 변경으로 이 경계를 신뢰하지 않고 계속 비교한다. 파일 200개 또는 총 64MiB 상한까지도 최신을 확정하지 못하면
조회 상한 안내와 확인 불가를 반환한다. 기록 시각이 없거나 오프셋이 없으면 최신 한도로 확정하지 않는다.
같은 `(tool, account_id)`의 여러 폴더는 창별 최신 유효 값을 합치며 빈 폴더가 정상 값을 덮지 않는다.
한도 비율·기간·리셋 간격은 숫자 형식과 유한값을 검증한다. 손상된 창만 확인 불가로 두고 다른 정상 창은 유지한다.
Copilot 월 합계는 UTC의 `월초 <= 기록 시각 <= 현재`만 포함한다. 읽기 성공·실패·누락 비용을 함께 확인하며,
실패·부분 집계는 잔여 비율 없이 확인 불가로 표시한다. 부분 합계는 최소 확인된 요청 수와 `partial=true`로 구분한다.

### 화면

- PC 연결 AI 카드: 계정이 하나면 기존 배치를 유지한다. 둘 이상이면 카드 안에 계정별 5시간/오늘/7일/월의 요청·토큰·비용과 한도를 나눈다. `account_id=null`은 ‘계정 미상’, 계정별 한도 부재는 ‘확인 불가’, 등록만 있고 기록이 없으면 ‘기록 없음’이다. 여러 계정 카드는 내부 세로 스크롤로 모두 볼 수 있다.
- 위젯: 여러 계정인 도구마다 등록 순서로 최대 3개 계정의 라벨·첫 한도 창을 표시하고 나머지는 `+N개 · 메인 창`으로 안내한다. 더블 클릭은 메인 창을 연다.
- 환경설정 계정 영역: 도구별로 라벨·도구·설정 경로·자동/수동·마지막 관측을 표시한다. 공급자 로그인 이름·이메일·토큰은 표시하지 않는다.
- 로그·차트: 전체/계정별/계정 미상 선택을 유지한다(`account_id`, `account_unknown=true` 쿼리). 선택을 바꾸는 동안 이전 선택의 응답으로 새 화면을 덮지 않는다. 라벨 변경 후 같은 ID의 선택을 유지한다.

## Areas of concern

- Claude 한도는 문서화되지 않은 엔드포인트에 의존한다. 응답 형식이 바뀌면 `확인 불가`로 떨어진다.
- Antigravity 토큰은 확인 가능한 본문 길이 ÷ 4 추정치이며 입력을 식별할 수 없으면 입력 추정 불가다.
  로그에 모델이 없거나 공식 단가가 없는 모델 이름은 비용 계산 불가로 남는다.
