# Spec: 계정·로그 수집 (Accounts & collection)

Claude Code·Codex·Copilot·Antigravity의 로컬 로그를 읽어 트레이스로 보내고, 계정별로 나누고, 계정별 한도를 붙인다.
코드: `desktop/accounts.py`, `desktop/ai_watcher.py`, `desktop/quota.py`, `desktop/claude_quota_syncer.py`.

## Requirements

- ACC-1 로컬 로그에서는 카운터(토큰 수·요청 수)·모델·시각·식별자만 수집한다. 프롬프트·응답 원문을 저장하거나 전송하지 않는다.
- ACC-2 한 도구의 여러 계정(설정 폴더)을 각각 수집하고, 각 기록을 그 시각에 로그인돼 있던 계정에 귀속한다. 알 수 없으면 `account_id=null`(계정 미상)이다.
- ACC-3 계정 식별자에는 공급자의 이메일·이름·토큰을 쓰지 않는다. 해시만 저장한다.
- ACC-4 같은 로그 파일(복사본·하드링크·정션)은 한 번만 센다. 재시작·경로 표기 변경으로 다시 보내지 않는다.
- ACC-5 공식 한도는 계정마다 따로 조회하고, 한 계정의 한도를 다른 계정에 복사하지 않는다.

## Design

### 계정 목록 (`AccountRegistry`, `~/.aimonitor/accounts.json`)

각 항목은 `account_id`, `tool`, `config_dir`, `label`, `source(auto/manual)`, `first_seen`, `last_seen`을 가진다.
기본 라벨은 `Claude 1`, `Codex 1`처럼 도구명과 번호다.
계정 식별자는 `<tool>:<sha256 앞 12자리>`이고, Claude의 `oauthAccount.accountUuid` 또는 Codex 인증의 계정 ID를 읽기만 하여 해시한다.
고유값이 없으면 실제 설정 폴더 경로의 해시를 사용한 `<tool>:dir-<12자리>`다.

자동 탐색 대상은 Claude·Codex 기본 폴더, `CLAUDE_CONFIG_DIR`·`CODEX_HOME`, 홈의 `.claude-*`·`.codex-*` 폴더이며
각각 `projects/`·`sessions/` 구조가 있어야 등록한다. 수동 등록(Claude·Codex·Copilot·Antigravity 폴더)은 `accounts.json`에서 읽는다.
심볼릭 링크·Windows 정션을 해석하여 같은 실제 경로는 한 번만 읽는다. 수동 등록 삭제는 수집 등록만 해제하며 기록을 지우지 않고, 자동 감지 폴더를 차단하지 않는다.

공개 함수: `list_accounts()`, `rename_account(account_id, label)`, `add_folder(tool, config_dir, label=None)`, `remove_folder(config_dir)`.
같은 이름의 `AccountRegistry` 메서드로 임시 홈·상태 경로를 주입할 수 있다.
`refresh()`는 현재 감지한 폴더의 계정 목록을, `account_at(config_dir, timestamp)`는 기록 시각의 계정을 반환한다.

### 계정 타임라인 (`account_timeline.json`)

`config_dir_hash`, `account_id`, `since`(UTC 오프셋 ISO)만 저장한다.
로그의 원래 timestamp로 계정을 정하며 첫 관측 이전·시각 누락·오프셋 없는 시각은 `null`이다.
로그인 변경을 관측하면 마지막 관측부터 새 관측 사이를 미상으로 표시하고 이후 새 계정에 귀속한다.
재시작 시에는 같은 계정이어도 마지막 관측부터 재시작 사이를 미상으로 남긴다. 앱 종료 중에 계정이 바뀌었다가 돌아왔을 수도 있기 때문이다.
목록·타임라인은 최상위 배열과 행·필드 형식을 검증해 손상된 행만 제외한다. 유효한 시각의 잘못된 계정 ID는 미상 구간이다.
타임라인 전체를 읽지 못하거나 최상위 형식이 잘못되면 귀속은 미상이며 원본을 덮어쓰지 않는다.
폴더별 연속 중복 관측은 합치고 전체 최대 5,000개를 최신 시각순으로 보관한다. 제거된 과거 구간은 현재 계정으로 추정하지 않는다.
백엔드의 읽기 전용 타임라인도 오프셋 없는 시각을 거부하며, 등록 목록 생성 실패는 한도 확인 불가로 격리한다.

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

**파일 정체성.** Claude·Codex 파일은 `projects/`·`sessions/` 아래 상대경로를 정체성 키로 사용하여 다른 폴더의 복사본도 한 번만 읽는다.
`(st_dev, st_ino)`가 같은 하드링크·정션 파일도 같은 그룹으로 묶으며, 파일 식별자가 0이면 물리적 동일성을 추측하지 않는다.
그룹의 가장 긴 복사본을 읽되 계정 귀속은 탐색 순서의 첫 폴더를 따른다.
서로 다른 계정이 같은 그룹을 공유하면 watcher의 `file_alias_conflicts`에 계정 해시·설정 경로·정체성 해시로 기록한다.

**커서.** 폴더별 커서는 `folder_offsets/<tool>-<config_dir_hash>.json`으로 분리한다.
경로 커서는 `resolve()`·플랫폼 `normcase`로, 세션 커서는 상대경로·구분자로 정규화해 저장하며, 이전 표기의 키는 로드 시 합쳐 최댓값을 쓴다.
기존 `ai_offsets.json`·`claude_offsets.json`의 절대경로 커서는 `claude:session:<상대경로>`·`codex:session:<상대경로>` 키로 이전한다.
물리적 별칭의 상대경로가 달라도 모든 별칭에 같은 커서를 저장하므로 첫 폴더가 없어져도 재전송하지 않는다.

**trace_id.** Claude 응답 ID가 있으면 내용 기반 `cc-<hash>`를 유지한다. Codex와 응답 ID가 없는 Claude는 절대경로 대신 세션 정체성 키와 줄 시작 커서로 만든다.

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

### 화면

- PC 연결 AI 카드: 계정이 하나면 기존 배치를 유지한다. 둘 이상이면 카드 안에 계정별 5시간/오늘/7일/월의 요청·토큰·비용과 한도를 나눈다. `account_id=null`은 ‘계정 미상’, 계정별 한도 부재는 ‘확인 불가’, 등록만 있고 기록이 없으면 ‘기록 없음’이다. 여러 계정 카드는 내부 세로 스크롤로 모두 볼 수 있다.
- 위젯: 여러 계정인 도구마다 등록 순서로 최대 3개 계정의 라벨·첫 한도 창을 표시하고 나머지는 `+N개 · 메인 창`으로 안내한다. 더블 클릭은 메인 창을 연다.
- 환경설정 계정 영역: 도구별로 라벨·도구·설정 경로·자동/수동·마지막 관측을 표시한다. 공급자 로그인 이름·이메일·토큰은 표시하지 않는다.
- 로그·차트: 전체/계정별/계정 미상 선택을 유지한다(`account_id`, `account_unknown=true` 쿼리). 선택을 바꾸는 동안 이전 선택의 응답으로 새 화면을 덮지 않는다. 라벨 변경 후 같은 ID의 선택을 유지한다.

## Areas of concern

- Claude 한도는 문서화되지 않은 엔드포인트에 의존한다. 응답 형식이 바뀌면 `확인 불가`로 떨어진다.
- Antigravity 토큰은 본문 길이 ÷ 4 추정치다. 공식 단가가 없는 모델 이름은 비용 계산 불가로 남는다.
