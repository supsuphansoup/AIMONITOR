# 기여 안내

버그 제보·제안·PR 모두 환영합니다. Issues and PRs in English are welcome too.

## 개발 환경

```bash
pip install -r backend/requirements.txt -r desktop/requirements.txt
python -m pytest -q     # 백엔드·데스크톱·SDK 테스트 (저장소 루트에서)
ruff check .            # 린트, 줄 길이 110
pythonw run_desktop.pyw # 앱 실행 (Windows)
```

CI·Docker·릴리스는 루트 `constraints.txt`로 버전을 고정합니다. 같은 환경을 재현하려면 설치 명령에 `-c constraints.txt`를 붙입니다 (Windows 배포 기준 Python 3.11, 백엔드는 3.10도 조건부 버전으로 지원).

exe 빌드: `pip install -c constraints.txt -r build-windows/requirements.txt` 후 `pyinstaller build-windows/aimonitor.spec --noconfirm` → `dist/AIMONITOR/`.
빌드는 `dist/AIMONITOR/` 최상위에 `LICENSE`·`THIRD_PARTY_NOTICES.txt`·`licenses/`를 만들고, 필수 라이선스 원문이 없으면 실패합니다.
배포 전에는 새 PC에서 GUI·트레이·종료·다중 모니터/DPI를 직접 확인합니다.
GUI·DPAPI 테스트는 Windows에서만 돌고, 다른 OS에서는 건너뜁니다. CI는 Linux(3.10–3.12)와 Windows에서 모두 실행합니다.

## 문서 순서

[docs/intent.md](docs/intent.md)(무엇을·왜) → [docs/spec/](docs/spec/README.md)(기능별 규칙과 수치) → [docs/plan.md](docs/plan.md)(지금 할 작업).
동작을 바꾸면 해당 명세를 **같은 커밋에서** 고칩니다. 앱이 읽는 파일이나 외부로 보내는 데이터를 바꾸면 [docs/privacy.md](docs/privacy.md)와 [privacy.en.md](docs/privacy.en.md)도 함께 고칩니다.

## 꼭 지킬 규칙

전체 규칙은 [AGENTS.md](AGENTS.md)에 있습니다. AI 코딩 에이전트도 같은 파일을 읽습니다. 요점은 다음과 같습니다.

- **모르는 값은 0이 아닙니다.** 단가를 모르면 `cost_source='unknown'`, 한도를 모르면 `확인 불가`.
- **원문을 남기지 않습니다.** 프롬프트·응답 텍스트는 DB·로그·오류 응답·CSV 어디에도 남기지 않습니다.
- **시각은 naive UTC로 저장하고 오프셋을 붙여 응답합니다.** 변환은 `backend/app/core/timeutil.py`만 씁니다.
- **단가는 공식 가격 페이지에서 확인합니다.** 기억으로 적지 않고 `PRICING_AS_OF`를 갱신합니다.
- **새 의존성은 먼저 이슈에서 논의합니다.**
- 동작을 바꿨으면 테스트를 추가합니다 (`backend/tests/`, 데스크톱은 `backend/tests/test_desktop_*.py`, SDK는 `sdk/python/tests/`).

## 커밋·PR

- 커밋 메시지는 [Conventional Commits](https://www.conventionalcommits.org/) 형식입니다 (`feat(desktop): …`, `fix: …`, `docs: …`).
- PR에는 무엇을 왜 바꿨는지와 확인 방법(실행한 테스트)을 적어 주세요. 화면이 바뀌면 스크린샷을 붙여 주세요.
- 보안 문제는 공개 이슈 대신 [SECURITY.md](SECURITY.md)의 방법으로 알려 주세요.
