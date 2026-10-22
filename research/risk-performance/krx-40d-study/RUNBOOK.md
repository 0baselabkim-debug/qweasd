# 연구용 수동 재현 및 야간 재개

사용자가 직접 코드를 고칠 필요는 없다. 다음 야간 재개 요청 시 Codex가 아래 절차를 수행한다. 예약·지연 실행은 설정되어 있지 않다.

1. 연구 브랜치 최신 상태 확인. 기존 main과 정규 수집·복구 실행/대기 상태 읽기 전용 확인.
2. `PYTHONPATH=.:research/risk-performance python research/risk-performance/krx40_study.py prepare`
   - 최신 main을 fetch한 뒤 정상 게시 검증, 입력 고정, 공식 달력과 보존 응답 누락 목록을 재계산한다.
   - `plans/<sha>.json` 및 `runs/<sha>/` 결과를 연구 브랜치에 보존한다.
   - 공개 DB가 아니라 `research/.../inputs/<main-commit>/dashboard.duckdb`만 읽는다.
3. 한국시간 23:00~다음 날 09:00 안에서 운영 실행/대기 작업이 없을 때만 수집 가능.
4. 기존 공통 concurrency `kis-validation-auth`, cancel-in-progress=false, 연구 브랜치 한정 수동 러너에서만 실행한다. **현재의 kis-history-probe.yml은 과거 2종목 KIS 시험이므로 그대로 dispatch하지 않는다.** 야간 재개 때 연구 브랜치 한정 수동 진입점을 연결하고 기존 KRX_API_KEY secret만 사용한다. main과 기존 운영 워크플로·cron에는 반영하지 않는다.
5. `PYTHONPATH=.:research/risk-performance python research/risk-performance/krx40_study.py collect-batch --plan research/risk-performance/krx-40d-study/plans/<sha>.json`
   - 최대 20회 후 종료. 전체 80회는 collection/authorization.json 및 events 원장에서 유지.
   - 자동 재시도 없음. 응답 오류의 원인 검토 후 필요한 경우에만 --retry-failed. 접근 제한·불명확한 중단은 별도 검토 전 재시도 불가.
   - 인증값은 환경변수로만 공급하고 명령행 인자·로그에 넣지 않는다.
6. 매 배치 뒤 prepare를 다시 실행해 새 응답을 재검증하고 새 plan을 보존한다. 성공 날짜는 제외되고 남은 예산은 유지된다. 호출 가능 시간·운영 상태가 바뀌면 중단, 자동 대기 없음.
7. 최종 공식 40일 문맥/기업행사/수급/기존 스윙 문맥을 검증한 뒤 기존 prepare_snapshot/build_signal 수용 검사로 이어간다. 이번 승인으로 실제 신호를 저장하지 않는다.
8. `PYTHONPATH=.:research/risk-performance python -m pytest -q`

실제 응답 원장은 `collection/events`, 원문은 `collection/responses`. API_EXECUTION.json은 특정 오프라인 결과 시점의 파생 요약이다. 새 배치 결과는 원장을 다시 읽어 확정해야 하며 예전 요약을 최신 실적처럼 사용하지 않는다.
