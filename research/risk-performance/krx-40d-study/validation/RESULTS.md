# 검증 기록

- 신규 배치/시각/원장/복구 검사: **25개 통과** (`new-tests.txt`).
- 초회 전체 회귀에서 새 코드가 설치되지 않은 PyYAML에 의존한 오류 1개를 발견했다. 운영 의존성을 바꾸지 않고, 지원할 기존 동시실행 블록의 엄격한 리터럴 검사로 교체했다. 당시 실패 기록은 `full-tests-initial-failure.txt`로 보존.
- 수정 후 전체 회귀: **499개 통과, 266.79초** (`full-tests.txt`). 기존 474개와 신규 25개 모두 포함.
- 정확한 코드 커밋: `01b5ffc9d79d1902f3748abcf534ff2d6fc3aacd`.
- GitHub CI: **success**, [실행 39711481993](https://github.com/0baselabkim-debug/qweasd/actions/runs/39711481993). 원본 연구 근거 검사, 전체 pytest 단계, Streamlit HTTP health 확인 성공. 원격 상태 근거는 `github-ci.json`.
- 네트워크 호출을 차단한 오프라인 재실행: 결과 디렉터리/해시 동일, 50.563초 (`reproducibility.json`).
- 초기 계획(보존 연구 자료) 및 최신 main의 추가 보존 응답까지 포함한 최종 계획 실행은 별도 runs에 보존. 최종 결과는 CURRENT_RUN.json이 지정한다.
- 실제 낮 시간 CLI 차단 확인: `daytime-execution-gate.json`. 종료 코드 1은 의도된 차단이며 시장 요청 0회.
- 테스트는 임시 폴더의 가상 HTTP/시각만 사용. 실제 시장 API 호출 없음.
- 운영 branch/DB/JSON/화면/워크플로/cron 수정 없음. 실제 신호 등록 없음.
